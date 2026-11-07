"""Both protocols share acquisition policy, never a command replay loop."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.solem_toolkit.api import APIConnectionError, SolemAPI
from custom_components.solem_toolkit.blnr import BLNRAPI


@pytest.fixture(params=[SolemAPI, BLNRAPI])
def setup(request, monkeypatch):
    api = request.param(SimpleNamespace(), "AA:00:00:00:00:71")
    monkeypatch.setattr("custom_components.solem_toolkit.api.CONNECTION_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr("custom_components.solem_toolkit.api.COMMAND_ACK_TIMEOUT", 0.001)
    monkeypatch.setattr("custom_components.solem_toolkit.blnr.BLNR_REPLY_TIMEOUT", 0.001)
    client = SimpleNamespace(is_connected=True, start_notify=AsyncMock(),
                             stop_notify=AsyncMock(), write_gatt_char=AsyncMock(),
                             disconnect=AsyncMock())

    async def write(_uuid, payload, response):
        frame = (bytes.fromhex("0600000600") if isinstance(api, BLNRAPI)
                 else bytes([0x32, 0x0B, 0x02, 0x40, 0, 0, 0, 0, 0, 0, 90, 0, 0]))
        client.start_notify.call_args.args[1](0, bytearray(frame))

    client.write_gatt_char.side_effect = write
    api._connect_client = AsyncMock(return_value=client)
    return api, client


@pytest.mark.asyncio
async def test_transient_connect_only_one_command(setup):
    api, client = setup
    api._connect_client.side_effect = [APIConnectionError("busy"), client]
    await api.turn_on()
    assert api._connect_client.await_count == 2
    assert client.write_gatt_char.await_count == 2  # command and commit, once each
    client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_exhaustion_preserves_cause(setup):
    api, client = setup
    error = APIConnectionError("busy")
    api._connect_client.side_effect = error
    with pytest.raises(APIConnectionError, match="3 attempt.*nothing sent: busy") as caught:
        await api.turn_on()
    assert caught.value.__cause__ is error
    assert api._connect_client.await_count == 3
    client.start_notify.assert_not_awaited()
    client.write_gatt_char.assert_not_awaited()
    assert not api._device_lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["connect", "backoff", "notify", "write"])
async def test_cancellation(setup, monkeypatch, phase):
    api, client = setup
    if phase == "connect":
        api._connect_client.side_effect = asyncio.CancelledError
    elif phase == "backoff":
        api._connect_client.side_effect = APIConnectionError("busy")
        monkeypatch.setattr("custom_components.solem_toolkit.api.asyncio.sleep",
                            AsyncMock(side_effect=asyncio.CancelledError))
    elif phase == "notify":
        client.start_notify.side_effect = asyncio.CancelledError
    else:
        client.write_gatt_char.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await api.turn_on()
    api._connect_client.assert_awaited_once()
    if phase in ("notify", "write"):
        client.disconnect.assert_awaited_once()
    assert not api._device_lock.locked()


@pytest.mark.asyncio
async def test_single_timeout_budget(setup):
    api, client = setup
    api.bluetooth_timeout = 0.01
    stopped = asyncio.Event()

    async def blocked():
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    api._connect_client.side_effect = blocked
    with pytest.raises(APIConnectionError, match="1 attempt.*nothing sent"):
        await asyncio.wait_for(api.turn_on(), 0.2)
    assert stopped.is_set()
    api._connect_client.assert_awaited_once()
    client.write_gatt_char.assert_not_awaited()
    assert not api._device_lock.locked()


@pytest.mark.asyncio
async def test_backoff_uses_remaining_budget(setup, monkeypatch):
    api, client = setup
    api.bluetooth_timeout = 0.02
    api._connect_client.side_effect = APIConnectionError("busy")
    monkeypatch.setattr("custom_components.solem_toolkit.api.CONNECTION_RETRY_BACKOFF_SECONDS", 0.001)

    async def stalled_pause(_seconds):
        await asyncio.Event().wait()

    monkeypatch.setattr("custom_components.solem_toolkit.api.asyncio.sleep", stalled_pause)
    with pytest.raises(APIConnectionError, match="budget exhausted.*nothing sent: busy") as caught:
        await asyncio.wait_for(api.turn_on(), 0.2)
    assert isinstance(caught.value.__cause__, APIConnectionError)
    api._connect_client.assert_awaited_once()
    client.write_gatt_char.assert_not_awaited()
    assert not api._device_lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["notify", "command", "commit", "ack"])
async def test_no_post_connect_retry(setup, phase):
    api, client = setup
    write = client.write_gatt_char.side_effect
    if phase == "notify":
        client.start_notify.side_effect = OSError("subscription failed")
    elif phase == "ack":
        client.write_gatt_char.side_effect = None
    else:
        count = 0

        async def fail_write(*args, **kwargs):
            nonlocal count
            count += 1
            if count == (1 if phase == "command" else 2):
                raise OSError("GATT failed")
            await write(*args, **kwargs)

        client.write_gatt_char.side_effect = fail_write
    with pytest.raises(APIConnectionError):
        await api.turn_on()
    api._connect_client.assert_awaited_once()
    assert client.write_gatt_char.await_count == {"notify": 0, "command": 1, "commit": 2, "ack": 2}[phase]
    client.disconnect.assert_awaited_once()
    assert not api._device_lock.locked()
