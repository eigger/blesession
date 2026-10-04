"""The integration in docs/adopting.md runs: the code block and the test block, as written."""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest

DOC = (Path(__file__).parent.parent / "docs" / "adopting.md").read_text()


def _block(after: str) -> str:
    """The first python code block after the heading `after`."""
    start = DOC.index(after)
    match = re.search(r"```python\n(.*?)```", DOC[start:], re.S)
    assert match, after
    return match.group(1)


@pytest.fixture
def acme_tag(monkeypatch):
    """The documented tag.py, importable as `acme_tag.tag`, with Home Assistant stubbed."""

    class HomeAssistantError(Exception):
        pass

    for name in ("homeassistant", "homeassistant.core", "homeassistant.exceptions"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["homeassistant.core"].HomeAssistant = object
    sys.modules["homeassistant.exceptions"].HomeAssistantError = HomeAssistantError

    package = types.ModuleType("acme_tag")
    module = types.ModuleType("acme_tag.tag")
    package.tag = module
    monkeypatch.setitem(sys.modules, "acme_tag", package)
    monkeypatch.setitem(sys.modules, "acme_tag.tag", module)
    exec(compile(_block("## A whole integration"), "adopting.md:tag.py", "exec"), module.__dict__)
    return module


async def test_the_documented_test_passes_against_the_documented_integration(acme_tag, monkeypatch):
    namespace: dict = {}
    exec(compile(_block("## Testing, without bleak"), "adopting.md:test", "exec"), namespace)
    test = namespace["test_a_rejected_key_names_the_auth_stage_and_is_not_retried"]
    await test(monkeypatch)


async def test_the_documented_payload_example_runs(monkeypatch):
    from blesession import SessionTrace, ble_session
    from blesession import session as session_mod
    from blesession.testing import FakeClient, FakeDevice, fake_connect

    client = FakeClient()
    client.add_characteristic(
        "svc", "wr", properties=("write-without-response",), max_write_without_response_size=24
    )
    monkeypatch.setattr(session_mod, "establish_connection", fake_connect(client))
    source = _block("## A protocol with a payload")
    body = "\n".join(f"    {line}" for line in source.splitlines())
    code = (
        "async def run(device, trace, SERVICE_UUID, WRITE_UUID, MAX_CHUNK, payload, pacing_s):\n"
        + body
        + "\n"
    )
    namespace = {"ble_session": ble_session}
    exec(compile(code, "adopting.md:payload", "exec"), namespace)
    trace = SessionTrace()
    # The link allows 24 bytes a write; the header takes 4, so 20 bytes of data fit.
    await namespace["run"](FakeDevice(), trace, "svc", "wr", 100, b"x" * 50, 0.0)
    assert len(client.writes) == 3 and trace.facts["sends"] == 3
    assert all(len(data) <= 24 for _char, data, _response in client.writes)
