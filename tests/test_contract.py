"""The contracts an integration relies on, one place each.

Deprecation shims for the older `cause` shapes, retryability, the shared write
primitive and its bound, the HA-free core.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap

import pytest

from blesession import (
    DeviceError,
    Failure,
    GattMismatch,
    Notifications,
    NotificationTimeout,
    SessionDropped,
    SessionTrace,
    WriteTimeout,
    build_report,
    default_retry_if,
    guarded_write,
    run_attempts,
    write_chunks,
)
from blesession.attempts import Attempt
from blesession.testing import FakeClient

# ── cause: one Failure, the older shapes deprecated ───────────────────────


def _report(cause, exc=None):
    exc = exc or NotificationTimeout(1, step="start")
    return build_report(
        operation="op", trace=SessionTrace(), exc=exc, cause=cause, facts={"via": "x"}
    )


def test_cause_takes_a_failure():
    seen = []

    def cause(failure: Failure):
        seen.append(failure)
        return "mine"

    exc = NotificationTimeout(1, step="start")
    report = _report(cause, exc)
    assert report["likely_cause"] == "mine"
    (failure,) = seen
    assert failure.exc is exc
    assert failure.error == str(exc)
    assert failure.facts == {"via": "x"}


@pytest.mark.parametrize(
    "legacy",
    [
        lambda stage, detail, error, facts: "old4",
        lambda stage, detail, error, facts, exc: "old5",
    ],
)
def test_the_older_positional_shapes_still_work_with_a_warning(legacy):
    with pytest.warns(DeprecationWarning, match="take one blesession.Failure"):
        report = _report(legacy)
    assert report["likely_cause"] in ("old4", "old5")


def test_a_legacy_callback_gets_the_same_arguments_as_before():
    got = {}

    def legacy(stage, detail, error, facts, exc):
        got.update(stage=stage, detail=detail, error=error, facts=facts, exc=exc)

    exc = SessionDropped("gone")
    with pytest.warns(DeprecationWarning):
        _report(legacy, exc)
    assert got["exc"] is exc and got["error"] == "gone" and got["facts"] == {"via": "x"}


# ── retryable ─────────────────────────────────────────────────────────────


def _attempt(error):
    return Attempt(number=1, trace=SessionTrace(), error=error)


def test_default_retry_stops_at_an_error_that_says_it_is_final():
    assert default_retry_if(_attempt(NotificationTimeout(1, step="x")))
    assert default_retry_if(_attempt(DeviceError("fault", code=5)))
    assert not default_retry_if(_attempt(GattMismatch("no profile")))
    assert not default_retry_if(_attempt(DeviceError("key rejected", retryable=False)))
    assert default_retry_if(_attempt(RuntimeError("anything else")))


async def test_run_attempts_does_not_retry_a_final_error():
    calls = 0

    async def attempt(_a):
        nonlocal calls
        calls += 1
        raise GattMismatch("no profile")

    last = await run_attempts(attempt, max_attempts=3, pause_s=0)
    assert calls == 1 and isinstance(last.error, GattMismatch)


def test_a_device_error_carries_its_code_and_leaves_the_stage_to_the_trace():
    exc = DeviceError("error frame", code=0x05)
    assert exc.code == 5 and exc.stage is None and exc.retryable is True
    report = _report(lambda f: f"code {f.exc.code}", exc)
    assert report["likely_cause"] == "code 5"


# ── one write primitive, one set of guarantees ────────────────────────────


async def test_a_hung_chunk_write_is_a_write_timeout_naming_the_step():
    client = FakeClient()
    client.write_delay_s = 3600
    with pytest.raises(WriteTimeout) as info:
        await write_chunks(client, "w", b"abc", 2, step="upload", write_timeout=0.01)
    assert info.value.step == "upload" and info.value.timeout == 0.01
    assert isinstance(info.value, NotificationTimeout)
    assert isinstance(info.value, TimeoutError)


async def test_write_timeout_none_removes_the_bound():
    client = FakeClient()
    client.write_delay_s = 0.05
    assert await write_chunks(client, "w", b"abc", 2, step="s", write_timeout=None) == 2


async def test_every_write_path_ends_the_same_way_on_a_dropped_link():
    dropped = asyncio.Event()
    dropped.set()
    for call in (
        lambda c: write_chunks_with(c, dropped),
        lambda c: guarded_write(c, "w", b"ab", step="s", dropped=dropped),
    ):
        client = FakeClient()
        with pytest.raises(SessionDropped) as info:
            await call(client)
        assert info.value.detail == "s" and client.writes == []
    client = FakeClient()
    async with Notifications(client, "n", dropped=dropped) as replies:
        with pytest.raises(SessionDropped):
            await replies.request("w", b"ab", timeout=1, step="s")
    assert client.writes == []


async def write_chunks_with(client, dropped):
    # write_chunks watches the event ble_session() registered for the client.
    from blesession import session as session_mod

    session_mod._DROPPED[client] = dropped
    try:
        return await write_chunks(client, "w", b"ab", 1, step="s")
    finally:
        session_mod._DROPPED.pop(client, None)


async def test_request_write_timeout_is_a_write_timeout():
    client = FakeClient()
    async with Notifications(client, "n") as replies:
        client.write_delay_s = 3600
        with pytest.raises(WriteTimeout):
            await replies.request("w", b"\xaa", timeout=0.01, step="start")


def test_the_cause_key_for_a_write_timeout_is_its_own():
    report = _report(None, WriteTimeout(1, step="upload"))
    assert report["likely_cause_key"] == "write_timeout"


# ── the core does not need Home Assistant ─────────────────────────────────


def test_importing_the_core_never_imports_homeassistant():
    code = textwrap.dedent(
        """
        import importlib, pkgutil, sys

        class Block:
            # In place before blesession is imported, so a core module that
            # imports homeassistant (even lazily at import time) fails here.
            def find_spec(self, name, path=None, target=None):
                if name == "homeassistant" or name.startswith("homeassistant."):
                    raise ImportError("homeassistant must not be imported by the core")

        sys.meta_path.insert(0, Block())
        import blesession

        for mod in pkgutil.iter_modules(blesession.__path__):
            if mod.name != "hass":
                importlib.import_module(f"blesession.{mod.name}")
        assert "homeassistant" not in sys.modules
        """
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


async def test_on_write_lets_a_fake_answer_a_command_the_way_a_device_does():
    client = FakeClient()
    client.on_write = lambda char, data: client.reply(b"\x00" + data)
    async with Notifications(client, "n") as replies:
        reply = await replies.request("w", b"\x01", timeout=1, step="cmd")
    assert reply == b"\x00\x01"


# ── write guarantees, edge by edge ────────────────────────────────────────


async def test_a_backend_timeout_from_the_write_is_not_renamed():
    for bound in (10.0, None):
        client = FakeClient()
        client.fail_write = TimeoutError("backend gatt timeout")
        with pytest.raises(TimeoutError, match="backend gatt timeout") as info:
            await guarded_write(client, "w", b"x", step="s", timeout=bound)
        assert not isinstance(info.value, WriteTimeout)


async def test_a_write_that_hung_because_the_link_dropped_is_a_dropped_link():
    dropped = asyncio.Event()
    client = FakeClient()
    client.write_delay_s = 3600
    asyncio.get_running_loop().call_later(0.01, dropped.set)
    with pytest.raises(SessionDropped) as info:
        await guarded_write(client, "w", b"x", step="upload", timeout=0.05, dropped=dropped)
    assert info.value.detail == "upload"


async def test_wrap_is_not_called_for_a_chunk_that_will_not_be_written():
    from blesession import session as session_mod

    client = FakeClient()
    dropped = asyncio.Event()
    dropped.set()
    session_mod._DROPPED[client] = dropped
    framed = []
    with pytest.raises(SessionDropped):
        await write_chunks(
            client, "w", b"abc", 1, step="s", wrap=lambda o, c: framed.append(o) or c
        )
    assert framed == []


# ── cause: arity edge cases ───────────────────────────────────────────────


def test_a_fifth_parameter_with_a_default_still_gets_the_error():
    got = []

    def legacy(stage, detail, error, facts, exc=None):
        got.append(exc)

    exc = SessionDropped("gone")
    with pytest.warns(DeprecationWarning, match=r"\(stage, detail, error, facts, exc\)"):
        _report(legacy, exc)
    assert got == [exc]


def test_a_failure_taking_callback_with_an_optional_extra_is_not_legacy(recwarn):
    def cause(failure, extra=None):
        return "new"

    assert _report(cause)["likely_cause"] == "new"
    assert not [w for w in recwarn if issubclass(w.category, DeprecationWarning)]


def test_the_deprecation_points_at_the_callers_line_and_is_logged_once(caplog, monkeypatch):
    import warnings

    from blesession import report as report_mod

    monkeypatch.setattr(report_mod, "_WARNED", set())

    def legacy(stage, detail, error, facts):
        return None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _report(legacy)
        _report(legacy)
    assert caught[0].filename == __file__
    assert len([r for r in caplog.records if "deprecated" in r.getMessage()]) == 1


# ── retryable, per instance ───────────────────────────────────────────────


def test_a_too_small_write_size_and_a_failed_lookup_are_retryable_a_missing_profile_is_not():
    from bleak.exc import BleakError

    from blesession import characteristic_or_raise

    client = FakeClient()
    client.add_characteristic("svc", "ch", max_write_without_response_size=20)
    with pytest.raises(GattMismatch) as small:
        characteristic_or_raise(client, "svc", "ch", min_write_size=240)
    assert small.value.retryable is True
    with pytest.raises(GattMismatch) as absent:
        characteristic_or_raise(client, "svc", "other")
    assert absent.value.retryable is False

    def boom(uuid):
        raise BleakError("not discovered")

    client.services.get_service = boom
    with pytest.raises(GattMismatch) as failed:
        characteristic_or_raise(client, "svc", "ch")
    assert failed.value.retryable is True


def test_each_distinct_callback_is_logged_even_when_bound_methods_share_an_id(caplog, monkeypatch):
    from blesession import report as report_mod

    monkeypatch.setattr(report_mod, "_WARNED", set())

    class Device:
        def a(self, stage, detail, error, facts):
            return None

        def b(self, stage, detail, error, facts):
            return None

    device = Device()
    with pytest.warns(DeprecationWarning):
        _report(device.a)
        _report(device.b)
        _report(device.a)  # the same definition again: not logged twice
    assert len([r for r in caplog.records if "deprecated" in r.getMessage()]) == 2


def test_two_partials_of_different_callbacks_are_each_logged(caplog, monkeypatch):
    import functools

    from blesession import report as report_mod

    monkeypatch.setattr(report_mod, "_WARNED", set())

    def one(prefix, stage, detail, error, facts):
        return None

    def two(prefix, stage, detail, error, facts):
        return None

    with pytest.warns(DeprecationWarning):
        _report(functools.partial(one, "x"))
        _report(functools.partial(two, "x"))
    assert len([r for r in caplog.records if "deprecated" in r.getMessage()]) == 2
