"""The fixed stage vocabulary every session reports in.

A session is a chain of stages, and a failure in each one means something
different: `connect` is usually a device that is asleep, `auth` a key it no
longer accepts, `transfer` a link that dropped mid-way. Integrations time
their own, finer stages (`unlock`, `readout`, `start`), and map each to one
of these primary stages so the generic cause sentences and troubleshooting
docs can be shared across devices.
"""

from __future__ import annotations

from collections.abc import Mapping

UNREACHABLE = "unreachable"
"""No radio sees the device; nothing was tried."""
CONNECT = "connect"
"""The BLE link never came up (or dropped during a post-connect settle)."""
SESSION = "session"
"""Connected, but failed before the protocol's first stage: service
discovery, the notification subscribe."""
AUTH = "auth"
"""Authentication, unlock, the START handshake — whatever must succeed
before data can flow."""
TRANSFER = "transfer"
"""The data exchange itself."""
FINISH = "finish"
"""The completion wait after the last data frame: a panel refresh, an
end-command acknowledgement, closing a memory session."""
DISCONNECT = "disconnect"
"""Only the close failed; the work was done."""

PRIMARY: frozenset[str] = frozenset(
    {UNREACHABLE, CONNECT, SESSION, AUTH, TRANSFER, FINISH, DISCONNECT}
)
"""The stages the generic cause sentences and shared docs know."""

ORDER: tuple[str, ...] = (UNREACHABLE, CONNECT, SESSION, AUTH, TRANSFER, FINISH, DISCONNECT)
"""Primary stages in the order a session passes through them."""

StageMap = Mapping[str, str]
"""Device stage name -> primary stage. A primary name maps to itself."""


def primary_of(name: str | None, stage_map: StageMap | None = None) -> str | None:
    """The primary stage for a device stage name.

    A name that is itself primary needs no map entry. A name the map does not
    know is returned unchanged so it still shows up in the report rather than
    being silently dropped; the cause sentences then have nothing generic to
    say about it, which is the honest answer.
    """
    if name is None:
        return None
    if name in PRIMARY:
        return name
    if stage_map and name in stage_map:
        return stage_map[name]
    return name
