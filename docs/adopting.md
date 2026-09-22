# Adopting blesession in an integration

What the library gives you, what stays yours, and one whole integration
using it. [`design.md`](design.md) is the *why*; this is the *how*.

## The split, in one table

| | |
|---|---|
| **blesession provides** | the session skeleton, the notification queue, stage timings, the failure vocabulary, the attempt/lock contract, the radio probe, the report and its key order, the test fake |
| **you provide** | the protocol frames, how many retries and how long a bound, the lock and its scope, pacing and cooldowns, bonding, and the sentences only your device can say |

The library supplies **mechanism and vocabulary**; you supply **policy and
device knowledge**. It never reads your config and never decides whether to
retry.

## Install

```json
{
  "requirements": ["blesession==0.2.0"]
}
```

Pin exactly, in `manifest.json`. Report keys and stage names are a contract
that troubleshooting docs quote, and until 1.0 a minor bump may change one
(each is listed in [`CHANGELOG.md`](../CHANGELOG.md)).

`blesession` depends only on `bleak` and `bleak-retry-connector`, both of
which Home Assistant already ships. `blesession.hass` imports
`homeassistant` lazily and is the only module that touches it.

## A whole integration

One device: unlock with a key, read a payload back, disconnect. Everything
below is the BLE half of a real integration — the config flow, the
coordinator and the entities are unchanged by adopting the library.

```python
"""acme_tag/tag.py"""

import asyncio

from blesession import (
    BleSessionError,
    Notifications,
    SessionReports,
    ble_session,
    report_attempt,
    run_attempts,
    stages,
)
from blesession.hass import ble_device_or_raise, radio_facts
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
KEY = b"\xde\xad\xbe\xef"


def _complete(frame: bytes) -> bool:
    """Your protocol: True for the frame that ends the readout."""
    return frame.startswith(b"\xff")


# Your stage names -> the shared vocabulary, so `failed_stage` means the
# same thing here as in every other integration. A name that is already a
# primary stage needs no entry.
STAGE_MAP = {"unlock": stages.AUTH, "readout": stages.TRANSFER}


class UnlockRejected(BleSessionError):
    """The tag refused the key. A ConnectionError, so it is an expected
    failure rather than a traceback."""

    stage = stages.AUTH


def likely_cause(stage, detail, error, facts):
    """What only this device can say. None -> the generic sentence."""
    if "rejected" in error:
        return "The tag rejected the key: it is paired to another hub, or is a different model."
    return None


class AcmeTag:
    def __init__(self, hass: HomeAssistant, address: str, retries: int = 3) -> None:
        self.hass = hass
        self.address = address
        self.retries = retries
        self.lock = asyncio.Lock()      # yours: scope it per device, per entry, or per domain
        self.reports = SessionReports()  # what the sensors publish

    async def read(self) -> bytes:
        """One poll: up to `retries` attempts, one lock hold each."""

        def file_it(attempt):
            # Every attempt, not just the last one run_attempts() returns:
            # a first attempt that failed and a second that worked is
            # exactly what `last_failure` is for.
            self.reports.record(
                report_attempt(
                    attempt,
                    operation="poll",
                    facts=radio_facts(self.hass, self.address, attempt.trace.link),
                    cause=likely_cause,
                    noun="tag",
                    attempts=self.retries,
                )
            )

        last = await run_attempts(
            self._read_once,
            lock=self.lock,
            max_attempts=self.retries,
            attempt_timeout_s=60,     # yours: a GATT write has no timeout of its own
            pause_s=1.0,
            stage_map=STAGE_MAP,
            on_attempt=file_it,
            name=self.address,        # only for the debug log
        )
        if last.error is not None:
            # One line, because every session error is a ConnectionError.
            raise HomeAssistantError(str(last.error)) from last.error
        return last.result

    async def _read_once(self, attempt) -> bytes:
        # Fresh handle inside the attempt: the route the queued one carried
        # may be gone, or a different proxy, after the wait for the lock.
        device = ble_device_or_raise(self.hass, self.address)
        trace = attempt.trace

        async with ble_session(device, trace=trace, name="acme tag") as client:
            async with Notifications(client, NOTIFY_UUID, settle=0.5) as replies:
                with trace.timed("unlock"):
                    await client.write_gatt_char(WRITE_UUID, b"\x01" + KEY, response=False)
                    reply = await replies.next(timeout=5, step="unlock")
                    if reply[0] != 0x00:
                        raise UnlockRejected(f"the tag rejected the key (0x{reply[0]:02x})")

                with trace.timed("readout"):
                    payload = await replies.wait_for(_complete, timeout=30, step="readout")

                trace.note(payload_bytes=len(payload))
                return payload
```

And the sensors, which is where all of it becomes visible:

```python
class AcmeTagSensor(SensorEntity):
    @property
    def extra_state_attributes(self):
        return self.tag.reports.last          # the most recent session


class AcmeTagLastFailureSensor(SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def extra_state_attributes(self):
        return self.tag.reports.last_failure   # kept until the next failure
```

## What each piece bought you

**`ble_session()`** connects *inside* the block, so a connect failure
raises out of it like any other session error and counts as an attempt. It
disconnects in `finally` under its own bound, and a disconnect that fails
never masks your error. It watches the link the whole time.

**`Notifications`** is a queue, not an event: a reply that lands between
two waits is kept, not lost. Every wait names its `step`, so a timeout
already says which part of your protocol went unanswered. And a wait ends
the moment the link drops — `SessionDropped`, not the full 30 seconds with
the lock held.

**`trace.timed("unlock")`** times the stage and, if something escapes it,
records that this is where the session died. The innermost stage wins and
the first failure wins, so a close that also fails does not overwrite the
real cause.

**`run_attempts()`** holds the lock for **one attempt**, releasing it
between tries so other devices go first, and bounds each attempt including
the connect. It never raises for a failed attempt: you get the `Attempt`
back and decide.

**`report_attempt()`** assembles the attributes in a fixed key order, so
the same key means the same thing on every integration adopting this.

## The report

Everything a session can put on a sensor, in the order it appears:

| key | when | meaning |
|---|---|---|
| `operation` | always | what you called it: `poll`, `write`, `pair`, … |
| `success` | always | `False` for a failure *and* for a declined attempt |
| `skipped` | a `guard` declined | what your guard returned |
| `error` | failure | the exception's message |
| `failed_stage` | failure | the primary stage: `unreachable` · `connect` · `session` · `auth` · `transfer` · `finish` · `disconnect` |
| `failed_detail` | failure | *your* stage name, when it differs (`unlock`) |
| `likely_cause` | usually | one sentence: yours first, then the generic one |
| `likely_cause_key` | generic sentences | the stable name (`connect.no_slot`), for a translation |
| `timed_out` | attempt bound fired | the transport died, not the device |
| `attempt` / `attempts` | via `report_attempt` | which try, out of how many |
| `via` / `via_type` | radio known | the radio the link **took**, `proxy` or `adapter` |
| `rssi` | radio known | as that radio saw the device |
| `paths` | always with facts | connectable radios that see it; `1` means no failover |
| `advertised_via` | only when it differs | the loudest scanner, when the link took another |
| `connect_s`, `session_s`, `disconnect_s` | timed | seconds, in the order stages finished |
| `unlock_s`, `readout_s`, … | timed | your own stages, same rule |
| `reused` | `client=` reuse | the link was already up; there was no connect |
| `disconnect_error` | close failed | the work was done; only the close failed |
| `stale_close_error` | rare | closing a handed-in link that had gone stale failed |
| your `note()` facts | always | `payload_bytes=…`, pacing counters, anything scalar |

A failed session at 3 am then reads off the entity without debug logging:

```yaml
operation: poll
success: false
error: No response from device within 30s after readout
failed_stage: transfer
failed_detail: readout
likely_cause: The tag stopped answering mid-transfer: link dropped or reset.
  The signal is weak (-91 dBm via garage-proxy) and no other radio reaches the tag
  — move the tag or add a proxy.
likely_cause_key: transfer.no_answer
attempt: 3
attempts: 3
via: garage-proxy
via_type: proxy
rssi: -91
paths: 1
connect_s: 2.41
unlock_s: 0.32
readout_s: 30.01
session_s: 30.35
disconnect_s: 0.08
```

### Translating the sentence

`likely_cause` is English. `likely_cause_key` names it, so `strings.json`
can carry your own:

```python
key = report.get("likely_cause_key")
sentence = self.translations[key] if key else report.get("likely_cause")
```

Keys are listed in `blesession.causes.CAUSES`. The key names the sentence,
not the whole string: several end in the weak-signal placement advice,
which you rebuild from `rssi`, `via` and `paths` — already in the report —
rather than translating that fragment. A sentence from *your* `cause`
callback carries no key, because you already own its wording.

## Keeping the link across sessions

For a device with a `keep_connection` option:

```python
async with ble_session(device, client=self._client, keep=self._keep) as client:
    self._client = client if self._keep else None
    ...
```

A handle that went stale is ignored and a fresh link opened, so you never
check — and if it is somehow still open, the session closes it rather than
leaving it holding a proxy slot. A reused link times no `connect` stage
and the trace notes `reused=True`.

## Testing, without bleak and without Home Assistant

`blesession.testing` is the one fake, so your tests and the library's
behave the same way:

```python
from blesession import session as session_mod
from blesession.testing import FakeClient, FakeDevice, fake_connect


async def test_a_rejected_key_names_the_auth_stage(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(session_mod, "establish_connection", fake_connect(client))
    client.reply(b"\x05")                    # the tag refuses

    tag = AcmeTag(hass, "AA:BB:CC:DD:EE:FF", retries=1)
    with pytest.raises(HomeAssistantError):
        await tag.read()

    report = tag.reports.last_failure
    assert report["failed_stage"] == "auth"
    assert report["failed_detail"] == "unlock"
    assert "paired to another hub" in report["likely_cause"]
```

`client.reply(data)` delivers a notification (or queues it for the next
subscriber), `client.drop()` takes the link down and fires the disconnect
callback as bleak does, `client.fail_write` / `fail_start_notify` /
`fail_disconnect` script errors, and `disconnect_delay_s` hangs a close.
You can retire your own `MagicMock` client.

For `blesession.hass`, plant a stub module in `sys.modules` — every
function there imports `homeassistant` inside the call. `tests/test_hass.py`
in this repo is a working example.

## A checklist

1. Map your stage names to the primary vocabulary (`STAGE_MAP`); often it
   is identity.
2. Replace your notification wait with `Notifications`, naming a `step` on
   every wait.
3. Wrap the session in `ble_session()` and time your stages with
   `trace.timed()`.
4. Move the retry loop to `run_attempts()`, keeping *your* counts, bounds
   and pause. Resolve the handle with `ble_device_or_raise()` inside the
   attempt.
5. Derive your device errors from `BleSessionError` so one `except
   ConnectionError` maps them all.
6. Record every attempt into `SessionReports` from `on_attempt`, and
   publish `last` and `last_failure`.
7. Write the device sentences you have — an empty `cause` callback is fine
   to start with.

Around 50–80 lines beyond the protocol itself.
