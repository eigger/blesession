import pytest

from blesession import SessionTrace, stages, traced


def test_timed_records_seconds_and_innermost_failure():
    trace = SessionTrace(stage_map={"unlock": stages.AUTH})
    with pytest.raises(RuntimeError):
        with trace.timed("session"):
            with trace.timed("unlock"):
                raise RuntimeError("nope")
    assert trace.failed_stage == "unlock"
    assert trace.failed_primary == stages.AUTH
    assert trace.failed_detail == "unlock"
    assert set(trace.timings) == {"unlock", "session"}
    assert list(trace.as_dict())[0] == "failed_stage"


def test_first_failure_wins_and_repeat_adds_up():
    trace = SessionTrace()
    with pytest.raises(ValueError):
        with trace.timed("transfer"):
            raise ValueError
    with pytest.raises(ValueError):
        with trace.timed("disconnect"):
            raise ValueError
    assert trace.failed_stage == "transfer"
    with trace.timed("transfer"):
        pass
    assert "transfer" in trace.timings


def test_primary_names_need_no_map_and_unknown_pass_through():
    trace = SessionTrace()
    trace.fail("transfer")
    assert trace.failed_primary == "transfer"
    assert trace.failed_detail is None
    trace.forgive("other")
    assert trace.failed_stage == "transfer"
    trace.forgive()
    assert trace.failed_stage is None
    trace.fail("readout")
    assert trace.failed_primary == "readout"  # unmapped: shown as is


def test_note_drops_none_and_cancellation_is_attributed():
    trace = SessionTrace()
    trace.note(parts=3, pacing=None)
    assert trace.facts == {"parts": 3}
    with pytest.raises(KeyboardInterrupt):
        with trace.timed("transfer"):
            raise KeyboardInterrupt
    assert trace.failed_stage == "transfer"


async def test_traced_decorator_uses_self_trace_when_present():
    class Host:
        trace = SessionTrace()

        @traced("readout")
        async def read(self):
            return 1

    class Bare:
        @traced("readout")
        async def read(self):
            return 2

    assert await Host().read() == 1
    assert "readout" in Host.trace.timings
    assert await Bare().read() == 2
