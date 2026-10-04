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
        import blesession

        class Block:
            def find_spec(self, name, path=None, target=None):
                if name == "homeassistant" or name.startswith("homeassistant."):
                    raise ImportError("homeassistant must not be imported by the core")

        sys.meta_path.insert(0, Block())
        for mod in pkgutil.iter_modules(blesession.__path__):
            if mod.name != "hass":
                importlib.import_module(f"blesession.{mod.name}")
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
