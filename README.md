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

There is no configuration beyond the toolkit setup. Specify the controller MAC address in each service call. Unless otherwise indicated, irrigation control services support BL-IP; the BL-NR subset is documented below:
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

## Partial BL-NR support (hardware validated)

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

BL-NR supports these additional experimental operations:

| Service | Parameters | Hardware evidence |
| --- | --- | --- |
| `turn_off_x_days` | `days: 1..255` | One day physically verified; other day values inferred from the parameter byte |
| `sprinkle_station_x_for_y_minutes` | `station: 1`, `minutes: 1..255` | App capture confirms one minute; other durations use the inferred duration byte |
| `stop_manual_sprinkle` | No duration | App capture confirms stop about 11 seconds after start |

The 255 limit is a conservative encoding bound, not a verified device limit.
Higher duration bytes and other station IDs are not enabled without captures.
Permanent disable, all-station watering and program execution remain unsupported.
Read-only information, station configuration and raw status queries remain available.

```yaml
action: solem_toolkit.sprinkle_station_x_for_y_minutes
data:
  device_mac: "AA:BB:CC:DD:EE:FF"
  model: bl_nr
  station: 1
  minutes: 2
```

Use `solem_toolkit.stop_manual_sprinkle` with the same MAC and model to stop.
For a suspension, use `solem_toolkit.turn_off_x_days` with configurable `days`.
Validate inferred values against the app and physical controller before relying on them.

BLNR has different UUIDs, checksum, frames and commit behavior from BL-IP.
The original commands were documented in [legacy issue #19](https://github.com/hcraveiro/Home-Assistant-Solem-Bluetooth-Watering-Controller/issues/19).
A new app HCI capture on 2026-10-27 confirms these frames:

| Action | Command | Control reply | Commit reply |
| --- | --- | --- | --- |
| Water one minute | `0504000a0001001400` | `06060242240001600fe400` | `0e060042240001600fea00` |
| Stop manual watering | `050400900000009900` | `060602400000016010bf00` | `0e060040000001600fc400` |

Manual operations await a valid control reply before sending `0d00000d00`,
then await a valid commit reply, matching the captured app sequence.
No control command is replayed automatically after a write, including on timeout.
An acknowledgement is not independent proof of physical watering state.
Other firmware variants remain unverified. Bluetooth connections stay on demand.

### BL-NR experimental read-only queries

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
but does not disclose the underlying BLE opcodes. Manual watering and stopping
frames are now documented from the app capture above.

Other protocol findings in the original capture:

- `0x03` writes the controller date/time; it is not implemented because
  the write layout is not fully established.
- `0x05` with `0xA0` and a one-day parameter has been physically
  verified; other values (multi-day disable) remain **unconfirmed**.
- `0x0D` commits control commands. Read-only queries **do not** send a commit.
- Read status `0xF8` is not decoded into battery voltage/percentage,
  watering state, or station activity without matching real measurements.

