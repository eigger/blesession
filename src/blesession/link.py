"""Which radio a link went over — the one place that probes bleak internals.

Home Assistant may reach a device through the local adapter or any of
several Bluetooth proxies, and the scanner whose advertisement was
strongest is not always the radio the connection took (a failover, or on a
bonded device the only proxy that holds the bond). The real answer lives
only on the client wrapper / backend, in private attributes whose shapes
change between habluetooth and bleak-esphome releases. When they do, this
is the one function to fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bleak import BleakClient
from bleak.backends.device import BLEDevice


@dataclass(frozen=True)
class LinkInfo:
    """What was learnt about the link at connect time.

    `via` is the radio the connection actually took: a scanner object
    (habluetooth's client wrapper records one), a habluetooth source id, a
    BlueZ D-Bus path, or None when unknowable. `source` is the scanner that
    advertised the device (habluetooth source id) — always a string on a
    proxy route, None on a plain local adapter.

    Both are opaque here; `blesession.hass.radio_facts()` turns them into
    scanner names.
    """

    via: Any = None
    source: str | None = None
    proxy: bool | None = None
    """Whether the advertising route was a remote scanner, when knowable."""


def advertising_source(ble_device: BLEDevice) -> str | None:
    """The habluetooth source id the advertisement came from, if recorded.

    Remote (ESPHome) scanners publish a plain dict with the proxy's source
    address; local adapters carry backend-specific details (a BlueZ object
    path, a WinRT / CoreBluetooth handle).
    """
    details = getattr(ble_device, "details", None)
    if isinstance(details, dict):
        for key in ("source", "scanner"):
            if value := details.get(key):
                return str(value)
    return None


def is_proxy(ble_device: BLEDevice) -> bool:
    """True when Home Assistant reached this device through a remote scanner.

    Integrations that adapt pacing to the transport (a longer packet
    interval over a proxy) read this; the report reads `via_type` instead,
    which reflects the radio the link actually took.
    """
    details = getattr(ble_device, "details", None)
    return isinstance(details, dict) and "source" in details


def connected_via(client: BleakClient) -> Any:
    """The radio the connection took, from the client, or None.

    Tried in order:
      client._connected_scanner   habluetooth's wrapper, newer versions
                                  (a BaseHaScanner object)
      client._backend._source     bleak-esphome (a source id string)
      client._backend.source
      client._backend._device_path  BlueZ (a D-Bus path)
    """
    if (scanner := getattr(client, "_connected_scanner", None)) is not None:
        return scanner
    backend = getattr(client, "_backend", None)
    if backend is None:
        return None
    for attr in ("_source", "source"):
        if value := getattr(backend, attr, None):
            return str(value)
    if path := getattr(backend, "_device_path", None):
        return str(path)
    return None


def probe_link(client: BleakClient, ble_device: BLEDevice) -> LinkInfo:
    """Everything knowable about the link right after connecting."""
    return LinkInfo(
        via=connected_via(client),
        source=advertising_source(ble_device),
        proxy=is_proxy(ble_device),
    )
