import asyncio

import pytest

from blesession import (
    ConnectFailed,
    Notifications,
    NotificationTimeout,
    SessionDropped,
    SessionTrace,
    ble_session,
    stages,
)
from blesession import notifications as notifications_mod
from blesession import session as session_mod
from blesession.testing import FakeClient, FakeDevice, fake_connect


@pytest.fixture
def client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(session_mod, "establish_connection", fake_connect(client))
    return client


async def test_session_times_connect_session_disconnect_and_records_link(client):
    trace = SessionTrace()
    device = FakeDevice(details={"source": "AA:11"})
    async with ble_session(device, trace=trace) as c:
        assert c is client
        assert trace.stage == stages.SESSION
    assert client.disconnects == 1
    assert not client.is_connected
    assert list(trace.timings) == ["connect", "session", "disconnect"]
    assert trace.failed_stage is None
    assert trace.link is not None
    assert trace.link.source == "AA:11"
    assert trace.link.proxy is True


async def test_connect_failure_becomes_connect_failed_with_stage(monkeypatch):
    monkeypatch.setattr(session_mod, "establish_connection", fake_connect(fail=OSError("no slot")))
    trace = SessionTrace()
    with pytest.raises(ConnectFailed) as info:
        async with ble_session(FakeDevice(), trace=trace):
            pass
    assert info.value.stage == stages.CONNECT
    assert "no slot" in str(info.value)
    assert trace.failed_stage == "connect"
    assert "session" not in trace.timings


async def test_body_failure_keeps_inner_stage_and_disconnect_failure_is_ignored(client):
    client.fail_disconnect = OSError("gone")
    trace = SessionTrace()
    with pytest.raises(RuntimeError, match="boom"):
        async with ble_session(FakeDevice(), trace=trace):
            with trace.timed("transfer"):
                raise RuntimeError("boom")
    assert trace.failed_stage == "transfer"
    assert client.disconnects == 1
    assert "disconnect" in trace.timings


async def test_disconnect_failure_after_success_is_forgiven(client):
    client.fail_disconnect = OSError("gone")
    trace = SessionTrace()
    async with ble_session(FakeDevice(), trace=trace):
        pass
    assert trace.failed_stage is None


async def test_keep_leaves_link_up_and_disconnect_is_bounded(client):
    async with ble_session(FakeDevice(), keep=True):
        pass
    assert client.disconnects == 0 and client.is_connected

    client.disconnect_delay_s = 10
    async with asyncio.timeout(2):
        async with ble_session(FakeDevice(), disconnect_timeout_s=0.01):
            pass
    assert client.disconnects == 1


async def test_settle_drop_is_connect_failed_with_settle_detail(client):
    async def drop_soon():
        await asyncio.sleep(0.01)
        client.drop()

    asyncio.get_running_loop().create_task(drop_soon())
    with pytest.raises(ConnectFailed) as info:
        async with ble_session(FakeDevice(), settle_s=1.0):
            pass
    assert info.value.detail == "settle"
    assert client.disconnects == 0  # nothing to disconnect


async def test_close_stale_is_called_before_connecting(client, monkeypatch):
    calls = []

    async def close_stale(address):
        calls.append(address)

    monkeypatch.setattr(session_mod, "close_stale_connections_by_address", close_stale)
    async with ble_session(FakeDevice(address="11:22"), close_stale=True):
        pass
    assert calls == ["11:22"]


async def test_notifications_queue_settle_and_timeouts(client, monkeypatch):
    sleeps = []

    async def sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(session_mod.asyncio, "sleep", sleep)
    client.reply(b"\x01")  # before subscribing: delivered on subscribe
    async with Notifications(client, "n", settle=0.5) as replies:
        assert sleeps == [0.5]
        assert await replies.next(1, step="start") == b"\x01"
        client.reply(b"\x02")
        client.reply(b"\x03")
        assert replies.pending == 2
        assert replies.clear() == [b"\x02", b"\x03"]
        with pytest.raises(NotificationTimeout) as info:
            await replies.next(0.01, step="finish")
        assert info.value.step == "finish"
        assert "finish" in str(info.value)
        assert isinstance(info.value, TimeoutError)
        client.reply(b"\x09")
        client.reply(b"\xaa")
        assert await replies.wait_for(lambda d: d == b"\xaa", 1, step="done") == b"\xaa"
    assert client.subscribed == {}


async def test_notifications_unsubscribe_failure_does_not_mask(client):
    client.fail_stop_notify = OSError("dropped")
    with pytest.raises(ValueError):
        async with Notifications(client, "n"):
            raise ValueError
    client.drop()
    client.fail_stop_notify = None
    async with Notifications(client, "n"):
        pass  # not connected: stop_notify skipped, no error


def test_notification_timeout_can_carry_its_own_wording():
    exc = NotificationTimeout(2, step="START", message="No response to START after 3 probes")
    assert str(exc) == "No response to START after 3 probes"
    assert exc.step == "START" and exc.detail == "START"


async def test_wait_ends_on_a_drop_instead_of_running_the_timeout_out(client):
    """A link that goes mid-wait fails now, not after the step's timeout."""
    trace = SessionTrace()
    with pytest.raises(SessionDropped) as info:
        async with ble_session(FakeDevice(), trace=trace) as c:
            async with Notifications(c, "n") as replies:
                with trace.timed("transfer"):
                    asyncio.get_running_loop().call_soon(c.drop)
                    await replies.next(600, step="part 3/40")
    assert "part 3/40" in str(info.value)
    assert info.value.detail == "part 3/40"
    # The trace names the stage; the error's own "session" does not override it.
    assert trace.failure(info.value) == ("transfer", None)
    assert trace.timings["transfer"] < 1


async def test_a_reply_that_beat_the_drop_is_still_delivered(client):
    async with ble_session(FakeDevice()) as c:
        async with Notifications(c, "n") as replies:
            c.reply(b"\x07")
            c.drop()
            assert await replies.next(1, step="finish") == b"\x07"
            with pytest.raises(SessionDropped):
                await replies.next(1, step="finish")


async def test_an_unwatched_client_waits_its_timeout_out():
    """Notifications on a connection nobody watches keeps the old behaviour."""
    bare = FakeClient()
    async with Notifications(bare, "n") as replies:
        bare.drop()
        with pytest.raises(NotificationTimeout):
            await replies.next(0.01, step="start")


async def test_unsubscribe_is_bounded_so_a_dead_proxy_cannot_hang_the_exit(client, monkeypatch):
    """__aexit__ runs after the attempt bound has already fired, so its own
    bound is the only thing between a wedged proxy and a hung lock."""
    monkeypatch.setattr(notifications_mod, "STOP_NOTIFY_TIMEOUT_S", 0.01)
    hang = asyncio.Event()

    async def never_returns(_characteristic):
        await hang.wait()

    client.stop_notify = never_returns
    async with asyncio.timeout(1):
        async with Notifications(client, "n"):
            pass  # the unsubscribe gives up; no error escapes


async def test_disconnect_failure_is_reported_as_a_fact_not_a_failure(client):
    client.fail_disconnect = OSError("gone")
    trace = SessionTrace()
    async with ble_session(FakeDevice(), trace=trace):
        pass
    assert trace.failed_stage is None
    assert trace.facts["disconnect_error"] == "gone"


async def test_a_caller_s_own_disconnected_callback_still_runs(client):
    seen = []
    async with ble_session(FakeDevice(), disconnected_callback=seen.append) as c:
        c.drop()
    assert seen == [client]


async def test_a_drop_from_a_failed_connect_retry_does_not_poison_the_session(monkeypatch):
    """establish_connection retries connect() on one client, so a failed
    attempt can fire the disconnect callback before the link that works."""
    client = FakeClient()

    async def connect(_cls, _device, _name, disconnected_callback=None, **_kwargs):
        client.disconnected_callback = disconnected_callback
        disconnected_callback(client)  # the attempt that failed
        return client

    monkeypatch.setattr(session_mod, "establish_connection", connect)
    async with ble_session(FakeDevice(), settle_s=0.05) as c:
        async with Notifications(c, "n") as replies:
            c.reply(b"\x01")
            assert await replies.next(1, step="start") == b"\x01"


async def test_dropping_an_already_dropped_link_does_nothing(client):
    """bleak calls the disconnect callback once per link; so does the fake."""
    fired = []
    async with ble_session(FakeDevice(), disconnected_callback=fired.append) as c:
        c.drop()
        c.drop()
    assert fired == [client]
    assert client.disconnects == 0  # nothing left to disconnect
