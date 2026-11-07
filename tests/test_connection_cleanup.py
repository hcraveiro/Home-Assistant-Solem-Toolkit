"""Acquisition retains ownership of clients the connector has not returned."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.solem_toolkit import api as module
from custom_components.solem_toolkit.api import APIConnectionError, SolemAPI


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError("failed"), asyncio.CancelledError()])
async def test_connector_failure_disconnects_constructed_client(monkeypatch, failure):
    api = SolemAPI(SimpleNamespace(), "AA:00:00:00:00:89")
    monkeypatch.setattr(api, "_resolve_ble_device", AsyncMock(return_value=object()))
    monkeypatch.setattr(module.bluetooth, "async_last_service_info", Mock(return_value=None))
    client = SimpleNamespace(disconnect=AsyncMock())
    monkeypatch.setattr(module, "BleakClient", Mock(return_value=client))

    async def establish(factory, device, **kwargs):
        assert kwargs["max_attempts"] == 1
        factory(device)
        raise failure

    monkeypatch.setattr(module, "establish_connection", establish)
    with pytest.raises(asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else APIConnectionError):
        await api._connect_client()
    client.disconnect.assert_awaited_once()
