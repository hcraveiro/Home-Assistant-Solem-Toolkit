"""Validate the BLE response rather than treating any notification as success."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.solem_toolkit.api import APIConnectionError, SolemAPI, _DEVICE_LOCKS

MAC = "AA:BB:CC:DD:EE:FF"


def status_frame(watering=False, station=0):
    payload = bytes([0x42 if watering else 0x40, 0, 0, 0, 0, 0, station, 90, 0, 0])
    return bytes([0x32, len(payload) + 1, 2]) + payload


@pytest.fixture
def setup_api(monkeypatch):
    _DEVICE_LOCKS.clear()
    api = SolemAPI(SimpleNamespace(data={}), MAC)
    monkeypatch.setattr("custom_components.solem_toolkit.api.COMMAND_ACK_TIMEOUT", 0.01)
    return api


def attach_frames(api, monkeypatch, frames):
    client = SimpleNamespace(is_connected=True)
    client.write_gatt_char = AsyncMock()
    client.stop_notify = AsyncMock()
    client.disconnect = AsyncMock()

    async def subscribe(_uuid, callback):
        for frame in frames:
            callback(0, bytearray(frame))

    client.start_notify = AsyncMock(side_effect=subscribe)
    monkeypatch.setattr(api, "_connect_client", AsyncMock(return_value=client))
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("frames", [
    [b"unrelated"],
    [bytes([0x32, 0x0B, 0x02, 0x40])],
    [bytes([0x32, 0x03, 0x02, 0x40, 0])],
])
async def test_unrelated_or_truncated_notifications_fail(setup_api, monkeypatch, frames):
    client = attach_frames(setup_api, monkeypatch, frames)
    with pytest.raises(APIConnectionError, match="No valid controller status"):
        await setup_api._write_and_commit(b"generic", "generic")
    assert client.write_gatt_char.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("reported_station", [0, 2])
async def test_start_fails_when_controller_reports_different_station(
    setup_api, monkeypatch, reported_station
):
    client = attach_frames(
        setup_api, monkeypatch,
        [status_frame(watering=reported_station != 0, station=reported_station)]
    )
    command = bytes.fromhex("3105120100003c")
    with pytest.raises(APIConnectionError, match="requested watering station"):
        await setup_api._write_and_commit(command, "start station 1")
    assert client.write_gatt_char.await_count == 2


@pytest.mark.asyncio
async def test_start_succeeds_when_station_matches(setup_api, monkeypatch):
    attach_frames(setup_api, monkeypatch, [status_frame(watering=True, station=1)])
    command = bytes.fromhex("3105120100003c")
    await setup_api._write_and_commit(command, "start station 1")


@pytest.mark.asyncio
async def test_stop_fails_when_device_still_watering(setup_api, monkeypatch):
    attach_frames(setup_api, monkeypatch, [status_frame(watering=True, station=1)])
    command = bytes.fromhex("31051500ff0000")
    with pytest.raises(APIConnectionError, match="still reports watering"):
        await setup_api._write_and_commit(command, "stop")


@pytest.mark.asyncio
async def test_stop_succeeds_after_idle_confirmation(setup_api, monkeypatch):
    attach_frames(setup_api, monkeypatch, [status_frame()])
    command = bytes.fromhex("31051500ff0000")
    await setup_api._write_and_commit(command, "stop")


@pytest.mark.asyncio
async def test_delayed_matching_status_succeeds_without_new_radio_requests(setup_api, monkeypatch):
    """An initial stale status must not terminate a command before a newer reply."""
    client = attach_frames(setup_api, monkeypatch, [status_frame()])
    original = client.start_notify.side_effect

    async def subscribe_with_later_status(uuid, callback):
        await original(uuid, callback)
        async def later():
            await asyncio.sleep(0.002)
            callback(0, bytearray(status_frame(watering=True, station=1)))
        asyncio.create_task(later())

    client.start_notify.side_effect = subscribe_with_later_status
    command = bytes.fromhex("3105120100003c")
    await setup_api._write_and_commit(command, "start station 1")
    assert client.write_gatt_char.await_count == 2


@pytest.mark.asyncio
async def test_delayed_idle_status_confirms_stop(setup_api, monkeypatch):
    """Stop must accept a later idle reading over an earlier watering one."""
    client = attach_frames(setup_api, monkeypatch, [status_frame(watering=True, station=1)])
    original = client.start_notify.side_effect

    async def subscribe_with_later_status(uuid, callback):
        await original(uuid, callback)
        async def later():
            await asyncio.sleep(0.002)
            callback(0, bytearray(status_frame()))
        asyncio.create_task(later())

    client.start_notify.side_effect = subscribe_with_later_status
    await setup_api._write_and_commit(bytes.fromhex("31051500ff0000"), "stop")
    assert client.write_gatt_char.await_count == 2
