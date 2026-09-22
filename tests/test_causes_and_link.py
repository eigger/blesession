from blesession import (
    CAUSES,
    AttemptTimedOut,
    ConnectFailed,
    LinkInfo,
    NotificationTimeout,
    SessionDropped,
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
        )
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
