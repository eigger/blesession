"""What only Home Assistant knows about a session: the radios.

Imported only from inside a running integration; `homeassistant` is not a
dependency of this package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .link import LinkInfo

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def radio_facts(hass: HomeAssistant, address: str, link: LinkInfo | None = None) -> dict[str, Any]:
    """Which radio the session went through, its RSSI and how many reach the device.

        via             the radio the link took (scanner name), when knowable;
                        else the scanner holding the strongest advertisement,
                        which is the one the client wrapper tries first
        via_type        "proxy" | "adapter"
        rssi            as seen by that radio
        paths           connectable radios that currently see the device;
                        1 means no failover is possible
        advertised_via  the scanner whose advertisement was strongest, only
                        when it is not the radio the link took (a failover, or
                        on a bonded device the one proxy that holds the bond)

    `link` is what ble_session() recorded on the trace; None (a session that
    never connected) falls back to the advertising scanner.
    """
    from homeassistant.components.bluetooth import (
        BaseHaRemoteScanner,
        BaseHaScanner,
        async_last_service_info,
        async_scanner_by_source,
        async_scanner_devices_by_address,
    )

    def scanner_for(value: Any) -> Any:
        if isinstance(value, BaseHaScanner):
            return value
        if isinstance(value, str) and value:
            return async_scanner_by_source(hass, value)
        return None

    advertised = scanner_for(link.source if link else None)
    if advertised is None:
        info = async_last_service_info(hass, address, connectable=True)
        advertised = async_scanner_by_source(hass, info.source) if info else None
    connected = scanner_for(link.via if link else None) or advertised

    facts: dict[str, Any] = {}
    if connected is not None:
        facts["via"] = connected.name
        facts["via_type"] = "proxy" if isinstance(connected, BaseHaRemoteScanner) else "adapter"
    elif link is not None and link.via:
        # A BlueZ D-Bus path or an id habluetooth does not know: still worth showing.
        facts["via"] = str(link.via)
    rssi_scanner = connected or advertised
    if rssi_scanner is not None:
        try:
            seen = rssi_scanner.get_discovered_device_advertisement_data(address)
        except Exception:  # noqa: BLE001 - a scanner without the method
            seen = None
        if seen is not None:
            facts["rssi"] = seen[1].rssi
    facts["paths"] = len(async_scanner_devices_by_address(hass, address, connectable=True))
    if advertised is not None and connected is not None and advertised is not connected:
        facts["advertised_via"] = advertised.name
    return facts
