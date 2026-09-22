# blesession

[![PyPI](https://img.shields.io/pypi/v/blesession.svg)](https://pypi.org/project/blesession/)
[![Python versions](https://img.shields.io/pypi/pyversions/blesession.svg)](https://pypi.org/project/blesession/)
[![CI](https://github.com/eigger/blesession/actions/workflows/ci.yml/badge.svg)](https://github.com/eigger/blesession/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

One BLE session, instrumented — for Home Assistant BLE integrations built on
[bleak](https://github.com/hbldh/bleak).

Connect, subscribe to notifications, exchange frames with per-step timeouts,
disconnect — and come out the other side with **where the time went, where
it failed, which radio it went over, and one sentence on what that most
likely means**, ready to publish as sensor attributes so a failed session at
3 am can be read off the entity without debug logging.

## Status

**0.3.0.** Verified on device over a Bluetooth proxy. The library is tested
without Home Assistant (`pytest`).
Read [`docs/design.md`](docs/design.md) for what belongs here, what
deliberately does not, how a Bluetooth-proxy route works without importing
Home Assistant, and the rollout plan. See [`CHANGELOG.md`](CHANGELOG.md) for
releases.

The design is extracted from integrations that already carry this
instrumentation (and had drifted apart), and is meant to be adopted by
others that today each hand-roll the same notification wait and have no
failure attribution at all.

## Install

```bash
pip install blesession
```

Requires Python 3.13+. The core depends only on bleak / bleak-retry-connector;
`blesession.hass` imports Home Assistant lazily and is not needed outside HA.

## What it provides

| piece | one line |
|---|---|
| `ble_session()` | connect inside the block, watch the link, bounded disconnect in `finally`, never masks the real error |
| `Notifications` | queued replies from one characteristic; every wait names its `step` and ends the moment the link drops |
| `SessionTrace` | nested stage timings; the innermost stage an exception escaped from |
| stage vocabulary | `unreachable · connect · session · auth · transfer · finish · disconnect`, plus a device `detail` |
| `run_attempts()` | the lock-per-attempt / fresh-handle-per-attempt contract; policy stays yours |
| `blesession.hass.ble_device_or_raise()` | the handle, resolved fresh inside the attempt, or `Unreachable` |
| `blesession.hass.radio_facts()` | `via`, `via_type`, `rssi`, `paths`, `advertised_via` as scanner names |
| `link.py` | the *one* place that probes bleak / habluetooth internals for the radio a link took |
| `blesession.testing` | `FakeClient` / `fake_connect()` so every integration's tests fake bleak the same way |
| `build_report()` | fixed attribute key order; generic likely-cause sentences, your device sentences first |
| `SessionReports` | the last session and the last failure, so a success does not erase the evidence |
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
                          facts=radio_facts(hass, address, trace.link), noun="device")
    # {'operation': 'write', 'success': False, 'error': ..., 'failed_stage': 'auth',
    #  'failed_detail': 'start', 'likely_cause': ..., 'via': ..., 'connect_s': ..., ...}
```

## Development & testing

```bash
pip install -e ".[dev]"
pytest                 # unit tests; FakeClient fakes bleak the same way for adopters
ruff check . && ruff format --check .
mypy                   # type-check src/ (the package ships py.typed)
python -m build
```

CI runs on every push/PR (`.github/workflows/ci.yml`): ruff lint+format, mypy,
the test suite on Python 3.13/3.14 (plus a lowest-pinned-dependencies job), and
a build that asserts `py.typed` and the licence are in the wheel.

**Releasing**: add a version section to [`CHANGELOG.md`](CHANGELOG.md)
(behaviour changes go under *Changed* with before/after), bump `version` in
`pyproject.toml`, merge, then tag: pushing a `v*` tag triggers
`.github/workflows/release.yml` to build and publish to PyPI (trusted publishing).

## License

MIT
