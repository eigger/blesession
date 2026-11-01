"""characteristic_or_raise: what the device exposes versus what the protocol needs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from bleak.exc import BleakError

from blesession import (
    BleSessionError,
    GattMismatch,
    WriteLimit,
    characteristic_or_raise,
    stages,
    write_limit,
)
from blesession.testing import FakeClient, FakeService

SERVICE = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR = "0000ffe1-0000-1000-8000-00805f9b34fb"


def _client(*, service=True, char=True, properties=("write-without-response",), size=244):
    client = FakeClient()
    characteristic = None
    if service and char:
        characteristic = client.add_characteristic(
            SERVICE, CHAR, properties=properties, max_write_without_response_size=size
        )
    elif service:
        client.services.by_uuid[SERVICE.lower()] = FakeService(SERVICE)
    return client, characteristic


def test_returns_the_characteristic():
    client, characteristic = _client()
    found = characteristic_or_raise(
        client, SERVICE, CHAR, properties=("write-without-response",), min_write_size=100
    )
    assert found is characteristic


@pytest.mark.parametrize(
    ("kwargs", "call", "text"),
    [
        ({"service": False}, {}, "XTE service"),
        ({"char": False}, {}, "XTE characteristic"),
        ({}, {"properties": ("notify",)}, "lacks notify"),
        ({"size": 20}, {"min_write_size": 100}, "write size 20 is too small"),
    ],
)
def test_mismatch_names_what_is_wrong(kwargs, call, text):
    client, _ = _client(**kwargs)
    with pytest.raises(GattMismatch, match=text) as info:
        characteristic_or_raise(client, SERVICE, CHAR, label="XTE", **call)
    assert info.value.stage == stages.SESSION
    assert isinstance(info.value, BleSessionError)


def test_no_requirements_means_only_existence_is_checked():
    client, characteristic = _client(properties=(), size=1)
    assert characteristic_or_raise(client, SERVICE, CHAR) is characteristic


def test_a_bare_string_is_one_property_not_its_characters():
    client, _ = _client(properties=("notify",))
    assert characteristic_or_raise(client, SERVICE, CHAR, properties="notify")
    with pytest.raises(GattMismatch, match="lacks write-without-response"):
        characteristic_or_raise(client, SERVICE, CHAR, properties="write-without-response")


def test_bleak_lookup_errors_are_a_mismatch():
    from bleak.exc import BleakError

    def boom(uuid):
        raise BleakError("Multiple Services with this UUID")

    client = SimpleNamespace(services=SimpleNamespace(get_service=boom))
    with pytest.raises(GattMismatch, match="GATT lookup failed: Multiple Services"):
        characteristic_or_raise(client, SERVICE, CHAR, label="XTE")


def test_a_mismatch_reports_its_own_cause_key():
    from blesession import SessionTrace, build_report

    client, _ = _client(char=False)
    with pytest.raises(GattMismatch) as info:
        characteristic_or_raise(client, SERVICE, CHAR)
    report = build_report(operation="write", trace=SessionTrace(), exc=info.value, noun="tag")
    assert report["failed_stage"] == stages.SESSION
    assert report["likely_cause_key"] == "session.gatt_mismatch"


def test_the_cause_key_holds_inside_a_device_stage():
    from blesession import SessionTrace, build_report

    client, _ = _client(char=False)
    trace = SessionTrace(stage_map={"unlock": stages.AUTH})
    with pytest.raises(GattMismatch) as info, trace.timed("unlock"):
        characteristic_or_raise(client, SERVICE, CHAR)
    report = build_report(operation="write", trace=trace, exc=info.value, noun="tag")
    assert report["failed_stage"] == stages.AUTH
    assert report["likely_cause_key"] == "session.gatt_mismatch"


def test_the_fake_finds_uuids_case_insensitively_like_bleak():
    client, characteristic = _client()
    assert characteristic_or_raise(client, SERVICE.upper(), CHAR.upper()) is characteristic


# ── write_limit ──────────────────────────────────────────────────────────


def _link(*, mtu=247, size=244, warn=False):
    """A client and characteristic as a backend reports them."""
    import warnings as _warnings

    client = FakeClient()
    char = client.add_characteristic(
        SERVICE, CHAR, properties=("write-without-response",), max_write_without_response_size=size
    )
    if warn:

        class Placeholder(FakeClient):
            @property
            def mtu_size(self):  # BlueZ before the MTU is acquired
                _warnings.warn("Using default MTU value", stacklevel=2)
                return 23

            @mtu_size.setter
            def mtu_size(self, value):
                pass

        client = Placeholder()
    else:
        client.mtu_size = mtu
    return client, char


def test_write_limit_prefers_the_characteristic():
    client, char = _link(mtu=247, size=200)
    limit = write_limit(client, char)
    assert (limit.size, limit.mtu, limit.source) == (200, 247, "characteristic")


def test_write_limit_uses_the_mtu_when_the_characteristic_only_shows_the_floor():
    client, char = _link(mtu=185, size=20)
    limit = write_limit(client, char)
    assert (limit.size, limit.mtu, limit.source) == (182, 185, "mtu")


def test_write_limit_ignores_bluez_placeholder_mtu():
    client, char = _link(size=20, warn=True)
    limit = write_limit(client, char, default=100)
    assert (limit.size, limit.mtu, limit.source) == (100, None, "default")


def test_write_limit_without_a_characteristic_uses_the_mtu():
    client, _ = _link(mtu=247)
    limit = write_limit(client)
    assert (limit.size, limit.source) == (244, "mtu")


def test_write_limit_with_response_ignores_the_characteristic_limit():
    client, char = _link(mtu=185, size=244)
    assert write_limit(client, char, response=True).size == 182


def test_write_limit_survives_a_backend_with_no_answers():
    class Bare:
        @property
        def mtu_size(self):
            raise BleakError("not connected")

    class Char:
        @property
        def max_write_without_response_size(self):
            raise AttributeError

    limit = write_limit(Bare(), Char())
    assert (limit.size, limit.mtu, limit.source) == (20, None, "default")


def test_write_limit_facts_omit_an_unreported_mtu():
    assert WriteLimit(182, 185, "mtu").facts() == {
        "mtu": 185,
        "write_size": 182,
        "write_size_source": "mtu",
    }
    assert WriteLimit(20, None, "default").facts() == {
        "write_size": 20,
        "write_size_source": "default",
    }
