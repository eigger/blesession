"""characteristic_or_raise: what the device exposes versus what the protocol needs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from blesession import BleSessionError, GattMismatch, characteristic_or_raise, stages

SERVICE = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR = "0000ffe1-0000-1000-8000-00805f9b34fb"


def _client(*, service=True, char=True, properties=("write-without-response",), size=244):
    characteristic = SimpleNamespace(
        uuid=CHAR, properties=list(properties), max_write_without_response_size=size
    )
    svc = SimpleNamespace(get_characteristic=lambda uuid: characteristic if char else None)
    services = SimpleNamespace(get_service=lambda uuid: svc if service else None)
    return SimpleNamespace(services=services), characteristic


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
