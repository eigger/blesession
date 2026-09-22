"""One sentence on what a failed session most likely means — the generic part.

Read from where it died (the primary stage), what kind of failure it was and
the radio situation. Best effort: the report keeps the exact `error` beside
it. A device's own sentences ("the tag rejected authentication: not a WOLINK
tag", "the cuff was not showing -P-") come from the integration's cause
callback, which build_report() consults first; these fill in when it has
nothing to say.

The kind of failure is read from the exception *and* from the error text,
never from one or the other: the type carries an integration that worded
its own timeout (`NotificationTimeout(message=...)`), and the text carries a
failure that says the same thing without carrying the type. `build_report()`
always has the exception to hand, so a text marker behind an
"only when there is no exception" guard would never be read at all.
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


def cause_key(stage: str | None, error: str, *, exc: BaseException | None = None) -> str | None:
    """The stable key for a failure, or None when there is nothing to say.

    The one place that decides *which* generic reading a failure gets;
    `generic_cause()` only renders the text for it. A key is part of the
    report contract exactly as a stage name is — it goes in the CHANGELOG
    when it changes — so an integration can translate the sentence for
    Home Assistant instead of publishing this English one.
    """
    err = error.lower()
    # The type first, so a NotificationTimeout an integration worded itself
    # still reads as one; the text marker stays beside it, so a failure that
    # says as much without carrying the type keeps its sentence too. Both,
    # never either — as the `settle` branch below does it.
    unanswered = isinstance(exc, NotificationTimeout) or "no response" in err
    lost = isinstance(exc, SessionDropped) or "link dropped" in err
    if isinstance(exc, AttemptTimedOut):
        return "attempt_timed_out"
    if stage == stages.UNREACHABLE:
        return "unreachable"
    if stage == stages.CONNECT:
        if "slot" in err:
            return "connect.no_slot"
        if getattr(exc, "detail", None) == "settle" or "settle" in err:
            return "connect.settle"
        return "connect.failed"
    if lost and stage != stages.DISCONNECT:
        # A drop the *close* reported is the close failing, not the session:
        # the work was already done, and the stage below says so.
        return "link_lost"
    if stage == stages.SESSION:
        return "session.refused"
    if stage == stages.AUTH:
        return "auth.no_answer" if unanswered else None
    if stage == stages.TRANSFER:
        return "transfer.no_answer" if unanswered else None
    if stage == stages.FINISH:
        return "finish.no_answer" if unanswered else None
    if stage == stages.DISCONNECT:
        return "disconnect.close_failed"
    return None


CAUSES: dict[str, str] = {
    "attempt_timed_out": (
        "The BLE stack stopped answering mid-session and the attempt was cut at "
        "its bound: usually a wedged adapter or a proxy that died. Restart the "
        "adapter / proxy if it repeats."
    ),
    "unreachable": (
        "No radio currently sees the {noun}: out of range, asleep, its battery "
        "flat, or the adapter / proxy is down."
    ),
    "connect.no_slot": (
        "The proxy has no free connection slot; add a proxy or reduce the BLE devices it serves."
    ),
    "connect.settle": (
        "The {noun} accepted the link and dropped it before encryption "
        "settled: on a multi-proxy setup usually a proxy that does not hold "
        "the bond, otherwise a stale bond — pair again if it repeats."
    ),
    "connect.failed": "The BLE link could not be established.{where}",
    "link_lost": (
        "The link to the {noun} went away mid-session: out of range, powered "
        "down, or the adapter / proxy reset.{where}"
    ),
    "session.refused": (
        "Connected, but the {noun} dropped or refused the session before the "
        "protocol started (service discovery / notifications); usually transient "
        "— if it repeats, the protocol or model may not match."
    ),
    "auth.no_answer": (
        "The {noun} did not answer the handshake: not ready, or the link dropped.{where}"
    ),
    "transfer.no_answer": (
        "The {noun} stopped answering mid-transfer: link dropped or reset.{where}"
    ),
    "finish.no_answer": ("The {noun} took the data but did not report completion in time.{where}"),
    "disconnect.close_failed": (
        "The work was done; only the session close failed. "
        "Harmless unless the next connection is refused."
    ),
}
"""Every key `cause_key()` can return, and the English sentence for it.

`{noun}` is what the integration calls the device. `{where}` is the
placement advice, empty unless the signal is actually weak — and English
too, so a translation does not translate that fragment: it rebuilds the
advice from `rssi`, `via` and `paths`, which the report already carries,
under the same `rssi <= WEAK_RSSI_DBM` and `paths == 1` rules `placement()`
uses.
"""


def generic_cause(
    stage: str | None,
    error: str,
    facts: Mapping[str, Any],
    *,
    exc: BaseException | None = None,
    noun: str = "device",
) -> str | None:
    """The generic sentence for a failure, or None when there is none.

    `cause_key()` picks the reading; this renders it. The pair is the whole
    generic table: a report carries both, so a reader gets the sentence and
    an integration can translate it.
    """
    key = cause_key(stage, error, exc=exc)
    if key is None:
        return None
    return CAUSES[key].format(noun=noun, where=placement(facts, noun=noun))
