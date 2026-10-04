"""docs/contract.md names everything it promises: it fails when it falls behind."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import blesession
from blesession import (
    CAUSES,
    FACT_KEYS,
    REPORT_KEYS,
    Attempt,
    SessionTrace,
    build_report,
    stages,
)
from blesession import errors as errors_mod
from blesession import testing as testing_mod

DOC = (Path(__file__).parent.parent / "docs" / "contract.md").read_text()


def _missing(names):
    """Names the contract does not mention exactly, in backticks."""
    return sorted(n for n in names if f"`{n}`" not in DOC)


def test_every_public_name_is_in_the_contract():
    assert not _missing(blesession.__all__)


def test_every_cause_key_is_in_the_contract():
    assert not _missing(CAUSES)


def test_every_report_and_radio_key_is_in_the_contract():
    assert not _missing((*REPORT_KEYS, *FACT_KEYS))


def test_a_report_only_uses_documented_keys():
    from blesession import (
        AttemptTimedOut,
        NotificationTimeout,
    )

    trace = SessionTrace(stage_map={"unlock": stages.AUTH})
    with trace.timed("unlock"):
        pass
    trace.note(parts=3)
    own = {*REPORT_KEYS, *FACT_KEYS, "unlock_s", "parts", "via", "rssi"}
    for exc, skipped in (
        (None, None),
        (NotificationTimeout(1, step="x"), None),
        (AttemptTimedOut(5), None),
        (None, "locked"),
    ):
        report = build_report(
            operation="op",
            trace=trace,
            exc=exc,
            skipped=skipped,
            facts={"via": "p", "rssi": -70},
            attempt=1,
            attempts=3,
        )
        assert set(report) <= own, set(report) - own
        # Their own keys come first, in the documented order.
        mine = [key for key in report if key in REPORT_KEYS]
        assert mine == [key for key in REPORT_KEYS if key in report]
        assert list(report)[: len(mine)] == mine


def test_every_primary_stage_is_in_the_contract():
    assert not _missing(stages.ORDER)


def test_every_error_class_is_in_the_contract():
    classes = [
        name
        for name, obj in vars(errors_mod).items()
        if isinstance(obj, type) and issubclass(obj, errors_mod.BleSessionError)
    ]
    assert not _missing(classes)


def test_every_public_member_of_attempt_and_trace_is_in_the_contract():
    attempt_members = [f.name for f in dataclasses.fields(Attempt)] + [
        "ok",
        "failed_stage",
        "failed_detail",
    ]
    trace_members = [
        name for name in dir(SessionTrace) if not name.startswith("_") and name not in {"decorate"}
    ]
    # A method is written with its call, `timed(name)`; an attribute by name.
    pattern = "`{}[(`]"
    members = [*attempt_members, *trace_members, "link"]  # `link`: an instance attribute
    assert not [m for m in members if not re.search(pattern.format(re.escape(m)), DOC)]


def test_the_test_fakes_are_in_the_contract():
    fakes = [
        name
        for name, obj in vars(testing_mod).items()
        if isinstance(obj, type) and obj.__module__ == testing_mod.__name__
    ] + ["fake_connect"]
    assert not _missing(fakes)
