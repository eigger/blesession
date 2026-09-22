"""The breakdown of one session, as an integration publishes it.

Outcome first, then the radio, then the stage timings and facts, so the
fields a reader looks at first are at the top of the attribute list, and
the same keys mean the same thing on every integration.

`SessionReports` holds the two slots those attributes go on: the last
session, and the last one that failed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .attempts import Attempt
from .causes import generic_cause
from .errors import AttemptTimedOut, error_text
from .trace import SessionTrace

Cause = Callable[[str | None, str | None, str, Mapping[str, Any]], str | None]
"""(primary stage, detail, error text, radio facts) -> the device's own
sentence, or None to fall back to the generic one."""

FACT_KEYS: tuple[str, ...] = ("via", "via_type", "rssi", "paths", "advertised_via")
"""Radio facts, in report order (see blesession.hass.radio_facts)."""


def build_report(
    *,
    operation: str,
    trace: SessionTrace,
    exc: BaseException | None = None,
    skipped: Any = None,
    facts: Mapping[str, Any] | None = None,
    cause: Cause | None = None,
    noun: str = "device",
    attempt: int | None = None,
    attempts: int | None = None,
    failed_stage: str | None = None,
    failed_detail: str | None = None,
) -> dict[str, Any]:
    """Assemble the attributes for one finished session.

        operation, success,
        skipped,                                                       # guard declined
        error, failed_stage, failed_detail, likely_cause, timed_out,   # failures
        attempt, attempts,
        via, via_type, rssi, paths, advertised_via,
        <stage>_s ...,                                                 # run order
        <trace facts> ...

    `failed_stage` / `failed_detail` default to `trace.failure(exc)` — the
    trace first, then what the error itself carries; pass them to override.

    `skipped` is what a `run_attempts()` guard returned when it declined to
    run the attempt at all. Nothing was tried, so the session did not
    succeed: `success` is False and there is no `error` to go with it.
    """
    facts = dict(facts or {})
    report: dict[str, Any] = {
        "operation": operation,
        "success": exc is None and skipped is None,
    }
    if skipped is not None:
        report["skipped"] = skipped
    if exc is not None:
        error = error_text(exc)
        stage, detail = trace.failure(exc)
        if failed_stage is not None:
            stage = failed_stage
        if failed_detail is not None:
            detail = failed_detail
        report["error"] = error
        if stage is not None:
            report["failed_stage"] = stage
        if detail is not None:
            report["failed_detail"] = detail
        # The attempt bound is the library's own mechanism, so its sentence
        # wins; for everything else the device's reading comes first.
        likely = None
        if isinstance(exc, AttemptTimedOut):
            likely = generic_cause(stage, error, facts, exc=exc, noun=noun)
        if likely is None and cause is not None:
            likely = cause(stage, detail, error, facts)
        if likely is None:
            likely = generic_cause(stage, error, facts, exc=exc, noun=noun)
        if likely is not None:
            report["likely_cause"] = likely
        if isinstance(exc, AttemptTimedOut):
            report["timed_out"] = True
    if attempt is not None:
        report["attempt"] = attempt
    if attempts is not None:
        report["attempts"] = attempts
    for key in FACT_KEYS:
        if key in facts:
            report[key] = facts[key]
    for key, value in facts.items():
        if key not in FACT_KEYS:
            report[key] = value
    for stage_name, seconds in trace.timings.items():
        report[f"{stage_name}_s"] = seconds
    report.update(trace.facts)
    return report


def report_attempt(
    attempt: Attempt[Any],
    *,
    operation: str,
    facts: Mapping[str, Any] | None = None,
    cause: Cause | None = None,
    noun: str = "device",
    attempts: int | None = None,
) -> dict[str, Any]:
    """build_report() for one Attempt from run_attempts()."""
    return build_report(
        operation=operation,
        trace=attempt.trace,
        exc=attempt.error,
        skipped=attempt.skipped,
        facts=facts,
        cause=cause,
        noun=noun,
        attempt=attempt.number,
        attempts=attempts,
    )


@dataclass
class SessionReports:
    """The two report slots an integration publishes, and the rule between them.

        reports = SessionReports()
        ...
        reports.record(report_attempt(attempt, operation="write", facts=facts))

        # on the duration / timestamp sensor
        return reports.last
        # on the diagnostic sensor that must survive the next success
        return reports.last_failure

    `last` is the most recent session, success or failure. `last_failure` is
    the most recent one that actually failed, kept until the next failure —
    a success must not erase the evidence, because the user who comes to
    read it at 3 am has usually had a working session since.

    A session a `guard` declined (`skipped`) is not a failure: nothing was
    tried, so it becomes `last` but leaves `last_failure` alone rather than
    overwriting the last real one with "the write lock was held".
    """

    last: dict[str, Any] | None = None
    last_failure: dict[str, Any] | None = None

    def record(self, report: dict[str, Any]) -> dict[str, Any]:
        """File `report` in both slots as it belongs, and hand it back."""
        self.last = report
        if report.get("error") is not None:
            self.last_failure = report
        return report

    def clear(self) -> None:
        """Forget both (the device was removed, or the user reset it)."""
        self.last = None
        self.last_failure = None
