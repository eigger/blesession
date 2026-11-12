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
- The device handle is resolved **inside** the attempt, not when the job was
  queued: the route it carries can be gone, or be a different proxy, after
  the wait for the lock. Inside Home Assistant that is
  `blesession.hass.ble_device_or_raise(hass, address)`.

What it leaves to the integration: the lock instance and its scope, how
many attempts, the bound, the pause, and — via `retry_if` — which failures
deserve another try.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from asyncio import sleep
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from . import stages
from .errors import AttemptTimedOut, error_text
from .stages import StageMap
from .trace import SessionTrace

_LOGGER = logging.getLogger(__name__)

_LOCK_WAIT = "lock_wait"
"""The stage timing that records how long an attempt waited for the lock (the
report's `lock_wait_s`). Not a stage a failure can hit: it is `record`ed, never
`timed`, so it cannot become `failed_stage`."""


@contextlib.asynccontextmanager
async def _held(lock: asyncio.Lock | None, trace: SessionTrace) -> AsyncIterator[None]:
    """Hold `lock` for the block, noting on `trace` how long getting it took.

    The wait is recorded as soon as the lock is won, so it precedes every
    other timing in the report: a user who waited 40 s for another device's
    job sees why the stages add up to 7. Nothing is recorded without a lock,
    and nothing when the wait is cut short (the lock was never held).
    """
    if lock is None:
        yield
        return
    started = perf_counter()
    async with lock:
        trace.record(_LOCK_WAIT, perf_counter() - started)
        yield


def _error_before_timeout(exc: BaseException) -> BaseException | None:
    """The first error in `exc`'s cause/context chain that is not the bound itself.

    The bound arrives as a `CancelledError` and leaves as the `TimeoutError`
    `asyncio.timeout` raises from it; a real error raised before that (the link
    dropped, then the close hung) survives only further down the chain. Guarded
    against a chain that loops back on itself.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        is_bound = current is exc and isinstance(current, TimeoutError)
        if not is_bound and not isinstance(current, asyncio.CancelledError):
            return current
        current = current.__cause__ or current.__context__
    return None


def _note_timeout_truth(trace: SessionTrace, exc: BaseException) -> None:
    """Keep what `AttemptTimedOut` would otherwise hide, as trace facts.

    `error_before_timeout`: the real error that preceded the bound, if any.
    `timed_out_in`: the stage the bound actually interrupted, when that is not
    the stage `failed_stage` names (the first failure wins there).
    """
    if (before := _error_before_timeout(exc)) is not None:
        trace.note(error_before_timeout=error_text(before))
    interrupted = stages.primary_of(trace.interrupted_stage, trace.stage_map)
    if interrupted is not None and interrupted != trace.failed_primary:
        trace.note(timed_out_in=interrupted)


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
    retrying: bool = False
    """Whether another attempt follows this one. Set by `run_attempts()` before
    `on_attempt` sees the attempt, so a recorder can tell a failure that is
    about to be retried from the final one. False on an Attempt built by hand."""
    state: dict[str, Any] = field(default_factory=dict)
    """Scratch for `retry_if` / the attempt function across attempts (pacing
    counters, ...). Copied forward from the previous attempt."""

    @property
    def ok(self) -> bool:
        return self.error is None and self.skipped is None

    @property
    def failed_stage(self) -> str | None:
        """The primary stage the failure hit (see SessionTrace.failure)."""
        return self.trace.failure(self.error)[0]

    @property
    def failed_detail(self) -> str | None:
        return self.trace.failure(self.error)[1]


type AttemptFn[T] = Callable[[Attempt[T]], Awaitable[T]]
type RetryIf[T] = Callable[[Attempt[T]], bool]
type Guard = Callable[[], Awaitable[Any]]
type OnAttempt[T] = Callable[[Attempt[T]], None]


def default_retry_if(attempt: Attempt[Any]) -> bool:
    """Retry anything but a timed-out attempt or an error that says it is final.

    An error is final when it carries `retryable = False` (`GattMismatch`, or
    a `DeviceError(retryable=False)`); any other error is assumed retryable.
    """
    return not attempt.timed_out and getattr(attempt.error, "retryable", True)


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
    raise or publish. An exception from your own `guard`, `on_attempt` or
    `retry_if` is not an attempt failure and propagates (`guard` runs holding
    the lock, which is released on the way out). `retry_if` runs before
    `on_attempt`, so an attempt whose `retry_if` raised is never handed to it.
    `on_attempt` sees every other attempt as it finishes — a
    failed one, a successful one, and one a `guard` declined — outside the
    lock, so recording it cannot hold up other devices. Only the last
    attempt is returned, so `on_attempt` is the only way to record the
    first attempt of a retry that went on to succeed.

    When a `lock` is given, how long the attempt waited for it is recorded on
    its trace as the stage timing `lock_wait` (the report's `lock_wait_s`),
    ahead of every other timing; with no lock there is none. It is a wait,
    not a stage: it never becomes `failed_stage`.

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
        async with _held(lock, attempt.trace):
            if guard is not None and (skipped := await guard()) is not None:
                attempt.skipped = skipped
            else:
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
                        _note_timeout_truth(attempt.trace, exc)
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
        # Whether another attempt follows, decided before `on_attempt` sees
        # this one: only the loop knows (`retry_if` may stop a retry early, a
        # timed-out attempt is final), and a recorder must not infer it from
        # the attempt count. A declined attempt is never retried: the guard
        # decided, not the device.
        attempt.retrying = bool(
            attempt.error is not None
            and attempt.skipped is None
            and number < max_attempts
            and retry_if(attempt)
        )
        # Outside the lock, and for a declined attempt too: `on_attempt` is
        # how a caller records every attempt, and one it never sees is one
        # that cannot be published.
        if on_attempt is not None:
            on_attempt(attempt)
        if not attempt.retrying:
            return attempt
        # Lock released: other devices go first. Module-level `sleep` so an
        # integration's tests can stub the pause.
        await sleep(pause_s)
    assert attempt is not None
    return attempt
