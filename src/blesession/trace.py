"""Where one session spent its time and, if it failed, where it died.

The debug log has all of this, but nobody has debug logging on when a
session fails at 3 am. The trace records the same breakdown as plain data
so the integration can publish it as sensor attributes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from time import perf_counter
from typing import TYPE_CHECKING, Any, TypeVar

from . import stages
from .stages import StageMap

if TYPE_CHECKING:
    from .link import LinkInfo

_T = TypeVar("_T")


class SessionTrace:
    """Per-stage timings, scalar facts, the link, and the stage a failure hit.

    `timed(name)` wraps one stage; it may nest, and the innermost stage an
    exception escapes from is what `failed_stage` records (the first one
    wins, so a close that also fails does not overwrite the real cause).
    `note()` attaches facts such as the number of parts sent.

        trace = SessionTrace(stage_map={"unlock": stages.AUTH, "readout": stages.TRANSFER})
        async with ble_session(device, trace=trace) as client:   # times "connect", "session"
            with trace.timed("unlock"):
                ...
        trace.failed_stage    # "unlock"  (the device's name)
        trace.failed_primary  # "auth"    (what the shared docs call it)
    """

    def __init__(self, stage_map: StageMap | None = None) -> None:
        self.stage_map = stage_map
        self._timings: dict[str, float] = {}
        self._stack: list[str] = []
        self.failed_stage: str | None = None
        self.facts: dict[str, Any] = {}
        self.link: LinkInfo | None = None
        """Which radio the link went over, set by ble_session() after connecting."""

    # ── Stages ───────────────────────────────────────────────────────────

    @property
    def stage(self) -> str | None:
        """The stage currently running, or None between stages."""
        return self._stack[-1] if self._stack else None

    @contextmanager
    def timed(self, name: str) -> Iterator[None]:
        """Time one stage; a repeated stage (a retried unlock) adds up."""
        self._stack.append(name)
        started = perf_counter()
        try:
            yield
        except BaseException:
            # BaseException so a cancellation (the attempt bound) is
            # attributed to the stage it interrupted.
            if self.failed_stage is None:
                self.failed_stage = name
            raise
        finally:
            self._stack.pop()
            self._timings[name] = round(self._timings.get(name, 0.0) + perf_counter() - started, 3)

    def record(self, name: str, seconds: float) -> None:
        """Add a stage measured elsewhere (a session handed over from another
        owner, a test double standing in for a writer)."""
        self._timings[name] = round(self._timings.get(name, 0.0) + seconds, 3)

    def fail(self, name: str) -> None:
        """Record a failure in a stage that reported it by return value.

        `timed` only sees exceptions; a check that returns False and leaves
        the raise to its caller has to say so itself, or the failure is
        attributed to no stage at all.
        """
        if self.failed_stage is None:
            self.failed_stage = name

    def forgive(self, name: str | None = None) -> None:
        """Un-record a failure the caller went on to swallow.

        `name` limits it to that stage; None clears whichever stage it was
        (a retry loop that swallows anything its attempt raised).
        """
        if name is None or self.failed_stage == name:
            self.failed_stage = None

    @property
    def failed_primary(self) -> str | None:
        """`failed_stage` mapped to the primary vocabulary."""
        return stages.primary_of(self.failed_stage, self.stage_map)

    @property
    def failed_detail(self) -> str | None:
        """The device's own stage name when it differs from the primary one."""
        if self.failed_stage is None or self.failed_stage == self.failed_primary:
            return None
        return self.failed_stage

    # ── Facts ────────────────────────────────────────────────────────────

    def note(self, **facts: Any) -> None:
        """Record scalar facts about the session (None values are dropped)."""
        for key, value in facts.items():
            if value is not None:
                self.facts[key] = value

    @property
    def timings(self) -> dict[str, float]:
        """Seconds per stage, in the order the stages finished."""
        return dict(self._timings)

    def as_dict(self) -> dict[str, Any]:
        """The failed stage (if any), the facts, then each stage's seconds as
        `<stage>_s`."""
        return {
            **({"failed_stage": self.failed_stage} if self.failed_stage else {}),
            **self.facts,
            **{f"{name}_s": seconds for name, seconds in self._timings.items()},
        }


def traced(
    stage: str,
) -> Callable[[Callable[..., Awaitable[_T]]], Callable[..., Awaitable[_T]]]:
    """Time a method as one stage of `self.trace`.

    A host without a trace runs the method untimed rather than failing on
    the bookkeeping.
    """

    def decorate(method: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
        @wraps(method)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> _T:
            trace = getattr(self, "trace", None)
            if trace is None:
                return await method(self, *args, **kwargs)
            with trace.timed(stage):
                return await method(self, *args, **kwargs)

        return wrapper

    return decorate
