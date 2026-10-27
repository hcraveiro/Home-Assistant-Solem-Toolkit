# Home Assistant Solem Toolkit Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/hcraveiro/Home-Assistant-Solem-Toolkit.svg)](https://github.com/hcraveiro/Home-Assistant-Solem-Toolkit/releases/)

Integrate Solem Bluetooth watering controllers into Home Assistant. BL-IP has established command support; BL-NR enable and one-day disable have been validated on real hardware, with experimental read-only diagnostics. 

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
* read_status - Read decoded BL-IP status or raw BL-NR status (`model: bl_nr`)
* read_info_block - Read an indexed BL-NR device information block (`model: bl_nr` only)
* read_station_config - Read an indexed BL-NR station/configuration block (`model: bl_nr` only)

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
BLE response as `response_hex`, with a `valid_frame` flag and a raw
`trailer_hex`. Physical BL-NR captures demonstrate that info (`0x02`) and
station (`0x0A`) replies can end in non-zero trailer bytes (`0x02` and `0x03`),
while status (`0xF8`) ends in `0x00`. The checksum and declared payload length
validate on all three captures; the semantics of the trailer remain unknown.
Do not interpret
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

### Protocol research and known limitations

The BL-NR protocol's proprietary GATT service (`a3eb0001`), command
characteristic (`a3eb0002`), notification characteristic (`a3eb0003`)
and the control/read opcodes were documented by the community in
[the original BLE capture](https://github.com/hcraveiro/Home-Assistant-Solem-Bluetooth-Watering-Controller/issues/19).

Hardware observations from a BL-NR controller:

| Request | Captured result | Validation |
| --- | --- | --- |
| `0x01` index 1 | ASCII `BLNR-D3F701`, reply `0x02`, trailer `0x02` | Checksum + length pass |
| `0x09` index 2 | ASCII `Station 1`, reply `0x0A`, trailer `0x03` | Checksum + length pass |
| `0xF7` status | `f80300000000fb00`, reply `0xF8`, trailer `0x00` | Checksum + length pass |

SOLEM's [official BL-NR product page](https://solem-irrigation.com/produit/bl-nr/)
documents a single station, 9V battery, MySOLEM control and Eco Mode.
[Installation guidance](https://manuals.plus/solem/bl-nr-bluetooth-watering-controller-manual)
also describes pairing, programming and manual operation in the app,
but does not disclose the underlying BLE opcodes. No BL-NR manual
watering or stopping packets have been independently captured yet.

Other protocol findings in the original capture:

- `0x03` writes the controller date/time; it is not implemented because
  the write layout is not fully established.
- `0x05` with `0xA0` and a one-day parameter has been physically
  verified; other values (multi-day disable) remain **unconfirmed**.
- `0x0D` commits control commands. Read-only queries **do not** send a commit.
- Read status `0xF8` is not decoded into battery voltage/percentage,
  watering state, or station activity without matching real measurements.
