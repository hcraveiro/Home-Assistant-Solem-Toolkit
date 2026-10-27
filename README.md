# Home Assistant Solem Toolkit Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/hcraveiro/Home-Assistant-Solem-Toolkit.svg)](https://github.com/hcraveiro/Home-Assistant-Solem-Toolkit/releases/)

Integrate Solem Watering Bluetooth Controllers (only tested in BL-IP) into your Home Assistant. This Integration is meant to only provide services for Home Assistant to control irrigation using your BL-IP controller. 

- [Home Assistant Solem Toolkit Integration](#home-assistant-solem-toolkit-integration)
    - [Installation](#installation)
    - [Services](#services)
    - [FAQ](#faq)
    - [Credits](#credits)

## Installation

This integration can be added as a custom repository in HACS and from there you can install it.

When the integration is installed in HACS, you need to put on configuration.yaml:

```yaml
solem_toolkit:
```
Then you can restart Home Assistant and the services from Solem Toolkit will be available.

## Services

There is no configuration, you only need to use the provided services. They are self-explanatory:
* list_characteristics - List the services and its characteristics
* turn_off_permanent - Turn off the Sprinkler permanently
* turn_off_x_days - Turn off the Sprinkler for X days
* turn_on - Turn the Sprinkler on
* sprinkle_station_x_for_y_minutes - Sprinkle station X (number starting from 1) for Y minutes (integer)
* sprinkle_all_stations_for_y_minutes - Sprinkle all stations for Y minutes (integer)
* run_program_x - Run program X
* stop_manual_sprinkle - Stop the Sprinkler if it is sprinkling

## FAQ

### Can I configure the MAC address of the controller?

No, as this is 'just' a toolkit you need to provide it to every service. I plan to have a different Integration that will use this toolkit that will take care of that.

## Credits

A big thank you to [pcman75](https://github.com/pcman75) for doing a [reverse engineering](https://github.com/pcman75/solem-blip-reverse-engineering) on Solem controllers which helped me a lot. 

## Experimental BLNR support

All existing services remain unchanged. They now accept two **optional** fields:

- `model`: defaults to `bl_ip` when omitted, preserving existing automations.
  Use `bl_nr` to select the experimental BLNR transport.
- `version`: optional firmware/model version string. Currently informational;
  specifying it never changes protocol behavior.

For instance, enable a BLNR controller using the existing service:

```yaml
action: solem_toolkit.turn_on
data:
  device_mac: "AA:BB:CC:DD:EE:FF"
  model: bl_nr
```

`solem_toolkit.turn_off_x_days` supports `model: bl_nr` only with
`days: 1`, based on a captured physical command. This can suppress
scheduled irrigation for one day. Other BLNR operations such as manual watering, Stop, permanent disable and program execution remain unsupported. Read-only information, station configuration and raw status queries are available experimentally.

BLNR has different UUIDs, checksum, frames and commit behavior from BL-IP.
The captured commands were documented in [legacy issue #19](https://github.com/hcraveiro/Home-Assistant-Solem-Bluetooth-Watering-Controller/issues/19).
Mocked tests are not a substitute for physical-device validation. Use a
connectable BLE adapter/proxy, test when temporary watering interruption is
acceptable, and do not equate command acknowledgement with verified state.

### BLNR experimental read-only queries

Firmware interpretation remains unverified. All three operations return the raw
BLE response as `response_hex`, with a `valid_frame` flag. Do not interpret
the raw `0xf8` payload as a battery percentage or controller state yet.

```yaml
action: solem_toolkit.read_info_block
data:
  device_mac: "AA:BB:CC:DD:EE:FF"
  model: bl_nr
  index: 1
```

```yaml
action: solem_toolkit.read_station_config
data:
  device_mac: "AA:BB:CC:DD:EE:FF"
  model: bl_nr
  index: 2
```

```yaml
action: solem_toolkit.read_status
data:
  device_mac: "AA:BB:CC:DD:EE:FF"
  model: bl_nr
```

For BL-IP, `read_status` preserves its existing decoded response and event.
BLNR status intentionally does not fire a BL-IP-shaped status event.
All BLNR read requests are sent **without** a control COMMIT.
