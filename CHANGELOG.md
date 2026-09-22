# Changelog

All notable changes to blesession. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[Semantic Versioning](https://semver.org/) — until 1.0, a minor bump may carry
a behaviour change, and every such change is listed under **Changed** with the
old and new behaviour.

Report keys (`failed_stage`, `likely_cause`, `via`, `<stage>_s`, …), the
primary stage names and the `likely_cause_key` names are part of the
contract: troubleshooting docs quote them and integrations translate them,
so any change to them is at least a minor bump and is listed here.

## [Unreleased]

### Added

- `ble_session(client=...)`: run a session on a link a previous `keep=True`
  session left up. It is used only if it is still up, so the caller never
  has to check a stale handle; otherwise a fresh link is opened and handed
  back as usual. A reused link times no `connect` stage and the trace notes
  `reused=True`, so a missing `connect_s` reads as "there was none" rather
  than as a measurement that went missing. It keeps the drop watch it
  already had, so a `Notifications` wait on it still ends the moment the
  link goes. `settle_s`, `close_stale` and the connect kwargs describe
  opening a link and are not applied to one already up — `close_stale` in
  particular would have killed the very link being reused.
- `still_up(client)`, the check behind it.
- `likely_cause_key` on the report: the stable name of the generic sentence
  (`connect.no_slot`, `auth.no_answer`, `link_lost`, …), so an integration
  can publish a Home Assistant translation instead of the English text.
  Only a sentence this library wrote carries one; a sentence from the
  integration's own `cause` callback does not, because it already owns the
  wording.
- `blesession.causes.CAUSES`, the key -> sentence table, and `cause_key()`,
  which picks the key. `generic_cause()` is now that pair rendered, with
  its signature and every sentence unchanged. A test holds the two halves
  of the table to each other, so a key can never be returned without a
  sentence to go with it.

## [0.3.0] — 2026-09-22

What every integration adopting the library would otherwise write the same
way. Additive only: no existing key, stage or behaviour changes.

### Added

- `blesession.hass.ble_device_or_raise(hass, address)`: the handle to
  connect with, or `Unreachable`. Resolving it inside the attempt (the
  route a queued handle carries can be stale after the wait for the lock)
  and raising rather than returning None (so an asleep device reaches the
  report with a stage and a likely cause, not as an `if device is None`
  branch worded differently in each integration) are the two things this
  stops everyone getting subtly differently.
- `Unreachable(address, connectable=False)`: wording only, for a handle
  looked up with `connectable=False`. It was not refused for being
  unconnectable, so the message no longer says no *connectable* radio saw
  it. The default message is unchanged.
- `SessionReports`: the two slots design §8 defines — `last` (any session)
  and `last_failure` (kept until the next failure, so a success does not
  erase the evidence). `record(report)` files a report in both as it
  belongs and hands it back; `clear()` forgets both. A session a `guard`
  declined becomes `last` but not `last_failure`: nothing was tried, so it
  must not overwrite the last real failure — the slot keys on `error`,
  not on `success`. Record from `run_attempts(on_attempt=...)`, not from
  the attempt it returns: it hands back the last attempt only, so filing
  that one alone loses a first attempt that failed and a second that
  worked, which is the failure the second slot exists to keep.

### Changed

- **`run_attempts(on_attempt=...)` is now called for an attempt a `guard`
  declined**, as it already was for a failed or successful one. It used to
  return from inside the lock before reaching it, so the only way to see a
  declined attempt was to inspect the returned one — which left the
  recommended `on_attempt` recording unable to publish it at all, and
  `SessionReports`' rule for a declined session (`last`, not
  `last_failure`) unreachable through that path. It is still called
  outside the lock, and a declined attempt is still not retried.

### Testing

- `blesession.hass` now has tests. It is the file most likely to break on a
  habluetooth release and was the only one with no coverage, because
  `homeassistant` is not a dependency; every function there imports it
  inside the call, so a stub module in `sys.modules` exercises the lot
  (`tests/test_hass.py`). Coverage of `hass.py` 0% → 100%, overall 90% → 96%.

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
  and `disconnect()` as bleak does — once per link — and wired up by
  `fake_connect()`.

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
- **`likely_cause` reads the exception as well as its wording.** A
  `NotificationTimeout` gives the "did not answer" sentence and a
  `ConnectFailed(detail="settle")` the bond-settle sentence even when the
  integration worded the message itself (`NotificationTimeout(message=...)`),
  which the English text markers alone could not do. Those markers stay
  beside the type checks, so a failure that says as much without carrying
  the type keeps the sentence it had in 0.1.0. This is purely additive:
  no failure that had a generic sentence loses it.
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
