"""Notifications from one characteristic, queued for the session's duration.

Every protocol needs the same thing around its exchange: subscribe,
optionally let the link settle, read replies with a timeout, and
unsubscribe in `finally` even when the link has dropped. Only the
interpretation of the replies is protocol-specific.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from bleak import BleakClient

from .errors import NotificationTimeout, SessionDropped
from .session import dropped_event
from .subscribe import STOP_NOTIFY_TIMEOUT_S, start_notify_with_recovery
from .transfer import guarded_write

_LOGGER = logging.getLogger(__name__)


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

    `recover=True` subscribes with `start_notify_with_recovery`, for a link
    that may still carry the previous connection's subscription.
    """

    def __init__(
        self,
        client: BleakClient,
        characteristic: Any,
        *,
        settle: float = 0.0,
        dropped: asyncio.Event | None = None,
        recover: bool = False,
    ) -> None:
        self._recover = recover
        self._client = client
        self._characteristic = characteristic
        self._settle = settle
        self._dropped = dropped if dropped is not None else dropped_event(client)
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def __aenter__(self) -> Notifications:
        if self._recover:
            await start_notify_with_recovery(self._client, self._characteristic, self._on_notify)
        else:
            await self._client.start_notify(self._characteristic, self._on_notify)
        try:
            if self._settle:
                # Some adapters/proxies drop a write issued right after the CCCD write.
                await asyncio.sleep(self._settle)
        except BaseException:
            # An async context manager does not call __aexit__ if __aenter__
            # fails. Undo the subscription before propagating cancellation or
            # another settle error.
            await self._stop_notify()
            raise
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        # Never let an unsubscribe failure on a dropped link mask the
        # original error; ble_session() still disconnects.
        await self._stop_notify()

    async def _stop_notify(self) -> None:
        try:
            if self._client.is_connected:
                async with asyncio.timeout(STOP_NOTIFY_TIMEOUT_S):
                    await self._client.stop_notify(self._characteristic)
        except Exception as exc:  # noqa: BLE001 - never mask the session's error
            _LOGGER.debug("stop_notify on %s failed (ignored): %s", self._characteristic, exc)

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

    async def next_burst(self, timeout: float, *, step: str, gap_s: float = 0.05) -> bytes:
        """The next notification plus the ones that follow it, joined.

        For a device that spreads one reply over several notifications, or
        sends unsolicited frames in a burst alongside the real one: the
        caller reassembles frames from the bytes. After the first frame it
        keeps collecting until `gap_s` passes with nothing new (and never
        past `timeout` overall); `gap_s=0` takes only what is already queued.
        A drop after the first frame ends the burst, not the call: what
        arrived is returned and the next wait reports the drop.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        parts = [await self.next(timeout, step=step)]
        while True:
            parts.extend(self.clear())
            wait = min(gap_s, deadline - loop.time())
            if wait <= 0:
                break
            try:
                async with asyncio.timeout(wait):
                    parts.append(await self._next(step))
            except (TimeoutError, SessionDropped):
                break
        return b"".join(parts)

    async def request(
        self,
        characteristic: Any,
        data: bytes,
        *,
        timeout: float,
        step: str,
        write_timeout: float | None = None,
        response: bool = False,
        accept: Callable[[bytes], bool] | None = None,
        pace_s: float = 0.0,
    ) -> bytes:
        """Write `data`, return the device's reply.

        Notifications received before the write are dropped first: they
        answer something earlier, not this request. A late reply to an
        earlier request that lands after that is indistinguishable from this
        one's, so give `accept` a way to tell them apart when that can
        happen. `accept` picks the reply out of several (as `wait_for`).

        `timeout` bounds the write and, separately, the wait for the reply;
        `write_timeout` bounds the write alone when it needs a different limit
        (a short reply window must not cut a slow write off). `pace_s`, the
        pause between the two that some tags need, is on top of both. A link
        that is already down raises `SessionDropped` rather than whatever the
        backend would say about the write.
        """
        if self._dropped is not None and self._dropped.is_set():
            raise SessionDropped(f"The link dropped before {step}", detail=step)
        self.clear()
        await guarded_write(
            self._client,
            characteristic,
            data,
            step=step,
            response=response,
            timeout=timeout if write_timeout is None else write_timeout,
            dropped=self._dropped,
        )
        if pace_s > 0:
            await asyncio.sleep(pace_s)
        if accept is None:
            return await self.next(timeout, step=step)
        return await self.wait_for(accept, timeout, step=step)

    async def wait_for(
        self, accept: Callable[[bytes], bool], timeout: float, *, step: str
    ) -> bytes:
        """The first notification `accept` returns True for, within `timeout` overall.

        `accept` may raise to turn an error frame into the session's failure.
        """
        timeout_scope = asyncio.timeout(timeout)
        try:
            async with timeout_scope:
                while True:
                    data = await self._next(step)
                    if accept(data):
                        return data
        except TimeoutError as exc:
            if timeout_scope.expired():
                raise NotificationTimeout(timeout, step=step) from exc
            raise

    def _requeue_first(self, data: bytes) -> None:
        rest = self.clear()
        for item in (data, *rest):
            self._queue.put_nowait(item)

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
            delivered = False
            try:
                await asyncio.wait({get, drop}, return_when=asyncio.FIRST_COMPLETED)
                if get.done():
                    delivered = True
                    return get.result()
            finally:
                drop.cancel()
                if not get.done():
                    # Queue.get() hands a cancelled getter on to the next
                    # waiter, so a notification that landed is not lost.
                    get.cancel()
                elif not delivered and not get.cancelled() and get.exception() is None:
                    # The step's timeout fired between the queue handing
                    # over a frame and this wait resuming: put it back
                    # rather than lose a reply that did arrive.
                    self._requeue_first(get.result())
        raise SessionDropped(f"The link dropped while waiting for {step}", detail=step)
