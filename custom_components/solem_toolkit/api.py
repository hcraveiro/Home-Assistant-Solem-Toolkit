"""Solem BLE API helper.

This is a lightweight subset of the Solem API used by the scheduling integration.
It focuses on robust BLE connection handling and command writes for manual actions.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakDBusError
from bleak_retry_connector import (
    BleakOutOfConnectionSlotsError,
    establish_connection,
)
from tenacity import retry, stop_after_attempt, wait_exponential

from homeassistant.core import HomeAssistant

from .const import CHARACTERISTIC_UUID, DEFAULT_BLUETOOTH_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class APIConnectionError(Exception):
    """Exception raised when a BLE connection or write fails."""


class SolemAPI:
    """API wrapper for the Solem BLE protocol."""

    def __init__(
        self,
        hass: HomeAssistant,
        mac_address: str,
        bluetooth_timeout: int = DEFAULT_BLUETOOTH_TIMEOUT,
    ) -> None:
        self.hass = hass
        self.mac_address = mac_address
        self.bluetooth_timeout = bluetooth_timeout

        self.characteristic_uuid: str = CHARACTERISTIC_UUID
        self._conn_lock = asyncio.Lock()

    async def scan_bluetooth(self) -> list[BLEDevice]:
        """Return a list of discovered BLE devices."""
        return await BleakScanner.discover(timeout=5.0)

    async def _resolve_ble_device(self) -> BLEDevice:
        """Resolve a BLEDevice for the configured MAC address."""
        # First attempt: direct lookup by address (fast-path on most platforms)
        ble_device: Optional[BLEDevice] = await BleakScanner.find_device_by_address(
            self.mac_address, timeout=5.0
        )
        if ble_device is not None:
            return ble_device

        # Fallback: full scan and manual match (some platforms/proxies behave like this)
        devices = await BleakScanner.discover(timeout=5.0)
        for d in devices:
            if (d.address or "").lower() == self.mac_address.lower():
                return d

        raise APIConnectionError("Device not found! Failed connecting!")

    async def _connect_client(self) -> BleakClient:
        """Establish a robust connection using bleak-retry-connector."""
        async with self._conn_lock:
            ble_device = await self._resolve_ble_device()
            try:
                client = await establish_connection(
                    BleakClient,
                    ble_device,
                    name=f"Solem - {self.mac_address}",
                    timeout=self.bluetooth_timeout,
                    max_attempts=3,
                )
                return client
            except BleakOutOfConnectionSlotsError as exc:
                raise APIConnectionError(
                    "Bluetooth adapter/proxy out of connection slots or device busy/unreachable"
                ) from exc
            except (BleakDBusError, TimeoutError, OSError) as exc:
                raise APIConnectionError("Timeout connecting to device") from exc
            except Exception as exc:  # noqa: BLE001
                raise APIConnectionError("Unexpected BLE connection error") from exc

    async def list_characteristics(self) -> dict:
        """Return discovered services/characteristics (debug helper)."""
        client = await self._connect_client()
        try:
            if not client.is_connected:
                raise APIConnectionError("Failed connecting!")

            # Home Assistant wraps BleakClient (HaBleakClientWrapper) and does not
            # expose BleakClient.get_services(). After connecting, discovered
            # services are available via the `services` attribute.
            services = getattr(client, "services", None)
            if services is None:
                # Last-resort fallback for non-HA clients / unexpected wrappers.
                inner = getattr(client, "_client", None) or getattr(client, "_bleak_client", None)
                if inner is not None and hasattr(inner, "get_services"):
                    services = await inner.get_services()
                else:
                    raise APIConnectionError("Services not available on this platform/client")
            result: dict = {}
            for svc in services:
                chars = []
                for c in svc.characteristics:
                    chars.append(
                        {
                            "uuid": str(c.uuid),
                            "properties": list(c.properties),
                            "descriptors": [str(d.uuid) for d in c.descriptors],
                        }
                    )
                result[str(svc.uuid)] = chars
            return result
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.4, min=0.4, max=2))
    async def _write_with_auth_retry(self, client: BleakClient, payload: bytes) -> None:
        """Write with a small retry loop (Solem can be picky right after connect)."""
        if not client.is_connected:
            raise APIConnectionError("Client not connected")

        await client.write_gatt_char(self.characteristic_uuid, payload, response=False)

    async def _write_and_commit(self, command: bytes) -> None:
        """Write a command then commit it (Solem protocol)."""
        client = await self._connect_client()
        try:
            if not client.is_connected:
                raise APIConnectionError("Failed connecting!")
            await self._write_with_auth_retry(client, command)
            # Commit frame
            commit = struct.pack(">BB", 0x3B, 0x00)
            await self._write_with_auth_retry(client, commit)
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    async def turn_on(self) -> None:
        """Turn on controller (enable watering)."""
        command = struct.pack(">HBBBH", 0x3105, 0x12, 0xFF, 0x00, 0xFFFF)
        await self._write_and_commit(command)

    async def turn_off_permanent(self) -> None:
        """Disable watering permanently."""
        command = struct.pack(">HBBBH", 0x3105, 0xC0, 0x00, 0x00, 0x0000)
        await self._write_and_commit(command)

    async def turn_off_x_days(self, days: int) -> None:
        """Disable watering for X days."""
        days = max(0, min(days, 365))
        command = struct.pack(">HBBBH", 0x3105, 0x15, 0x00, days, 0xFFFF)
        await self._write_and_commit(command)

    async def sprinkle_station_x_for_y_minutes(self, station: int, minutes: int) -> None:
        """Manually water a station for Y minutes."""
        station = max(1, min(station, 16))
        minutes = max(1, min(minutes, 240))
        command = struct.pack(">HBBBBH", 0x3105, 0x22, station, 0x00, minutes, 0xFFFF)
        await self._write_and_commit(command)

    async def sprinkle_all_stations_for_y_minutes(self, minutes: int) -> None:
        """Manually water all stations for Y minutes each."""
        minutes = max(1, min(minutes, 240))
        command = struct.pack(">HBBBH", 0x3105, 0x23, 0x00, minutes, 0xFFFF)
        await self._write_and_commit(command)

    async def run_program_x(self, program: int) -> None:
        """Run a controller program by id (1-3 on most devices)."""
        program = max(1, min(program, 3))
        command = struct.pack(">HBBBH", 0x3105, 0x21, program, 0x00, 0xFFFF)
        await self._write_and_commit(command)

    async def stop_manual_sprinkle(self) -> None:
        """Stop any running manual watering session."""
        command = struct.pack(">HBBBH", 0x3105, 0x24, 0x00, 0x00, 0xFFFF)
        await self._write_and_commit(command)
