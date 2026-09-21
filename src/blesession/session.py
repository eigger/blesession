"""One BLE session: connect for the duration of a block, then disconnect."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

import bleak
from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import close_stale_connections_by_address, establish_connection

from . import stages
from .errors import ConnectFailed
from .link import probe_link
from .trace import SessionTrace

_LOGGER = logging.getLogger(__name__)

DISCONNECT_TIMEOUT_S = 10.0
"""Default bound on the disconnect, which runs outside any attempt bound: a
proxy that hung the session can hang the disconnect too, and the link is
dropped anyway when the proxy comes back."""

_SETTLE_POLL_STEP_S = 0.25


def current_client_class() -> type[BleakClient]:
    """The BleakClient to connect with, looked up at connect time.

    Inside Home Assistant, habluetooth replaces `bleak.BleakClient` with its
    own wrapper once the bluetooth manager starts (habluetooth/usage.py);
    that wrapper is what routes a connection over the local adapter or an
    ESPHome Bluetooth proxy. Reading the attribute at call time rather than
    at import time means this package works no matter which was imported
    first.
    """
    return bleak.BleakClient


@contextlib.asynccontextmanager
async def ble_session(
    ble_device: BLEDevice,
    *,
    trace: SessionTrace | None = None,
    name: str | None = None,
    settle_s: float = 0.0,
    disconnect_timeout_s: float = DISCONNECT_TIMEOUT_S,
    keep: bool = False,
    close_stale: bool = False,
    **connect_kwargs: Any,
) -> AsyncIterator[BleakClient]:
    """Connect to the device for the duration of the block, then disconnect.

    Connecting happens inside the context so a connection failure raises
    out of the block like any other session error and counts as an attempt.
    The disconnect runs in `finally` with its own bound, and a disconnect
    failure never masks the original error.

    `trace`        times "connect" and "session" (the block) and records the
                   link; the block's own stages nest inside "session"
    `settle_s`     pause after connecting before the first GATT operation, for
                   devices whose encryption settles after the L2CAP link is up;
                   a drop during the settle raises ConnectFailed(detail="settle")
    `keep`         leave the link up on exit (printers' keep_connection); the
                   caller owns it from then on
    `close_stale`  bleak_retry_connector.close_stale_connections_by_address
                   before connecting
    `connect_kwargs`  passed to establish_connection (use_services_cache,
                   pair, max_attempts, ...); `client_class` overrides the
                   BleakClient looked up at connect time (see
                   current_client_class)

    No Home Assistant import is needed for the link to go through a
    Bluetooth proxy: HA patches bleak's client class, and `ble_device` (from
    async_ble_device_from_address) carries the route.
    """
    trace = trace if trace is not None else SessionTrace()
    address = ble_device.address
    client: BleakClient | None = None
    try:
        with trace.timed(stages.CONNECT):
            if close_stale:
                await close_stale_connections_by_address(address)
            client_class = connect_kwargs.pop("client_class", None) or current_client_class()
            try:
                client = await establish_connection(
                    client_class, ble_device, name or address, **connect_kwargs
                )
            except ConnectFailed:
                raise
            except Exception as exc:
                raise ConnectFailed(str(exc) or type(exc).__name__) from exc
            trace.link = probe_link(client, ble_device)
            if settle_s:
                await _settle(client, settle_s)
        with trace.timed(stages.SESSION):
            yield client
    finally:
        if client is not None and not keep and client.is_connected:
            with trace.timed(stages.DISCONNECT):
                try:
                    async with asyncio.timeout(disconnect_timeout_s):
                        await client.disconnect()
                except Exception as exc:  # noqa: BLE001 - never mask the session's error
                    _LOGGER.debug("Disconnect from %s failed (ignored): %s", address, exc)
            # A disconnect failure was swallowed; the session's own failure
            # (if any) was recorded first and stays.
            trace.forgive(stages.DISCONNECT)


async def _settle(client: BleakClient, settle_s: float) -> None:
    """Wait for bonding / encryption to settle, catching a drop early."""
    waited = 0.0
    while waited < settle_s:
        step = min(_SETTLE_POLL_STEP_S, settle_s - waited)
        await asyncio.sleep(step)
        waited += step
        if not client.is_connected:
            raise ConnectFailed(
                f"Link dropped {waited:.2f}s into the post-connect settle", detail="settle"
            )
