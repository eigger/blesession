"""One sentence on what a failed session most likely means — the generic part.

Read from where it died (the primary stage), the error text and the radio
situation. Best effort: the report keeps the exact `error` beside it. A
device's own sentences ("the tag rejected authentication: not a WOLINK
tag", "the cuff was not showing -P-") come from the integration's cause
callback, which build_report() consults first; these fill in when it has
nothing to say.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import stages
from .errors import AttemptTimedOut

WEAK_RSSI_DBM = -85
"""At or below this the placement advice is worth giving; above it the radio
is not the first suspect."""


def placement(facts: Mapping[str, Any], *, noun: str = "device") -> str:
    """Placement advice when the signal is actually weak, else an empty string.

    A single radio is the normal case and on its own says nothing about the
    cause; it is mentioned only alongside a weak signal.
    """
    rssi = facts.get("rssi")
    if not isinstance(rssi, int) or rssi > WEAK_RSSI_DBM:
        return ""
    text = f" The signal is weak ({rssi} dBm via {facts.get('via', 'unknown')})"
    if facts.get("paths") == 1:
        text += f" and no other radio reaches the {noun}"
    return text + f" — move the {noun} or add a proxy."


def generic_cause(
    stage: str | None,
    error: str,
    facts: Mapping[str, Any],
    *,
    exc: BaseException | None = None,
    noun: str = "device",
) -> str | None:
    """The generic sentence for a failure, or None when there is none.

    Keyed on the primary stage and a few error-text markers that every
    transport produces the same way.
    """
    err = error.lower()
    where = placement(facts, noun=noun)
    if isinstance(exc, AttemptTimedOut):
        return (
            "The BLE stack stopped answering mid-session and the attempt was cut at "
            "its bound: usually a wedged adapter or a proxy that died. Restart the "
            "adapter / proxy if it repeats."
        )
    if stage == stages.UNREACHABLE:
        return (
            f"No radio currently sees the {noun}: out of range, asleep, its battery "
            "flat, or the adapter / proxy is down."
        )
    if stage == stages.CONNECT:
        if "slot" in err:
            return (
                "The proxy has no free connection slot; add a proxy or reduce the "
                "BLE devices it serves."
            )
        if "settle" in err:
            return (
                f"The {noun} accepted the link and dropped it before encryption "
                "settled: on a multi-proxy setup usually a proxy that does not hold "
                "the bond, otherwise a stale bond — pair again if it repeats."
            )
        return f"The BLE link could not be established.{where}"
    if stage == stages.SESSION:
        return (
            f"Connected, but the {noun} dropped or refused the session before the "
            "protocol started (service discovery / notifications); usually transient "
            "— if it repeats, the protocol or model may not match."
        )
    if stage == stages.AUTH:
        if "no response" in err:
            return (
                f"The {noun} did not answer the handshake: not ready, or the link dropped.{where}"
            )
        return None
    if stage == stages.TRANSFER:
        if "no response" in err:
            return f"The {noun} stopped answering mid-transfer: link dropped or reset.{where}"
        return None
    if stage == stages.FINISH:
        if "no response" in err:
            return f"The {noun} took the data but did not report completion in time.{where}"
        return None
    if stage == stages.DISCONNECT:
        return (
            "The work was done; only the session close failed. "
            "Harmless unless the next connection is refused."
        )
    return None
