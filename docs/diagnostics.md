# Shared diagnostics implementation guide for integrations

**English** | [한국어](diagnostics.ko.md)

Use this guide when building or aligning diagnostics in a Home Assistant integration that uses blesession. It defines how integrations collect, retain and expose information with consistent meanings. Each integration implements its entities and diagnostics download; blesession does not create them.

**Required** means required to follow this shared layout; **recommended** is a default that may be adapted to the device. The [contract](contract.md) defines fields and APIs, and the [adoption guide](adopting.md) covers protocol implementation. Examples target the current source APIs; check that the installed library provides the features used. Declare `"requirements": ["blesession"]`; this guide does not pin a version.

## 1. Layout and responsibilities

```text
BLE operation → SessionTrace / radio_facts → report → SessionReports.record
                                                        ├─ HA entity updates
                                                        └─ diagnostics download
```

| Area | blesession provides | Integration provides |
|---|---|---|
| Session | Stages, timings and link observations | The same trace through the operation, device stage mapping |
| Report | Outcome, shared causes and field layout | Stable operation names, device causes and additional facts |
| Retention | Latest attempt/failure/retry, counts and timestamps | Per-device ownership, HA updates and explicit reset policy |
| Presentation | Report dictionaries and statistics | Entities, translations, downloads and redaction |

**Required:** keep one `SessionReports` on each BLE device's runtime object. Separate devices in a multi-device integration. Distinguish `print`, `poll`, `measure` and other operations with stable names; use `reports.of(operation)` for the latest result of one operation. Do not record into shallow copies of `SessionReports`.

## 2. Record every attempt exactly once

**Required:** record through `run_attempts(on_attempt=...)`. Recording its returned final attempt again duplicates counts; recording only that final attempt loses intermediate failure evidence.

The following adapter accepts an optional device `cause` callback taking one `Failure`. A report-construction failure produces a minimal fallback. A `Warning` turned into an exception in a development environment still propagates.

```python
from blesession import fallback_report, report_attempt
from blesession.hass import radio_facts


def make_recorder(reports, hass, address, operation, max_attempts, *, cause=None):
    def record(attempt):
        try:
            report = report_attempt(
                attempt,
                operation=operation,
                attempts=max_attempts,
                facts=radio_facts(hass, address, attempt.trace.link),
                cause=cause,
            )
        except Warning:
            raise
        except Exception:
            report = fallback_report(operation, attempt=attempt)
        reports.record(report)

    return record
```

Pass `on_attempt=make_recorder(reports, hass, address, "write", max_attempts)` to the existing `run_attempts()` call. Supply `attempt_timeout_s` and resolve the device handle inside the attempt. Keep `guard` results JSON-compatible, such as strings. If the adapter uses a fallback, also log the cause through the integration's existing debug logger.

Without `run_attempts()`, use `build_report()` and `reports.record()` with the same policy. Propagate external cancellation (`CancelledError`) without recording a failure. An `AttemptTimedOut` returned after the operation deadline is a reportable outcome. The integration decides whether expected sleeping-device polls are omitted and documents that policy.

| Outcome | Storage and statistics |
|---|---|
| Success | Updates `last`, the operation slot, `sessions`, `last_success_at`; resets the failure streak |
| Final failure | Updates `last`, `last_failure` and the operation slot; increments `failures`, `sessions`, `consecutive_failures` |
| Failure followed by another attempt | Updates `last`, `last_retry` and the operation slot; does not count as a final failure |
| Declined (`skipped`) | Updates `last` and the operation slot; does not count as success or failure |

A success must not clear `last_failure` or `last_retry`. Statistics normally cover the runtime object's lifetime. Document any persistence policy; do not call `clear()` on each reconnect or success. Compute `failures / sessions` only when `sessions > 0`.

## 3. Recommended entities

These are shared roles. Follow each integration's naming and translation scheme and retain existing entity unique IDs. Use `EntityCategory.DIAGNOSTIC` for diagnostic entities.

| Role | State | Attributes |
|---|---|---|
| Latest operation / operation duration | An actually measured device-specific value | Complete `reports.of(operation)` for that operation |
| Last failure | `reports.last_failure_at` | Complete `reports.last_failure` |
| Failure count | `reports.failures` | Optional completed-session and consecutive-failure summary |
| Optional last success | `reports.last_success_at` | Optional context for success history |

**Required:** expose a redacted copy of the **whole report**, rather than selecting a fixed list of fields. This preserves new common fields without changing each entity. Keep field names, units and meanings unchanged. Do not mutate the stored report.

The last-failure timestamp is UTC and is `None` before a failure. Distinguish measured operation duration from individual `<stage>_s` timings: nested stages and lock waiting cannot simply be added together. While a job is running, hide the previous operation's report or clearly identify it as historical rather than pairing it with current progress.

An existing integration that exposes failures only, such as ICScale, may expose successful reports through the download. Review existing entity conventions and default enablement when adding sensors. Do not attach every report to every sensor.

## 4. Connect recording to Home Assistant updates

Saving a report does not itself refresh HA entity attributes. A new report must reach the UI even when the sensor's state value did not change.

**Required:** connect `SessionReports.add_listener()` to the existing coordinator update path or entity state writing. Do not register both paths for the same entity. Remove the subscription on entity removal or integration unload. A callback must not start BLE work or perform network lookups.

This lifecycle fragment assumes an existing sensor with `self._reports` and attributes returning a redacted copy of its report. Coordinator entities may instead route the notification through their existing coordinator.

```python
async def async_added_to_hass(self):
    await super().async_added_to_hass()
    self.async_on_remove(
        self._reports.add_listener(self.async_write_ha_state)
    )
```

The example assumes records are made on HA's event loop. Do not call `async_write_ha_state()` directly from another thread. Explicitly notify the UI after `clear()` if an immediate refresh is needed: `clear()` does not call recording listeners. See HA's [entity lifecycle guidance](https://contributors.home-assistant.io/core/entities/lifecycle/).

## 5. Preserve the newly added diagnostics

Keep the following on report attributes rather than making a separate sensor for each. The [report contract](contract.md) remains the reference for exact fields and cause keys.

| Information | Collection path and interpretation |
|---|---|
| `lock_wait_s` | Recorded by `run_attempts(lock=...)`; not BLE transfer time or a failure stage |
| `link_dropped`, `dropped_after_s` | Spontaneous drop and link lifetime observed by `ble_session()`; a reused link may outlive the current operation |
| `mtu` | Actual backend value read before `ble_session()` closes; omitted when unknown |
| `last_seen_s`, `paths_passive` | Advertisement age and non-connectable scanners from `radio_facts()` |
| `slots_free`, `slots` | Allocation state of the radio named by `via` at lookup time, not proven state at the earlier failure |
| `error_before_timeout`, `timed_out_in` | Original error before the timeout and stage actually interrupted; retain alongside `error` and `failed_stage` |
| `via_unconfirmed` | Read alongside the displayed radio when deciding whether the actual connection route was confirmed |
| `sessions`, `consecutive_failures`, `last_success_at` | `SessionReports` summary statistics, not automatically inserted into individual reports |

**Required:** omit unknown observations or use `None`; do not invent `0`, `False` or success. Handle older records with missing fields. Display `likely_cause` as a hint, not a confirmed diagnosis. For example, `paths` is not a count of free connection slots.

`NotificationTimeout.rejected` counts received frames that were not accepted. `SessionDropped.sent/total` and `WriteTimeout.sent/total` describe completed/required writes in an interrupted `write_chunks()`, not bytes. Do not assume every exception attribute is automatically a top-level report key. If structured values are needed, add non-conflicting device facts such as `transfer_writes_sent`.

An integration implementing its own connection path does not gain `ble_session()` drop/MTU collection merely by exposing the report. Connect a supported collection path or leave those observations absent; do not fabricate missing information.

## 6. Shared diagnostics download layout

**Recommended:** implement `diagnostics.py` and keep the following `blesession` section. Attributes and a diagnostics download are separate features. Downloads read stored data without starting connections, polls or scans. Repeat this section under each device in a multi-device entry.

This helper builds an internal snapshot **before redaction**. `None` means no record exists yet. Keep new report fields while placing aggregate statistics consistently.

```python
from copy import deepcopy


def diagnostics_snapshot(reports):
    def timestamp(value):
        return value.isoformat() if value is not None else None

    return {
        "blesession": {
            "summary": {
                "last_kind": reports.last_kind,
                "sessions": reports.sessions,
                "failures": reports.failures,
                "consecutive_failures": reports.consecutive_failures,
                "last_success_at": timestamp(reports.last_success_at),
                "last_failure_at": timestamp(reports.last_failure_at),
            },
            "last": deepcopy(reports.last),
            "last_failure": deepcopy(reports.last_failure),
            "last_retry": deepcopy(reports.last_retry),
            "by_operation": deepcopy(reports.by_operation_last),
        }
    }
```

In the integration's `async_get_config_entry_diagnostics(hass, entry)`, resolve the device's `reports` from runtime data, apply the next section's redaction to this copy and return it. The helper itself is not HA's diagnostics entry point. Exclude live Python objects, exceptions and raw bytes; test that the final payload is JSON-serializable. Follow HA's [diagnostics implementation guidance](https://contributors.home-assistant.io/core/integrations/diagnostics/) for the entry point and testing.

## 7. Redaction and device extensions

**Required:** redact a copy before exposing entity attributes or a download. Use a shared integration redactor and check all of the following.

- Do not export keys, tokens, passwords, bonding material or entire raw configuration objects.
- Remove or consistently anonymize Bluetooth/MAC addresses, device identifiers, private IPs/hostnames, personal names and measurements. `via` and `advertised_via` may also identify the installation.
- Process nested dictionaries/lists and identifiers embedded in strings such as `error`, `error_before_timeout` and `likely_cause`. Redacting selected keys with HA's `async_redact_data` alone does not remove addresses inside other strings.
- Preserve useful operation names, stages, timings and counts without modifying the original `SessionReports`. Review new fields for private data as they are added.

Add device facts as scalars through `trace.note(payload_bytes=..., protocol_retries=...)`. `REPORT_KEYS` are reserved: `note(error=...)` and `note(skipped=...)` are ignored. Do not overwrite other shared fields either; choose device/protocol-specific names. Keep large packets, images and measurement histories out of reports. Return `None` from `cause(failure)` when there is no device-specific explanation, preserving the common cause.

Translations can use `likely_cause_key`, falling back to the original sentence for unfamiliar keys. Do not replace original field/key values with translated labels. Propagating new fields after a library update is separate from creating entities, translations or downloads.

## 8. Adoption checklist

- [ ] All operations of a device record into the same `SessionReports`, exactly once per attempt.
- [ ] A success retains the previous failure; a failed attempt followed by success does not increase final failure counts.
- [ ] Declined work and external cancellation are distinguished from operation failures.
- [ ] New attributes update even if state values stay unchanged; listeners are removed on unload.
- [ ] An arbitrary new safe report field reaches attributes and downloads unchanged.
- [ ] Operation slots, new observations and summary statistics retain their shared meanings.
- [ ] Empty initial reports, older records and unsupported collection paths are handled.
- [ ] Downloads do not initiate BLE work and the final payload is JSON-serializable.
- [ ] Nested string identifiers/credentials are redacted without changing stored reports.
- [ ] Device documentation explains where diagnostics are shown, the statistics reset scope and how to download them.

For an existing integration, check the collection path, recording point, entity updates and download in that order. Adopting this guide does not require changing the device protocol, retry policy or existing entity unique IDs.
