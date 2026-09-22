"""One sentence on what a failed session most likely means — the generic part.

Read from where it died (the primary stage), what kind of failure it was and
the radio situation. Best effort: the report keeps the exact `error` beside
it. A device's own sentences ("the tag rejected authentication: not a WOLINK
tag", "the cuff was not showing -P-") come from the integration's cause
callback, which build_report() consults first; these fill in when it has
nothing to say.

The kind of failure is read from the exception where there is one, so an
integration that words its own timeout (`NotificationTimeout(message=...)`)
keeps the generic sentence; the error text is only the fallback for a caller
that has no exception to hand.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import stages
from .errors import AttemptTimedOut, NotificationTimeout, SessionDropped

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

    Keyed on the primary stage and the kind of failure: the attempt bound,
    a link that dropped, a step that went unanswered.
    """
    err = error.lower()
    where = placement(facts, noun=noun)
    # The type first, so a NotificationTimeout an integration worded itself
    # still reads as one; the text marker stays beside it, so a failure that
    # says as much without carrying the type keeps its sentence too. Both,
    # never either — as the `settle` branch below does it.
    unanswered = isinstance(exc, NotificationTimeout) or "no response" in err
    lost = isinstance(exc, SessionDropped) or "link dropped" in err
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
        if getattr(exc, "detail", None) == "settle" or "settle" in err:
            return (
                f"The {noun} accepted the link and dropped it before encryption "
                "settled: on a multi-proxy setup usually a proxy that does not hold "
                "the bond, otherwise a stale bond — pair again if it repeats."
            )
        return f"The BLE link could not be established.{where}"
    if lost:
        return (
            f"The link to the {noun} went away mid-session: out of range, powered "
            f"down, or the adapter / proxy reset.{where}"
        )
    if stage == stages.SESSION:
        return (
            f"Connected, but the {noun} dropped or refused the session before the "
            "protocol started (service discovery / notifications); usually transient "
            "— if it repeats, the protocol or model may not match."
        )
    if stage == stages.AUTH:
        if unanswered:
            return (
                f"The {noun} did not answer the handshake: not ready, or the link dropped.{where}"
            )
        return None
    if stage == stages.TRANSFER:
        if unanswered:
            return f"The {noun} stopped answering mid-transfer: link dropped or reset.{where}"
        return None
    if stage == stages.FINISH:
        if unanswered:
            return f"The {noun} took the data but did not report completion in time.{where}"
        return None
    if stage == stages.DISCONNECT:
        return (
            "The work was done; only the session close failed. "
            "Harmless unless the next connection is refused."
        )
    return None
