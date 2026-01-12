"""Service handlers for Solem Toolkit."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from .api import APIConnectionError, SolemAPI
from .const import DEFAULT_BLUETOOTH_TIMEOUT, DOMAIN, MIN_BLUETOOTH_TIMEOUT

_LOGGER = logging.getLogger(__name__)


def _get_timeout(call: ServiceCall) -> int:
    timeout = call.data.get("bluetooth_timeout", DEFAULT_BLUETOOTH_TIMEOUT)
    try:
        timeout_int = int(timeout)
    except (TypeError, ValueError) as exc:
        raise HomeAssistantError("Invalid bluetooth_timeout") from exc
    return max(MIN_BLUETOOTH_TIMEOUT, timeout_int)


async def async_list_characteristics(hass: HomeAssistant, call: ServiceCall) -> None:
    device_mac = call.data.get("device_mac")
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        result = await api.list_characteristics()
        # Log output so the user can read it from HA logs.
        for svc_uuid, chars in result.items():
            _LOGGER.info("Service: %s", svc_uuid)
            for c in chars:
                _LOGGER.info("  Characteristic: %s (properties=%s)", c["uuid"], c["properties"])
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def async_turn_off_permanent(hass: HomeAssistant, call: ServiceCall) -> None:
    device_mac = call.data.get("device_mac")
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        await api.turn_off_permanent()
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def async_turn_off_x_days(hass: HomeAssistant, call: ServiceCall) -> None:
    device_mac = call.data.get("device_mac")
    days = int(call.data.get("days", 1))
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        await api.turn_off_x_days(days)
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def async_turn_on(hass: HomeAssistant, call: ServiceCall) -> None:
    device_mac = call.data.get("device_mac")
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        await api.turn_on()
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def async_sprinkle_station_x_for_y_minutes(hass: HomeAssistant, call: ServiceCall) -> None:
    device_mac = call.data.get("device_mac")
    station = int(call.data.get("station", 1))
    minutes = int(call.data.get("minutes", 1))
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        await api.sprinkle_station_x_for_y_minutes(station, minutes)
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def async_sprinkle_all_stations_for_y_minutes(hass: HomeAssistant, call: ServiceCall) -> None:
    device_mac = call.data.get("device_mac")
    minutes = int(call.data.get("minutes", 1))
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        await api.sprinkle_all_stations_for_y_minutes(minutes)
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def async_run_program_x(hass: HomeAssistant, call: ServiceCall) -> None:
    device_mac = call.data.get("device_mac")
    program = int(call.data.get("program", 1))
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        await api.run_program_x(program)
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def async_stop_manual_sprinkle(hass: HomeAssistant, call: ServiceCall) -> None:
    device_mac = call.data.get("device_mac")
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        await api.stop_manual_sprinkle()
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


def async_setup_services(hass: HomeAssistant) -> None:
    async def _handle_list_characteristics(call: ServiceCall) -> None:
        await async_list_characteristics(hass, call)

    async def _handle_turn_off_permanent(call: ServiceCall) -> None:
        await async_turn_off_permanent(hass, call)

    async def _handle_turn_off_x_days(call: ServiceCall) -> None:
        await async_turn_off_x_days(hass, call)

    async def _handle_turn_on(call: ServiceCall) -> None:
        await async_turn_on(hass, call)

    async def _handle_sprinkle_station(call: ServiceCall) -> None:
        await async_sprinkle_station_x_for_y_minutes(hass, call)

    async def _handle_sprinkle_all(call: ServiceCall) -> None:
        await async_sprinkle_all_stations_for_y_minutes(hass, call)

    async def _handle_run_program(call: ServiceCall) -> None:
        await async_run_program_x(hass, call)

    async def _handle_stop_manual(call: ServiceCall) -> None:
        await async_stop_manual_sprinkle(hass, call)

    hass.services.async_register(DOMAIN, "list_characteristics", _handle_list_characteristics)
    hass.services.async_register(DOMAIN, "turn_off_permanent", _handle_turn_off_permanent)
    hass.services.async_register(DOMAIN, "turn_off_x_days", _handle_turn_off_x_days)
    hass.services.async_register(DOMAIN, "turn_on", _handle_turn_on)
    hass.services.async_register(DOMAIN, "sprinkle_station_x_for_y_minutes", _handle_sprinkle_station)
    hass.services.async_register(DOMAIN, "sprinkle_all_stations_for_y_minutes", _handle_sprinkle_all)
    hass.services.async_register(DOMAIN, "run_program_x", _handle_run_program)
    hass.services.async_register(DOMAIN, "stop_manual_sprinkle", _handle_stop_manual)
