# Common Bluetooth troubleshooting

**English** | [한국어](troubleshooting.ko.md)

Use this guide with your integration’s device-specific troubleshooting page. It covers local Bluetooth adapters and ESPHome proxies, including integrations that do not use blesession. No Python library installation is needed to follow it. Start with the [proxy introduction and installation guide](bluetooth.md) if the Bluetooth route has not been configured.

Model support, wake-up sequences, pairing, keys, safe commands and retry settings belong to the device integration. This guide does not add diagnostic entities, automatic reconnection, retries or timeout controls to an integration. Use only information and controls your installed integration actually exposes.

## Quick checks

1. Record when the failure happened and what operation was requested. Before repeating a write or physical action, check whether the device already performed it.
2. Check the exact supported model/firmware, battery or power, and the device’s required awake/connection mode. A sleeping device may be expected to become unavailable.
3. Separate **discovery** from **connection**: an advertisement or a recent entity value does not prove a current GATT connection or successful read/write. Advertisement-only devices may not require GATT at all.
4. If the model permits only one connection, close the phone app or other gateway using it. Keep model-required pairing/bonding intact; do not reset or re-pair as a generic first step.
5. Check the actual adapter/proxy, its power and network, and whether other devices on the same route also fail. For proxy connections, verify GATT support (`bluetooth_proxy.active: true`), separately from active scanning.
6. Compare a successful and a failed operation at similar placement. Change one condition at a time, then follow the integration’s safe retry procedure.

See the official [HA Bluetooth guide](https://www.home-assistant.io/integrations/bluetooth/) and [ESPHome proxy settings](https://esphome.io/components/bluetooth_proxy/) for adapter access and proxy capabilities. Leave scan timing and connection limits at their defaults initially. Increasing scan intensity, retries or timeouts is not a general repair.

## Symptoms and failure stages

The stage names below are the blesession vocabulary. Other integrations may show different names or no stage report. A session need not contain every stage; a reused connection may have no new connect timing. Device-specific details such as `unlock` or `readout` refine a stage rather than creating a universal device workflow.

| Symptom / `failed_stage` | Meaning to investigate | Next check |
|---|---|---|
| No device handle / `unreachable` | No suitable radio currently supplies the device handle; connection was not started | Power, wake-up/advertising conditions, range, and whether the relevant adapter/proxy is available |
| Link never comes up or drops during settling / `connect` | Discovery was possible but establishing a usable link failed | Other connection owners, GATT capability, free slots, model pairing/bonding and the affected route |
| Connected but protocol has not started / `session` | Service discovery, characteristic access or notification subscription failed | Exact model/firmware and supported GATT profile; whether the link drops immediately |
| Handshake/key/unlock fails / `auth` | The required protocol opening step did not complete | Device mode, required credentials and the exact device response; do not assume every timeout means a wrong key |
| Data exchange stops / `transfer` | The exchange lost the link, received no expected reply, or a write stalled | Battery/power, proximity, adapter/proxy responsiveness, network and transfer-specific model restrictions |
| Completion confirmation missing / `finish` | Data may have been accepted but final acknowledgement or completion was not observed | Inspect the actual device result before resending; use the model’s completion and recovery procedure |
| Only closing fails / `disconnect` | The work may already be done while link cleanup failed | Check actual device state and subsequent connection availability before repeating the operation |
| Proxy offline, several devices fail together | A shared route or network may be affected | Proxy power, Ethernet/Wi-Fi and ESPHome connectivity; local adapters instead need host/USB/Bluetooth checks |

A `disconnect_error` can be recorded separately while the operation succeeded; it does not mean the transfer failed. `disconnect.timed_out` indicates the attempt bound fired during close and the returned result may have been discarded even though the work was done. Check the device’s actual state. Do not repeat a physical command solely because cleanup or completion reporting failed.

If supplied, `session.gatt_mismatch` points to the required service, characteristic, property or write size not being available. A wrong model/firmware is one possibility; discovery/MTU negotiation may also be incomplete. `write_timeout` describes a write that did not return within its bound; a notification timeout describes an expected reply that did not arrive. These are different observations, not proof of one root cause. `connect.no_slot` suggests exhausted connection capacity: stop overlapping jobs or unnecessary persistent connections before changing proxy capacity.

## Read the available diagnostics

Your integration may expose attributes on an entity, a diagnostics download or logs. Use its documented entry point; there is no common entity name or guaranteed diagnostics button. If a field is absent, do not interpret it as zero, success, or evidence that a stage was skipped.

| Field, when available | How to read it |
|---|---|
| `operation`, `success`, `skipped` | Identify the action and whether it succeeded. A declined attempt can have `success: false` with `skipped` and no error because no work was attempted. |
| `failed_stage`, `failed_detail`, `error` | Read together: primary stage, model-specific step and original error. Keep the original wording for diagnosis. |
| `likely_cause`, `likely_cause_key` | A device-specific explanation or generic hint. A key may be absent for an integration’s own explanation. Hints are not confirmed findings; compare them with the exact error and route facts. |
| `attempt`, `attempts`, `retrying`, `timed_out` | Attempt context if the integration records it. A retried failure is distinct from the final failure. These fields do not promise configurable retry/timeout options. |
| `via`, `via_type` | Radio used for the link when resolvable; type may be `proxy` or `adapter`. Read with `via_unconfirmed`. |
| `via_unconfirmed` | `true` means the displayed route is not confirmed from the actual link and may be based on the scanner hearing the strongest advertisement. Do not call it a proven connection route. |
| `rssi` | Signal observed by the available radio at that observation; it is not an end-to-end connectivity measurement. Compare trends on the same route. No universal RSSI threshold guarantees operation. |
| `paths` | Connectable radios currently seeing the device, not spare connection slots. A value of 1 offers no alternate visible route, but does not itself indicate a fault. |
| `advertised_via` | Strongest advertising scanner when different from the selected link route. Strongest advertisement and actual connection may differ. |
| `<stage>_s`, `reused`, `disconnect_error` | Recorded timings, possible link reuse, and cleanup error. Only recorded stages appear; compare equivalent operations. |

When an integration uses `SessionReports`, `last` is the most recent recorded attempt, `last_failure` retains the most recent final failure, and `last_retry` retains a failed attempt followed by another try. A later success does not erase those historical slots. Check timestamps and operation context; an old failure beside a current success is not a new outage. The integration decides which reports and timestamps to expose. Library details are in the [report contract](contract.md); reading the developer contract is optional for using this guide.

## Restore the affected path

Choose the checks matching the evidence rather than restarting everything at once.

1. **Device:** restore stable power and the documented wake-up/ready mode. Verify the model’s safe state after an interrupted operation. Follow its instructions for firmware, keys and authentication. Resetting, removing bonds or re-pairing may lose required state and is appropriate only when the model guide calls for it.
2. **Other connection owner:** close a competing app/gateway where the device allows only one owner, then allow its documented disconnection interval. Stop overlapping commands from automations. Do not assume all devices accept a second connection.
3. **Adapter or proxy:** confirm the route is online and supports the operation. For a local adapter, follow the HA guide for host access, USB interference or container/VM passthrough as applicable. For a proxy, inspect ESPHome connectivity and logs; check power and unused persistent connections. Restart only the affected route if it remains unresponsive, allowing for other devices it serves.
4. **Network, for proxies:** check the proxy’s actual Ethernet/Wi-Fi path, IP reachability and API connection. Ethernet firmware requires Ethernet. Check the configured API key/port if ESPHome itself is disconnected; a device’s BLE authentication key is a separate credential. See [HA ESPHome registration](https://www.home-assistant.io/integrations/esphome/).
5. **Verify:** perform one safe operation using the integration’s instructions, compare its outcome and route with the earlier failure, and check recovery of normal updates. Do not continuously resend writes or apply another integration’s retry/pacing settings.

Proximity and stable power/network can help isolate radio problems. Keep the receiver away from metal enclosures and interfering equipment. Multiple radios, automatic failover, bonding location and kept connections depend on the installed integration/device/backend; this guide does not guarantee them.

## Diagnostics and logs

Start with the integration’s existing troubleshooting/diagnostics instructions. Record HA and integration versions, device model/firmware, adapter or proxy model/revision, ESPHome version where applicable, the route and failure time with timezone. Include whether advertisements are visible and whether actual operations complete. Gather only the failing interval and a nearby successful attempt if available.

If the integration provides **Enable debug logging** on its integration entry, enable it for reproduction and disable it afterward. Otherwise use its documented logger configuration; the integration logger commonly follows its domain but must be checked. blesession uses loggers under `blesession` (for example `blesession.hass` and `blesession.report`); enabling only that namespace may not include the integration’s protocol logs. ESPHome device logs are separate. Do not enable every Bluetooth logger indefinitely or claim logs exist where the integration does not provide them.

Before sharing, remove Bluetooth/MAC addresses, device identifiers, private hostnames/IPs, Wi-Fi credentials, API encryption keys, authentication/bonding keys, access tokens, personal measurements and names. Diagnostics may be redacted automatically, but inspect attachments and surrounding logs yourself. Keep consistent anonymous labels if comparing routes.

## If the problem persists

Return to the integration’s device-specific troubleshooting page and its existing Issues/support link. Add the relevant sanitized error/report and context to that integration’s existing reporting process; do not create a second generic report or assume every Bluetooth fault belongs in the blesession library tracker. State what you actually tested and what remains unverified. Maintainers can identify a shared-library problem and route it onward when appropriate.

Official setup references checked **2026-10-18**. Stage and report interpretations were checked against this repository’s code and [contract](contract.md). This documentation work did not test physical devices or compile/flash firmware.
