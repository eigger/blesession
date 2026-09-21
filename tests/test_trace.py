import pytest

from blesession import (
    ConnectFailed,
    NotificationTimeout,
    SessionTrace,
    Unreachable,
    stages,
    traced,
)


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


def test_record_adds_a_stage_measured_elsewhere():
    trace = SessionTrace()
    trace.record("connect", 0.25)
    trace.record("connect", 0.25)
    assert trace.timings == {"connect": 0.5}


def test_failure_merges_trace_and_error():
    """The trace names the stage; the error adds a detail only for that stage."""
    # No stage timed: the error is all there is.
    assert SessionTrace().failure(Unreachable("AA")) == ("unreachable", None)
    assert SessionTrace().failure(RuntimeError("x")) == (None, None)
    assert SessionTrace().failure(None) == (None, None)

    # The error describes the stage the trace attributed the failure to.
    trace = SessionTrace()
    with pytest.raises(ConnectFailed):
        with trace.timed("connect"):
            raise ConnectFailed("dropped", detail="settle")
    assert trace.failure(ConnectFailed("dropped", detail="settle")) == ("connect", "settle")

    # A step name is not a stage detail; the device's own stage name wins.
    trace = SessionTrace(stage_map={"handshake": stages.AUTH})
    with pytest.raises(NotificationTimeout):
        with trace.timed("handshake"):
            raise NotificationTimeout(5, step="START")
    assert trace.failure(NotificationTimeout(5, step="START")) == ("auth", "handshake")
    trace = SessionTrace()
    with pytest.raises(NotificationTimeout):
        with trace.timed("transfer"):
            raise NotificationTimeout(5, step="part 3/40")
    assert trace.failure(NotificationTimeout(5, step="part 3/40")) == ("transfer", None)
