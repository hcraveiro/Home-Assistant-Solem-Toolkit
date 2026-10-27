"""Compatibility tests for optional model/version service routing."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.solem_toolkit.api import SolemAPI
from custom_components.solem_toolkit.blnr import BLNRAPI
from custom_components.solem_toolkit.services import (
    _get_api,
    async_turn_off_x_days,
    async_turn_on,
)

MAC = "AA:BB:CC:DD:EE:FF"


def call(**fields):
    return SimpleNamespace(data={"device_mac": MAC, **fields})


def test_old_service_call_defaults_to_bl_ip():
    api = _get_api(SimpleNamespace(), call(), "turn_on")
    assert type(api) is SolemAPI


def test_version_does_not_change_legacy_protocol():
    api = _get_api(SimpleNamespace(), call(version="5.1.7"), "turn_on")
    assert type(api) is SolemAPI


def test_explicit_bl_ip_still_uses_legacy_protocol():
    assert type(_get_api(SimpleNamespace(), call(model="bl_ip"), "turn_on")) is SolemAPI


def test_blnr_model_routes_to_separate_api():
    assert type(_get_api(SimpleNamespace(), call(model="bl_nr", version="1"), "turn_on")) is BLNRAPI


@pytest.mark.parametrize("action", [
    "sprinkle_all_stations_for_y_minutes",
    "run_program_x",
])
def test_unverified_blnr_commands_are_rejected(action):
    with pytest.raises(HomeAssistantError, match="does not support"):
        _get_api(SimpleNamespace(), call(model="bl_nr"), action)


@pytest.mark.parametrize("days", [0, 256, -1, "bad", 1.5, True])
def test_blnr_rejects_unverified_off_durations(days):
    with pytest.raises(HomeAssistantError, match="BLNR days"):
        _get_api(SimpleNamespace(), call(model="bl_nr", days=days), "turn_off_x_days")


def test_blnr_accepts_one_day_integer_or_string():
    for days in (1, "1"):
        assert isinstance(
            _get_api(SimpleNamespace(), call(model="bl_nr", days=days), "turn_off_x_days"),
            BLNRAPI,
        )


@pytest.mark.parametrize("model", ["unknown", "", None, 123])
def test_unknown_models_do_not_fall_back_silently(model):
    with pytest.raises(HomeAssistantError, match="Unsupported model"):
        _get_api(SimpleNamespace(), call(model=model), "turn_on")


@pytest.mark.asyncio
async def test_existing_turn_on_routes_to_blnr(monkeypatch):
    sent = AsyncMock()
    monkeypatch.setattr(BLNRAPI, "turn_on", sent)
    await async_turn_on(SimpleNamespace(), call(model="bl_nr"))
    sent.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_one_day_disable_routes_to_blnr(monkeypatch):
    sent = AsyncMock()
    monkeypatch.setattr(BLNRAPI, "turn_off_x_days", sent)
    await async_turn_off_x_days(SimpleNamespace(), call(model="bl_nr", days=10))
    sent.assert_awaited_once_with(10)


def test_blnr_read_actions_are_allowed():
    for action in ("read_status", "read_info_block", "read_station_config"):
        assert isinstance(
            _get_api(SimpleNamespace(), call(model="bl_nr"), action),
            BLNRAPI,
        )


@pytest.mark.asyncio
async def test_blnr_status_uses_raw_query(monkeypatch):
    from custom_components.solem_toolkit.services import async_read_status

    read = AsyncMock(return_value={"response_hex": "f80300000000"})
    monkeypatch.setattr(BLNRAPI, "read_raw", read)
    result = await async_read_status(SimpleNamespace(), call(model="bl_nr"))
    assert result["response_hex"] == "f80300000000"
    read.assert_awaited_once_with(0xF7)


@pytest.mark.parametrize("legacy_name", ["blnr", "blip"])
def test_legacy_model_spelling_is_rejected_explicitly(legacy_name):
    with pytest.raises(HomeAssistantError, match="Unsupported model"):
        _get_api(SimpleNamespace(), call(model=legacy_name), "turn_on")


@pytest.mark.asyncio
async def test_legacy_read_status_without_model_keeps_bl_ip_response(monkeypatch):
    from custom_components.solem_toolkit import services

    raw_status = object()
    read = AsyncMock(return_value=raw_status)
    monkeypatch.setattr(SolemAPI, "read_status", read)
    monkeypatch.setattr(
        services.protocol,
        "as_event_data",
        lambda status: {"state": "idle"} if status is raw_status else {},
    )

    result = await services.async_read_status(SimpleNamespace(), call())
    assert result == {"state": "idle", "device_mac": MAC}
    read.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("opcode,action,default_index", [
    (0x01, "read_info_block", 1),
    (0x09, "read_station_config", 2),
])
async def test_blnr_indexed_read_default_matches_service_ui(
    monkeypatch, opcode, action, default_index
):
    from custom_components.solem_toolkit.services import _async_blnr_indexed_read

    read = AsyncMock(return_value={"valid_frame": True})
    monkeypatch.setattr(BLNRAPI, "read_raw", read)
    result = await _async_blnr_indexed_read(
        SimpleNamespace(), call(model="bl_nr"), opcode, action
    )
    assert result == {"valid_frame": True}
    read.assert_awaited_once_with(opcode, default_index)


@pytest.mark.asyncio
@pytest.mark.parametrize("opcode,action", [
    (0x01, "read_info_block"),
    (0x09, "read_station_config"),
])
async def test_blnr_only_read_rejects_bl_ip_without_sending_command(
    monkeypatch, opcode, action
):
    from custom_components.solem_toolkit.services import _async_blnr_indexed_read

    read = AsyncMock()
    monkeypatch.setattr(BLNRAPI, "read_raw", read)
    with pytest.raises(HomeAssistantError, match="only for model bl_nr"):
        await _async_blnr_indexed_read(
            SimpleNamespace(), call(), opcode, action
        )
    read.assert_not_awaited()



@pytest.mark.asyncio
@pytest.mark.parametrize("minutes", [1, 2, 10, 255])
async def test_blnr_manual_service_routes_configurable_duration(monkeypatch, minutes):
    from custom_components.solem_toolkit.services import async_sprinkle_station_x_for_y_minutes

    sent = AsyncMock()
    monkeypatch.setattr(BLNRAPI, "sprinkle_station_x_for_y_minutes", sent)
    await async_sprinkle_station_x_for_y_minutes(
        SimpleNamespace(), call(model="bl_nr", station=1, minutes=str(minutes))
    )
    sent.assert_awaited_once_with(1, minutes)


@pytest.mark.parametrize("fields", [
    {"minutes": 0}, {"minutes": 256}, {"minutes": 1.5},
    {"minutes": True}, {"station": 2}, {"station": "bad"},
])
def test_blnr_invalid_manual_parameters_rejected_before_transport(fields):
    with pytest.raises(HomeAssistantError, match="BLNR"):
        _get_api(SimpleNamespace(), call(model="bl_nr", **fields), "sprinkle_station_x_for_y_minutes")


@pytest.mark.asyncio
async def test_blnr_stop_service_routes_to_blnr(monkeypatch):
    from custom_components.solem_toolkit.services import async_stop_manual_sprinkle

    sent = AsyncMock()
    monkeypatch.setattr(BLNRAPI, "stop_manual_sprinkle", sent)
    await async_stop_manual_sprinkle(SimpleNamespace(), call(model="bl_nr"))
    sent.assert_awaited_once()


@pytest.mark.asyncio
async def test_blnr_permanent_disable_routes_to_blnr(monkeypatch):
    from custom_components.solem_toolkit.services import async_turn_off_permanent

    sent = AsyncMock()
    monkeypatch.setattr(BLNRAPI, "turn_off_permanent", sent)
    await async_turn_off_permanent(SimpleNamespace(), call(model="bl_nr"))
    sent.assert_awaited_once()
