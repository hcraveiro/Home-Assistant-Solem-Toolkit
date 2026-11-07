"""BLNR connection backoff retains serialization across API instances."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.solem_toolkit.api import APIConnectionError
from custom_components.solem_toolkit.blnr import BLNRAPI, BLNR_COMMIT


@pytest.mark.asyncio
async def test_waiting_command_proceeds_after_retry_exhaustion(monkeypatch):
    first = BLNRAPI(SimpleNamespace(), "AA:00:00:00:00:42")
    second = BLNRAPI(SimpleNamespace(), "AA:00:00:00:00:42")
    assert first._device_lock is second._device_lock
    in_backoff = asyncio.Event()
    release = asyncio.Event()

    async def pause(_seconds):
        in_backoff.set()
        await release.wait()

    monkeypatch.setattr("custom_components.solem_toolkit.api.asyncio.sleep", pause)
    first._connect_client = AsyncMock(side_effect=APIConnectionError("busy"))
    client = SimpleNamespace(
        is_connected=True, start_notify=AsyncMock(), stop_notify=AsyncMock(),
        write_gatt_char=AsyncMock(), disconnect=AsyncMock(),
    )
    second._connect_client = AsyncMock(return_value=client)

    async def write(_uuid, payload, response):
        if payload == BLNR_COMMIT:
            client.start_notify.call_args.args[1](0, bytearray.fromhex("0600000600"))

    client.write_gatt_char.side_effect = write
    task = asyncio.create_task(first.turn_on())
    await asyncio.wait_for(in_backoff.wait(), 1)
    waiter = asyncio.create_task(second.turn_on())
    second._connect_client.assert_not_awaited()
    release.set()
    results = await asyncio.wait_for(
        asyncio.gather(task, waiter, return_exceptions=True), 1
    )
    assert isinstance(results[0], APIConnectionError)
    assert results[1] is None
    assert first._connect_client.await_count == 3
    second._connect_client.assert_awaited_once()
    client.disconnect.assert_awaited_once()
    assert not first._device_lock.locked()
