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
    assert type(_get_api(SimpleNamespace(), call(model="blnr", version="1"), "turn_on")) is BLNRAPI


@pytest.mark.parametrize("action", [
    "stop_manual_sprinkle",
    "sprinkle_station_x_for_y_minutes",
    "sprinkle_all_stations_for_y_minutes",
    "run_program_x",
    "turn_off_permanent",
])
def test_unverified_blnr_commands_are_rejected(action):
    with pytest.raises(HomeAssistantError, match="does not yet support"):
        _get_api(SimpleNamespace(), call(model="blnr"), action)


@pytest.mark.parametrize("days", [0, 2, 15, "3"])
def test_blnr_rejects_unverified_off_durations(days):
    with pytest.raises(HomeAssistantError, match="only one-day"):
        _get_api(SimpleNamespace(), call(model="blnr", days=days), "turn_off_x_days")


def test_blnr_accepts_one_day_integer_or_string():
    for days in (1, "1"):
        assert isinstance(
            _get_api(SimpleNamespace(), call(model="blnr", days=days), "turn_off_x_days"),
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
    await async_turn_on(SimpleNamespace(), call(model="blnr"))
    sent.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_one_day_disable_routes_to_blnr(monkeypatch):
    sent = AsyncMock()
    monkeypatch.setattr(BLNRAPI, "turn_off_one_day", sent)
    await async_turn_off_x_days(SimpleNamespace(), call(model="blnr", days=1))
    sent.assert_awaited_once()


def test_blnr_read_actions_are_allowed():
    for action in ("read_status", "read_info_block", "read_station_config"):
        assert isinstance(
            _get_api(SimpleNamespace(), call(model="blnr"), action),
            BLNRAPI,
        )


@pytest.mark.asyncio
async def test_blnr_status_uses_raw_query(monkeypatch):
    from custom_components.solem_toolkit.services import async_read_status

    read = AsyncMock(return_value={"response_hex": "f80300000000"})
    monkeypatch.setattr(BLNRAPI, "read_raw", read)
    result = await async_read_status(SimpleNamespace(), call(model="blnr"))
    assert result["response_hex"] == "f80300000000"
    read.assert_awaited_once_with(0xF7)
