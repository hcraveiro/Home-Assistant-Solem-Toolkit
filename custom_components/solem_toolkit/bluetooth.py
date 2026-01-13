"""Bluetooth helpers for Solem Toolkit.

This module centralizes Bluetooth device discovery so other integrations can reuse it
without re-implementing scanning logic.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


async def async_scan_devices(hass: HomeAssistant, timeout: int = 5) -> list[Any]:
    """Return a list of discovered BLE devices.

    Prefer Home Assistant's bluetooth discovery when available. Fall back to a direct
    scan via bleak if bluetooth discovery is unavailable.

    The returned objects typically expose `.name` and `.address` attributes.
    """

    try:
        from homeassistant.components.bluetooth import async_discovered_devices

        # Home Assistant manages scanning; no need to run an active scan here.
        return async_discovered_devices(hass)
    except Exception:
        # Fallback to an active scan.
        from bleak import BleakScanner

        return await BleakScanner.discover(timeout=timeout)
