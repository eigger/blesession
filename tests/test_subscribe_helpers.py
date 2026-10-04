"""stop_notify_best_effort and the response=None write: cleanup shared with Notifications."""

from __future__ import annotations

import asyncio

import pytest

from blesession import stop_notify_best_effort
from blesession.testing import FakeClient


@pytest.mark.asyncio
async def test_stop_notify_releases_a_subscription():
    client = FakeClient()
    await client.start_notify("uuid", lambda *_: None)
    await stop_notify_best_effort(client, "uuid")
    assert client.subscribed == {}


@pytest.mark.asyncio
async def test_stop_notify_does_nothing_on_a_link_that_is_down():
    client = FakeClient()
    await client.start_notify("uuid", lambda *_: None)
    client.drop()
    await stop_notify_best_effort(client, "uuid")
    assert "uuid" in client.subscribed, "a dead link was written to"


@pytest.mark.asyncio
async def test_stop_notify_never_raises():
    client = FakeClient()
    client.fail_stop_notify = RuntimeError("proxy said no")
    await stop_notify_best_effort(client, "uuid")


@pytest.mark.asyncio
async def test_stop_notify_is_bounded():
    class Hanging(FakeClient):
        async def stop_notify(self, characteristic):
            await asyncio.Event().wait()

    await asyncio.wait_for(stop_notify_best_effort(Hanging(), "uuid", timeout=0.01), timeout=0.5)


@pytest.mark.asyncio
async def test_a_write_can_leave_the_type_to_the_backend():
    from blesession import guarded_write

    client = FakeClient()
    await guarded_write(client, "uuid", b"\x01", step="x", response=None)
    assert client.writes == [("uuid", b"\x01", None)]
