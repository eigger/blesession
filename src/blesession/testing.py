"""A bleak client stand-in for integration tests.

Every integration mocks the same four methods with MagicMock; this is the
one fake, so a test can push notifications and script disconnects the same
way everywhere.

    client = FakeClient()
    client.reply(b"\x01")                    # queued for the next start_notify handler
    async with Notifications(client, "uuid") as replies:
        assert await replies.next(1, step="x") == b"\x01"
    client.drop()                             # is_connected -> False from now on
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


class FakeClient:
    """Minimal bleak.BleakClient look-alike."""

    def __init__(self, *, address: str = "AA:BB:CC:DD:EE:FF", connected: bool = True) -> None:
        self.address = address
        self.is_connected = connected
        self.writes: list[tuple[Any, bytes, bool]] = []
        self.subscribed: dict[Any, Callable[[Any, bytearray], None]] = {}
        self.disconnects = 0
        self.fail_start_notify: BaseException | None = None
        self.fail_stop_notify: BaseException | None = None
        self.fail_disconnect: BaseException | None = None
        self.fail_write: BaseException | None = None
        self.disconnect_delay_s: float = 0.0
        self._pending: list[bytes] = []

    # ── bleak surface ────────────────────────────────────────────────────

    async def start_notify(
        self, characteristic: Any, handler: Callable[[Any, bytearray], None]
    ) -> None:
        if self.fail_start_notify is not None:
            raise self.fail_start_notify
        self.subscribed[characteristic] = handler
        pending, self._pending = self._pending, []
        for data in pending:
            handler(characteristic, bytearray(data))

    async def stop_notify(self, characteristic: Any) -> None:
        if self.fail_stop_notify is not None:
            raise self.fail_stop_notify
        self.subscribed.pop(characteristic, None)

    async def write_gatt_char(
        self, characteristic: Any, data: bytes, response: bool = False
    ) -> None:
        if self.fail_write is not None:
            raise self.fail_write
        self.writes.append((characteristic, bytes(data), response))

    async def disconnect(self) -> None:
        self.disconnects += 1
        if self.disconnect_delay_s:
            await asyncio.sleep(self.disconnect_delay_s)
        if self.fail_disconnect is not None:
            raise self.fail_disconnect
        self.is_connected = False

    # ── test controls ────────────────────────────────────────────────────

    def reply(self, data: bytes, characteristic: Any = None) -> None:
        """Deliver a notification now, or queue it for the next subscriber."""
        if characteristic is None and len(self.subscribed) == 1:
            characteristic = next(iter(self.subscribed))
        handler = self.subscribed.get(characteristic)
        if handler is None:
            self._pending.append(bytes(data))
        else:
            handler(characteristic, bytearray(data))

    def drop(self) -> None:
        """The link went away."""
        self.is_connected = False


class FakeDevice:
    """Minimal bleak BLEDevice look-alike."""

    def __init__(
        self, address: str = "AA:BB:CC:DD:EE:FF", name: str | None = None, details: Any = None
    ):
        self.address = address
        self.name = name
        self.details = details if details is not None else {}


def fake_connect(
    client: FakeClient | None = None, *, fail: BaseException | None = None
) -> Callable[..., Any]:
    """An establish_connection replacement returning `client` (or raising `fail`).

    monkeypatch.setattr(blesession.session, "establish_connection", fake_connect(client))
    """

    async def _connect(_cls: Any, _device: Any, _name: str, **_kwargs: Any) -> FakeClient:
        if fail is not None:
            raise fail
        return client if client is not None else FakeClient()

    return _connect
