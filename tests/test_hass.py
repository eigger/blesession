"""blesession.hass without Home Assistant.

`hass.py` is the file most likely to break on a habluetooth release — it is
the only one that asks Home Assistant anything — and it was the only one
with no tests, because `homeassistant` is deliberately not a dependency.
Every function there imports it inside the call, so a stub module in
`sys.modules` is enough to exercise the lot.

The stub mirrors only the shapes `hass.py` actually relies on: two scanner
base classes to tell a proxy from an adapter, a source id -> scanner
lookup, the strongest advertisement, and the connectable scanners that see
an address. If Home Assistant changes one of those, the fix belongs in
`hass.py` and the stub moves with it.
"""

import sys
import types

import pytest

from blesession import LinkInfo, Unreachable
from blesession.testing import FakeDevice

ADDRESS = "AA:BB:CC:DD:EE:FF"


class StubScanner:
    """A BaseHaScanner: a name, and what it last saw."""

    def __init__(self, name, rssi=None):
        self.name = name
        self.rssi = rssi

    def get_discovered_device_advertisement_data(self, address):
        if self.rssi is None:
            return None
        return (FakeDevice(address), types.SimpleNamespace(rssi=self.rssi))


class StubProxy(StubScanner):
    """A BaseHaRemoteScanner: an ESPHome Bluetooth proxy."""


class DeafScanner(StubScanner):
    """A scanner build that has no advertisement lookup at all."""

    get_discovered_device_advertisement_data = None

    def __getattr__(self, name):
        raise AttributeError(name)


@pytest.fixture
def bluetooth(monkeypatch):
    """Stand `homeassistant.components.bluetooth` up in sys.modules.

    Tests set `.scanners` (source id -> scanner), `.strongest` (what
    async_last_service_info returns) and `.reaching` (how many connectable
    scanners see the address).
    """
    module = types.ModuleType("homeassistant.components.bluetooth")
    module.BaseHaScanner = StubScanner
    module.BaseHaRemoteScanner = StubProxy
    module.scanners = {}
    module.strongest = None
    module.reaching = 1
    module.device = FakeDevice(ADDRESS)

    module.async_scanner_by_source = lambda _hass, source: module.scanners.get(source)
    module.async_last_service_info = lambda _hass, _address, connectable=True: module.strongest
    module.async_scanner_devices_by_address = lambda _hass, _address, connectable=True: (
        [object()] * module.reaching
    )
    module.async_ble_device_from_address = lambda _hass, _address, connectable=True: module.device

    package = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    components.bluetooth = module
    package.components = components
    for name, stub in (
        ("homeassistant", package),
        ("homeassistant.components", components),
        ("homeassistant.components.bluetooth", module),
    ):
        monkeypatch.setitem(sys.modules, name, stub)
    return module


@pytest.fixture
def radio_facts(bluetooth):
    # Imported here: the module reads homeassistant only inside the call, so
    # the stub has to be in place before the first one, not before the import.
    from blesession.hass import radio_facts

    return lambda link=None: radio_facts(object(), ADDRESS, link)


def test_the_scanner_the_link_took_wins_over_the_one_that_advertised(bluetooth, radio_facts):
    took = StubProxy("living-room-proxy", rssi=-71)
    advertised = StubProxy("office-proxy", rssi=-60)
    bluetooth.scanners = {"office": advertised}
    bluetooth.reaching = 2

    facts = radio_facts(LinkInfo(via=took, source="office"))

    assert facts == {
        "via": "living-room-proxy",
        "via_type": "proxy",
        "rssi": -71,  # as the radio that carried it saw the device, not the loudest
        "paths": 2,
        "advertised_via": "office-proxy",
    }


def test_a_source_id_is_resolved_and_a_local_adapter_is_not_a_proxy(bluetooth, radio_facts):
    bluetooth.scanners = {"hci0": StubScanner("hci0", rssi=-55)}

    facts = radio_facts(LinkInfo(via="hci0", source="hci0"))

    assert facts == {"via": "hci0", "via_type": "adapter", "rssi": -55, "paths": 1}
    assert "advertised_via" not in facts  # same radio: nothing to say


def test_a_session_that_never_connected_falls_back_to_the_advertisement(bluetooth, radio_facts):
    bluetooth.scanners = {"office": StubProxy("office-proxy", rssi=-88)}
    bluetooth.strongest = types.SimpleNamespace(source="office")

    assert radio_facts() == {
        "via": "office-proxy",
        "via_type": "proxy",
        "rssi": -88,
        "paths": 1,
    }


def test_an_id_habluetooth_does_not_know_is_still_reported(bluetooth, radio_facts):
    """A BlueZ D-Bus path resolves to no scanner; showing it beats showing nothing."""
    facts = radio_facts(LinkInfo(via="/org/bluez/hci0/dev_AA_BB"))

    assert facts == {"via": "/org/bluez/hci0/dev_AA_BB", "paths": 1}
    assert "via_type" not in facts and "rssi" not in facts


def test_no_radio_at_all_reports_only_the_count(bluetooth, radio_facts):
    bluetooth.reaching = 0

    assert radio_facts() == {"paths": 0}


def test_a_scanner_without_the_advertisement_lookup_loses_only_the_rssi(bluetooth, radio_facts):
    facts = radio_facts(LinkInfo(via=DeafScanner("odd-build")))

    assert facts == {"via": "odd-build", "via_type": "adapter", "paths": 1}


def test_a_scanner_that_has_not_seen_the_device_loses_only_the_rssi(bluetooth, radio_facts):
    facts = radio_facts(LinkInfo(via=StubProxy("proxy-that-forgot", rssi=None)))

    assert facts == {"via": "proxy-that-forgot", "via_type": "proxy", "paths": 1}


def test_ble_device_or_raise_hands_back_the_handle(bluetooth):
    from blesession.hass import ble_device_or_raise

    assert ble_device_or_raise(object(), ADDRESS) is bluetooth.device


def test_ble_device_or_raise_is_how_an_asleep_device_becomes_a_session_failure(bluetooth):
    from blesession.hass import ble_device_or_raise

    bluetooth.device = None
    with pytest.raises(Unreachable) as info:
        ble_device_or_raise(object(), ADDRESS)
    assert info.value.stage == "unreachable"
    assert ADDRESS in str(info.value)


def test_a_handle_asked_for_unconnectable_is_not_refused_for_being_unconnectable(bluetooth):
    """Only the wording: saying no *connectable* radio saw it would name a
    reason that was never asked about."""
    from blesession.hass import ble_device_or_raise

    bluetooth.device = None
    with pytest.raises(Unreachable) as info:
        ble_device_or_raise(object(), ADDRESS, connectable=False)
    assert str(info.value).startswith(f"No radio sees {ADDRESS}")

    with pytest.raises(Unreachable) as info:
        ble_device_or_raise(object(), ADDRESS)
    assert str(info.value).startswith(f"No connectable radio sees {ADDRESS}")
