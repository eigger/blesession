"""Attempts, the lock, and the bound: the contract, with the policy left to you.

What the loop fixes, each learnt from an integration that got it wrong first:

- The lock is held for **one attempt**, not the whole retry sequence, so
  between attempts (the pause, or after a timed-out attempt) other devices
  go first.
- Every attempt has a bound, connecting included. A GATT write has no
  timeout of its own; a proxy that dies mid-transfer leaves the attempt
  hanging, and while it holds the lock every other device hangs with it.
- The checks that can change while waiting for the lock (a write lock, a
  superseded job, a duplicate payload) run under the lock, before the
  attempt, via `guard`.
- A timed-out attempt is not retried by default: the transport is dead, not
  the device unwilling, and a retry would only hang the lock again.

What it leaves to the integration: the lock instance and its scope, how
many attempts, the bound, the pause, and — via `retry_if` — which failures
deserve another try.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import AttemptTimedOut
from .stages import StageMap
from .trace import SessionTrace

_LOGGER = logging.getLogger(__name__)


@dataclass
class Attempt[T]:
    """One try: its number, its trace, and how it ended."""

    number: int
    trace: SessionTrace
    result: T | None = None
    error: BaseException | None = None
    timed_out: bool = False
    skipped: Any = None
    """What `guard` returned when it declined to run this attempt."""
    state: dict[str, Any] = field(default_factory=dict)
    """Scratch for `retry_if` / the attempt function across attempts (pacing
    counters, ...). Copied forward from the previous attempt."""

    @property
    def ok(self) -> bool:
        return self.error is None and self.skipped is None

    @property
    def failed_stage(self) -> str | None:
        """The primary stage the failure hit, from the trace or the error."""
        if self.error is None:
            return None
        if (stage := self.trace.failed_primary) is not None:
            return stage
        return getattr(self.error, "stage", None)

    @property
    def failed_detail(self) -> str | None:
        if self.error is None:
            return None
        if (detail := self.trace.failed_detail) is not None:
            return detail
        return getattr(self.error, "detail", None)


type AttemptFn[T] = Callable[[Attempt[T]], Awaitable[T]]
type RetryIf[T] = Callable[[Attempt[T]], bool]
type Guard = Callable[[], Awaitable[Any]]
type OnAttempt[T] = Callable[[Attempt[T]], None]


def default_retry_if(attempt: Attempt[Any]) -> bool:
    """Retry anything but a timed-out attempt."""
    return not attempt.timed_out


async def run_attempts[T](
    attempt_fn: AttemptFn[T],
    *,
    lock: asyncio.Lock | None = None,
    max_attempts: int = 1,
    attempt_timeout_s: float | None = None,
    pause_s: float = 1.0,
    retry_if: RetryIf[T] = default_retry_if,
    guard: Guard | None = None,
    on_attempt: OnAttempt[T] | None = None,
    stage_map: StageMap | None = None,
    name: str = "",
) -> Attempt[T]:
    """Run `attempt_fn` up to `max_attempts` times and return the last Attempt.

    Never raises for a failed attempt: the returned Attempt carries the
    error, the trace and the stage, and the integration decides what to
    raise or publish. `on_attempt` sees every attempt as it finishes (for
    logging, or recording each one on a sensor).

    `attempt_fn` receives the Attempt (use `attempt.trace` for its stages
    and `attempt.state` for anything carried between attempts) and returns
    the result or raises.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    state: dict[str, Any] = {}
    attempt: Attempt[T] | None = None
    for number in range(1, max_attempts + 1):
        attempt = Attempt(number=number, trace=SessionTrace(stage_map), state=dict(state))
        async with lock if lock is not None else contextlib.nullcontext():
            if guard is not None and (skipped := await guard()) is not None:
                attempt.skipped = skipped
                return attempt
            bound = asyncio.timeout(attempt_timeout_s)
            try:
                async with bound:
                    attempt.result = await attempt_fn(attempt)
            except Exception as exc:  # noqa: BLE001 - every failure is an attempt outcome
                attempt.error = exc
                if bound.expired():
                    attempt.timed_out = True
                    assert attempt_timeout_s is not None
                    timed_out = AttemptTimedOut(
                        attempt_timeout_s,
                        stage=attempt.trace.failed_primary,
                        detail=attempt.trace.failed_detail,
                    )
                    timed_out.__cause__ = exc
                    attempt.error = timed_out
                _LOGGER.debug(
                    "%s attempt %d/%d failed in %s: %s",
                    name or "session",
                    number,
                    max_attempts,
                    attempt.failed_stage or "?",
                    attempt.error,
                    exc_info=attempt.error,
                )
        state = attempt.state
        if on_attempt is not None:
            on_attempt(attempt)
        if attempt.ok or number == max_attempts or not retry_if(attempt):
            return attempt
        # Lock released: other devices go first.
        await asyncio.sleep(pause_s)
    assert attempt is not None
    return attempt
