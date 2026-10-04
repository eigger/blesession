"""Writing a payload larger than one ATT write, a chunk at a time.

Image tags and printers take their data as consecutive writes of at most the
link's write size. The slicing, the optional gap between writes and the early
exit when the link has gone are the same everywhere; the chunk size, the gap,
whether a write is acknowledged and how a chunk is framed are the device's.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from bleak import BleakClient

from .errors import SessionDropped, WriteTimeout
from .session import dropped_event

WRITE_TIMEOUT_S = 10.0
"""The default bound on one write. A GATT write has no timeout of its own; a
proxy that died mid-transfer leaves it hanging until the attempt bound fires.
This is the shorter, per-write limit under that bound; `None` removes it."""


def _raise_if_dropped(dropped: asyncio.Event | None, step: str, phase: str) -> None:
    if dropped is not None and dropped.is_set():
        raise SessionDropped(f"The link dropped {phase} {step}", detail=step)


async def guarded_write(
    client: BleakClient,
    characteristic: Any,
    data: bytes,
    *,
    step: str,
    response: bool = False,
    timeout: float | None = WRITE_TIMEOUT_S,
    dropped: asyncio.Event | None = None,
    phase: str = "before",
) -> None:
    """One write with the library's guarantees: the one place they live.

    A link already down raises `SessionDropped` naming `step` (`phase` says
    whether that was "before" or "during" it), instead of whatever the backend
    raises for a write to a dead link; a write that does not return within
    `timeout` raises `WriteTimeout` naming `step`. `dropped` defaults to the
    event `ble_session()` registered for `client`.

    `Notifications.request()` and `write_chunks()` both write through this, so
    a protocol that mixes them gets the same behaviour from each.
    """
    if dropped is None:
        dropped = dropped_event(client)
    _raise_if_dropped(dropped, step, phase)
    scope = asyncio.timeout(timeout)
    try:
        async with scope:
            await client.write_gatt_char(characteristic, data, response=response)
    except TimeoutError as exc:
        if not scope.expired():
            raise  # the backend's own timeout: not ours to rename
        # The bound fired. A write that hung because the link went away is the
        # link's failure, not the adapter's.
        _raise_if_dropped(dropped, step, "during")
        assert timeout is not None
        raise WriteTimeout(timeout, step=step) from exc


async def write_chunks(
    client: BleakClient,
    characteristic: Any,
    data: bytes,
    size: int,
    *,
    step: str,
    response: bool = False,
    gap_s: float = 0.0,
    wrap: Callable[[int, bytes], bytes] | None = None,
    on_chunk: Callable[[int], None] | None = None,
    write_timeout: float | None = WRITE_TIMEOUT_S,
) -> int:
    """Write `data` to `characteristic` in chunks of at most `size` bytes.

    Returns how many writes were made. `wrap(offset, chunk)` frames each chunk
    for protocols that number or address them; `gap_s` pauses after every
    chunk; `on_chunk(sent)` is called after each write, so the progress of a
    transfer that later fails is still known to the caller's trace.

    Each write goes through `guarded_write()`: a link that is down ends the
    transfer with `SessionDropped` naming `step`, and a write that does not
    return within `write_timeout` (default `WRITE_TIMEOUT_S`, `None` for no
    bound) with `WriteTimeout`. An empty `data` writes nothing.
    """
    if size < 1:
        raise ValueError(f"size must be at least 1, not {size}")
    dropped = dropped_event(client)
    sent = 0
    for offset in range(0, len(data), size):
        _raise_if_dropped(dropped, step, "during")  # before framing a chunk nobody will send
        chunk = data[offset : offset + size]
        if wrap is not None:
            chunk = wrap(offset, chunk)
        await guarded_write(
            client,
            characteristic,
            chunk,
            step=step,
            response=response,
            timeout=write_timeout,
            dropped=dropped,
            phase="during",
        )
        sent += 1
        if on_chunk is not None:
            on_chunk(sent)
        if gap_s > 0:
            await asyncio.sleep(gap_s)
    return sent
