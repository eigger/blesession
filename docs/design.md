# blesession — design

*Status: 0.1.0 — verified on device over a Bluetooth proxy. Written from
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
  session.py        ble_session(), current_client_class()
  notifications.py  Notifications
  trace.py          SessionTrace, traced()
  stages.py         the fixed stage vocabulary, primary_of()
  errors.py         BleSessionError and subclasses
  link.py           LinkInfo, probe_link(), connected_via(), is_proxy()
  attempts.py       Attempt, run_attempts() — the lock/attempt contract
  causes.py         the generic likely-cause sentences
  report.py         build_report(), report_attempt()
  const.py          option keys and defaults
  hass.py           radio_facts() — imports homeassistant lazily
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

Rules, all of which at least one integration currently gets differently:

- Connecting happens *inside* the context, so a connect failure raises out
  of the block like any other session error and counts as an attempt.
- The disconnect runs in `finally`, with its own timeout, and a disconnect
  failure never masks the original exception. It is logged at debug, not
  warning.
- `keep=True` skips the disconnect; the caller owns the link.
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
- `clear()` drops what arrived so far (protocols that must ignore a late
  reply from a previous command).
- `wait_for(accept)` lets `accept` raise to turn an error frame into the
  session's failure.
- `__aexit__` unsubscribes and ignores a failure on a dropped link.
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
    on_attempt=None,            # (Attempt) -> None, after each one (log, record on a sensor)
    stage_map=...,
)
```

`run_attempts()` never raises for a failed attempt: the returned `Attempt`
carries the error, the trace, `failed_stage` / `failed_detail`, `timed_out`
and whatever `guard` returned; the integration decides what to raise or
publish. `attempt.state` is a dict carried from one attempt to the next
for pacing counters and the like. Whether a failure in stage X deserves
pacing, a cooldown, or giving up is decided in `retry_if` and that state.

### 7. Radio facts — `blesession.hass.radio_facts()`

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
error, failed_stage, failed_detail, likely_cause, timed_out,     # failures only
attempt, attempts,
via, via_type, rssi, paths, advertised_via,
<stage>_s ...,                                                     # run order
<note facts> ...
```

Two sensor conventions, so troubleshooting docs can be shared across
integrations:

- **last session** — the most recent, success or failure, on the
  duration/timestamp sensor's attributes
- **last failure** — kept until the next failure, so a success does not
  erase the evidence

### 9. Generic likely-cause sentences — `causes.py`

The generic sentences key on the primary stage and the error text, take the
noun as a parameter, and return `None` when they have nothing to say so the
integration's own table takes over:

- weak signal placement: `rssi <= -85`, plus "and no other radio reaches
  the {noun}" when `paths == 1`
- `connect` + "slot" → the proxy has no free connection slot
- `unreachable` → no radio sees it: range, asleep, battery, adapter down
- `session` → dropped or refused before the protocol started; if it
  repeats, the model/profile may not match
- attempt deadline → the BLE stack stopped answering; restart adapter/proxy
- `disconnect` → the work was done; only the close failed

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
  SessionDropped          is_connected went False mid-session
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
- the lock, the retry count, and the sensors that publish the report

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
disconnect. `fake_connect(client)` replaces `establish_connection`. The
library's own tests use nothing else, and an integration adopting the
library can retire its own client mocks.

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
- **Long-lived links.** Integrations that keep a client object and a
  `keep_connection` option will show, when adopting `keep=True`, whether
  the trace needs a notion of "this session reused an open link".
- **Not planned.** Retry policy, pacing, cooldowns, advertisement parsing.
  If the same policy shows up in three integrations, it becomes a
  documented recipe here before it becomes code.
