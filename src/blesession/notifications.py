"""Notifications from one characteristic, queued for the session's duration.

Every protocol needs the same thing around its exchange: subscribe,
optionally let the link settle, read replies with a timeout, and
unsubscribe in `finally` even when the link has dropped. Only the
interpretation of the replies is protocol-specific.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any

from bleak import BleakClient

from .errors import NotificationTimeout, SessionDropped
from .session import dropped_event

STOP_NOTIFY_TIMEOUT_S = 5.0
"""Bound on the unsubscribe. It runs in `__aexit__`, which is *after* an
attempt bound has fired, so it is the one wait nothing else bounds: a proxy
that stopped answering would otherwise hang here holding the lock."""


class Notifications:
    """Queued notifications from one characteristic.

        async with Notifications(client, NOTIFY_UUID, settle=0.5) as replies:
            await client.write_gatt_char(WRITE_UUID, cmd, response=False)
            reply = await replies.next(timeout=5, step="start")
            done = await replies.wait_for(is_done, timeout=120, step="finish")

    A queue rather than an event: a reply that lands between two waits is
    kept, not lost. Every wait names its `step`, so the NotificationTimeout
    it raises already says which stage of the protocol went unanswered.

    A wait also ends the moment the link drops, with `SessionDropped` rather
    than the full step timeout: `dropped` defaults to the event
    `ble_session()` registered for this client (see `dropped_event`). Pass
    one explicitly when you own the connection yourself; pass
    `dropped=asyncio.Event()` (never set) to wait the timeout out regardless.
    """

    def __init__(
        self,
        client: BleakClient,
        characteristic: Any,
        *,
        settle: float = 0.0,
        dropped: asyncio.Event | None = None,
    ) -> None:
        self._client = client
        self._characteristic = characteristic
        self._settle = settle
        self._dropped = dropped if dropped is not None else dropped_event(client)
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def __aenter__(self) -> Notifications:
        await self._client.start_notify(self._characteristic, self._on_notify)
        if self._settle:
            # Some adapters/proxies drop a write issued right after the CCCD write.
            await asyncio.sleep(self._settle)
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        # Never let an unsubscribe failure on a dropped link mask the
        # original error; ble_session() still disconnects.
        with contextlib.suppress(Exception):
            if self._client.is_connected:
                async with asyncio.timeout(STOP_NOTIFY_TIMEOUT_S):
                    await self._client.stop_notify(self._characteristic)

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        self._queue.put_nowait(bytes(data))

    @property
    def pending(self) -> int:
        """Notifications received and not yet read."""
        return self._queue.qsize()

    def clear(self) -> list[bytes]:
        """Drop (and return) notifications received so far."""
        dropped = []
        while not self._queue.empty():
            dropped.append(self._queue.get_nowait())
        return dropped

    async def next(self, timeout: float, *, step: str) -> bytes:
        """The next notification, or NotificationTimeout naming `step`.

        Raises SessionDropped instead, without waiting `timeout` out, when
        the link goes while this wait is running.
        """
        try:
            async with asyncio.timeout(timeout):
                return await self._next(step)
        except TimeoutError as exc:
            raise NotificationTimeout(timeout, step=step) from exc

    async def wait_for(
        self, accept: Callable[[bytes], bool], timeout: float, *, step: str
    ) -> bytes:
        """The first notification `accept` returns True for, within `timeout` overall.

        `accept` may raise to turn an error frame into the session's failure.
        """
        try:
            async with asyncio.timeout(timeout):
                while True:
                    data = await self._next(step)
                    if accept(data):
                        return data
        except TimeoutError as exc:
            raise NotificationTimeout(timeout, step=step) from exc

    async def _next(self, step: str) -> bytes:
        """The next queued notification, or SessionDropped once the link goes.

        What is already queued is delivered first: a device that sent its
        last reply and then dropped the link answered the step.
        """
        if not self._queue.empty():
            return self._queue.get_nowait()
        if self._dropped is None:
            return await self._queue.get()
        if not self._dropped.is_set():
            get = asyncio.ensure_future(self._queue.get())
            drop = asyncio.ensure_future(self._dropped.wait())
            try:
                await asyncio.wait({get, drop}, return_when=asyncio.FIRST_COMPLETED)
                if get.done():
                    return get.result()
            finally:
                drop.cancel()
                if not get.done():
                    # Queue.get() hands a cancelled getter on to the next
                    # waiter, so a notification that landed is not lost.
                    get.cancel()
        raise SessionDropped(f"The link dropped while waiting for {step}", detail=step)
