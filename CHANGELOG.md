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

## [0.9.0] — 2026-10-05

`SessionReports` takes over the failure count, the failure time, the
per-operation slot and the listeners every integration wrote by hand, and
`run_attempts()` says whether an attempt will be retried.

### Added

- `Attempt.retrying` (default `False`): set by `run_attempts()` before
  `on_attempt`; true when the attempt failed, was not declined, is not the last
  and `retry_if` returned true. The report carries it as `retrying: True`
  (after `attempts` in `REPORT_KEYS`; `build_report(retrying=)`).
- `classify(report)` and `Kind` (`ok`, `failure`, `retried`, `skipped`).
- `SessionReports`: `last_retry`, `failures`, `last_failure_at`,
  `by_operation_last` / `of(operation)`, `last_kind`, `add_listener()`.
- `fallback_report(operation, *, trace=None, exc=None, attempt=None)`: a
  minimal report that cannot fail.

### Changed

- **`SessionReports.last_failure` now means a failure no further attempt
  follows.** Before, a report recorded from `on_attempt` for an attempt that
  failed and was retried became `last_failure`; it now goes to `last_retry`.
  Callers that record only single-attempt reports (no `retrying` key) see no
  difference. `tests/test_attempts.py` and the docs are updated.
- `run_attempts()` calls `retry_if` **before** `on_attempt` (so `retrying` can
  be set); an attempt whose `retry_if` raised is no longer handed to
  `on_attempt`.
- `build_report()` no longer raises when a `cause` callback raises, and
  `blesession.hass.radio_facts()` returns `{}` when its lookup raises; both
  are logged at debug and the report is built with what is known.
- `SessionReports.clear()` also resets the counters and `last_failure_at`.

## [0.8.0] — 2026-10-04

The unsubscribe `Notifications` already did, for an integration's own
subscription, and `response=None` on the write path.

### Added

- `stop_notify_best_effort(client, characteristic, *, timeout=None)`:
  the unsubscribe `Notifications` already did, for a subscription the
  integration made itself. Skipped on a link that is down, bounded, never raises.

### Changed

- `guarded_write()`, `write_chunks()` and `Notifications.request()` accept
  `response=None`, which passes `None` through so the backend picks the write
  type, as bleak's `write_gatt_char` does when called without `response`.
  Before, the annotation was `bool` (the call already worked at runtime); the
  default is still `False`. `FakeClient.write_gatt_char` takes it too.
- The log line for a failed unsubscribe now comes from the `blesession.subscribe`
  logger (it was `blesession.notifications`), and the module attribute
  `blesession.notifications.STOP_NOTIFY_TIMEOUT_S` is gone: the bound is
  `blesession.subscribe.STOP_NOTIFY_TIMEOUT_S`, read when the unsubscribe runs.

## [0.7.0] — 2026-10-04

The shape an integration depends on, made explicit: one failure object for
the `cause` callback, one write primitive, errors that say whether a retry can
help, and a contract document that the build keeps in step with the code.

### Added

- `docs/contract.md`: every public name with its tier, what each piece
  guarantees, every error, report key and cause key, the callback shapes, what
  the integration provides, and the versioning and deprecation rule.
  `tests/test_docs_contract.py` fails when the code gains a public name, stage,
  error, report key, cause key, test fake, or `Attempt` / `SessionTrace` member
  that the page does not mention. `tests/test_adopting_example.py` runs the
  integration and the test that `docs/adopting.md` shows, as written.
- `Failure(stage, detail, error, exc, facts)` and the `Cause` type: what a
  `cause` callback receives. `REPORT_KEYS`: the report's own keys, in order.
- `guarded_write()`: one write with the drop check and a bound, shared by
  `Notifications.request()` and `write_chunks()`. `WRITE_TIMEOUT_S` (10 s).
- `WriteTimeout`, a `NotificationTimeout` raised when a write does not return.
  Its generic sentence has its own key, `write_timeout`.
- `BleSessionError.retryable` and `DeviceError(message, code=, retryable=)`:
  an error says whether another attempt can change the outcome, and an
  integration has a base type for a fault its device reported.
- `FakeClient.add_characteristic()` and `.services`, `.on_write` and
  `.write_delay_s`, so `characteristic_or_raise()`, a command/answer exchange
  and a hung write are testable without a hand-made mock.

### Changed

- The `cause` callback takes one `Failure`. Old: `(stage, detail, error,
  facts)` (0.5) or `(stage, detail, error, facts, exc)` (0.6). New:
  `cause(failure)`. The two older shapes still work and raise a
  `DeprecationWarning`; they are removed in 1.0.
- `write_chunks()` bounds each write (`write_timeout=`, default
  `WRITE_TIMEOUT_S`; `None` for the old unbounded behaviour). Old: a chunk write
  that hung ran to the attempt bound. New: it raises `WriteTimeout` naming the
  step after 10 s.
- A write that times out in `Notifications.request()` raises `WriteTimeout`
  (a `NotificationTimeout`, same `step` and `timeout`), not a plain
  `NotificationTimeout`.
- `default_retry_if` also stops at an error with `retryable = False`.
  `GattMismatch` is one: a device that lacks the profile was retried to
  `max_attempts` before.
- `likely_cause_key` has a new value, `write_timeout`, for a `WriteTimeout`
  (before: `transfer.no_answer` and its siblings, by stage).
- The `error` text of a write that did not return changes. Old (from
  `Notifications.request()`): "No response from device within Ns after STEP".
  New: "A write did not complete within Ns during STEP". Code that matched the
  old text to recognise a hung write should test `isinstance(exc, WriteTimeout)`.
  `WriteTimeout` is still a `NotificationTimeout`, so a `cause` callback that
  tests that type matches it first; test for `WriteTimeout` before it.
- A `TimeoutError` raised by the backend's own write is not renamed: only the
  library's bound becomes `WriteTimeout`, and a write that hung because the
  link dropped is `SessionDropped`.
- `GattMismatch` is final (`retryable = False`) for a missing service,
  characteristic or property. A too-small write size and a failed lookup stay
  retryable, because both can be transient (the MTU is not negotiated yet;
  services are not discovered yet).
- `Cause` is typed `Callable[[Failure], ...]`: a type checker flags a callback
  with the older shape before the runtime does. At runtime it still works, with
  a `DeprecationWarning` and one logged warning per callback (Python hides the
  former outside tests).
- `BleSessionError.__init__` accepts `retryable=`.

## [0.6.0] — 2026-10-04

### Added

- `characteristic_or_raise(client, service_uuid, char_uuid, properties=,
  min_write_size=, label=)`: the service / characteristic lookup every
  protocol opens with, including the required properties and the minimum
  write-without-response size. It raises the new `GattMismatch`. A too-small
  write size can also be a link that has not negotiated its MTU yet.
- `GattMismatch`: a `BleSessionError` in the `session` stage for a device
  that does not expose what the protocol needs. Its generic sentence has its
  own key, `session.gatt_mismatch` (a new `likely_cause_key`).

### Changed

- The `cause` callback of `build_report()` / `report_attempt()` now receives
  the error as a fifth argument: `(stage, detail, error, facts, exc)`, so a
  device sentence can test the error's type instead of matching its text.
  Old: four arguments. New: five; a four-argument callback raises
  `TypeError`. Add the parameter (`exc`) to yours.

## [0.5.1] — 2026-10-04

### Added

- `Notifications.request(write_timeout=)`: a separate bound for the write,
  for a short reply window that must not cut a slow write off. It defaults
  to `timeout`, so existing calls behave as before.

## [0.5.0] — 2026-10-03

### Added

- `Notifications.request()`: clear stale replies, write, and return the reply
  (or the first one `accept` takes), with an optional pause between the
  write and the wait. It replaces the clear/write/`next` triple that
  integrations repeat for every command.
- `Notifications.next_burst()`: the next notification plus the ones that
  trail in behind it (until `gap_s` of quiet), joined, for devices that
  spread one reply over several notifications.
- `start_notify_with_recovery()` and `Notifications(recover=True)`: a
  subscription that fails because the previous connection's subscription is
  still held (BlueZ "notify acquired", ESPHome proxy "already enabled") is
  released and retried up to `NOTIFY_ATTEMPTS` times. Off by default, so
  `Notifications` behaves as before.
- `write_chunks()`: write a payload as consecutive chunks of at most `size`
  bytes, with an optional `gap_s` between writes, a `wrap(offset, chunk)` for
  protocols that frame each chunk and an `on_chunk(sent)` progress hook.
  Returns the number of writes. A link that is down ends it with
  `SessionDropped` naming the step before the next write.

### Changed

- PyPI publishing now starts when a GitHub Release is published, using a
  version name for the release title and tag; pushing a tag alone no longer
  starts deployment.

## [0.4.1] — 2026-10-03

### Fixed

- A `keep=True` session now closes a connection if connect or post-connect
  settle fails before the client is handed to the caller.
- A notification subscription is cleaned up if the settle wait in
  `Notifications.__aenter__()` is cancelled or fails.
- `Notifications.wait_for()` now preserves a `TimeoutError` raised by its
  `accept` callback instead of reporting it as a missing response.

## [0.4.0] — 2026-10-03

Two timing and delivery bugs, and a close that hung no longer reads as a dead stack.

### Added

- `likely_cause_key` `disconnect.timed_out` (see Fixed).

### Fixed

- Stage timings no longer round on every addition: a stage recorded many
  times (a per-frame `@traced` write) lost sub-millisecond runs entirely or
  inflated them. Seconds accumulate unrounded and are rounded to 3 places
  only when read (`timings`, `as_dict()`, the report).
- A notification taken off the queue just as the step's timeout fired is
  kept for the next wait instead of being dropped.
- An attempt bound that fires while `ble_session` is closing the link is now
  reported as the new `likely_cause_key` `disconnect.timed_out`
  (`failed_stage=disconnect`) rather than `attempt_timed_out`: the work was
  already done, and "the BLE stack stopped answering mid-session… restart the
  adapter" was the wrong advice. The block's result is still discarded, and
  the sentence says so. `timed_out` stays True. A new key: a minor bump at
  release. The docs said the close ran outside the attempt bound; it runs
  inside it (with its own bound), and now say so.
- A `stop_notify` failure or timeout in `Notifications` leaves a debug log line
  instead of vanishing.

## [0.3.0] — 2026-10-02

Two diagnostics that misled when a connect failed or its route was unknown.

### Fixed

- A failed connect's message no longer repeats the address
  (`AA:BB - AA:BB: Failed to connect…` is now `AA:BB: Failed to connect…`);
  bleak_retry_connector words it `<name> - <address>` and the name defaults to
  the address.

### Added

- `radio_facts()` reports `via_unconfirmed: True` when `via` could not be
  resolved from the link — no link, or a route habluetooth does not know. `via`
  then is the radio that heard the device best, not necessarily one the
  connect went through; before, it read as the path the link took. A new report
  key (also appended to `FACT_KEYS`), hence the minor bump; nothing else about
  `via` changes.

## [0.2.0] — 2026-09-22

A failed session now ends when the link ends and says so, a link can
outlive one session, and what every integration was going to write for
itself lives here instead.

### Added

- `SessionDropped` is now actually raised. `ble_session()` watches the link
  for the whole session (it passes its own `disconnected_callback` to
  `establish_connection`, chaining yours if you pass one), and every
  `Notifications` wait on that client ends the moment the link goes instead
  of running its step timeout out with the lock held. A notification that
  arrived before the drop is still delivered first. `dropped_event(client)`
  exposes the event; `Notifications(..., dropped=...)` takes one for a
  connection you own.
- `STOP_NOTIFY_TIMEOUT_S` (5s), a bound on the unsubscribe in
  `Notifications.__aexit__`. It runs *after* an attempt bound has fired, so
  nothing else bounded it: a proxy that stopped answering could hang there
  holding the lock — the one thing the attempt bound exists to prevent.
- `ble_session(client=...)`: run a session on a link a previous `keep=True`
  session left up. It is used only if it is still up, so the caller never
  has to check a stale handle; otherwise a fresh link is opened and handed
  back as usual. A reused link times no `connect` stage and the trace notes
  `reused=True`, so a missing `connect_s` reads as "there was none" rather
  than as a measurement that went missing. It keeps the drop watch it
  already had. `settle_s`, `close_stale` and the connect kwargs describe
  opening a link and are not applied to one already up — `close_stale` in
  particular would have closed the very link being reused.
- `still_up(client)`, the check behind it: the drop event as well as
  `is_connected`, because the callback can arrive first. A handle it turns
  down that is somehow still open is closed under the disconnect bound
  before the fresh link is opened, and a close that fails is noted as
  `stale_close_error`. The caller is about to overwrite its reference with
  the client handed back, so an abandoned link would hold a proxy's
  connection slot until something else noticed.
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
- `SessionReports`: the two slots `docs/design.md` §8 defines — `last` (any
  session) and `last_failure` (kept until the next failure, so a success
  does not erase the evidence). `record(report)` files a report in both as
  it belongs and hands it back; `clear()` forgets both. A session a `guard`
  declined becomes `last` but not `last_failure`: nothing was tried, so it
  must not overwrite the last real failure — the slot keys on `error`, not
  on `success`. Record from `run_attempts(on_attempt=...)`, not from the
  attempt it returns: it hands back the last attempt only, so filing that
  one alone loses a first attempt that failed and a second that worked.
- `build_report(skipped=...)`, filled in by `report_attempt()` from
  `Attempt.skipped`.
- `likely_cause_key` on the report: the stable name of the generic sentence
  (`connect.no_slot`, `auth.no_answer`, `link_lost`, …), so an integration
  can publish a Home Assistant translation instead of the English text.
  Only a sentence this library wrote carries one; a sentence from the
  integration's own `cause` callback does not, because it already owns the
  wording. The key names the sentence, not the whole string: several
  sentences end in the weak-signal placement advice, which a translation
  rebuilds from `rssi`, `via` and `paths` — already in the report beside
  the key — rather than translating that fragment.
- `blesession.causes.CAUSES`, the key -> sentence table, and `cause_key()`,
  which picks the key. `generic_cause()` is now that pair rendered, with
  its signature and every sentence unchanged. A test holds the two halves
  of the table to each other, so a key can never be returned without a
  sentence to go with it.
- `blesession.testing`: `FakeClient.disconnected_callback`, fired by
  `drop()` and `disconnect()` as bleak does — once per link — and wired up
  by `fake_connect()`.
- [`docs/adopting.md`](docs/adopting.md): a whole integration, end to end,
  with what the library provides and what stays yours.

### Changed

- **`report_attempt()` on an attempt a `guard` declined**: was
  `success: True` (nothing had raised), now `success: False` with a
  `skipped` key carrying what the guard returned. Nothing was tried, so the
  session did not succeed.
- **`run_attempts(on_attempt=...)` is now called for an attempt a `guard`
  declined**, as it already was for a failed or successful one. It used to
  return from inside the lock before reaching it, so a declined attempt
  could not be published at all through the recording `SessionReports`
  recommends. It is still called outside the lock, and a declined attempt
  is still not retried.
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
  the type keeps the sentence it had in 0.1.0. Purely additive: no failure
  that had a generic sentence loses it.
- A new generic sentence (`link_lost`) for a link that went away
  mid-session, used for `session` / `auth` / `transfer` / `finish` in place
  of the per-stage "no response" ones when the failure is a
  `SessionDropped`. The `disconnect` stage keeps its own: a drop the close
  reported is the close failing, not the session.
- The version is now single-sourced from `blesession.__version__`;
  `pyproject.toml` reads it. Releases bump one line.

### Testing

- `blesession.hass` now has tests. It is the file most likely to break on a
  habluetooth release and was the only one with no coverage, because
  `homeassistant` is not a dependency; every function there imports it
  inside the call, so a stub module in `sys.modules` exercises the lot
  (`tests/test_hass.py`). 68 tests, coverage 96%.

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
