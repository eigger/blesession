# Changelog

All notable changes to blesession. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[Semantic Versioning](https://semver.org/) — until 1.0, a minor bump may carry
a behaviour change, and every such change is listed under **Changed** with the
old and new behaviour.

Report keys (`failed_stage`, `likely_cause`, `via`, `<stage>_s`, …) and the
primary stage names are part of the contract: troubleshooting docs quote
them, so any change to them is at least a minor bump and is listed here.

## [0.1.0a1] — 2026-09-22

Pre-release for on-device testing with `hass-ble-esl`. The API is what the
ESL integration shaped; `hass-omron` is next and may still move it.

### Added

- `ble_session()`: connect inside the block, bounded disconnect in `finally`,
  `settle_s`, `keep`, `close_stale`; the client class is resolved at connect
  time so Home Assistant's proxy-aware wrapper is picked up regardless of
  import order.
- `Notifications`: queued replies from one characteristic; every wait names
  its `step`, and `NotificationTimeout` carries it.
- `SessionTrace` / `traced()`: nested stage timings, the innermost stage a
  failure escaped from, `note()` facts, `stage_map` to the primary vocabulary.
- Primary stage vocabulary: `unreachable`, `connect`, `session`, `auth`,
  `transfer`, `finish`, `disconnect`.
- `run_attempts()`: lock held per attempt, attempt bound with `timed_out`,
  `guard` under the lock, `retry_if`, `on_attempt`, `state` carried between
  attempts.
- `build_report()` / `report_attempt()`: fixed attribute key order, device
  cause callback first, generic sentences as fallback.
- Errors: `BleSessionError(ConnectionError)` with `Unreachable`,
  `ConnectFailed`, `SessionDropped`, `NotificationTimeout`, `AttemptTimedOut`.
- `link.py`: the one place that probes bleak / habluetooth internals for the
  radio a link took; `is_proxy()`.
- `blesession.hass.radio_facts()`: `via`, `via_type`, `rssi`, `paths`,
  `advertised_via` as scanner names (imports Home Assistant lazily).
- `blesession.testing`: `FakeClient`, `FakeDevice`, `fake_connect()`.
- `SessionTrace.record()` for a stage measured elsewhere; `NotificationTimeout(message=)`
  for protocols that word their own probe timeouts.
