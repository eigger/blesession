# Changelog

All notable changes to blesession. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[Semantic Versioning](https://semver.org/) — until 1.0, a minor bump may carry
a behaviour change, and every such change is listed under **Changed** with the
old and new behaviour.

Report keys (`failed_stage`, `likely_cause`, `via`, `<stage>_s`, …) and the
primary stage names are part of the contract: troubleshooting docs quote
them, so any change to them is at least a minor bump and is listed here.

## [0.2.0] — 2026-09-22

A failed session now ends when the link ends, and says so. Every change
here is about the same thing: a dead link used to be found out by waiting a
timeout out, and two failures used to disappear from the report entirely.

### Added

- `SessionDropped` is now actually raised. `ble_session()` watches the link
  for the whole session (it passes its own `disconnected_callback` to
  `establish_connection`, chaining yours if you pass one), and every
  `Notifications` wait on that client ends the moment the link goes instead
  of running its step timeout out. A notification that arrived before the
  drop is still delivered first. `dropped_event(client)` exposes the event;
  `Notifications(..., dropped=...)` takes one for a connection you own.
- `STOP_NOTIFY_TIMEOUT_S` (5s), a bound on the unsubscribe in
  `Notifications.__aexit__`. It runs *after* an attempt bound has fired, so
  nothing else bounded it: a proxy that stopped answering could hang there
  holding the lock — the one thing the attempt bound exists to prevent.
- `build_report(skipped=...)`, filled in by `report_attempt()` from
  `Attempt.skipped`.
- `blesession.testing`: `FakeClient.disconnected_callback`, fired by `drop()`
  and `disconnect()` as bleak does, and wired up by `fake_connect()`.

### Changed

- **`report_attempt()` on an attempt a `guard` declined**: was
  `success: True` (nothing had raised), now `success: False` with a
  `skipped` key carrying what the guard returned. Nothing was tried, so the
  session did not succeed.
- **A disconnect that fails after a successful session**: was swallowed and
  lost, now noted as a `disconnect_error` fact on the trace and so on the
  report. It still does not fail the session. The `trace.forgive()` call
  that used to follow it was unreachable and is gone; `forgive()` itself is
  unchanged.
- **`likely_cause` is keyed on the exception, not on its wording.** A
  `NotificationTimeout` gives the "did not answer" sentence and a
  `ConnectFailed(detail="settle")` the bond-settle sentence even when the
  integration worded the message itself (`NotificationTimeout(message=...)`);
  previously both were matched on the English error text, which is still the
  fallback when there is no exception to read.
- A new generic sentence for a link that went away mid-session, used for
  `session` / `auth` / `transfer` / `finish` in place of the per-stage
  "no response" ones when the failure is a `SessionDropped`.

## [0.1.0] — 2026-09-22

First release. Same code as 0.1.0a2, verified on device over a Bluetooth
proxy: `via` / `via_type` / `advertised_via`, `failed_stage` /
`failed_detail` / `likely_cause` as designed.

## [0.1.0a2] — 2026-09-22

### Fixed

- `build_report()` dropped an error's `detail` whenever the trace had a
  failed stage, so a `ConnectFailed(detail="settle")` inside `connect` lost
  it on the direct path while `report_attempt()` kept it. Both now use one
  rule, `SessionTrace.failure(exc)`: the trace names the stage; the error's
  detail applies when it is about that same stage or the trace has none.

## [0.1.0a1] — 2026-09-22

Pre-release for on-device testing. The API may still move as more
integrations adopt it.

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
