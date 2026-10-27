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

Solem **BLNR** uses a separate BLE protocol and is **not** interchangeable with
the standard BL-IP services. A preliminary implementation provides only:

- `solem_toolkit.blnr_turn_on` — enable the controller (does **not** start watering).
- `solem_toolkit.blnr_turn_off_one_day` — disable the controller for one day.
  **Warning:** this can prevent scheduled irrigation during that period.

Both services take `device_mac`. They require a connectable Home Assistant Bluetooth
adapter or active BLE proxy. They are intentionally **not** used by Smart Water
Controller's BL-IP irrigation Start/Stop commands. Neither manual station watering,
program execution, nor a general-purpose BLNR Stop command has been reverse-engineered.

The command bytes, proprietary GATT UUIDs and commit were taken from the hardware
capture supplied in [legacy issue #19](https://github.com/hcraveiro/Home-Assistant-Solem-Bluetooth-Watering-Controller/issues/19).
Unit tests mock BLE transactions; **physical behavior and response validation must
still be checked on a BLNR controller** before calling the device supported.

Test carefully in a controlled period when watering interruption is acceptable.
Missing/invalid replies are reported as an *unknown outcome*; the Toolkit never
automatically replays these commands.
