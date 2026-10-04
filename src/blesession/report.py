"""The breakdown of one session, as an integration publishes it.

Outcome first, then the radio, then the stage timings and facts, so the
fields a reader looks at first are at the top of the attribute list, and
the same keys mean the same thing on every integration.

`SessionReports` holds the two slots those attributes go on: the last
session, and the last one that failed.
"""

from __future__ import annotations

import inspect
import logging
import os
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .attempts import Attempt
from .causes import cause_key, generic_cause
from .errors import AttemptTimedOut, error_text
from .trace import SessionTrace

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Failure:
    """What the `cause` callback is told about a failed session.

    One object rather than a list of arguments, so a field added later does
    not break every integration's callback.

    `stage` is the primary stage the failure happened in (`stages.AUTH`, ...)
    and `detail` the device's own stage name when it differs; `exc` is the
    error itself (test its type: `isinstance(failure.exc, NotificationTimeout)`);
    `error` its text; `facts` the radio facts (`via`, `rssi`, `paths`, ...).
    """

    stage: str | None
    detail: str | None
    error: str
    exc: BaseException
    facts: Mapping[str, Any]


Cause = Callable[[Failure], str | None]
"""The device's own sentence for a failure, or None to fall back to the
generic one. Take one `Failure`."""

_LEGACY_ARGS = (
    "(stage, detail, error, facts)",
    "(stage, detail, error, facts, exc)",
)
_WARNED: set[tuple[str, str, int]] = set()
_PACKAGE_DIR = os.path.dirname(__file__)


def _legacy_shape(cause: Callable[..., Any]) -> int | None:
    """4 or 5 when `cause` is one of the pre-0.7 positional shapes, else None.

    A callback is legacy when it takes 4 or 5 positional arguments (its
    required ones, or all of them when none is required); a 5th that has a
    default still gets `exc`. A callback with `*args`, one parameter, or one
    that cannot be inspected is called with a `Failure`.
    """
    try:
        params = list(inspect.signature(cause).parameters.values())
    except (TypeError, ValueError):
        return None
    if any(p.kind is p.VAR_POSITIONAL for p in params):
        return None
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    required = [p for p in positional if p.default is p.empty]
    count = len(required) or len(positional)
    if count not in (4, 5):
        return None
    return 5 if len(positional) >= 5 else 4


def _identity(cause: Callable[..., Any]) -> tuple[str, str, int]:
    """A key that names the callback's definition, not the object.

    Bound methods are new objects on every access, and the id of a freed one is
    handed to the next, so identity would both repeat the warning and silence a
    different callback. The code that defines it is what stays the same.
    """
    func = getattr(cause, "__func__", cause)
    code = getattr(func, "__code__", None)
    qualname = getattr(func, "__qualname__", type(func).__qualname__)
    return (
        getattr(func, "__module__", None) or "",
        qualname,
        code.co_firstlineno if code is not None else 0,
    )


def _call_cause(cause: Callable[..., str | None], failure: Failure) -> str | None:
    """Call `cause` with a `Failure`, or with the pre-0.7 positional arguments.

    The two older shapes, `(stage, detail, error, facts)` and
    `(stage, detail, error, facts, exc)`, still work; they raise a
    `DeprecationWarning` (Python hides it outside tests) and log one warning
    per callback so it shows in Home Assistant's log too. They go in 1.0.
    """
    shape = _legacy_shape(cause)
    if shape is None:
        return cause(failure)
    message = (
        f"A `cause` callback taking {_LEGACY_ARGS[shape - 4]} is deprecated and "
        "removed in blesession 1.0; take one blesession.Failure instead"
    )
    warnings.warn(message, DeprecationWarning, skip_file_prefixes=(_PACKAGE_DIR,))
    key = _identity(cause)
    if key not in _WARNED:
        _WARNED.add(key)
        _LOGGER.warning("%s (%r)", message, cause)
    args = (failure.stage, failure.detail, failure.error, failure.facts)
    return cause(*args) if shape == 4 else cause(*args, failure.exc)


REPORT_KEYS: tuple[str, ...] = (
    "operation",
    "success",
    "skipped",
    "error",
    "failed_stage",
    "failed_detail",
    "likely_cause",
    "likely_cause_key",
    "timed_out",
    "attempt",
    "attempts",
)
"""The report's own keys, in order. After them: `FACT_KEYS`, any other key in
`facts=`, one `<stage>_s` per timed stage, then the trace's `note()` facts."""

FACT_KEYS: tuple[str, ...] = (
    "via",
    "via_type",
    "rssi",
    "paths",
    "advertised_via",
    "via_unconfirmed",
)
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
        error, failed_stage, failed_detail,                            # failures
        likely_cause, likely_cause_key, timed_out,
        attempt, attempts,
        via, via_type, rssi, paths, advertised_via,
        <stage>_s ...,                                                 # run order
        <trace facts> ...

    `failed_stage` / `failed_detail` default to `trace.failure(exc)` — the
    trace first, then what the error itself carries; pass them to override.

    `likely_cause_key` is the stable name for a sentence this library wrote
    (see `blesession.causes.CAUSES`), so an integration can publish its own
    translation instead of the English one. A sentence from `cause` — the
    integration's own — carries no key: it already owns the wording.

    The key names the sentence, not the whole string: several sentences end
    in the weak-signal placement advice, which is English as well. A
    translation rebuilds that from `rssi`, `via` and `paths`, which are in
    the report beside the key, rather than translating the fragment.

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
        generic_key = cause_key(stage, error, exc=exc)
        report["error"] = error
        if stage is not None:
            report["failed_stage"] = stage
        if detail is not None:
            report["failed_detail"] = detail
        # The attempt bound is the library's own mechanism, so its sentence
        # wins; for everything else the device's reading comes first.
        likely: str | None = None
        likely_key: str | None = None
        if isinstance(exc, AttemptTimedOut):
            likely, likely_key = generic_cause(stage, error, facts, exc=exc, noun=noun), generic_key
        if likely is None and cause is not None:
            likely = _call_cause(cause, Failure(stage, detail, error, exc, facts))
        if likely is None:
            likely, likely_key = generic_cause(stage, error, facts, exc=exc, noun=noun), generic_key
        if likely is not None:
            report["likely_cause"] = likely
        # Only a sentence this library wrote gets a key. A device sentence
        # comes from the integration, which can translate its own without
        # being handed a name for it.
        if likely_key is not None:
            report["likely_cause_key"] = likely_key
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
        last = await run_attempts(attempt, max_attempts=3, on_attempt=file_it)
        ...

        # on the duration / timestamp sensor
        return reports.last
        # on the diagnostic sensor that must survive the next success
        return reports.last_failure

    **Record every attempt, not just the one `run_attempts()` returns.** It
    hands back the *last* attempt only, so filing that one alone loses a
    first attempt that failed and a second that worked — exactly the
    intermittent failure `last_failure` exists to keep. Record from
    `on_attempt`, which sees each attempt as it finishes:

        def file_it(a):
            reports.record(report_attempt(a, operation="write", attempts=3))

    Filing the returned attempt instead is the right call only when you
    want `last_failure` to mean "the last session that failed *overall*"
    rather than "the last attempt that failed".

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
