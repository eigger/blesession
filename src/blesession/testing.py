"""A bleak client stand-in for integration tests.

Every integration mocks the same four methods with MagicMock; this is the
one fake, so a test can push notifications and script disconnects the same
way everywhere.

    client = FakeClient()
    client.reply(b"\x01")                    # queued for the next start_notify handler
    client.on_write = lambda char, data: client.reply(b"\x02")   # answer each command
    async with Notifications(client, "uuid") as replies:
        assert await replies.next(1, step="x") == b"\x01"
    client.drop()                             # link gone: is_connected False, callback fired

    client.add_characteristic(SERVICE, CHAR)  # what characteristic_or_raise() looks up
    client.write_delay_s = 3600               # a write that hangs (WriteTimeout)
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


class FakeCharacteristic:
    """The parts of a bleak characteristic a protocol checks."""

    def __init__(
        self,
        uuid: str,
        *,
        properties: tuple[str, ...] = ("write-without-response", "notify"),
        max_write_without_response_size: int = 244,
    ) -> None:
        self.uuid = uuid
        self.properties = list(properties)
        self.max_write_without_response_size = max_write_without_response_size


class FakeService:
    """A service: `get_characteristic(uuid)`, case-insensitive like bleak."""

    def __init__(self, uuid: str) -> None:
        self.uuid = uuid
        self.characteristics: dict[str, FakeCharacteristic] = {}

    def get_characteristic(self, uuid: str) -> FakeCharacteristic | None:
        return self.characteristics.get(str(uuid).lower())


class FakeServices:
    """`client.services`: `get_service(uuid)`, None when the device lacks it."""

    def __init__(self) -> None:
        self.by_uuid: dict[str, FakeService] = {}

    def get_service(self, uuid: str) -> FakeService | None:
        return self.by_uuid.get(str(uuid).lower())


class FakeClient:
    """Minimal bleak.BleakClient look-alike."""

    def __init__(self, *, address: str = "AA:BB:CC:DD:EE:FF", connected: bool = True) -> None:
        self.address = address
        self.is_connected = connected
        self.writes: list[tuple[Any, bytes, bool | None]] = []
        self.subscribed: dict[Any, Callable[[Any, bytearray], None]] = {}
        self.disconnects = 0
        self.fail_start_notify: BaseException | None = None
        self.fail_stop_notify: BaseException | None = None
        self.fail_disconnect: BaseException | None = None
        self.fail_write: BaseException | None = None
        self.disconnect_delay_s: float = 0.0
        self.on_write: Callable[[Any, bytes], None] | None = None
        """Called after each recorded write: answer a command with `reply()`."""
        self.write_delay_s: float = 0.0
        """A write that takes this long (use a large value for one that hangs)."""
        self.services = FakeServices()
        self.disconnected_callback: Callable[[Any], None] | None = None
        """Set by fake_connect(); fired by drop() and disconnect(), as bleak does."""
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
        self, characteristic: Any, data: bytes, response: bool | None = False
    ) -> None:
        if self.write_delay_s:
            await asyncio.sleep(self.write_delay_s)
        if self.fail_write is not None:
            raise self.fail_write
        self.writes.append((characteristic, bytes(data), response))
        if self.on_write is not None:
            self.on_write(characteristic, bytes(data))

    async def disconnect(self) -> None:
        self.disconnects += 1
        if self.disconnect_delay_s:
            await asyncio.sleep(self.disconnect_delay_s)
        if self.fail_disconnect is not None:
            raise self.fail_disconnect
        self.drop()

    # ── test controls ────────────────────────────────────────────────────

    def add_characteristic(
        self,
        service_uuid: str,
        char_uuid: str,
        *,
        properties: tuple[str, ...] = ("write-without-response", "notify"),
        max_write_without_response_size: int = 244,
    ) -> FakeCharacteristic:
        """Expose a characteristic, so `characteristic_or_raise()` finds it."""
        service = self.services.by_uuid.setdefault(service_uuid.lower(), FakeService(service_uuid))
        char = FakeCharacteristic(
            char_uuid,
            properties=properties,
            max_write_without_response_size=max_write_without_response_size,
        )
        service.characteristics[char_uuid.lower()] = char
        return char

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
        """The link went away: is_connected goes False and the disconnect
        callback fires, so a wait on the link ends the way it does on device.

        Dropping an already dropped link does nothing; bleak calls the
        callback once per link.
        """
        if not self.is_connected:
            return
        self.is_connected = False
        if self.disconnected_callback is not None:
            self.disconnected_callback(self)


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

    The `disconnected_callback` ble_session() passes is wired to the client,
    so `client.drop()` reaches the session the way a real disconnect does.

    monkeypatch.setattr(blesession.session, "establish_connection", fake_connect(client))
    """

    async def _connect(_cls: Any, _device: Any, _name: str, **kwargs: Any) -> FakeClient:
        if fail is not None:
            raise fail
        connected = client if client is not None else FakeClient()
        connected.disconnected_callback = kwargs.get("disconnected_callback")
        return connected

    return _connect
