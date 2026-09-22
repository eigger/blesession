"""One BLE session: connect for the duration of a block, then disconnect."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from time import perf_counter
from typing import Any
from weakref import WeakKeyDictionary

import bleak
from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import close_stale_connections_by_address, establish_connection

from . import stages
from .errors import ConnectFailed, error_text
from .link import probe_link
from .trace import SessionTrace

_LOGGER = logging.getLogger(__name__)

DISCONNECT_TIMEOUT_S = 10.0
"""Default bound on the disconnect, which runs outside any attempt bound: a
proxy that hung the session can hang the disconnect too, and the link is
dropped anyway when the proxy comes back."""

_SETTLE_POLL_STEP_S = 0.25

_DROPPED: WeakKeyDictionary[Any, asyncio.Event] = WeakKeyDictionary()


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


def dropped_event(client: Any) -> asyncio.Event | None:
    """The event set when this client's link drops, or None if nobody watches it.

    `ble_session()` registers one per client from the disconnect callback it
    passes to establish_connection. `Notifications` looks it up so a wait
    fails as `SessionDropped` the moment the link goes, instead of running
    its step timeout out against a device that is no longer there.
    """
    try:
        return _DROPPED.get(client)
    except TypeError:  # a client that cannot be weak-referenced
        return None


def _watch_drops(client: Any, dropped: asyncio.Event) -> None:
    with contextlib.suppress(TypeError):
        _DROPPED[client] = dropped


def still_up(client: BleakClient | None) -> bool:
    """True when a client handed back by a `keep=True` session can carry another.

    `is_connected` alone is not the whole answer: the drop event this
    module registered is the same callback the link has been reporting to
    since it came up, so a link that went away between sessions is known
    here even where `is_connected` has not caught up.
    """
    if client is None or not client.is_connected:
        return False
    dropped = dropped_event(client)
    return dropped is None or not dropped.is_set()


@contextlib.asynccontextmanager
async def ble_session(
    ble_device: BLEDevice,
    *,
    trace: SessionTrace | None = None,
    name: str | None = None,
    client: BleakClient | None = None,
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
                   link; the block's own stages nest inside "session". A
                   disconnect that fails is noted as `disconnect_error`
                   rather than failing the session.
    `client`       a link a previous `keep=True` session left up, to run this
                   session on. It is used only if it is still up (see
                   `still_up`); a stale one is ignored and a fresh link
                   opened, so the caller never has to check. A reused link
                   times no `connect` stage — there was nothing to connect —
                   and the trace notes `reused=True` so a missing `connect_s`
                   reads as "there was none" rather than as a measurement
                   that went missing. `settle_s`, `close_stale` and the
                   connect kwargs describe opening a link and are not applied
                   to one already up.
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
                   current_client_class). A `disconnected_callback` of your
                   own still runs — the session chains its own in front.

    The link is watched for the whole session: `dropped_event(client)` is set
    the moment it goes, and `Notifications` waits on it (see SessionDropped).
    A reused link keeps the watch it already had.

    Keeping a link across sessions is two lines in the caller, and the stale
    handle is this function's problem rather than theirs:

        async with ble_session(device, client=self._client, keep=self._keep) as client:
            self._client = client if self._keep else None
            ...

    No Home Assistant import is needed for the link to go through a
    Bluetooth proxy: HA patches bleak's client class, and `ble_device` (from
    async_ble_device_from_address) carries the route.
    """
    trace = trace if trace is not None else SessionTrace()
    address = ble_device.address
    reused = client if still_up(client) else None
    client = reused
    dropped = asyncio.Event()
    caller_callback: Callable[[Any], None] | None = connect_kwargs.pop(
        "disconnected_callback", None
    )

    def on_disconnect(disconnected: Any) -> None:
        dropped.set()
        if caller_callback is not None:
            caller_callback(disconnected)

    try:
        if reused is not None:
            trace.note(reused=True)
            trace.link = probe_link(reused, ble_device)
        else:
            with trace.timed(stages.CONNECT):
                if close_stale:
                    await close_stale_connections_by_address(address)
                client_class = connect_kwargs.pop("client_class", None) or current_client_class()
                try:
                    client = await establish_connection(
                        client_class,
                        ble_device,
                        name or address,
                        disconnected_callback=on_disconnect,
                        **connect_kwargs,
                    )
                except ConnectFailed:
                    raise
                except Exception as exc:
                    raise ConnectFailed(str(exc) or type(exc).__name__) from exc
                # establish_connection builds the client once and retries
                # connect() on it, so a failed attempt can already have fired
                # the callback. The link in hand is up; anything before it was
                # about a link that never was. Nothing awaits in between, so no
                # real drop can be cleared here.
                dropped.clear()
                _watch_drops(client, dropped)
                trace.link = probe_link(client, ble_device)
                if settle_s:
                    await _settle(client, settle_s, dropped)
        assert client is not None  # both branches above set it or raised
        with trace.timed(stages.SESSION):
            yield client
    finally:
        if client is not None and not keep and client.is_connected:
            with trace.timed(stages.DISCONNECT):
                try:
                    async with asyncio.timeout(disconnect_timeout_s):
                        await client.disconnect()
                except Exception as exc:  # noqa: BLE001 - never mask the session's error
                    # The work was done; only the close failed. Kept as a
                    # fact rather than a failure so it reaches the report
                    # instead of disappearing (see docs/design.md §3).
                    _LOGGER.debug("Disconnect from %s failed (ignored): %s", address, exc)
                    trace.note(disconnect_error=error_text(exc))


async def _settle(client: BleakClient, settle_s: float, dropped: asyncio.Event) -> None:
    """Wait for bonding / encryption to settle, catching a drop early."""
    started = perf_counter()
    while (left := settle_s - (perf_counter() - started)) > 0:
        # The disconnect callback is the fast path; is_connected is the
        # fallback for a backend that never fires one.
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(min(_SETTLE_POLL_STEP_S, left)):
                await dropped.wait()
        if dropped.is_set() or not client.is_connected:
            raise ConnectFailed(
                f"Link dropped {perf_counter() - started:.2f}s into the post-connect settle",
                detail="settle",
            )
