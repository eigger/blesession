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

from .errors import SessionDropped
from .session import dropped_event


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
) -> int:
    """Write `data` to `characteristic` in chunks of at most `size` bytes.

    Returns how many writes were made. `wrap(offset, chunk)` frames each chunk
    for protocols that number or address them; `gap_s` pauses after every
    chunk; `on_chunk(sent)` is called after each write, so the progress of a
    transfer that later fails is still known to the caller's trace.

    A link that is down ends the transfer with `SessionDropped` naming `step`,
    before the next write, instead of whatever the backend raises for a write
    to a dead link. An empty `data` writes nothing.
    """
    if size < 1:
        raise ValueError(f"size must be at least 1, not {size}")
    dropped = dropped_event(client)
    sent = 0
    for offset in range(0, len(data), size):
        if dropped is not None and dropped.is_set():
            raise SessionDropped(f"The link dropped during {step}", detail=step)
        chunk = data[offset : offset + size]
        if wrap is not None:
            chunk = wrap(offset, chunk)
        await client.write_gatt_char(characteristic, chunk, response=response)
        sent += 1
        if on_chunk is not None:
            on_chunk(sent)
        if gap_s > 0:
            await asyncio.sleep(gap_s)
    return sent
