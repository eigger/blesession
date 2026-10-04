"""One BLE session, instrumented.

    from blesession import ble_session, Notifications, SessionTrace, stages

    trace = SessionTrace(stage_map={"start": stages.AUTH})
    async with ble_session(ble_device, trace=trace) as client:
        async with Notifications(client, NOTIFY_UUID, settle=0.5) as replies:
            with trace.timed("start"):
                await client.write_gatt_char(WRITE_UUID, START, response=False)
                await replies.next(timeout=5, step="start")
            ...

See docs/design.md for what belongs here and what does not.
"""

from . import stages
from .attempts import Attempt, default_retry_if, run_attempts
from .causes import CAUSES, cause_key, generic_cause, placement
from .errors import (
    AttemptTimedOut,
    BleSessionError,
    ConnectFailed,
    DeviceError,
    GattMismatch,
    NotificationTimeout,
    SessionDropped,
    Unreachable,
    WriteTimeout,
    error_text,
)
from .gatt import characteristic_or_raise
from .link import LinkInfo, connected_via, is_proxy, probe_link
from .notifications import Notifications
from .report import FACT_KEYS, Cause, Failure, SessionReports, build_report, report_attempt
from .session import DISCONNECT_TIMEOUT_S, ble_session, dropped_event, still_up
from .subscribe import NOTIFY_ATTEMPTS, STOP_NOTIFY_TIMEOUT_S, start_notify_with_recovery
from .trace import SessionTrace, traced
from .transfer import WRITE_TIMEOUT_S, guarded_write, write_chunks

__version__ = "0.7.0"
"""The one place the version is written; pyproject.toml reads it."""

__all__ = [
    "CAUSES",
    "Cause",
    "DISCONNECT_TIMEOUT_S",
    "FACT_KEYS",
    "NOTIFY_ATTEMPTS",
    "STOP_NOTIFY_TIMEOUT_S",
    "Attempt",
    "AttemptTimedOut",
    "BleSessionError",
    "ConnectFailed",
    "DeviceError",
    "Failure",
    "GattMismatch",
    "LinkInfo",
    "NotificationTimeout",
    "Notifications",
    "SessionDropped",
    "SessionReports",
    "SessionTrace",
    "Unreachable",
    "WRITE_TIMEOUT_S",
    "WriteTimeout",
    "ble_session",
    "build_report",
    "characteristic_or_raise",
    "cause_key",
    "connected_via",
    "default_retry_if",
    "dropped_event",
    "error_text",
    "generic_cause",
    "guarded_write",
    "is_proxy",
    "placement",
    "probe_link",
    "report_attempt",
    "run_attempts",
    "stages",
    "start_notify_with_recovery",
    "still_up",
    "traced",
    "write_chunks",
]
