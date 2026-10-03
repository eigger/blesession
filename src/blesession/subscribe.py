"""Subscribing to a characteristic, surviving the stale state a reconnect leaves.

A link that was just re-established can still carry the last one's
subscription: BlueZ keeps the notify session acquired for a moment, and the
ESPHome proxy backend reports the same thing as "notifications are already
enabled". The first `start_notify` then fails although nothing is wrong with
the device. Releasing the stale subscription and subscribing again clears it.

This matches on bleak backend wording, so like `link.py` it is the one place
that does, and the first thing to look at when a backend release changes the
message.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from bleak import BleakClient
from bleak.exc import BleakError

_LOGGER = logging.getLogger(__name__)

NOTIFY_ATTEMPTS = 3
"""Subscribe attempts before the last error is raised."""

_STALE_SUBSCRIPTION = (
    "notify acquired",
    "notpermitted",
    "already enabled",
    # BlueZ's wording when it still holds the previous connection's session.
    "register notify session",
)
_NO_DISCOVERY = ("service discovery has not been performed", "not been performed")


async def _refresh_services(client: BleakClient) -> None:
    """Re-run GATT discovery on backends that still expose it."""
    get_services = getattr(client, "get_services", None)
    if not callable(get_services):
        return
    try:
        await get_services()
    except Exception as exc:  # noqa: BLE001 - best effort, the retry decides
        _LOGGER.debug("get_services refresh failed (ignored): %s", exc)


async def start_notify_with_recovery(
    client: BleakClient,
    characteristic: Any,
    callback: Callable[[Any, bytearray], Any],
    *,
    attempts: int = NOTIFY_ATTEMPTS,
) -> None:
    """`client.start_notify`, retried when the failure is a stale subscription.

    A stale subscription is released with `stop_notify` and the services are
    refreshed before the next attempt; a missing service discovery only
    refreshes. Any other error is raised at once: whether to retry it is the
    integration's policy. The last attempt's error is raised as it was.
    """
    for attempt in range(1, attempts + 1):
        try:
            await client.start_notify(characteristic, callback)
            return
        except BleakError as exc:
            message = str(exc).lower()
            stale = any(text in message for text in _STALE_SUBSCRIPTION)
            if not (stale or any(text in message for text in _NO_DISCOVERY)):
                raise
            if attempt == attempts:
                raise
            _LOGGER.debug(
                "start_notify on %s failed (%d/%d), retrying: %s",
                characteristic,
                attempt,
                attempts,
                exc,
            )
            if stale:
                try:
                    await client.stop_notify(characteristic)
                except Exception:  # noqa: BLE001 - nothing to release is fine
                    pass
            await _refresh_services(client)
            await asyncio.sleep(0.25 * attempt)
