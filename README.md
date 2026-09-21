# blesession

One BLE session, instrumented — for Home Assistant BLE integrations built on
[bleak](https://github.com/hbldh/bleak).

Connect, subscribe to notifications, exchange frames with per-step timeouts,
disconnect — and come out the other side with **where the time went, where
it failed, which radio it went over, and one sentence on what that most
likely means**, ready to publish as sensor attributes so a failed session at
3 am can be read off the entity without debug logging.

## Status

**0.0.1 — core written, no integration on it yet.** The library is tested
without Home Assistant (`pytest`); the first adopter is `hass-ble-esl`.
Read [`docs/design.md`](docs/design.md) for what belongs here, what
deliberately does not, how a Bluetooth-proxy route works without importing
Home Assistant, and the rollout plan.

The design is extracted from two integrations that already carry this
instrumentation and have drifted apart:

- [`hass-ble-esl`](https://github.com/eigger/hass-ble-esl) — e-paper shelf
  labels (write an image, wait for the panel)
- [`hass-omron`](https://github.com/eigger/hass-omron) — blood pressure
  monitors (bonded, unlock, read record memory)

and is meant to be adopted by the rest of the family (`hass-catprinter`,
`hass-niimbot`, `hass-gicisky`, `hass-zhsunyco`, `hass-lywsd02`,
`hass-marklife`, `hass-minibig`, `hass-vson`, …), which today each hand-roll
the same notification wait and have no failure attribution at all.

## What it provides

| piece | one line |
|---|---|
| `ble_session()` | connect inside the block, bounded disconnect in `finally`, never masks the real error |
| `Notifications` | queued replies from one characteristic; every wait names its `step` |
| `SessionTrace` | nested stage timings; the innermost stage an exception escaped from |
| stage vocabulary | `unreachable · connect · session · auth · transfer · finish · disconnect`, plus a device `detail` |
| `run_attempts()` | the lock-per-attempt / fresh-handle-per-attempt contract; policy stays yours |
| `blesession.hass.radio_facts()` | `via`, `via_type`, `rssi`, `paths`, `advertised_via` as scanner names |
| `link.py` | the *one* place that probes bleak / habluetooth internals for the radio a link took |
| `blesession.testing` | `FakeClient` / `fake_connect()` so every integration's tests fake bleak the same way |
| `build_report()` | fixed attribute key order; generic likely-cause sentences, your device sentences first |
| errors | `ConnectionError` subclasses so an off device never becomes a traceback |

## What it does not provide

Retry counts, backoff, packet pacing, lock scope, bonding and pairing,
cooldowns, advertisement parsing, protocol frames. Those are the parts each
integration learned from its own device and keeps.

## Layout

```
src/blesession/         pure Python + bleak, tested without Home Assistant
src/blesession/hass.py  imports homeassistant lazily; only used inside HA
docs/design.md          the design
```

```python
from blesession import Notifications, SessionTrace, ble_session, build_report, stages

trace = SessionTrace(stage_map={"start": stages.AUTH})
try:
    async with ble_session(ble_device, trace=trace) as client:
        async with Notifications(client, NOTIFY_UUID, settle=0.5) as replies:
            with trace.timed("start"):
                await client.write_gatt_char(WRITE_UUID, START, response=False)
                await replies.next(timeout=5, step="start")
            with trace.timed("transfer"):
                ...
except ConnectionError as exc:          # every session error is one
    report = build_report(operation="write", trace=trace, exc=exc,
                          facts=radio_facts(hass, address, trace.link), noun="tag")
    # {'operation': 'write', 'success': False, 'error': ..., 'failed_stage': 'auth',
    #  'failed_detail': 'start', 'likely_cause': ..., 'via': ..., 'connect_s': ..., ...}
```

## License

MIT
