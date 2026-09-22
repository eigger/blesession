import asyncio

import pytest

from blesession import (
    AttemptTimedOut,
    ConnectFailed,
    SessionReports,
    SessionTrace,
    Unreachable,
    ble_session,
    build_report,
    report_attempt,
    run_attempts,
    stages,
)
from blesession import attempts as attempts_mod
from blesession import session as session_mod
from blesession.testing import FakeClient, FakeDevice, fake_connect


@pytest.fixture
def no_sleep(monkeypatch):
    sleeps = []

    async def sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(attempts_mod, "sleep", sleep)
    return sleeps


async def test_success_first_try(no_sleep):
    async def attempt(a):
        return "ok"

    result = await run_attempts(attempt, max_attempts=3)
    assert result.ok and result.result == "ok" and result.number == 1
    assert no_sleep == []


async def test_retries_release_lock_between_attempts_and_report_each(no_sleep):
    lock = asyncio.Lock()
    seen = []
    states = []

    async def attempt(a):
        assert lock.locked()
        states.append(dict(a.state))
        a.state["transfer_failures"] = a.state.get("transfer_failures", 0) + 1
        with a.trace.timed("transfer"):
            raise RuntimeError(f"fail {a.number}")

    result = await run_attempts(
        attempt, lock=lock, max_attempts=3, pause_s=2, on_attempt=lambda a: seen.append(a.number)
    )
    assert seen == [1, 2, 3]
    assert no_sleep == [2, 2]
    assert not lock.locked()
    assert result.number == 3 and result.failed_stage == "transfer"
    assert states == [{}, {"transfer_failures": 1}, {"transfer_failures": 2}]


async def test_timed_out_attempt_is_not_retried_and_names_the_stage():
    hang = asyncio.Event()

    async def attempt(a):
        with a.trace.timed("readout"):
            await hang.wait()

    result = await run_attempts(
        attempt, max_attempts=3, attempt_timeout_s=0.01, stage_map={"readout": stages.TRANSFER}
    )
    assert result.number == 1
    assert result.timed_out
    assert isinstance(result.error, AttemptTimedOut)
    assert result.error.stage == stages.TRANSFER
    assert result.failed_stage == stages.TRANSFER and result.failed_detail == "readout"
    assert isinstance(result.error.__cause__, TimeoutError)


async def test_retry_if_decides_and_guard_skips_under_lock(no_sleep):
    async def attempt(a):
        raise ConnectFailed("no")

    result = await run_attempts(attempt, max_attempts=3, retry_if=lambda a: False)
    assert result.number == 1 and result.failed_stage == stages.CONNECT

    lock = asyncio.Lock()

    async def guard():
        assert lock.locked()
        return "locked"

    result = await run_attempts(attempt, lock=lock, guard=guard, max_attempts=3)
    assert result.skipped == "locked" and not result.ok and result.error is None


async def test_error_stage_used_when_no_stage_was_timed(no_sleep):
    async def attempt(a):
        raise Unreachable("AA")

    result = await run_attempts(attempt)
    assert result.failed_stage == stages.UNREACHABLE
    report = report_attempt(result, operation="write", attempts=1)
    assert report["failed_stage"] == "unreachable"
    assert "No radio" in report["likely_cause"]


async def test_report_order_and_cause_precedence(monkeypatch, no_sleep):
    client = FakeClient()
    monkeypatch.setattr(session_mod, "establish_connection", fake_connect(client))

    async def attempt(a):
        async with ble_session(FakeDevice(), trace=a.trace):
            a.trace.note(parts=4)
            with a.trace.timed("start"):
                raise RuntimeError("device error 5")

    result = await run_attempts(attempt, stage_map={"start": stages.AUTH})
    facts = {"rssi": -90, "via": "office", "via_type": "proxy", "paths": 1}

    def cause(stage, detail, error, f):
        return "rejected: not a WOLINK tag" if "device error 5" in error else None

    report = report_attempt(result, operation="write", facts=facts, cause=cause, attempts=3)
    assert list(report) == [
        "operation",
        "success",
        "error",
        "failed_stage",
        "failed_detail",
        "likely_cause",
        "attempt",
        "attempts",
        "via",
        "via_type",
        "rssi",
        "paths",
        "connect_s",
        "start_s",
        "session_s",
        "disconnect_s",
        "parts",
    ]
    assert report["failed_stage"] == "auth" and report["failed_detail"] == "start"
    assert report["likely_cause"] == "rejected: not a WOLINK tag"

    generic = build_report(
        operation="write", trace=result.trace, exc=result.error, facts=facts, noun="tag"
    )
    assert "likely_cause" not in generic  # auth with an unknown error: nothing generic to say

    weak = build_report(
        operation="write", trace=SessionTrace(), exc=ConnectFailed("x"), facts=facts, noun="tag"
    )
    assert weak["likely_cause"].endswith(
        "no other radio reaches the tag — move the tag or add a proxy."
    )


def test_success_report_is_short():
    trace = SessionTrace()
    with trace.timed("connect"):
        pass
    facts = {"via": "hci0", "via_type": "adapter"}
    report = build_report(operation="poll", trace=trace, facts=facts)
    assert report == {
        "operation": "poll",
        "success": True,
        **facts,
        "connect_s": trace.timings["connect"],
    }


async def test_direct_report_and_attempt_report_agree_on_the_detail(no_sleep):
    """A ConnectFailed(detail="settle") raised inside the connect stage keeps
    its detail on both paths."""

    async def attempt(a):
        with a.trace.timed("connect"):
            raise ConnectFailed("dropped in settle", detail="settle")

    result = await run_attempts(attempt)
    via_attempt = report_attempt(result, operation="write")
    direct = build_report(operation="write", trace=result.trace, exc=result.error)
    for report in (via_attempt, direct):
        assert (report["failed_stage"], report["failed_detail"]) == ("connect", "settle")
        assert "before encryption settled" in report["likely_cause"]


async def test_a_skipped_attempt_is_not_a_success():
    """A guard that declined means nothing was tried; the report must not
    read as a session that worked."""

    async def attempt(a):
        raise AssertionError("never runs")

    async def guard():
        return "write locked"

    result = await run_attempts(attempt, guard=guard)
    report = report_attempt(result, operation="write", attempts=3)
    assert list(report)[:3] == ["operation", "success", "skipped"]
    assert report["success"] is False
    assert report["skipped"] == "write locked"
    assert "error" not in report and "likely_cause" not in report


def test_a_failure_that_only_says_so_in_words_keeps_its_sentence():
    """build_report() always has the exception, so a type check alone would
    silently retire the text markers on the one path that matters."""
    trace = SessionTrace()
    with pytest.raises(RuntimeError):
        with trace.timed("transfer"):
            raise RuntimeError("no response after part 3/40")
    report = build_report(
        operation="write", trace=trace, exc=RuntimeError("no response after part 3/40")
    )
    assert "stopped answering mid-transfer" in report["likely_cause"]


def test_a_success_does_not_erase_the_last_failure():
    """The user reading the failure sensor at 3 am has usually had a working
    session since; that is the whole point of the second slot."""
    reports = SessionReports()
    assert reports.last is None and reports.last_failure is None

    failed = reports.record(
        build_report(operation="write", trace=SessionTrace(), exc=ConnectFailed("no slot"))
    )
    assert reports.last is failed and reports.last_failure is failed

    ok = reports.record(build_report(operation="write", trace=SessionTrace()))
    assert reports.last is ok
    assert reports.last_failure is failed  # kept

    reports.clear()
    assert reports.last is None and reports.last_failure is None


async def test_a_skipped_session_is_not_the_failure_to_keep():
    """`success: False` with nothing tried must not overwrite the last real
    failure with "the write lock was held"."""

    async def attempt(a):
        raise ConnectFailed("no slot")

    reports = SessionReports()
    failed = reports.record(report_attempt(await run_attempts(attempt), operation="write"))

    async def never_runs(a):
        raise AssertionError

    skipped = await run_attempts(never_runs, guard=lambda: _declined())
    reports.record(report_attempt(skipped, operation="write"))

    assert reports.last["skipped"] == "write locked"
    assert reports.last["success"] is False
    assert reports.last_failure is failed


async def _declined():
    return "write locked"
