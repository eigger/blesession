"""Session errors: every one a ConnectionError, every one naming its stage.

A device that is off, out of range, asleep or unwilling is the ordinary case
for a BLE integration, and must reach Home Assistant as an expected failure
(`UpdateFailed`, `HomeAssistantError`) rather than a traceback that renders
as "this error originated from a custom integration". Deriving from
ConnectionError lets an integration make that mapping in one `except`.
"""

from __future__ import annotations

from . import stages


class BleSessionError(ConnectionError):
    """A session failed in `stage` (a primary stage) with optional `detail`."""

    stage: str | None = None
    detail: str | None = None

    def __init__(self, message: str = "", *, stage: str | None = None, detail: str | None = None):
        super().__init__(message)
        if stage is not None:
            self.stage = stage
        if detail is not None:
            self.detail = detail


class Unreachable(BleSessionError):
    """No connectable radio currently sees the device."""

    stage = stages.UNREACHABLE

    def __init__(self, address: str) -> None:
        super().__init__(
            f"No connectable radio sees {address} "
            "(out of range, asleep, or the adapter / proxy is down)"
        )


class ConnectFailed(BleSessionError):
    """establish_connection raised, or the link dropped during the settle."""

    stage = stages.CONNECT


class SessionDropped(BleSessionError):
    """The link went away mid-session."""

    stage = stages.SESSION


class NotificationTimeout(BleSessionError, TimeoutError):
    """No notification arrived in time. Carries the `step` that was waiting.

    Also a TimeoutError so protocol code that already catches one keeps
    working; unlike asyncio's it always carries a message.
    """

    def __init__(self, timeout: float, *, step: str) -> None:
        super().__init__(f"No response from device within {timeout:g}s after {step}", detail=step)
        self.step = step
        self.timeout = timeout


class AttemptTimedOut(BleSessionError, TimeoutError):
    """The attempt bound fired: the transport is dead, not the device unwilling.

    `stage` is the stage that was running when the bound hit, taken from
    the trace by run_attempts().
    """

    def __init__(self, timeout: float, *, stage: str | None = None, detail: str | None = None):
        super().__init__(
            f"Attempt timed out after {timeout:g}s; the BLE stack stopped answering",
            stage=stage,
            detail=detail,
        )
        self.timeout = timeout


def error_text(exc: BaseException) -> str:
    """The message, or the type name for exceptions that carry none."""
    return str(exc) or type(exc).__name__
