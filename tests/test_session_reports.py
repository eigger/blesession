"""What `SessionReports` records, and what `run_attempts()` tells it.

A failure that another attempt follows is not a failure; only the loop knows
whether one follows, so it says so (`Attempt.retrying`) before `on_attempt`.
"""

import asyncio
import logging
from datetime import UTC, datetime

import pytest

from blesession import (
    Attempt,
    AttemptTimedOut,
    ConnectFailed,
    DeviceError,
    SessionReports,
    SessionTrace,
    build_report,
    classify,
    fallback_report,
    report_attempt,
    run_attempts,
)
from blesession import attempts as attempts_mod
from blesession.report import REPORT_KEYS


@pytest.fixture
def no_sleep(monkeypatch):
    async def sleep(_s):
        return None

    monkeypatch.setattr(attempts_mod, "sleep", sleep)


async def _record_all(attempt_fn, **kwargs):
    reports, seen = SessionReports(), []

    def file_it(a):
        seen.append(a.retrying)
        reports.record(report_attempt(a, operation="write", attempts=kwargs.get("max_attempts", 1)))

    await run_attempts(attempt_fn, on_attempt=file_it, **kwargs)
    return reports, seen


async def test_a_failed_attempt_another_follows_is_retrying(no_sleep):
    async def attempt(a):
        if a.number < 3:
            raise ConnectFailed("no slot")
        return "ok"

    reports, seen = await _record_all(attempt, max_attempts=3)
    assert seen == [True, True, False]
    assert reports.failures == 0 and reports.last_failure is None
    assert reports.last_retry["attempt"] == 2 and reports.last_retry["retrying"] is True
    assert reports.last["success"] is True and "retrying" not in reports.last


async def test_the_final_failure_after_retries_is_the_failure(no_sleep):
    async def attempt(a):
        raise ConnectFailed("no slot")

    reports, seen = await _record_all(attempt, max_attempts=3)
    assert seen == [True, True, False]
    assert reports.failures == 1
    assert reports.last_failure["attempt"] == 3 and "retrying" not in reports.last_failure
    assert reports.last_kind == "failure"


async def test_an_error_that_says_it_is_final_ends_the_loop_as_a_failure(no_sleep):
    """`retry_if` stops it before the last number: the attempt count would say
    "retried"; the loop knows better."""

    class Refused(DeviceError):
        retryable = False

    async def attempt(a):
        raise Refused("rejected key")

    reports, seen = await _record_all(attempt, max_attempts=3)
    assert seen == [False]
    assert reports.failures == 1 and reports.last_failure["attempt"] == 1


async def test_a_timed_out_attempt_is_final_not_retried(no_sleep):
    async def attempt(a):
        await asyncio.sleep(10)

    reports, seen = await _record_all(attempt, max_attempts=3, attempt_timeout_s=0.01)
    assert seen == [False]
    assert reports.failures == 1
    assert reports.last_failure["timed_out"] is True


async def test_a_declined_attempt_is_skipped_and_counts_nothing(no_sleep):
    async def attempt(a):
        raise AssertionError("never runs")

    async def guard():
        return "write locked"

    reports, seen = await _record_all(attempt, max_attempts=3, guard=guard)
    assert seen == [False]
    assert reports.last_kind == "skipped"
    assert reports.failures == 0 and reports.last_failure is None and reports.last_retry is None


async def test_a_retry_that_the_guard_then_declines_is_not_a_failure(no_sleep):
    calls = []

    async def guard():
        calls.append(1)
        return "superseded" if len(calls) == 2 else None

    async def attempt(a):
        raise ConnectFailed("no slot")

    reports, _ = await _record_all(attempt, max_attempts=3, guard=guard)
    assert reports.failures == 0
    assert reports.last_retry["attempt"] == 1
    assert reports.last["skipped"] == "superseded"


async def test_retry_if_runs_before_on_attempt_and_its_error_skips_the_recorder(no_sleep):
    seen = []

    def retry_if(_a):
        raise RuntimeError("bad predicate")

    async def attempt(a):
        raise ConnectFailed("no slot")

    with pytest.raises(RuntimeError, match="bad predicate"):
        await run_attempts(attempt, max_attempts=3, retry_if=retry_if, on_attempt=seen.append)
    assert seen == []  # documented: retry_if is evaluated first


def test_an_attempt_built_by_hand_is_final():
    a = Attempt(number=1, trace=SessionTrace(), error=ConnectFailed("x"))
    assert a.retrying is False
    assert classify(report_attempt(a, operation="write", attempts=3)) == "failure"


def test_the_retrying_key_comes_after_attempts_and_only_when_true():
    trace = SessionTrace()
    base = {
        "operation": "w",
        "trace": trace,
        "exc": ConnectFailed("x"),
        "attempt": 1,
        "attempts": 3,
    }
    on = build_report(**base, retrying=True)
    off = build_report(**base)
    assert "retrying" not in off and on["retrying"] is True
    mine = [k for k in on if k in REPORT_KEYS]
    assert mine == [k for k in REPORT_KEYS if k in on]
    assert list(on).index("retrying") == list(on).index("attempts") + 1


@pytest.mark.parametrize(
    ("report", "kind"),
    [
        ({"operation": "w", "success": True}, "ok"),
        ({"operation": "w", "success": False, "error": "x"}, "failure"),
        ({"operation": "w", "success": False, "error": "x", "retrying": True}, "retried"),
        ({"operation": "w", "success": False, "skipped": "locked"}, "skipped"),
        ({"operation": "w", "success": False, "skipped": 0}, "skipped"),  # falsy but not None
        ({"operation": "w", "success": False, "skipped": "", "error": "x"}, "skipped"),
    ],
)
def test_classify(report, kind):
    assert classify(report) == kind


def test_failures_are_counted_and_stamped():
    reports = SessionReports()
    when = datetime(2000, 1, 1, tzinfo=UTC)
    reports.record({"operation": "w", "success": False, "error": "x"}, now=when)
    reports.record({"operation": "w", "success": True})
    reports.record({"operation": "w", "success": False, "error": "y"})
    assert reports.failures == 2
    assert reports.last_failure["error"] == "y"
    assert reports.last_failure_at.tzinfo is UTC and reports.last_failure_at > when


def test_each_operation_keeps_its_own_latest_report():
    reports = SessionReports()
    printed = reports.record({"operation": "print", "success": True})
    polled = reports.record({"operation": "update", "success": False, "error": "x"})
    assert reports.last is polled
    assert reports.of("print") is printed and reports.of("update") is polled
    assert reports.of("nope") is None


def test_record_hands_back_the_same_report_object():
    reports = SessionReports()
    report = {"operation": "w", "success": False, "error": "x"}
    assert reports.record(report) is report
    assert reports.last is report and reports.last_failure is report


def test_clear_resets_everything_but_keeps_listeners():
    reports = SessionReports()
    calls = []
    reports.add_listener(lambda: calls.append(1))
    reports.record({"operation": "w", "success": False, "error": "x"})
    reports.clear()
    assert reports.last is None and reports.last_failure is None and reports.last_retry is None
    assert reports.failures == 0 and reports.last_failure_at is None
    assert reports.of("w") is None and reports.last_kind is None
    reports.record({"operation": "w", "success": True})
    assert calls == [1, 1]


def test_listeners_run_after_record_and_can_be_removed():
    reports = SessionReports()
    seen = []
    remove = reports.add_listener(lambda: seen.append(reports.last["success"]))
    reports.record({"operation": "w", "success": True})
    remove()
    remove()  # removing twice is fine
    reports.record({"operation": "w", "success": False, "error": "x"})
    assert seen == [True]


def test_a_listener_that_raises_does_not_stop_record_or_the_others(caplog):
    reports = SessionReports()
    seen = []

    def bad():
        raise RuntimeError("boom")

    reports.add_listener(bad)
    reports.add_listener(lambda: seen.append(1))
    with caplog.at_level(logging.ERROR):
        reports.record({"operation": "w", "success": True})
    assert seen == [1]
    assert "listener raised" in caplog.text


def test_a_cause_callback_that_raises_does_not_fail_the_report(caplog):
    def cause(_failure):
        raise RuntimeError("callback bug")

    with caplog.at_level(logging.DEBUG, logger="blesession.report"):
        report = build_report(
            operation="w", trace=SessionTrace(), exc=ConnectFailed("no slot"), cause=cause
        )
    assert report["error"] == "no slot"
    assert report["likely_cause"]  # the generic sentence
    assert "cause callback raised" in caplog.text


def test_fallback_report_cannot_fail_and_keeps_the_stage():
    trace = SessionTrace()
    with pytest.raises(ConnectFailed), trace.timed("connect"):
        raise ConnectFailed("no slot")
    report = fallback_report("print", trace=trace, exc=ConnectFailed("no slot"))
    assert report["operation"] == "print" and report["success"] is False
    assert report["error"] == "no slot" and report["failed_stage"] == "connect"
    assert fallback_report("print") == {"operation": "print", "success": True}


def test_fallback_report_takes_the_loop_facts_from_an_attempt():
    retried = Attempt(number=1, trace=SessionTrace(), error=ConnectFailed("x"), retrying=True)
    declined = Attempt(number=2, trace=SessionTrace(), skipped="locked")
    assert classify(fallback_report("w", attempt=retried)) == "retried"
    skipped = fallback_report("w", attempt=declined)
    assert skipped["skipped"] == "locked" and skipped["success"] is False
    assert classify(skipped) == "skipped"
    assert fallback_report("w", attempt=retried)["attempt"] == 1


def test_a_timed_out_failure_is_a_failure():
    timed = AttemptTimedOut(5)
    reports = SessionReports()
    reports.record(build_report(operation="w", trace=SessionTrace(), exc=timed))
    assert reports.failures == 1
