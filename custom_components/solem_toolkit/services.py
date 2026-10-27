"""Service handlers for Solem Toolkit."""

from __future__ import annotations

import logging

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError

from . import protocol
from .api import APIConnectionError, SolemAPI
from .blnr import BLNRAPI
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
    _LOGGER.info("Solem %s - Service call: list_characteristics", device_mac)
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        result = await api.list_characteristics()
        # Log output so the user can read it from HA logs.
        for svc_uuid, chars in result.items():
            _LOGGER.info("Service: %s", svc_uuid)
            for c in chars:
                _LOGGER.info("  Characteristic: %s (properties=%s)",
                             c["uuid"], c["properties"])
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def async_turn_off_permanent(hass: HomeAssistant, call: ServiceCall) -> None:
    device_mac = call.data.get("device_mac")
    _LOGGER.info("Solem %s - Service call: turn_off_permanent", device_mac)
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        await api.turn_off_permanent()
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def async_turn_off_x_days(hass: HomeAssistant, call: ServiceCall) -> None:
    device_mac = call.data.get("device_mac")
    days = int(call.data.get("days", 1))
    _LOGGER.info(
        "Solem %s - Service call: turn_off_x_days (days=%s)", device_mac, days)
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        await api.turn_off_x_days(days)
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def async_turn_on(hass: HomeAssistant, call: ServiceCall) -> None:
    device_mac = call.data.get("device_mac")
    _LOGGER.info("Solem %s - Service call: turn_on", device_mac)
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        await api.turn_on()
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def async_sprinkle_station_x_for_y_minutes(hass: HomeAssistant, call: ServiceCall) -> None:
    device_mac = call.data.get("device_mac")
    station = int(call.data.get("station", 1))
    minutes = int(call.data.get("minutes", 1))
    _LOGGER.info(
        "Solem %s - Service call: sprinkle_station_x_for_y_minutes (station=%s, minutes=%s)",
        device_mac,
        station,
        minutes,
    )
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        await api.sprinkle_station_x_for_y_minutes(station, minutes)
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def async_sprinkle_all_stations_for_y_minutes(hass: HomeAssistant, call: ServiceCall) -> None:
    device_mac = call.data.get("device_mac")
    minutes = int(call.data.get("minutes", 1))
    _LOGGER.info(
        "Solem %s - Service call: sprinkle_all_stations_for_y_minutes (minutes=%s)",
        device_mac,
        minutes,
    )
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        await api.sprinkle_all_stations_for_y_minutes(minutes)
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def async_run_program_x(hass: HomeAssistant, call: ServiceCall) -> None:
    device_mac = call.data.get("device_mac")
    program = int(call.data.get("program", 1))
    _LOGGER.info(
        "Solem %s - Service call: run_program_x (program=%s)", device_mac, program)
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        await api.run_program_x(program)
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def async_stop_manual_sprinkle(hass: HomeAssistant, call: ServiceCall) -> None:
    device_mac = call.data.get("device_mac")
    _LOGGER.info("Solem %s - Service call: stop_manual_sprinkle", device_mac)
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        await api.stop_manual_sprinkle()
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def async_read_status(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Read the device status, and return it to the caller.

    A service rather than a sensor on purpose, for now: the only way to read
    this device is to open a BLE connection, so a polling sensor would compete
    with the commands for the radio. This gives the reading on demand, which is
    how the battery encoding was pinned down: one reading a day for eighteen
    days, against the recorder, with no irrigation needed to trigger it.

    The reading is also fired on the event bus - by `api.py`, for every command
    and not just this one - which is how it reaches a dashboard. The returned
    response is the second half: a script that calls this service can read the
    value directly with `response_variable`, without waiting for an event.
    """
    device_mac = call.data.get("device_mac")
    _LOGGER.info("Solem %s - Service call: read_status", device_mac)
    api = SolemAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        status = await api.read_status()
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc

    if status is None:
        # Reaching here means the device answered but with nothing we can
        # decode, which is worth a warning: it is the one outcome the capture
        # never showed, so it would be news.
        _LOGGER.warning(
            "Solem %s - The device replied but with no readable status.",
            device_mac,
        )
        return None

    data = protocol.as_event_data(status)
    data["device_mac"] = device_mac
    return data


async def async_blnr_control(hass: HomeAssistant, call: ServiceCall, *, enable: bool) -> None:
    """Execute one of the two captured BLNR commands (experimental)."""
    device_mac = call.data.get("device_mac")
    if not isinstance(device_mac, str) or not device_mac.strip():
        raise HomeAssistantError("device_mac is required")
    api = BLNRAPI(hass, device_mac, bluetooth_timeout=_get_timeout(call))
    try:
        if enable:
            await api.turn_on()
        else:
            await api.turn_off_one_day()
    except APIConnectionError as exc:
        raise HomeAssistantError(str(exc)) from exc


def async_setup_services(hass: HomeAssistant) -> None:
    async def _handle_blnr_on(call: ServiceCall) -> None:
        await async_blnr_control(hass, call, enable=True)

    async def _handle_blnr_off_one_day(call: ServiceCall) -> None:
        await async_blnr_control(hass, call, enable=False)

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

    async def _handle_read_status(call: ServiceCall) -> ServiceResponse:
        return await async_read_status(hass, call)

    hass.services.async_register(DOMAIN, "blnr_turn_on", _handle_blnr_on)
    hass.services.async_register(
        DOMAIN, "blnr_turn_off_one_day", _handle_blnr_off_one_day
    )
    hass.services.async_register(
        DOMAIN, "list_characteristics", _handle_list_characteristics)
    hass.services.async_register(
        DOMAIN, "turn_off_permanent", _handle_turn_off_permanent)
    hass.services.async_register(
        DOMAIN, "turn_off_x_days", _handle_turn_off_x_days)
    hass.services.async_register(DOMAIN, "turn_on", _handle_turn_on)
    hass.services.async_register(
        DOMAIN, "sprinkle_station_x_for_y_minutes", _handle_sprinkle_station)
    hass.services.async_register(
        DOMAIN, "sprinkle_all_stations_for_y_minutes", _handle_sprinkle_all)
    hass.services.async_register(DOMAIN, "run_program_x", _handle_run_program)
    hass.services.async_register(
        DOMAIN, "stop_manual_sprinkle", _handle_stop_manual)
    # OPTIONAL rather than ONLY: the service stays callable from an automation
    # that just wants the event fired and the log line written, without having
    # to ask for a response it would then ignore.
    hass.services.async_register(
        DOMAIN,
        "read_status",
        _handle_read_status,
        supports_response=SupportsResponse.OPTIONAL,
    )
