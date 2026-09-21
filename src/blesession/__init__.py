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
from .causes import generic_cause, placement
from .errors import (
    AttemptTimedOut,
    BleSessionError,
    ConnectFailed,
    NotificationTimeout,
    SessionDropped,
    Unreachable,
    error_text,
)
from .link import LinkInfo, connected_via, is_proxy, probe_link
from .notifications import Notifications
from .report import FACT_KEYS, build_report, report_attempt
from .session import DISCONNECT_TIMEOUT_S, ble_session
from .trace import SessionTrace, traced

__version__ = "0.1.0a1"

__all__ = [
    "DISCONNECT_TIMEOUT_S",
    "FACT_KEYS",
    "Attempt",
    "AttemptTimedOut",
    "BleSessionError",
    "ConnectFailed",
    "LinkInfo",
    "NotificationTimeout",
    "Notifications",
    "SessionDropped",
    "SessionTrace",
    "Unreachable",
    "ble_session",
    "build_report",
    "connected_via",
    "default_retry_if",
    "error_text",
    "generic_cause",
    "is_proxy",
    "placement",
    "probe_link",
    "report_attempt",
    "run_attempts",
    "stages",
    "traced",
]
