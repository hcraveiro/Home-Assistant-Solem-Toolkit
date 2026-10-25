"""Exercise Bluetooth discovery, connection lifecycle, and public service commands."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.solem_toolkit import api as module
from custom_components.solem_toolkit.api import APIConnectionError, SolemAPI, _DEVICE_LOCKS

MAC = "AA:BB:CC:DD:EE:FF"


@pytest.fixture
def api():
    _DEVICE_LOCKS.clear()
    return SolemAPI(SimpleNamespace(data={}), MAC)


def test_shared_device_lock_ignores_mac_case(api):
    other = SolemAPI(api.hass, MAC.lower())
    assert api._device_lock is other._device_lock


@pytest.mark.asyncio
async def test_discovery_uses_connectable_home_assistant_device(api, monkeypatch):
    device = SimpleNamespace(address=MAC)
    lookup = Mock(return_value=device)
    monkeypatch.setattr(module.bluetooth, "async_ble_device_from_address", lookup)
    assert await api._resolve_ble_device() is device
    lookup.assert_called_once_with(api.hass, MAC, connectable=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("scanners, message", [
    (0, "No connectable Bluetooth adapter"),
    (1, "Device not found"),
])
async def test_discovery_fails_without_connectable_source(api, monkeypatch, scanners, message):
    monkeypatch.setattr(module.bluetooth, "async_ble_device_from_address", Mock(return_value=None))
    monkeypatch.setattr(module.bluetooth, "async_scanner_count", Mock(return_value=scanners))
    with pytest.raises(APIConnectionError, match=message):
        await api._resolve_ble_device()


@pytest.mark.asyncio
async def test_successful_connection_uses_single_attempt(api, monkeypatch):
    device = object()
    client = SimpleNamespace(is_connected=True)
    monkeypatch.setattr(api, "_resolve_ble_device", AsyncMock(return_value=device))
    monkeypatch.setattr(module.bluetooth, "async_last_service_info", Mock(return_value=None))
    connect = AsyncMock(return_value=client)
    monkeypatch.setattr(module, "establish_connection", connect)
    assert await api._connect_client() is client
    assert connect.await_args.kwargs["max_attempts"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error, expected", [
    (TimeoutError("late"), "Timeout connecting"),
    (OSError("disconnected"), "Timeout connecting"),
    (RuntimeError("unexpected"), "Unexpected BLE connection"),
])
async def test_connection_errors_become_api_errors(api, monkeypatch, error, expected):
    monkeypatch.setattr(api, "_resolve_ble_device", AsyncMock(return_value=object()))
    monkeypatch.setattr(module.bluetooth, "async_last_service_info", Mock(return_value=None))
    monkeypatch.setattr(module, "establish_connection", AsyncMock(side_effect=error))
    with pytest.raises(APIConnectionError, match=expected):
        await api._connect_client()


@pytest.mark.asyncio
async def test_disconnect_error_is_ignored(api):
    client = SimpleNamespace(disconnect=AsyncMock(side_effect=OSError("gone")))
    await api._disconnect_client(client)
    client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_characteristics_disconnects_after_read(api, monkeypatch):
    characteristic = SimpleNamespace(uuid="char", properties=["write"], descriptors=[])
    service = SimpleNamespace(uuid="service", characteristics=[characteristic])
    client = SimpleNamespace(is_connected=True, services=[service])
    monkeypatch.setattr(api, "_connect_client", AsyncMock(return_value=client))
    disconnect = AsyncMock()
    monkeypatch.setattr(api, "_disconnect_client", disconnect)
    assert (await api.list_characteristics())["service"][0]["uuid"] == "char"
    disconnect.assert_awaited_once_with(client)


@pytest.mark.asyncio
async def test_characteristics_disconnects_when_unavailable(api, monkeypatch):
    client = SimpleNamespace(is_connected=True, services=None)
    monkeypatch.setattr(api, "_connect_client", AsyncMock(return_value=client))
    disconnect = AsyncMock()
    monkeypatch.setattr(api, "_disconnect_client", disconnect)
    with pytest.raises(APIConnectionError, match="Services not available"):
        await api.list_characteristics()
    disconnect.assert_awaited_once_with(client)


@pytest.mark.asyncio
async def test_write_rejects_disconnected_client(api):
    client = SimpleNamespace(is_connected=False, write_gatt_char=AsyncMock())
    with pytest.raises(APIConnectionError, match="Client not connected"):
        await api._write_with_auth_retry(client, b"test")
    client.write_gatt_char.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_calls_ble_once(api):
    client = SimpleNamespace(is_connected=True, write_gatt_char=AsyncMock())
    await api._write_with_auth_retry(client, b"test")
    client.write_gatt_char.assert_awaited_once_with(
        api.characteristic_uuid, b"test", response=False
    )


@pytest.mark.asyncio
async def test_subscription_timeout_never_falls_back_to_blind_write(api, monkeypatch):
    client = SimpleNamespace(start_notify=AsyncMock(side_effect=asyncio.TimeoutError()))
    with pytest.raises(APIConnectionError, match="no command was sent"):
        await api._subscribe_ack(client)


@pytest.mark.asyncio
async def test_notification_collector_copies_payload():
    collector = module._NotifyCollector()
    data = bytearray(b"data")
    collector.on_notify(0, data)
    data[0] = 0
    assert collector.event.is_set()
    assert collector.frames == [b"data"]


@pytest.mark.asyncio
async def test_public_commands_delegate_to_single_command_path(api, monkeypatch):
    send = AsyncMock(return_value=[])
    monkeypatch.setattr(api, "_write_and_commit", send)
    for command in (
        api.turn_on,
        api.turn_off_permanent,
        lambda: api.turn_off_x_days(3),
        lambda: api.sprinkle_station_x_for_y_minutes(2, 5),
        lambda: api.sprinkle_all_stations_for_y_minutes(5),
        lambda: api.run_program_x(2),
        api.stop_manual_sprinkle,
    ):
        await command()
    assert send.await_count == 7
    descriptions = [entry.args[1] for entry in send.await_args_list]
    assert "stop manual sprinkle" in descriptions
    assert "sprinkle station 2 for 5 minute(s)" in descriptions


@pytest.mark.asyncio
async def test_read_status_does_not_send_commit_twice(api, monkeypatch):
    send = AsyncMock(return_value=[])
    monkeypatch.setattr(api, "_write_and_commit", send)
    monkeypatch.setattr(module.protocol, "decode_status", Mock(return_value=None))
    assert await api.read_status() is None
    assert send.await_args.kwargs["commit"] is False
    assert send.await_args.args[0] == module.protocol.STATUS_REQUEST


@pytest.mark.asyncio
async def test_status_logging_ignores_unknown_packets(api):
    api._log_status([b"other"], "test")
    assert api.last_status is None
