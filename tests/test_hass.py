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

    allocations = None
    """What get_allocations() returns; an Exception instance is raised."""

    def __init__(self, name, rssi=None):
        self.name = name
        self.rssi = rssi

    def get_allocations(self):
        if isinstance(self.allocations, Exception):
            raise self.allocations
        return self.allocations

    def get_discovered_device_advertisement_data(self, address):
        if self.rssi is None:
            return None
        return (FakeDevice(address), types.SimpleNamespace(rssi=self.rssi))


class StubProxy(StubScanner):
    """A BaseHaRemoteScanner: an ESPHome Bluetooth proxy."""


class DeafScanner(StubScanner):
    """A scanner build that has no advertisement lookup at all."""

    get_discovered_device_advertisement_data = None
    get_allocations = None  # and no slot accounting either

    def __getattr__(self, name):
        raise AttributeError(name)


@pytest.fixture
def bluetooth(monkeypatch):
    """Stand `homeassistant.components.bluetooth` up in sys.modules.

    Tests set `.scanners` (source id -> scanner), `.strongest` (what
    async_last_service_info returns), `.reaching` (how many connectable
    scanners see the address), `.passive` (how many more, non-connectable,
    hear it) and `.heard` (what async_last_service_info returns when asked for
    any radio, connectable or not; `.strongest` when unset).
    """
    module = types.ModuleType("homeassistant.components.bluetooth")
    module.BaseHaScanner = StubScanner
    module.BaseHaRemoteScanner = StubProxy
    module.scanners = {}
    module.strongest = None
    module.reaching = 1
    module.passive = 0
    module.heard = None
    module.device = FakeDevice(ADDRESS)

    module.async_scanner_by_source = lambda _hass, source: module.scanners.get(source)
    module.async_last_service_info = lambda _hass, _address, connectable=True: (
        module.strongest if connectable or module.heard is None else module.heard
    )
    module.async_scanner_devices_by_address = lambda _hass, _address, connectable=True: (
        [object()] * (module.reaching + (0 if connectable else module.passive))
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
        "via_unconfirmed": True,
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


def test_a_link_whose_route_is_unknown_falls_back_and_says_so(bluetooth, radio_facts):
    bluetooth.scanners = {"office": StubProxy("office-proxy", rssi=-88)}
    bluetooth.strongest = types.SimpleNamespace(source="office")

    facts = radio_facts(LinkInfo(via="/org/bluez/hci9/dev_X"))

    assert facts["via"] == "office-proxy"
    assert facts["via_unconfirmed"] is True


def test_a_lookup_that_raises_yields_no_facts_not_an_exception(bluetooth, radio_facts):
    """Facts are evaluated before build_report; they must not fail the report."""

    def boom(_hass, _address, connectable=True):
        raise RuntimeError("adapter went away")

    bluetooth.async_scanner_devices_by_address = boom
    assert radio_facts() == {}


@pytest.fixture
def now(monkeypatch):
    """The clock a service info's `time` is stamped with, as a one-item list."""
    import blesession.hass as hass_mod

    clock = [1000.0]
    monkeypatch.setattr(hass_mod, "_monotonic_now", lambda: clock[0])
    return clock


def test_last_seen_is_the_age_of_the_advertisement_any_radio_heard(bluetooth, radio_facts, now):
    bluetooth.heard = types.SimpleNamespace(source="office", time=957.25)

    assert radio_facts()["last_seen_s"] == 42.8  # one decimal; the clock is coarse anyway


def test_last_seen_asks_for_any_radio_not_only_connectable_ones(bluetooth, radio_facts, now):
    bluetooth.strongest = types.SimpleNamespace(source="office", time=999.0)
    bluetooth.heard = types.SimpleNamespace(source="passive", time=990.0)

    assert radio_facts()["last_seen_s"] == 10.0


def test_last_seen_is_left_out_when_it_cannot_be_determined(bluetooth, radio_facts, now):
    assert "last_seen_s" not in radio_facts()  # no advertisement at all
    bluetooth.heard = types.SimpleNamespace(source="office")  # a build with no `time`
    assert "last_seen_s" not in radio_facts()
    bluetooth.heard = types.SimpleNamespace(source="office", time=None)
    assert "last_seen_s" not in radio_facts()


def test_last_seen_is_never_negative(bluetooth, radio_facts, now):
    bluetooth.heard = types.SimpleNamespace(source="office", time=1000.4)  # coarse clock skew

    assert radio_facts()["last_seen_s"] == 0.0


def test_last_seen_is_left_out_without_the_clock(bluetooth, radio_facts, monkeypatch):
    import blesession.hass as hass_mod

    monkeypatch.setattr(hass_mod, "_monotonic_now", lambda: None)
    bluetooth.heard = types.SimpleNamespace(source="office", time=1.0)

    assert "last_seen_s" not in radio_facts()


def test_the_clock_is_the_one_habluetooth_stamps_with():
    import blesession.hass as hass_mod

    assert isinstance(hass_mod._monotonic_now(), float)


def test_passive_radios_are_counted_beside_the_connectable_ones(bluetooth, radio_facts):
    bluetooth.reaching = 0
    bluetooth.passive = 2

    assert radio_facts() == {"paths": 0, "paths_passive": 2}


def test_paths_keeps_meaning_connectable_radios(bluetooth, radio_facts):
    bluetooth.reaching = 1
    bluetooth.passive = 1

    facts = radio_facts()

    assert facts["paths"] == 1 and facts["paths_passive"] == 1


def test_no_passive_radio_leaves_the_fact_out(bluetooth, radio_facts):
    bluetooth.passive = 0

    assert "paths_passive" not in radio_facts()


def test_the_slots_of_the_radio_reported_as_via(bluetooth, radio_facts):
    took = StubProxy("living-room-proxy", rssi=-71)
    took.allocations = types.SimpleNamespace(adapter="x", slots=3, free=1, allocated=["a", "b"])

    facts = radio_facts(LinkInfo(via=took))

    assert facts["slots_free"] == 1 and facts["slots"] == 3


def test_slots_are_left_out_when_the_radio_cannot_say(bluetooth, radio_facts):
    for allocations in (None, RuntimeError("proxy went away"), types.SimpleNamespace(free=1)):
        scanner = StubProxy("p", rssi=-60)
        scanner.allocations = allocations
        facts = radio_facts(LinkInfo(via=scanner))
        assert "slots" not in facts and "slots_free" not in facts
        assert facts["via"] == "p"  # the other facts survive


def test_a_scanner_without_get_allocations_loses_only_the_slots(bluetooth, radio_facts):
    facts = radio_facts(LinkInfo(via=DeafScanner("old-habluetooth")))

    assert facts == {"via": "old-habluetooth", "via_type": "adapter", "paths": 1}


def test_an_old_home_assistant_lacking_the_lookups_loses_only_those_facts(
    bluetooth, radio_facts, now, monkeypatch
):
    """`last_seen_s` and `paths_passive` ask for any radio; if that raises the
    rest of the facts still stand."""

    def boom(_hass, _address, connectable=True):
        if not connectable:
            raise TypeError("connectable is not a parameter here")
        return bluetooth.strongest

    bluetooth.async_last_service_info = boom
    real = bluetooth.async_scanner_devices_by_address

    def devices(hass, address, connectable=True):
        if not connectable:
            raise TypeError("no")
        return real(hass, address, connectable=connectable)

    bluetooth.async_scanner_devices_by_address = devices

    assert radio_facts(LinkInfo(via=StubProxy("p", rssi=-60))) == {
        "via": "p",
        "via_type": "proxy",
        "rssi": -60,
        "paths": 1,
    }


def test_the_new_facts_are_report_facts_in_a_fixed_place():
    from blesession import FACT_KEYS

    assert FACT_KEYS[:6] == (
        "via",
        "via_type",
        "rssi",
        "paths",
        "advertised_via",
        "via_unconfirmed",
    )
    assert FACT_KEYS[6:] == ("last_seen_s", "paths_passive", "slots_free", "slots")


def test_passive_only_radios_become_the_unreachable_reading_in_a_report(bluetooth, radio_facts):
    from blesession import SessionTrace, build_report

    bluetooth.reaching = 0
    bluetooth.passive = 1
    facts = radio_facts()
    report = build_report(
        operation="poll", trace=SessionTrace(), exc=Unreachable(ADDRESS), facts=facts, noun="tag"
    )

    assert report["likely_cause_key"] == "unreachable.passive_only"
    assert report["paths_passive"] == 1 and "connectable adapter" in report["likely_cause"]
    assert list(report).index("paths") < list(report).index("paths_passive")
