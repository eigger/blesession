# Bluetooth proxy setup and recommended devices

**English** | [한국어](bluetooth.ko.md)

A Bluetooth proxy places a Bluetooth receiver near your device and connects it to Home Assistant over your home network. You can keep using a working local Bluetooth adapter; a proxy is useful when the Home Assistant host is too far away. Installing a custom integration alone does not configure a proxy.

This user guide also applies to integrations that do not use blesession. You do not need to install the Python library to use the guide or configure a proxy. Follow your device integration’s instructions for its own setup, authentication and support.

## 1. Choose a proxy

Our default recommendation is **Seeed Studio XIAO W5500 Ethernet Adapter V1.2** for wired Ethernet, PoE and an external antenna. For a **USB-A dongle form factor**, choose **M5Stack AtomS3U**. AtomS3U has a USB-A connector and connects to your network over Wi-Fi.

| Device | Before buying | Purchase |
|---|---|---|
| **XIAO W5500 Ethernet Adapter V1.2** | Verify V1.2, the included XIAO module/antenna and PoE power options in the selected bundle. | [AliExpress](https://ko.aliexpress.com/item/1005009310322353.html) |
| **M5Stack AtomS3U** | USB-A dongle; needs USB power and Wi-Fi. Requires a custom ESPHome build as described below. | [AliExpress](https://ko.aliexpress.com/item/1005005643544014.html) |

For the XIAO W5500, ESPHome’s [official installer](https://esphome.io/projects/?type=bluetooth) supports **V1.2 only**; older revisions have wiring/RF limitations. Check the [Seeed board guide](https://wiki.seeedstudio.com/esphome_xiao_w5500_ethernet_adapter/) and [AtomS3U specifications](https://docs.m5stack.com/en/core/AtomS3U) against the product you order. Marketplace links do not guarantee the selected revision or accessories. Recommendations are not hardware validation of every integration/device combination.

## 2. Install proxy firmware

Flashing replaces the firmware on the proxy. Save any existing configuration first. Choose the instructions for your exact device.

### XIAO W5500 V1.2: official installer

1. Connect the board to your computer using a USB data cable.
2. Open the [ESPHome Bluetooth installer](https://esphome.io/projects/?type=bluetooth) in Chrome or Edge, select **Seeed Studio XIAO W5500 Ethernet Adapter V1.2**, and connect to its serial port. Follow the installation prompts.
3. Connect Ethernet to your home network and use the power method supported by your board/bundle. The installed Ethernet firmware disables Wi-Fi; it needs a wired network connection.
4. Continue with Home Assistant registration below. For download-mode or board-specific instructions, use the [Seeed guide](https://wiki.seeedstudio.com/esphome_xiao_w5500_ethernet_adapter/).

### AtomS3U: build with ESPHome Device Builder

AtomS3U is not a named target in the official proxy installer. Its **Generic ESP32** image targets a different chip and must not be flashed to this ESP32-S3 device.

1. Install/open [ESPHome Device Builder](https://esphome.io/install/getting-started/) and create a new ESP32-S3 device named `atoms3u-proxy`. Retain the Wi-Fi credentials and API encryption key generated during setup.
2. Edit its configuration. The minimal example below uses the AtomS3U’s ESP32-S3 and 8 MB flash; it does not enable its microphone, LED or infrared functions. Put your Wi-Fi credentials and generated API key in the Device Builder’s `secrets.yaml` under the three names shown. Use your generated key, not the placeholder. If you need a new key, run `openssl rand -base64 32` in a terminal and use its output for `atoms3u_api_key`. Keep this key private.

```yaml
esphome:
  name: atoms3u-proxy
  friendly_name: AtomS3U Bluetooth Proxy

esp32:
  variant: esp32s3
  flash_size: 8MB
  framework:
    type: esp-idf

logger:

api:
  encryption:
    key: !secret atoms3u_api_key

ota:
  - platform: esphome

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

esp32_ble_tracker:

bluetooth_proxy:
  active: true
```

`secrets.yaml` entries:

```yaml
wifi_ssid: "YOUR_WIFI_NAME"
wifi_password: "YOUR_WIFI_PASSWORD"
atoms3u_api_key: "YOUR_GENERATED_BASE64_API_KEY"
```

3. Select **Validate**, then **Install**. For the first installation, connect AtomS3U to the computer’s USB-A port and choose the USB/“plug into this computer” installation route. If needed, download the firmware and upload it with [ESPHome Web](https://web.esphome.io/). Allow the browser to access the serial port. If the port is unavailable, follow [M5Stack’s AtomS3U download-mode instructions](https://docs.m5stack.com/en/arduino/m5atoms3u/program): hold the reset button for about two seconds until the green LED lights, then release it.
4. After flashing, power-cycle the device and check its Device Builder logs for a Wi-Fi connection and running API/Bluetooth proxy. Keep the API encryption key for the next step.

If adapting an old configuration, remove any `wifi.on_connect` action that calls `ble.disable`; it turns Bluetooth off after Wi-Fi connects. BLE provisioning (`esp32_improv`) is unnecessary when credentials are supplied in YAML; omit it from this dedicated proxy configuration. See [ESPHome BLE actions](https://esphome.io/components/esp32_ble/) and [BLE provisioning](https://esphome.io/components/esp32_improv/). The [M5Stack AtomS3U configuration](https://github.com/m5stack/esphome-yaml/blob/main/examples/kit/atoms3u.yaml) confirms the chip/flash/framework settings. The [ESP32 platform reference](https://esphome.io/components/esp32/) explains the chip/framework settings. This example is a configuration starting point, not a supplied or hardware-tested firmware image; validate it with your installed ESPHome version before flashing.

## 3. Add the proxy to Home Assistant

1. Keep the proxy and Home Assistant on a network where they can reach one another.
2. Open **Settings → Devices & services**. Configure the discovered ESPHome device. If it is not discovered, choose **Add integration → ESPHome** and enter the proxy’s IP address or hostname.
3. Enter the API encryption key when requested. This is the `api.encryption.key`, not the Wi-Fi password. The default native API port is **6053**.
4. Confirm the ESPHome device is connected. Then open the [Bluetooth integration](https://www.home-assistant.io/integrations/bluetooth/) and check that Bluetooth is configured. Add it through **Add integration → Bluetooth** if needed. A proxy does not require a USB Bluetooth adapter on the Home Assistant host.

The [Home Assistant ESPHome guide](https://www.home-assistant.io/integrations/esphome/) covers registration and connection options. Adding ESPHome registers the proxy; add your scale, printer or other device using its own integration afterward.

## 4. Check the device connection

Place the proxy near the BLE device, with stable power and network access. Wake the BLE device using its model’s instructions, then add/use its integration in Home Assistant. Close another phone app or gateway connection if the device allows only one connection.

Confirm that the expected entity updates or device command completes. Merely seeing a Bluetooth advertisement does not prove a read/write connection works. Integrations that connect to devices need `bluetooth_proxy.active: true` (GATT support). Tracker **active scanning** requests extra discovery data and is a separate setting; passive scanning does not itself prevent GATT connections. Keep scan timing and connection limits at their defaults initially. See [ESPHome proxy settings](https://esphome.io/components/bluetooth_proxy/) and [BLE scanning](https://esphome.io/components/esp32_ble_tracker/).

<a id="troubleshooting"></a>

## Troubleshooting

Use [common Bluetooth troubleshooting](troubleshooting.md) for stage diagnostics, route interpretation and recovery order, alongside the quick checks below and your device’s guide.

| Symptom | What to try |
|---|---|
| Proxy offline in Home Assistant | Check power and network first. For XIAO Ethernet firmware, connect Ethernet. Recheck its IP address/API key and ESPHome logs. |
| BLE device not discovered (`unreachable`) | Check battery/power, wake the device and move it close to the proxy. Follow the model’s pairing/discovery instructions. |
| Device found, connection fails (`connect`) | Check GATT support, close other connected apps/gateways, and follow the model’s authentication requirements. Discovery alone does not confirm a connection. |
| No connection slot (`connect.no_slot`) | Stop overlapping operations or unused long-lived connections. If needed, spread devices across another proxy. |
| Authentication/session fails | Check the exact model/firmware and required keys or pairing. Repeated retries do not correct a wrong key or unsupported model. |
| Transfer disconnects (`link_lost`) | Check device battery, proxy power/network and distance at the failure time. Restore the connection before retrying. |
| A write hangs (`write_timeout`) | Check proxy responsiveness and connection load. Restore the affected proxy/device, then follow the integration’s retry instructions. |
| No completion confirmation | Check whether the device already performed the operation before sending it again, especially for commands with physical effects. |

Error names and available diagnostics vary by integration. Use its own troubleshooting guide for model-specific steps, retry controls and logs. A proxy has finite connection capacity; increasing retries or scan intensity is not a general fix. Keep it close to the BLE device and away from network equipment or metal enclosures that interfere with reception.

### Reporting a problem

Include Home Assistant, integration and ESPHome versions; proxy/device model and revision; network method; failure time; and steps to reproduce. Include whether discovery works and whether the actual operation succeeds. If the integration offers diagnostics or debug logging, follow its instructions, collect the failing interval, then disable debug logging. A strong advertisement signal is not proof of which proxy handled the connection.

Before posting files publicly, remove Bluetooth/MAC addresses, authentication keys, tokens, internal addresses and personal measurements or names. Compare a successful attempt with the failed one when possible.

Official instructions checked **2026-10-18**. The documentation update did not include physical-device testing.
