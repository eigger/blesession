"""Exercise the adapters integrations copy from the diagnostics guide."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from blesession import SessionReports, run_attempts

DOCS = Path(__file__).parent.parent / "docs"


def _blocks(language: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", (DOCS / language).read_text(encoding="utf-8"), re.S)


@pytest.fixture
def adapters(monkeypatch):
    english = _blocks("diagnostics.md")
    assert english == _blocks("diagnostics.ko.md")
    namespace = {}
    for block in english:
        if "def make_recorder(" in block or "def diagnostics_snapshot(" in block:
            exec(compile(block, "diagnostics.md", "exec"), namespace)
    monkeypatch.setitem(
        namespace,
        "radio_facts",
        lambda *_: {"paths": 1, "slots_free": 2, "slots": 3, "last_seen_s": 0.5},
    )
    return namespace


async def test_recorder_keeps_retry_evidence_without_counting_a_final_failure(adapters):
    reports = SessionReports()
    record = adapters["make_recorder"](reports, None, "device", "write", 2)

    async def operation(attempt):
        with attempt.trace.timed("transfer"):
            if attempt.number == 1:
                raise ConnectionError("interrupted")
            attempt.trace.note(new_safe_fact=42)
            return "done"

    result = await run_attempts(operation, max_attempts=2, pause_s=0, on_attempt=record)

    assert result.result == "done"
    assert reports.last_retry["retrying"] is True
    assert reports.last_retry["failed_stage"] == "transfer"
    assert reports.last_failure is None and reports.failures == 0
    assert reports.sessions == 1 and reports.last_success_at is not None
    assert reports.last["new_safe_fact"] == 42
    assert reports.last["slots_free"] == 2
    assert reports.of("write") is reports.last


async def test_external_cancellation_does_not_become_a_failure(adapters):
    reports = SessionReports()
    record = adapters["make_recorder"](reports, None, "device", "poll", 1)

    async def cancelled(attempt):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_attempts(cancelled, on_attempt=record)

    assert reports.last is None and reports.sessions == 0 and reports.failures == 0


async def test_recorder_fallback_retains_the_actual_failed_attempt(adapters, monkeypatch):
    reports = SessionReports()

    def broken_report(*args, **kwargs):
        raise RuntimeError("diagnostic adapter failed")

    monkeypatch.setitem(adapters, "report_attempt", broken_report)

    async def operation(attempt):
        raise ConnectionError("device refused")

    await run_attempts(
        operation,
        on_attempt=adapters["make_recorder"](reports, None, "device", "write", 1),
    )
    assert reports.last_failure["error"] == "device refused"
    assert reports.last_failure["operation"] == "write"
    assert reports.failures == 1


def test_snapshot_preserves_new_fields_history_and_json_without_mutating_reports(adapters):
    reports = SessionReports()
    reports.record({"operation": "write", "success": False, "error": "interrupted"})
    reports.record({"operation": "poll", "success": True, "new_safe_fact": 42})
    snapshot = adapters["diagnostics_snapshot"](reports)
    payload = json.loads(json.dumps(snapshot))["blesession"]

    assert payload["summary"]["sessions"] == 2
    assert payload["summary"]["consecutive_failures"] == 0
    assert payload["summary"]["last_success_at"].endswith("+00:00")
    assert payload["last_failure"]["error"] == "interrupted"
    assert payload["last"]["new_safe_fact"] == 42
    assert set(payload["by_operation"]) == {"write", "poll"}
    snapshot["blesession"]["last_failure"]["error"] = "redacted"
    snapshot["blesession"]["by_operation"]["poll"]["new_safe_fact"] = 0
    assert reports.last_failure["error"] == "interrupted"
    assert reports.of("poll")["new_safe_fact"] == 42


def test_snapshot_supports_an_empty_runtime(adapters):
    payload = adapters["diagnostics_snapshot"](SessionReports())["blesession"]
    assert payload["last"] is None and payload["last_retry"] is None
    assert payload["summary"]["sessions"] == 0
    assert payload["summary"]["last_success_at"] is None
    json.dumps(payload)
