"""Regression tests for BLE command safety and serialized access."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.solem_toolkit.api import APIConnectionError, SolemAPI, _DEVICE_LOCKS

MAC = "AA:BB:CC:DD:EE:FF"


@pytest.fixture
def api(monkeypatch):
    _DEVICE_LOCKS.clear()
    instance = SolemAPI(SimpleNamespace(data={}), MAC)
    monkeypatch.setattr("custom_components.solem_toolkit.api.RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr("custom_components.solem_toolkit.api.COMMAND_MAX_ATTEMPTS", 3)
    monkeypatch.setattr("custom_components.solem_toolkit.api.COMMAND_ACK_TIMEOUT", 0.01)
    return instance


def client_with_notification(frames=(b"valid",)):
    client = SimpleNamespace(is_connected=True)
    client.write_gatt_char = AsyncMock()
    client.disconnect = AsyncMock()
    client.stop_notify = AsyncMock()

    async def start_notify(_characteristic, callback):
        for frame in frames:
            callback(0, bytearray(frame))

    client.start_notify = AsyncMock(side_effect=start_notify)
    return client


@pytest.mark.asyncio
async def test_connect_failures_retry_only_before_first_write(api, monkeypatch):
    client = client_with_notification()
    connect = AsyncMock(side_effect=[
        APIConnectionError("unreachable"),
        APIConnectionError("busy"),
        client,
    ])
    monkeypatch.setattr(api, "_connect_client", connect)
    monkeypatch.setattr(api, "_log_status", lambda *_: None)
    await api._write_and_commit(b"command", "start")
    assert connect.await_count == 3
    assert client.write_gatt_char.await_count == 2


@pytest.mark.asyncio
async def test_connect_failure_exhausts_retry_budget(api, monkeypatch):
    connect = AsyncMock(side_effect=APIConnectionError("unreachable"))
    monkeypatch.setattr(api, "_connect_client", connect)
    with pytest.raises(APIConnectionError, match="unreachable"):
        await api._write_and_commit(b"command", "start")
    assert connect.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", [1, 2])
async def test_failed_write_is_not_replayed(api, monkeypatch, failure_at):
    client = client_with_notification()
    writes = 0

    async def write(*_args, **_kwargs):
        nonlocal writes
        writes += 1
        if writes == failure_at:
            raise OSError("link dropped")

    client.write_gatt_char.side_effect = write
    connect = AsyncMock(return_value=client)
    monkeypatch.setattr(api, "_connect_client", connect)
    with pytest.raises(APIConnectionError, match="outcome unknown"):
        await api._write_and_commit(b"command", "start")
    assert connect.await_count == 1
    assert writes == failure_at


@pytest.mark.asyncio
async def test_notification_subscription_failure_blocks_writes(api, monkeypatch):
    client = client_with_notification()
    client.start_notify.side_effect = OSError("no notify")
    monkeypatch.setattr(api, "_connect_client", AsyncMock(return_value=client))
    with pytest.raises(APIConnectionError, match="no command was sent"):
        await api._write_and_commit(b"command", "start")
    client.write_gatt_char.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_notification_never_replays(api, monkeypatch):
    client = client_with_notification(frames=())
    connect = AsyncMock(return_value=client)
    monkeypatch.setattr(api, "_connect_client", connect)
    with pytest.raises(APIConnectionError, match="outcome is unknown"):
        await api._write_and_commit(b"command", "start")
    assert connect.await_count == 1
    assert client.write_gatt_char.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_commands_share_per_mac_lock(api, monkeypatch):
    other = SolemAPI(api.hass, MAC.lower())
    assert other._device_lock is api._device_lock
    active = 0
    max_active = 0

    async def connect():
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        return client_with_notification()

    async def disconnect(_client):
        nonlocal active
        active -= 1

    for instance in (api, other):
        monkeypatch.setattr(instance, "_connect_client", connect)
        monkeypatch.setattr(instance, "_disconnect_client", disconnect)
        monkeypatch.setattr(instance, "_log_status", lambda *_: None)
    await asyncio.gather(
        api._write_and_commit(b"first", "first"),
        other._write_and_commit(b"second", "second"),
    )
    assert max_active == 1


@pytest.mark.asyncio
async def test_cancelled_operation_is_not_retried(api, monkeypatch):
    client = client_with_notification()

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError()

    client.write_gatt_char.side_effect = cancelled
    connect = AsyncMock(return_value=client)
    monkeypatch.setattr(api, "_connect_client", connect)
    with pytest.raises(asyncio.CancelledError):
        await api._write_and_commit(b"command", "start")
    assert connect.await_count == 1
