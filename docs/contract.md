# The contract

What an integration may rely on, and what it must provide in return. Read
this and [`adopting.md`](adopting.md) and you can integrate a device without
reading the source; [`design.md`](design.md) is the *why*.

Everything here is checked by `tests/test_docs_contract.py`: a name in
`blesession.__all__`, a stage, an error class, a report key or a cause key
that this page does not mention fails the build. A contract that drifts from
the code is worse than none.

## 1. Tiers and versioning

| tier | what | a change means |
|---|---|---|
| **Contract** | stage names, report keys, cause keys, error classes and the attributes listed in §5, the callback shapes in §7, the guarantees in §3 | a CHANGELOG entry under *Changed* with the old and new behaviour; before 1.0 a minor bump, after it a major one |
| **Helper** | the functions and classes that build on the contract (`ble_session`, `Notifications`, `write_chunks`, …) | new keyword arguments with defaults are additions; a removed or renamed one follows the deprecation rule below |
| **Internal-facing** | probes of bleak / habluetooth internals (`link.py`, `connected_via`, `probe_link`, the backend-wording matches in `subscribe.py`) | may change in any release when a backend does; the *results* (`via`, `rssi`, …) are contract, how they are found is not |

**Deprecation.** A shape that was in a release is removed only after a minor
release that still accepts it and raises `DeprecationWarning`. The older
`cause` shapes (§7) are the current example: accepted in 0.7, removed in 1.0.
Pin exactly in `manifest.json` (`blesession==0.7.0`); a bump is then a
reviewed change, and the CHANGELOG says what it touches.

## 2. Public names

Every name in `blesession.__all__`, with its tier. *Contract* names are
listed because their shape is promised; *Helper* names because their
behaviour is.

| name | tier | what it is |
|---|---|---|
| `ble_session` | Helper | connect inside the block, watch the link, bounded close (§3.1) |
| `Notifications` | Helper | queued replies from one characteristic (§3.2) |
| `write_chunks` | Helper | a payload as consecutive writes (§3.3) |
| `guarded_write` | Helper | one write with the drop check and the bound; what `request()` and `write_chunks()` use (§3.3) |
| `WRITE_TIMEOUT_S` | Helper | the default bound on one write (10 s) |
| `characteristic_or_raise` | Helper | the service/characteristic lookup, with required properties and write size (§3.4) |
| `start_notify_with_recovery` | Helper | subscribe, releasing a subscription the last link left behind |
| `stop_notify_best_effort` | Helper | unsubscribe in cleanup: skipped on a dead link, bounded, never raises |
| `NOTIFY_ATTEMPTS`, `STOP_NOTIFY_TIMEOUT_S`, `DISCONNECT_TIMEOUT_S` | Helper | the bounds those helpers use |
| `run_attempts` | Helper | the lock-per-attempt loop (§3.5) |
| `Attempt` | Contract | one try: `number`, `trace`, `state`, `result`, `error`, `skipped`, `retrying`, `timed_out`, `ok`, `failed_stage`, `failed_detail` |
| `default_retry_if` | Helper | retry unless the attempt timed out or the error is final (§5) |
| `SessionTrace` | Contract | nested stage timings and facts (§4) |
| `traced` | Helper | time a method as one stage of `self.trace` |
| `stages` | Contract | the primary stage names (§4) |
| `build_report`, `report_attempt`, `fallback_report` | Contract | the attribute dict (§6); a minimal one that cannot fail |
| `classify`, `Kind` | Contract | how a report counts: `ok`, `failure`, `retried`, `skipped` (§6.1) |
| `SessionReports` | Helper | the `last` / `last_failure` / `last_retry` slots, the failure count and time, the latest report per operation, listeners (§6.1) |
| `REPORT_KEYS`, `FACT_KEYS` | Contract | the report's own keys, and the radio keys, in report order (§6) |
| `Failure`, `Cause` | Contract | what a `cause` callback receives and returns (§7) |
| `CAUSES`, `cause_key`, `generic_cause`, `placement` | Contract | the generic sentences, their keys, the weak-signal advice (§8) |
| `BleSessionError`, `Unreachable`, `ConnectFailed`, `GattMismatch`, `SessionDropped`, `NotificationTimeout`, `WriteTimeout`, `AttemptTimedOut`, `DeviceError` | Contract | the errors (§5) |
| `error_text` | Helper | the message of an exception, or its type name when it has none |
| `LinkInfo`, `connected_via`, `probe_link`, `is_proxy` | Internal-facing | the radio the link took; `radio_facts()` turns it into names |
| `dropped_event`, `still_up` | Helper | the drop event `ble_session()` registered for a client; whether a kept link can carry another session |

`blesession.hass` (`ble_device_or_raise`, `radio_facts`) and
`blesession.testing` (`FakeClient`, `FakeDevice`, `fake_connect`, and the
`FakeCharacteristic`, `FakeService` and `FakeServices` a `FakeClient` exposes)
are Helper tier. Importing `blesession` never imports Home Assistant; `hass` does, inside
each call.

## 3. What each piece guarantees

### 3.1 `ble_session(device, ...)`

- Connecting happens *inside* the block: a connect failure raises out of it
  as `ConnectFailed` and counts as an attempt. (`Unreachable`, for a device no
  radio has a handle for, comes from `blesession.hass.ble_device_or_raise()`,
  which you call inside the attempt, before `ble_session`.)
- The block runs in the trace's `session` stage; an integration's own stages
  nest in it, and a failure before any of them is attributed to `session`.
- The link is watched for the whole block (`dropped_event(client)`); a drop
  during `settle_s` is `ConnectFailed(detail="settle")`.
- The close runs in `finally` under its own bound (`DISCONNECT_TIMEOUT_S`) and
  never masks the original exception; a failed close is the `disconnect_error`
  fact. `keep=True` leaves a successfully opened link to the caller; `client=`
  runs the next session on it (a stale one is ignored).

### 3.2 `Notifications(client, characteristic, settle=, recover=, dropped=)`

- A queue: a reply that lands between two waits is kept.
- Every wait names its `step`. A timeout is `NotificationTimeout(step=...)`.
- A wait ends as `SessionDropped` the moment the link goes, not when its
  timeout runs out; what is already queued is delivered first.
- `request(characteristic, data, *, timeout, step, write_timeout=None, response=False, accept=None, pace_s=0.0)`
  drops stale replies, writes (through `guarded_write`), pauses `pace_s`, and
  returns the reply. `timeout` bounds the write and, separately, the reply;
  `write_timeout` gives the write its own limit.
- `next`, `wait_for(accept, ...)` (`accept` may raise to turn an error frame
  into the failure), `next_burst`, `clear()`, `pending`.
- `__aexit__` unsubscribes under `STOP_NOTIFY_TIMEOUT_S` and ignores a failure
  on a dead link.

### 3.3 Writing: `guarded_write` and `write_chunks`

One primitive, so every write path behaves the same:

- a link already down raises `SessionDropped` naming the `step`, before the
  write;
- a write that does not return within the bound raises `WriteTimeout`
  (a `NotificationTimeout`, so existing handlers still match) naming the
  `step`. The default bound is `WRITE_TIMEOUT_S` (10 s); `None` removes it.
  The attempt bound (§3.5) is a separate, longer limit above it.

`guarded_write(client, characteristic, data, *, step, response=False, timeout=WRITE_TIMEOUT_S, dropped=None, phase="before")`
is the one write; `response=None` leaves the write type to the backend (bleak picks
from the characteristic's properties), as bleak's own `write_gatt_char` does when called without `response`;
`dropped` defaults to the event `ble_session()` registered, and
`phase` ("before" or "during") only words the `SessionDropped` message. A
`TimeoutError` the backend's write raises itself is not renamed: only the
library's own bound becomes `WriteTimeout`, and a write that hung because the
link dropped is `SessionDropped`, not `WriteTimeout`.

`write_chunks(client, characteristic, data, size, *, step, response=False, gap_s=0, wrap=None, on_chunk=None, write_timeout=WRITE_TIMEOUT_S)`
returns the number of writes made. `wrap(offset, chunk)` frames a chunk,
`on_chunk(sent)` runs after each write so a trace keeps the progress of a
transfer that later fails. An empty `data` writes nothing.

### 3.4 `characteristic_or_raise(client, service_uuid, char_uuid, *, properties=(), min_write_size=None, label="device")`

Returns the characteristic or raises `GattMismatch` (stage `session`): the
service or characteristic is missing, a required property is absent, the write
size is too small, or the lookup itself failed. The first three are final
(`retryable=False`: the device will not grow the profile); a too-small write
size and a failed lookup are raised `retryable=True`, because a write size read
right after connecting can be bleak's 20-byte default until the MTU is known
and services may not be discovered yet. Check the size once the link has
settled.

### 3.5 `run_attempts(attempt_fn, *, lock, max_attempts, attempt_timeout_s, pause_s, retry_if, guard, on_attempt, stage_map, name)`

- The lock is held for **one attempt**; between attempts it is released.
- Every attempt is bounded, connecting included, **when you pass
  `attempt_timeout_s`** (the default `None` is no bound: always pass one). A
  bound that fires is `AttemptTimedOut` and `timed_out=True`; it is not
  retried by default (the transport is dead, not the device unwilling).
- `guard` runs under the lock before the attempt and may decline it
  (`skipped`); `on_attempt` sees every attempt, including declined ones.
- `Attempt.retrying` says whether another attempt follows. The loop sets it
  **before** `on_attempt`: true when the attempt failed, was not declined, is
  not the last, and `retry_if` returned true. `retry_if` is therefore called
  before `on_attempt`; an attempt whose `retry_if` raised is not handed to
  `on_attempt`. An `Attempt` built by hand has `retrying=False`.
- It never raises for a failed attempt: the returned `Attempt` carries the
  outcome.

**The integration's side:** resolve the device handle *inside* the attempt
(`blesession.hass.ble_device_or_raise`), scope the lock (per device, entry or
domain), choose the counts and bounds, and decide in `retry_if` which
failures deserve another try.

## 4. Stages

The primary set is fixed. A device names its own finer stages and maps them
with `SessionTrace(stage_map=...)`; the report carries both.

| stage | meaning |
|---|---|
| `unreachable` | no radio sees the device; nothing was tried |
| `connect` | the link never came up (or dropped during the settle) |
| `session` | connected, failed before the protocol's first stage: service discovery, the subscribe, a GATT lookup |
| `auth` | authentication, unlock, the handshake |
| `transfer` | the data exchange |
| `finish` | the completion wait after the last data frame |
| `disconnect` | only the close failed; the work was done |

`SessionTrace(stage_map=None)`: `timed(name)` (nests; a repeated stage adds
up), `record(name, seconds)`, `fail(name)`, `forgive(name=None)`,
`note(**facts)` (scalars only), `failure(exc)` → `(stage, detail)`,
`as_dict()`; read `stage` (the stage running now), `failed_primary`,
`failed_detail`, `timings`, `facts`, and `link` (what `ble_session()` learnt
about the radio). The **innermost** stage an exception escapes from, and the
first failure, win.

## 5. Errors

Every library error is a `ConnectionError`, so one `except ConnectionError`
maps them all to `UpdateFailed` / `HomeAssistantError`. The library logs each
failed attempt at debug (with the traceback, for a bug hunt) and nothing at
warning; what the *final* failure is logged as is yours. The convention that
reads well in Home Assistant: one warning, no traceback, because an off device
is the ordinary case.

`WriteTimeout` is a `NotificationTimeout`. A `cause` callback that tests
`isinstance(failure.exc, NotificationTimeout)` therefore also matches it; test
for `WriteTimeout` first, or exclude it, when your sentence is about a silent
device and you want the generic `write_timeout` reading for a hung write.

| class | raised when | `stage` | `retryable` | also carries |
|---|---|---|---|---|
| `BleSessionError` | base class: `BleSessionError(message="", *, stage=None, detail=None, retryable=None)` | per subclass | `True` | `stage`, `detail`, `retryable` |
| `Unreachable` | no radio has a handle for the address | `unreachable` | `True` | |
| `ConnectFailed` | `establish_connection` raised, or the link dropped in the settle | `connect` | `True` | `detail="settle"` for a settle drop |
| `GattMismatch` | the device lacks the service, characteristic, property or write size | `session` | **`False`**, `True` for a too-small write size or a failed lookup (§3.4) | |
| `SessionDropped` | the link went away mid-session | `session` | `True` | `detail` = the step that was waiting |
| `NotificationTimeout` | a wait ran out | | `True` | `step`, `timeout`; also a `TimeoutError` |
| `WriteTimeout` | a write did not return in time | | `True` | `step`, `timeout`; a `NotificationTimeout` |
| `AttemptTimedOut` | the attempt bound fired | the stage that was running | `True`\* | `timeout`; also a `TimeoutError` |
| `DeviceError` | **you raise it**: the device answered with an error | from the trace | as you set it | `code`, `retryable` |

\* never retried by default: `default_retry_if` stops at `timed_out` first.

`retryable` is read by `default_retry_if`. Raise `DeviceError(..., code=...,
retryable=False)` (or a subclass) for a fault a retry cannot fix, such as a
rejected key; leave the wording and the meaning of the code to your `cause`
callback.

Where an error is not one of these (a `BleakError` from your own protocol
code), `run_attempts` still records it with the stage from the trace.

## 6. The report

`build_report(operation=, trace=, exc=, skipped=, facts=, cause=, noun=, attempt=, attempts=, retrying=, failed_stage=, failed_detail=)`
and `report_attempt(attempt, operation=, facts=, cause=, noun=, attempts=)`
return a dict in a fixed key order: `REPORT_KEYS`, then `FACT_KEYS`, then any
other key passed in `facts=`, then one `<stage>_s` per timed stage, then the
trace's `note()` facts. Everything a session can put on a sensor:

| key | when | meaning |
|---|---|---|
| `operation` | always | what you called it |
| `success` | always | `False` for a failure *and* a declined attempt |
| `skipped` | a `guard` declined | what the guard returned |
| `error` | failure | the exception's message |
| `failed_stage` | failure | the primary stage |
| `failed_detail` | failure | your stage name (or the wait's `step`) when it differs |
| `likely_cause` | usually | one sentence: yours first, then the generic one |
| `likely_cause_key` | generic sentence | its stable name (§8) |
| `timed_out` | attempt bound fired | the transport died, not the device |
| `attempt`, `attempts` | via `report_attempt` | which try, out of how many |
| `retrying` | a failed attempt another follows | `True`; absent otherwise (§6.1) |
| `via`, `via_type`, `rssi`, `paths`, `advertised_via`, `via_unconfirmed` | radio known | `FACT_KEYS`, from `radio_facts()` |
| `<stage>_s` | timed | seconds, in the order stages finished |
| `reused`, `disconnect_error`, `stale_close_error` | when they happen | `keep`/`client=` reuse; a close that failed |
| your `note()` facts | always | anything scalar |

A key is added without notice; one is renamed, removed or changes meaning
only per §1.

A report never fails to build because of its diagnostics: a `cause` callback
that raises is logged at debug and the generic sentence is used, and
`blesession.hass.radio_facts()` returns `{}` (logged at debug) when the adapter
or scanner lookup raises.

`fallback_report(operation, *, trace=None, exc=None, attempt=None)` is the
report for when building one failed anyway: outcome, `error`, the failed stage,
and, given an `attempt`, `skipped`, `attempt` and `retrying` (the attempt's own
trace and error win). It cannot raise.

### 6.1 `classify()` and `SessionReports`

`classify(report)` reads the facts in the report alone:

| kind | when |
|---|---|
| `skipped` | the report has a `skipped` key (a guard declined) |
| `ok` | no `error` |
| `retried` | `error` and `retrying` — another attempt follows |
| `failure` | `error`, no `retrying` |

`SessionReports.record(report, *, now=None)` files it: every kind becomes
`last` and the latest of its `operation` (`of(operation)`); a `failure` also
sets `last_failure`, increments `failures` and sets `last_failure_at` (UTC); a
`retried` sets `last_retry` only — the write may still succeed, so it is not a
failure, but the evidence of the intermittent attempt is kept. `last_kind` is
the kind of the report filed last. `add_listener(cb)` is called after every
`record` and returns the function that removes it; a listener that raises is
logged and does not stop `record`. `clear()` forgets every report and count
(listeners stay).

Whether a session is recorded at all is the integration's call, made by not
calling `record`: **a cancelled session (`CancelledError`) is never recorded**,
and a poll that finds an advertisement-driven device asleep is the
integration's to leave out.

## 7. Callback shapes

- **`cause`** — `Callable[[Failure], str | None]`. `Failure` has `stage`
  (primary), `detail`, `error` (text), `exc` (the error: test its type), and
  `facts` (the radio facts). Return your sentence, or `None` for the generic
  one. It runs only for a failure, with the exception present. A callback taking
  `(stage, detail, error, facts)` or `(stage, detail, error, facts, exc)` still
  works, raises `DeprecationWarning` (Python hides it outside tests, so one
  warning per callback is also logged) and goes in 1.0. A callback with `*args`
  receives a single `Failure`. `Cause` is now typed `Callable[[Failure], ...]`, so
  a type checker flags an old-shape callback before the runtime does.
- **`retry_if`** — `Callable[[Attempt], bool]`, called for a failed attempt.
- **`guard`** — `async () -> value | None`; a non-`None` value declines the
  attempt and becomes `Attempt.skipped`.
- **`on_attempt`** — `Callable[[Attempt], None]`, after every attempt, outside
  the lock. Record reports here, not from what `run_attempts` returns.
- **`stage_map`** — `{your stage name: primary stage}`.

## 8. Cause keys

The generic sentences. `likely_cause_key` names the sentence, not the string:
several end in weak-signal advice you rebuild from `rssi`, `via`, `paths`. A
sentence from your own `cause` callback carries no key.

| key | reading |
|---|---|
| `attempt_timed_out` | the BLE stack stopped answering and the attempt was cut at its bound |
| `disconnect.timed_out` | the work was done; the bound fired while the link closed |
| `unreachable` | no radio sees the device |
| `connect.no_slot` | the proxy has no free connection slot |
| `connect.settle` | the link dropped before encryption settled |
| `connect.failed` | the link could not be established |
| `link_lost` | the link went away mid-session |
| `write_timeout` | a write did not complete: the adapter or proxy stopped taking data |
| `session.gatt_mismatch` | the device lacks the GATT profile the protocol needs |
| `session.refused` | connected, but dropped or refused the session before the protocol started |
| `auth.no_answer` | the handshake went unanswered |
| `transfer.no_answer` | the device stopped answering mid-transfer |
| `finish.no_answer` | the data was taken but completion was not reported |
| `disconnect.close_failed` | the work was done; only the close failed |

Types are the contract: the sentences read the exception's type. The English
text markers beside them (`"no response"`, `"slot"`) are a fallback for an
error that lost its type, such as a proxy's backend wording.

## 9. What the integration provides

1. A **stage map** from your stage names to §4.
2. Your **protocol frames**, with a `step` on every wait.
3. **Errors** derived from `BleSessionError` (or `DeviceError`, for a fault
   the device reported: `DeviceError(message, code=, retryable=)`), so one
   `except ConnectionError` covers them; `retryable=False`, as a class
   attribute or per instance, where a retry cannot help.
4. A **`cause` callback** with the sentences only your device can say; an
   empty one is fine.
5. The **lock**, its scope, the retry count, the attempt bound, the pause.
6. **Recording** every attempt into `SessionReports` from `on_attempt` (or
   one session per `record` outside `run_attempts`), except a cancelled one,
   and deciding which expected failures (a device asleep) are left out.
7. **Tests** with `blesession.testing.FakeClient`, not a hand-made mock.

What stays yours, deliberately: retry counts and backoff, packet pacing, lock
scope, bonding and pairing, cooldowns, advertisement parsing, and the
protocol itself.
