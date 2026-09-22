# blesession — design

*Status: verified on device over a Bluetooth proxy. Written from
integrations that already have this instrumentation and a survey of ones
that do not.*

## Why

Every Home Assistant BLE integration in this family opens a link, subscribes
to a notification characteristic, exchanges a few frames with a timeout,
and drops the link. Each one wrote that by hand, and each one wrote the
notification wait differently (an `asyncio.Event`, a `Future`, a `Queue`).
A few then grew the same ~300 lines of troubleshooting instrumentation —
per-stage timings, the stage a failure happened in, which radio the link
went over, one sentence on what the failure most likely means — published
as sensor attributes so a failed session at 3 am can be read off the entity
without debug logging. The others have none of it, and would want it the
day a user reports the same 3 am failure.

The early implementations already drifted where it matters most: the
connected-scanner probe pokes a different private bleak/habluetooth
attribute in each (`client._connected_scanner` vs `client._backend._source`),
and both will break on the next habluetooth change — in two places.

`blesession` is the shared frame: the session skeleton, the notification
queue, the stage trace, the failure vocabulary, the report, and the one
place that knows how to ask habluetooth which radio a link took.

## What it is not

It is **not** a connection-policy library. Everything an integration learned
the hard way about *its* device stays in the integration:

- retry counts, backoff, packet pacing
- lock scope: domain-wide, per entry, or per device object
- bonding, pairing agents, post-connect bond settle, GATT cache clearing
- cooldowns between sessions, advertisement-triggered polls
- advertisement parsing (`bluetooth-sensor-state-data` already covers it)
- the protocol frames themselves

The library supplies **mechanism and vocabulary**; the integration supplies
**policy and device knowledge**.

## Package layout

```
blesession/
  __init__.py       re-exports the public API
  session.py        ble_session(), current_client_class(), dropped_event()
                    still_up()
  notifications.py  Notifications
  trace.py          SessionTrace, traced()
  stages.py         the fixed stage vocabulary, primary_of()
  errors.py         BleSessionError and subclasses
  link.py           LinkInfo, probe_link(), connected_via(), is_proxy()
  attempts.py       Attempt, run_attempts() — the lock/attempt contract
  causes.py         cause_key() and the CAUSES table it names
  report.py         build_report(), report_attempt(), SessionReports
  const.py          option keys and defaults
  hass.py           ble_device_or_raise(), radio_facts() — homeassistant, lazily
  testing.py        FakeClient, FakeDevice, fake_connect() for integration tests
```

Everything but `hass.py` is pure Python + bleak and is tested without Home
Assistant. `homeassistant` is deliberately not a dependency: `hass.py` is
only ever imported from a running integration, where it is present.

### How a proxy route works without importing Home Assistant

The core connects with `bleak.BleakClient` and never mentions habluetooth,
yet inside Home Assistant the link goes over the local adapter *or an
ESPHome Bluetooth proxy*. That is because HA's bluetooth manager replaces
`bleak.BleakClient` with its own `HaBleakClientWrapper` at startup
(`habluetooth/usage.py`), and the wrapper picks the radio. The `BLEDevice`
the integration passes in (from `async_ble_device_from_address`) carries
the route in `details["source"]`. `bleak-retry-connector` works in HA for
the same reason.

Two consequences for the core:

- `ble_session()` looks `bleak.BleakClient` up **at connect time**
  (`current_client_class()`), not at import time, so it gets the wrapper
  no matter which module was imported first. A test or a non-HA script can
  pass `client_class=` instead.
- Which radio the link *actually* took is only knowable from the wrapper's
  private attributes; `link.py` probes them and stores the raw answer on
  `trace.link`, and `hass.radio_facts()` turns it into scanner names.

## The pieces

### 1. Session skeleton — `ble_session()`

```python
async with ble_session(
    ble_device,
    *,
    trace=None,              # SessionTrace; times "connect", "session" (the block), "disconnect"
    client=None,             # a link a previous keep=True session left up
    settle_s=0.0,            # pause after connect before the first GATT op
    disconnect_timeout_s=10, # bound on the disconnect, outside the attempt bound
    keep=False,              # True: leave the link up (e.g. keep_connection)
    close_stale=False,       # bleak_retry_connector.close_stale_connections_by_address first
    **connect_kwargs,        # use_services_cache, pair, ... -> establish_connection
) as client:
    ...
```

The block runs inside the trace's `session` stage, so an integration's own
stages nest in it and a failure before any of them (service discovery, the
CCCD write) is attributed to `session` without anyone having to say so.
The connect wraps any bleak error in `ConnectFailed`; a drop during
`settle_s` is `ConnectFailed(detail="settle")`.

The session also **watches the link for its whole duration**. It passes its
own `disconnected_callback` to `establish_connection` (chaining the
caller's, if any) and registers the resulting event under the client, where
`dropped_event(client)` finds it. That is what lets a notification wait end
when the link ends rather than when its timeout runs out (§2); `settle_s`
uses the same event, falling back to polling `is_connected` for a backend
that never fires a callback.

Rules, all of which at least one integration currently gets differently:

- Connecting happens *inside* the context, so a connect failure raises out
  of the block like any other session error and counts as an attempt.
- The disconnect runs in `finally`, with its own timeout, and a disconnect
  failure never masks the original exception. It is logged at debug, not
  warning.
- `keep=True` skips the disconnect; the caller owns the link. `client=`
  hands it back for the next session:

  ```python
  async with ble_session(device, client=self._client, keep=self._keep) as client:
      self._client = client if self._keep else None
  ```

  A handle that went stale is ignored and a fresh link opened, so the
  caller never checks — and if that handle is somehow still open, it is
  closed first, under the disconnect bound, rather than abandoned holding
  a proxy slot the caller no longer has a reference to. A reused link times no `connect` stage — there was
  nothing to connect — and the trace notes `reused=True`, so a missing
  `connect_s` reads as "there was none" rather than as a measurement that
  went missing. It keeps the drop watch it already had. `settle_s`,
  `close_stale` and the connect kwargs describe *opening* a link and are
  not applied to one already up; `close_stale` in particular would have
  killed the very link being reused.
- The scanner the link went over is captured on the client right after
  connecting (see §7) so it is available even if the session dies later.

### 2. Notifications — `Notifications`

```python
async with Notifications(client, NOTIFY_UUID, settle=0.5) as replies:
    await client.write_gatt_char(WRITE_UUID, cmd, response=False)
    reply = await replies.next(timeout=5, step="start")
    done  = await replies.wait_for(is_done, timeout=120, step="finish")
```

- Queue-based: replies that arrive before the wait are not lost (an
  `Event`-based wait can miss a reply that lands between two waits).
- `step` is **required** on every wait. `NotificationTimeout` carries it,
  so a timeout already names the stage that failed.
- A wait ends as `SessionDropped` the moment the link goes, instead of
  running its step timeout out against a device that is no longer there —
  the difference between a 2-second failure and a 120-second one, with the
  lock held throughout. What is already queued is delivered first: a device
  that sent its last reply and then dropped the link answered the step.
  The event comes from `dropped_event(client)`; `dropped=` overrides it for
  a connection the caller owns, and an unwatched client keeps the plain
  timeout behaviour.
- `clear()` drops what arrived so far (protocols that must ignore a late
  reply from a previous command).
- `wait_for(accept)` lets `accept` raise to turn an error frame into the
  session's failure.
- `__aexit__` unsubscribes and ignores a failure on a dropped link, under
  its own `STOP_NOTIFY_TIMEOUT_S` bound — it runs *after* an attempt bound
  has fired, so it is the one wait nothing else bounds (§5).
- Multi-channel protocols open one `Notifications` per characteristic. A
  handle-indexed dispatcher is out of scope.

### 3. Stage vocabulary — fixed primary stages

Integrations that compare across devices need the same first-level names.
The primary set is fixed; a device adds a `detail`.

| stage         | meaning                                                   | device detail examples                     |
|---------------|-----------------------------------------------------------|--------------------------------------------|
| `unreachable` | no radio sees the device; nothing was tried               |                                            |
| `connect`     | the link never came up                                    | `settle` (dropped during bond settle)      |
| `session`     | connected, failed before the protocol's first stage       | service discovery, CCCD write; `services`  |
| `auth`        | authentication / unlock / handshake                       | `handshake`, `pair`, `unlock`              |
| `transfer`    | the data exchange                                         | image parts; `readout`; a print job        |
| `finish`      | the completion wait after the last data frame             | panel refresh; end-command ack             |
| `disconnect`  | only the close failed (harmless)                          |                                            |

`SessionTrace.timed(name)` accepts any name; `SessionTrace(stage_map=...)`
maps a device name to its primary stage, and the report carries both
(`failed_stage` = primary, `failed_detail` = the device's name when it
differs). The generic cause sentences (§9) key on the primary stage only.
A name the map does not know passes through unchanged rather than being
dropped.

### 4. Stage trace — `SessionTrace`

- `timed(name)` context manager, nests; a repeated stage adds up.
- The **innermost stage an exception escapes from** is `failed_stage`, and
  the first one wins, so a close that also fails does not overwrite the
  real cause.
- `fail(name)` for a check that reports failure by return value.
- `forgive(name)` for a failure the caller went on to swallow (a retry).
- `note(**facts)` for scalars: attempt number, pacing, bytes, parts.
- `as_dict()` → `failed_stage`, facts, then `<stage>_s` in run order.
- `@traced("stage")` decorator for session methods.

Earlier timing helpers that inferred the failed stage from which keys were
present are retired in favour of this; a transfer-specific flag becomes
`trace.failed_stage == "transfer"`.

### 5. Timeouts — three levels

| level        | what                                          | owner                           |
|--------------|-----------------------------------------------|---------------------------------|
| step         | one notification wait                         | protocol code, via `Notifications` |
| attempt      | one try, connecting included                  | `run_attempts()`, value from the integration |
| disconnect   | the close, *outside* the attempt bound        | `ble_session()`                 |
| unsubscribe  | `stop_notify`, also outside the attempt bound | `Notifications` (`STOP_NOTIFY_TIMEOUT_S`) |

The last two are the waits that run in `finally` / `__aexit__`, i.e. after
the attempt bound has already fired. Anything unbounded there hangs the
lock exactly as the bound was meant to prevent, so both have their own.

The attempt bound exists because a GATT write has no timeout of its own: a
proxy that dies mid-transfer leaves the attempt hanging, and if it holds a
lock, everything else hangs with it. The library wraps the attempt in
`asyncio.timeout` and records `timed_out=True` on the report; whether a
timed-out attempt is retried is policy (§6), with the default being *no* —
the transport is dead, not the device unwilling.

### 6. Attempts and the lock — `run_attempts()`

The lock itself and its scope belong to the integration. What the library
fixes is the **contract**:

- The lock is held for **one attempt**, not the whole retry sequence.
  Between attempts (the pause, or after a timed-out attempt) it is released
  so other devices go first.
- The device handle is resolved fresh **inside** each attempt; the one seen
  when the job was queued may be stale after the wait.
- Guards that can change while waiting for the lock (write lock, superseded
  job, duplicate payload) run under the lock, before the attempt.

```python
last = await run_attempts(
    attempt,                    # async (Attempt) -> T; uses attempt.trace, attempt.state
    lock=ble_lock,
    max_attempts=3,
    attempt_timeout_s=600,
    pause_s=1.0,
    retry_if=default_retry_if,  # (Attempt) -> bool; default: not timed_out
    guard=None,                 # async () -> skip-value | None, run under the lock
    on_attempt=None,            # (Attempt) -> None, after every one — failed,
                                # successful, or declined — outside the lock
    stage_map=...,
)
```

`run_attempts()` never raises for a failed attempt: the returned `Attempt`
carries the error, the trace, `failed_stage` / `failed_detail`, `timed_out`
and whatever `guard` returned; the integration decides what to raise or
publish. `attempt.state` is a dict carried from one attempt to the next
for pacing counters and the like. Whether a failure in stage X deserves
pacing, a cooldown, or giving up is decided in `retry_if` and that state.

### 7. The device handle and the radios — `blesession.hass`

#### `ble_device_or_raise(hass, address)`

```python
async def attempt(a):
    device = ble_device_or_raise(hass, address)   # fresh, under the lock
    async with ble_session(device, trace=a.trace) as client:
        ...
```

Four lines every integration writes the same way, and the place two things
are easy to get wrong. It resolves the handle **inside** the attempt: the
handle carries the route a radio last advertised, and after a wait for the
lock that route may be gone or may now be a different proxy (§6). And it
**raises** rather than returning None, so "the device is asleep" arrives as
an ordinary session failure — `Unreachable` is a `ConnectionError` carrying
`stage="unreachable"`, so it reaches the report with a stage and a likely
cause like everything else, instead of as an `if device is None` branch each
integration words differently.

#### `radio_facts(hass, address, link)`

Integrations previously each probed the radio a different way. One function:

```python
facts = radio_facts(hass, address, trace.link)
# {"via": "living-room-proxy", "via_type": "proxy", "rssi": -71, "paths": 2,
#  "advertised_via": "office-proxy"}   # only when it differs from via
```

- `via` — the radio the link actually took, when knowable
- `via_type` — `adapter` | `proxy`
- `rssi` — as seen by that radio
- `paths` — connectable radios that currently see the device; 1 means no
  failover is possible
- `advertised_via` — the scanner whose advertisement was strongest, only
  when it is not the one the link took (a failover, or on multi-proxy
  setups the radio that holds the bond)

`link.connected_via(client)` is the single place that probes private
attributes, in order: `client._connected_scanner` (habluetooth's wrapper),
`client._backend._source` / `.source` / `._device_path` (bleak-esphome /
BlueZ). `link.advertising_source(ble_device)` reads
`ble_device.details["source"]`. When habluetooth changes, `link.py` is the
one file to fix. `ble_session()` runs the probe right after connecting and
keeps the raw result on `trace.link`, so it survives the session dying
later; `radio_facts()` resolves it to scanner names when the report is
built.

`is_proxy(ble_device)` is exposed for integrations that adapt pacing to the
transport (e.g. a longer packet interval over a proxy).

### 8. Report — `build_report()` and attribute keys

```python
report = build_report(
    operation="write",          # write | poll | pair | status | ...
    trace=trace,
    exc=exc,                    # None on success
    facts=radio_facts(...),
    cause=my_likely_cause,      # (stage, detail, error, facts) -> str | None
    noun="device",              # what the generic sentences call the device
)
report = report_attempt(attempt, operation="write", facts=..., cause=..., attempts=3)
```

Key order is fixed so the fields a reader looks at first are at the top of
the attribute list:

```
operation, success,
skipped,                                                         # guard declined
error, failed_stage, failed_detail,                              # failures only
likely_cause, likely_cause_key, timed_out,
attempt, attempts,
via, via_type, rssi, paths, advertised_via,
<stage>_s ...,                                                     # run order
<note facts> ...                            # incl. disconnect_error, if any
```

An attempt a `guard` declined is **not** a success: nothing was tried, so
`success` is False and `skipped` carries what the guard returned, with no
`error` beside it. A disconnect that fails after the work was done is the
other way round — it does not fail the session, and is carried as the
`disconnect_error` fact rather than being swallowed.

Two sensor conventions, so troubleshooting docs can be shared across
integrations, and `SessionReports` holds them:

```python
reports = SessionReports()

def file_it(a):                       # every attempt, as it finishes
    reports.record(report_attempt(a, operation="write", attempts=3, facts=facts))

last = await run_attempts(attempt, max_attempts=3, on_attempt=file_it)
reports.last           # on the duration / timestamp sensor
reports.last_failure   # on the diagnostic sensor
```

Record from `on_attempt`, not from what `run_attempts()` returns: it hands
back the **last** attempt only, so filing that one alone loses a first
attempt that failed and a second that worked — exactly the intermittent
failure the second slot exists to keep. `on_attempt` sees every attempt,
including one a `guard` declined, so the rule above holds for whichever
slot each one belongs in. Filing the returned attempt is right only when
`last_failure` should mean "the last session that failed overall" rather
than "the last attempt that failed".

- **last session** — the most recent, success or failure, on the
  duration/timestamp sensor's attributes
- **last failure** — kept until the next failure, so a success does not
  erase the evidence: the user who comes to read it has usually had a
  working session since

A session a `guard` declined is `last` but not `last_failure`. Nothing was
tried, so it must not overwrite the last real failure with "the write lock
was held" — the slot keys on `error`, not on `success`.

### 9. Generic likely-cause sentences — `causes.py`

Two halves. `cause_key(stage, error, exc=...)` picks *which* reading a
failure gets and returns a stable name for it; `CAUSES` maps that name to
the English sentence, with `{noun}` and `{where}` filled in.
`generic_cause()` is the pair rendered, and the report carries both — the
sentence for a reader, the key so an integration can publish a Home
Assistant translation instead. A key is part of the contract exactly as a
stage name is. A sentence from the integration's own `cause` callback
carries no key: it already owns that wording.

The key names the *sentence*, not the whole string. Several sentences end
in `{where}`, the weak-signal placement advice, which is English too — a
translation rebuilds it from `rssi`, `via` and `paths`, which sit in the
report beside the key, under the same `rssi <= -85` and `paths == 1` rules
`placement()` uses. Translating a fragment out of a rendered sentence is
what the key exists to avoid.

The readings key on the primary stage and the kind of failure, take the
noun as a parameter, and return `None` when they have nothing to say so the
integration's own table takes over:

- weak signal placement: `rssi <= -85`, plus "and no other radio reaches
  the {noun}" when `paths == 1`
- `connect` + "slot" → the proxy has no free connection slot
- `unreachable` → no radio sees it: range, asleep, battery, adapter down
- `session` → dropped or refused before the protocol started; if it
  repeats, the model/profile may not match
- a `SessionDropped` in any protocol stage → the link went away mid-session
- attempt deadline → the BLE stack stopped answering; restart adapter/proxy
- `disconnect` → the work was done; only the close failed

The sentences read the **exception type** where there is one
(`NotificationTimeout`, `SessionDropped`, `ConnectFailed(detail="settle")`,
`AttemptTimedOut`), so an integration that words its own message keeps
them. The English error-text markers sit beside those checks rather than
behind them — `isinstance(...) or "no response" in err`, never either/or —
because `build_report()` always has the exception, so a fallback that only
ran without one would never run at all, and a failure that says "no
response" without carrying the type would silently lose its sentence.
Some markers have no type to go with them at all (a proxy's "no slot
free").

Device sentences stay in the integration's `cause` callback, which runs
first.

### 10. Errors — `errors.py`

A device that is off, out of range, or asleep is the ordinary case and must
not produce a traceback ("this error originated from a custom integration").
Only a bug should.

```
BleSessionError(stage, detail=None)
  Unreachable             no handle for the address
  ConnectFailed           establish_connection raised, or the link dropped in settle
  SessionDropped          the link went away mid-session (carries the step
                          that was waiting), raised by a Notifications wait
  NotificationTimeout     a step wait ran out (carries step)
  AttemptTimedOut         the attempt bound fired
```

All are `ConnectionError` subclasses so an integration can map them to
`UpdateFailed` / `HomeAssistantError` in one line. Log-level rule: a failed
attempt is debug, the final failure is one warning, never an error with a
traceback.

### 11. Option keys — `const.py`

`retry_count`, `keep_connection`, `scan_interval`, `attempt_timeout` exist
under different names across integrations. The library exports the names
and defaults so config flows can share code; it does not read config.

## What an integration keeps

For a simple write-and-disconnect device, adopting the library leaves
roughly:

- the protocol frames and the writer, using `Notifications` with `step=`
- a stage table: device stage name → primary stage (often identity)
- a `cause` callback with the device-specific sentences (may be empty)
- the lock, the retry count, and the sensors that read `SessionReports`

Around 50–80 lines beyond the protocol itself.

## Rollout

1. Prove the API against the integrations that already have instrumentation
   (timing helpers → `SessionTrace`, per-integration radio probes → one
   function).
2. Move the matched code here; publish 0.1.0; integrations depend on it from
   `manifest.json` `requirements`.
3. Adopt next on the **simplest** remaining integration. If the API only
   fits the ones that shaped it, this is where it shows.
4. The rest one at a time; devices that keep a long-lived link last — they
   exercise `keep=True`.

## Testing — `blesession.testing`

Every integration's tests fake the same four bleak methods with
`MagicMock`. `FakeClient` is the one fake: `reply(data)` delivers a
notification (or queues it for the next subscriber), `drop()` takes the
link down, `fail_*` script errors, `disconnect_delay_s` a hanging
disconnect. `drop()` also fires the `disconnected_callback`, as bleak does,
so a drop reaches the session the way it does on device.
`fake_connect(client)` replaces `establish_connection` and wires that
callback up. The
library's own tests use nothing else, and an integration adopting the
library can retire its own client mocks.

`hass.py` is the exception, and the file most likely to break on a
habluetooth release, so it is tested against a stub module planted in
`sys.modules` (`tests/test_hass.py`). Every function there imports
`homeassistant` inside the call, which is what makes that possible. The
stub mirrors only the shapes `hass.py` relies on — two scanner base
classes, a source id lookup, the strongest advertisement, the connectable
scanners for an address — so when Home Assistant changes one of them, the
fix is in `hass.py` and the stub moves with it.

## Decisions taken

- `run_attempts()` ships in 0.1 as a helper, not a requirement:
  `ble_session()` + `SessionTrace` + `build_report()` work without it.
- `advertised_via` is always computed and omitted when it equals `via`.
- The client class is resolved at connect time, so import order relative
  to Home Assistant's bluetooth setup does not matter.
- `NotificationTimeout` and `AttemptTimedOut` are both `ConnectionError`
  *and* `TimeoutError`, so existing `except TimeoutError` protocol code
  keeps working while integrations can map everything with one
  `except ConnectionError`.

## Direction after 0.1

- **Versioning.** 0.x until a third integration is on it; integrations pin
  an exact version in `manifest.json`. Anything that changes a report key
  or a stage name is a minor bump and a CHANGELOG entry, because
  troubleshooting docs quote them.
- **Shared troubleshooting doc.** Once two integrations publish the same
  keys, one `docs/troubleshooting.md` here (with a `docs/ko/` copy)
  explains `failed_stage` / `likely_cause` / `via` for all of them; each
  integration's own doc links to it and adds only its device sentences.
- **Bonded devices.** Bond settle with retry, pairing agents, and
  pair-on-connect stay in each integration. If a second bonded device type
  appears, the connect-with-settle loop is the candidate to move here,
  behind `ble_session(settle_s=..., settle_attempts=...)`.
- **Long-lived links.** `keep=True` leaves the link up and `client=` runs
  the next session on it, with `reused=True` on the trace as the notion of
  "this session reused an open link". What is still open is the *policy*
  around it: when to give a kept link up (an idle timeout, a failure
  count), which is the integration's, and whether a kept link needs its
  own stage timings. The first integration with a `keep_connection` option
  to adopt this answers both.
- **Not planned.** Retry policy, pacing, cooldowns, advertisement parsing.
  If the same policy shows up in three integrations, it becomes a
  documented recipe here before it becomes code.
