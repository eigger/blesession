import pytest
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakAbortedError,
    BleakConnectionError,
    BleakNotFoundError,
    BleakOutOfConnectionSlotsError,
)

from blesession import (
    CAUSES,
    AttemptTimedOut,
    ConnectFailed,
    GattMismatch,
    LinkInfo,
    NotificationTimeout,
    SessionDropped,
    WriteTimeout,
    cause_key,
    connected_via,
    generic_cause,
    is_proxy,
    placement,
    probe_link,
    stages,
)
from blesession.testing import FakeClient, FakeDevice

WEAK = {"rssi": -90, "via": "office", "paths": 1}
FINE = {"rssi": -60, "via": "office", "paths": 1}


def test_placement_only_when_weak_and_names_single_path():
    assert placement(FINE, noun="tag") == ""
    assert placement({"rssi": "n/a"}) == ""
    text = placement(WEAK, noun="tag")
    assert text.startswith(" The signal is weak (-90 dBm via office)")
    assert "no other radio reaches the tag" in text
    assert "no other radio" not in placement({**WEAK, "paths": 2})


def test_generic_cause_per_stage():
    assert "cut at its bound" in generic_cause(None, "", {}, exc=AttemptTimedOut(5))
    assert "No radio currently sees the cuff" in generic_cause(
        stages.UNREACHABLE, "", {}, noun="cuff"
    )
    assert "no free connection slot" in generic_cause(stages.CONNECT, "no slot free", {})
    assert "before encryption settled" in generic_cause(stages.CONNECT, "dropped in settle", {})
    assert generic_cause(stages.CONNECT, "x", WEAK).endswith("add a proxy.")
    assert "before the protocol started" in generic_cause(stages.SESSION, "", {})
    assert "did not answer the handshake" in generic_cause(stages.AUTH, "No response within", {})
    assert generic_cause(stages.AUTH, "device error 5", {}) is None
    assert "mid-transfer" in generic_cause(stages.TRANSFER, "no response after part", {})
    assert generic_cause(stages.TRANSFER, "stalled", {}) is None
    assert "did not report completion" in generic_cause(stages.FINISH, "no response", {})
    assert generic_cause(stages.FINISH, "device error", {}) is None
    assert "only the session close failed" in generic_cause(stages.DISCONNECT, "", {})
    assert generic_cause("readout", "", {}) is None


def test_is_proxy_and_advertising_source():
    assert is_proxy(FakeDevice(details={"source": "AA:11"}))
    assert not is_proxy(FakeDevice(details="/org/bluez/hci0/dev_AA"))
    assert not is_proxy(FakeDevice(details={}))


def test_connected_via_probe_order():
    class Backend:
        _source = "proxy-1"

    client = FakeClient()
    assert connected_via(client) is None
    client._backend = Backend()
    assert connected_via(client) == "proxy-1"
    Backend._source = None
    Backend._device_path = "/org/bluez/hci0/dev_AA"
    assert connected_via(client) == "/org/bluez/hci0/dev_AA"
    scanner = object()
    client._connected_scanner = scanner
    assert connected_via(client) is scanner
    link = probe_link(client, FakeDevice(details={"source": "AA:11"}))
    assert link == LinkInfo(via=scanner, source="AA:11", proxy=True)


def test_cause_reads_the_exception_not_its_wording():
    """An integration that words its own timeout keeps the generic sentence."""
    own_words = NotificationTimeout(5, step="START", message="No reply to START after 3 probes")
    assert "did not answer the handshake" in generic_cause(
        stages.AUTH, str(own_words), {}, exc=own_words
    )
    settle = ConnectFailed("the tag went away at 0.40s", detail="settle")
    assert "before encryption settled" in generic_cause(stages.CONNECT, str(settle), {}, exc=settle)


def test_a_dropped_link_reads_as_a_drop_in_every_protocol_stage():
    dropped = SessionDropped("The link dropped while waiting for part 3/40", detail="part 3/40")
    for stage in (stages.AUTH, stages.TRANSFER, stages.FINISH):
        sentence = generic_cause(stage, str(dropped), WEAK, exc=dropped, noun="tag")
        assert "went away mid-session" in sentence
        assert sentence.endswith("add a proxy.")
    # The connect stage keeps its own reading; nothing dropped mid-session there.
    assert "could not be established" in generic_cause(stages.CONNECT, "no route", {}, noun="tag")
    # Nor does the close: a drop it reports is the close failing, not the session.
    assert "only the session close failed" in generic_cause(
        stages.DISCONNECT, "link dropped", {}, noun="tag"
    )


def test_every_key_has_a_sentence_and_every_sentence_a_key():
    """The two halves of the generic table are one contract; a key with no
    sentence would be a KeyError in the middle of reporting a failure."""
    reachable = {
        cause_key(stage, error, exc=exc)
        for stage in (None, "readout", *stages.ORDER)
        for error, exc in (
            ("", None),
            ("no slot free", None),
            ("dropped in settle", None),
            ("no response within 5s", None),
            ("the link dropped while waiting", None),
            ("", AttemptTimedOut(5)),
            ("", NotificationTimeout(5, step="x")),
            ("", SessionDropped("gone")),
            ("", ConnectFailed("x", detail="settle")),
            ("", GattMismatch("no service")),
            ("", WriteTimeout(5, step="x")),
            ("", NotificationTimeout(5, step="x", rejected=3)),
            ("", _connect_failure(BleakNotFoundError("gone"))),
            ("", _connect_failure(BleakAbortedError("aborted"))),
            ("", _connect_failure(BleakOutOfConnectionSlotsError("full"))),
        )
    } | {
        cause_key(stages.UNREACHABLE, "", facts={"paths": 0, "paths_passive": 1}),
    }
    assert reachable - {None} == set(CAUSES)


def test_a_key_names_the_stage_and_the_reading():
    assert cause_key(stages.CONNECT, "no slot free") == "connect.no_slot"
    assert cause_key(stages.CONNECT, "dropped in settle") == "connect.settle"
    assert cause_key(stages.CONNECT, "whatever") == "connect.failed"
    assert cause_key(stages.AUTH, "no response") == "auth.no_answer"
    assert cause_key(stages.AUTH, "device error 5") is None  # nothing generic to say
    assert cause_key(stages.DISCONNECT, "link dropped") == "disconnect.close_failed"
    assert cause_key("readout", "") is None  # an unmapped stage is not guessed at


def test_the_sentence_is_the_key_rendered():
    facts = {"rssi": -90, "via": "office", "paths": 1}
    assert generic_cause(stages.AUTH, "no response", facts, noun="tag") == CAUSES[
        "auth.no_answer"
    ].format(noun="tag", where=placement(facts, noun="tag"))


def test_a_timed_out_attempt_reports_its_own_sentence_and_key():
    from blesession import SessionTrace, build_report

    trace = SessionTrace()
    exc = AttemptTimedOut(30, stage=stages.CONNECT)
    report = build_report(
        operation="op",
        trace=trace,
        exc=exc,
        cause=lambda failure: "the device's reading",
    )
    assert report["timed_out"] is True
    assert report["likely_cause_key"] == cause_key(stages.CONNECT, str(exc), exc=exc)
    # The library's own sentence wins over the integration's.
    assert report["likely_cause"] != "the device's reading"


def test_failed_stage_and_detail_can_be_overridden():
    from blesession import SessionTrace, build_report

    report = build_report(
        operation="op",
        trace=SessionTrace(),
        exc=RuntimeError("x"),
        failed_stage="unlock",
        failed_detail="part 3",
    )
    assert report["failed_stage"] == "unlock"
    assert report["failed_detail"] == "part 3"


def test_cause_callback_receives_the_error_to_test_its_type():
    from blesession import NotificationTimeout, SessionTrace, build_report

    seen = []

    def cause(failure):
        seen.append(failure.exc)
        return "silent tag" if isinstance(failure.exc, NotificationTimeout) else None

    exc = NotificationTimeout(1, step="start")
    report = build_report(operation="op", trace=SessionTrace(), exc=exc, cause=cause)
    assert seen == [exc]
    assert report["likely_cause"] == "silent tag"


@pytest.mark.parametrize(
    "text", ["Not connected", "Peripheral AA:BB is not connected", "Disconnected"]
)
@pytest.mark.parametrize("stage", [stages.AUTH, stages.TRANSFER, stages.FINISH])
def test_a_raw_backend_not_connected_error_reads_as_a_lost_link(stage, text):
    assert cause_key(stage, text, exc=BleakError(text)) == "link_lost"


def test_not_connected_text_does_not_change_connect_or_disconnect_readings():
    assert cause_key(stages.CONNECT, "Not connected") == "connect.failed"
    assert cause_key(stages.DISCONNECT, "Not connected") == "disconnect.close_failed"


def _connect_failure(original):
    """A ConnectFailed the way ble_session raises it: `from` the original."""
    exc = ConnectFailed(str(original) or type(original).__name__)
    exc.__cause__ = original
    return exc


@pytest.mark.parametrize(
    ("original", "key"),
    [
        (BleakOutOfConnectionSlotsError("full"), "connect.no_slot"),
        (BleakNotFoundError("gone"), "connect.not_found"),
        (BleakAbortedError("aborted"), "connect.aborted"),
        (BleakConnectionError("after all retries"), "connect.failed"),
        (OSError("something else"), "connect.failed"),
    ],
)
def test_a_connect_failure_is_read_from_the_type_of_the_original_error(original, key):
    exc = _connect_failure(original)
    assert cause_key(stages.CONNECT, str(exc), exc=exc) == key
    assert generic_cause(stages.CONNECT, str(exc), {}, exc=exc, noun="tag")


def test_the_slot_text_marker_and_the_settle_reading_keep_their_precedence():
    # The text marker still works with no type to go with it.
    exc = _connect_failure(BleakError("out of connection slot"))
    assert cause_key(stages.CONNECT, str(exc), exc=exc) == "connect.no_slot"
    # A settle drop is the library's own reading, whatever else the chain holds.
    settle = ConnectFailed("Link dropped 1s into the post-connect settle", detail="settle")
    settle.__cause__ = BleakAbortedError("aborted")
    assert cause_key(stages.CONNECT, str(settle), exc=settle) == "connect.settle"
    # A slot error outranks the not-found/aborted readings.
    both = _connect_failure(BleakOutOfConnectionSlotsError("aborted: no slot"))
    assert cause_key(stages.CONNECT, str(both), exc=both) == "connect.no_slot"


def test_the_connect_kinds_read_in_the_other_stages_only_as_connect():
    exc = _connect_failure(BleakNotFoundError("gone"))
    assert cause_key(stages.SESSION, str(exc), exc=exc) == "session.refused"


@pytest.mark.parametrize("stage", [stages.AUTH, stages.TRANSFER, stages.FINISH])
def test_frames_that_arrived_and_were_all_rejected_are_not_a_silent_device(stage):
    silent = NotificationTimeout(5, step="x")
    chatty = NotificationTimeout(5, step="x", rejected=5)
    assert cause_key(stage, str(silent), exc=silent) == f"{stage}.no_answer"
    assert cause_key(stage, str(chatty), exc=chatty) == "unrecognised_answer"
    text = generic_cause(stage, str(chatty), WEAK, exc=chatty, noun="tag")
    assert text is not None and "kept answering" in text and "tag" in text
    assert placement(WEAK, noun="tag") in text


def test_a_rejected_count_changes_nothing_outside_auth_transfer_finish():
    chatty = NotificationTimeout(5, step="x", rejected=5)
    assert cause_key(stages.SESSION, str(chatty), exc=chatty) == "session.refused"
    assert cause_key("readout", str(chatty), exc=chatty) is None
    assert cause_key(stages.DISCONNECT, str(chatty), exc=chatty) == "disconnect.close_failed"


def test_a_write_timeout_keeps_reading_as_one():
    hung = WriteTimeout(5, step="x")
    assert hung.rejected == 0
    for stage in (stages.AUTH, stages.TRANSFER, stages.FINISH):
        assert cause_key(stage, str(hung), exc=hung) == "write_timeout"


def test_a_worded_timeout_with_rejected_frames_still_reads_as_unrecognised():
    own = NotificationTimeout(5, step="x", message="the tag never confirmed", rejected=2)
    assert str(own) == "the tag never confirmed"
    assert cause_key(stages.AUTH, str(own), exc=own) == "unrecognised_answer"


def test_radios_that_hear_the_device_but_cannot_connect_are_their_own_reading():
    passive = {"paths_passive": 2}
    assert cause_key(stages.UNREACHABLE, "x", facts=passive) == "unreachable.passive_only"
    assert cause_key(stages.UNREACHABLE, "x", facts={"paths": 0, **passive}) == (
        "unreachable.passive_only"
    )
    # A connectable radio hearing it, or nobody hearing it, is the old reading.
    assert cause_key(stages.UNREACHABLE, "x", facts={"paths": 1, **passive}) == "unreachable"
    assert cause_key(stages.UNREACHABLE, "x", facts={"paths": 0}) == "unreachable"
    assert cause_key(stages.UNREACHABLE, "x", facts={"paths_passive": 0}) == "unreachable"
    assert cause_key(stages.UNREACHABLE, "x") == "unreachable"
    text = generic_cause(stages.UNREACHABLE, "x", passive, noun="tag")
    assert text is not None and "tag" in text and "connectable adapter" in text
