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
    retryable: bool = True
    """Whether another attempt can change the outcome. `default_retry_if`
    reads it; an error that is deterministic (the device lacks the profile)
    says False so no integration has to special-case it."""

    def __init__(self, message: str = "", *, stage: str | None = None, detail: str | None = None):
        super().__init__(message)
        if stage is not None:
            self.stage = stage
        if detail is not None:
            self.detail = detail


class Unreachable(BleSessionError):
    """No radio currently sees the device.

    `connectable` is what was asked for, and only wording: a handle looked
    up with `connectable=False` was not refused for being unconnectable, so
    saying no *connectable* radio saw it would name the wrong reason.
    """

    stage = stages.UNREACHABLE

    def __init__(self, address: str, *, connectable: bool = True) -> None:
        super().__init__(
            f"No {'connectable radio' if connectable else 'radio'} sees {address} "
            "(out of range, asleep, or the adapter / proxy is down)"
        )


class ConnectFailed(BleSessionError):
    """establish_connection raised, or the link dropped during the settle."""

    stage = stages.CONNECT


class SessionDropped(BleSessionError):
    """The link went away mid-session."""

    stage = stages.SESSION


class GattMismatch(BleSessionError):
    """The link is up but the device does not expose what the protocol needs.

    A service or characteristic is missing, lacks a property, or the write
    size is too small for the protocol's frames: another model or firmware,
    not a flaky link.
    """

    stage = stages.SESSION
    retryable = False


class DeviceError(BleSessionError):
    """The device answered, and the answer was an error.

    A device-reported fault (an error frame, a rejected key, a NAK) as
    opposed to silence or a lost link. `code` is the device's own code, when
    it has one. The wording and what each code means stay in the integration:
    raise this (or a subclass) from the protocol code, test its type and
    `code` in the `cause` callback, and the generic sentences leave it alone.
    `retryable=False` stops the attempt loop for a fault a retry cannot fix
    (a rejected key).
    """

    def __init__(
        self,
        message: str = "",
        *,
        code: int | str | None = None,
        retryable: bool | None = None,
        stage: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message, stage=stage, detail=detail)
        self.code = code
        if retryable is not None:
            self.retryable = retryable


class NotificationTimeout(BleSessionError, TimeoutError):
    """No notification arrived in time. Carries the `step` that was waiting.

    Also a TimeoutError so protocol code that already catches one keeps
    working; unlike asyncio's it always carries a message.
    """

    def __init__(self, timeout: float, *, step: str, message: str | None = None) -> None:
        super().__init__(
            message or f"No response from device within {timeout:g}s after {step}", detail=step
        )
        self.step = step
        self.timeout = timeout


class WriteTimeout(NotificationTimeout):
    """A write did not complete in time: the adapter or proxy stopped taking data.

    A `NotificationTimeout` (so `except NotificationTimeout` and
    `except TimeoutError` keep working, and it carries `step` and `timeout`),
    but not silence from the device: the write never returned. The reports
    tell the two apart.
    """

    def __init__(self, timeout: float, *, step: str) -> None:
        super().__init__(
            timeout,
            step=step,
            message=f"A write did not complete within {timeout:g}s during {step}",
        )


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
