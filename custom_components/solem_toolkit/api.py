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

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from . import protocol
from .const import (
    CHARACTERISTIC_UUID,
    COMMAND_ACK_TIMEOUT,
    COMMAND_MAX_ATTEMPTS,
    DEFAULT_BLUETOOTH_TIMEOUT,
    EVENT_DEVICE_STATUS,
    NOTIFY_CHARACTERISTIC_UUID,
    NOTIFY_SUBSCRIBE_TIMEOUT,
    RETRY_BACKOFF_SECONDS,
    STATUS_COLLECT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

# One lock per device MAC, shared by every SolemAPI instance/service call, so
# overlapping commands are serialized instead of racing for the same BLE slot
# (this is what causes "Connection request ignored, state: CONNECTING" floods
# on ESPHome Bluetooth proxies).
_DEVICE_LOCKS: dict[str, asyncio.Lock] = {}


def _get_device_lock(mac_address: str) -> asyncio.Lock:
    """Return the shared connection lock for a given device MAC address."""
    key = (mac_address or "").lower()
    lock = _DEVICE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _DEVICE_LOCKS[key] = lock
    return lock


class APIConnectionError(Exception):
    """Exception raised when a BLE connection or write fails."""


class _NotifyCollector:
    """Signals the first device reply and keeps every reply that follows.

    The ack logic only needs to know that *something* came back, which is why
    this used to be a bare asyncio.Event with the payload thrown away. But the
    device answers the commit with its full status - state, running station,
    seconds left, battery - so the bytes were being discarded 18 of them at a
    time, on every single command. Keeping them costs no extra radio traffic.
    """

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.frames: list[bytes] = []

    def on_notify(self, _handle: int, data: bytearray) -> None:
        # Runs in bleak's callback context: an exception here would surface as
        # an unrelated BLE error, or be swallowed, so nothing in it may fail.
        # Signal first, so a malformed payload can never cost us an ack.
        try:
            self.frames.append(bytes(data))
            self.event.set()
        except Exception:  # noqa: BLE001
            pass


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
        self._device_lock = _get_device_lock(mac_address)

        # Last status the device reported, for callers that want to read it
        # back instead of inferring it from the commands they sent. Note that
        # a SolemAPI is built per service call, so this lives as long as the
        # call unless the caller keeps the instance.
        self.last_status: protocol.SolemStatus | None = None

    async def scan_bluetooth(self) -> list[BLEDevice]:
        """Return a list of discovered BLE devices."""
        return await BleakScanner.discover(timeout=5.0)

    async def _resolve_ble_device(self) -> BLEDevice:
        """Resolve a connectable BLEDevice for the configured MAC address.

        Home Assistant keeps a central registry of everything its adapters and
        Bluetooth proxies can see, including which source can actually open a
        connection. Asking that registry (instead of running our own
        `BleakScanner`) matters for two reasons:

        * a fresh scan can return a device advertised by a *non-connectable*
          source, and handing that to `establish_connection` ends in a long
          hang followed by `BleakOutOfConnectionSlotsError` while the proxy
          sits idle, never having been asked to connect;
        * starting a scan while another integration is connecting fights over
          the same radio, and ESPHome proxies have to stop scanning to connect.
        """
        ble_device: Optional[BLEDevice] = bluetooth.async_ble_device_from_address(
            self.hass, self.mac_address.upper(), connectable=True
        )
        if ble_device is not None:
            return ble_device

        # Not in the registry: either out of range, or only seen by a
        # non-connectable scanner (e.g. a proxy in passive/scan-only mode).
        if bluetooth.async_scanner_count(self.hass, connectable=True) == 0:
            raise APIConnectionError(
                "No connectable Bluetooth adapter or proxy is available to "
                "Home Assistant"
            )

        raise APIConnectionError(
            "Device not found: no connectable Bluetooth adapter or proxy has "
            "seen this device recently (out of range, or the nearest proxy "
            "cannot accept connections)"
        )

    async def _connect_client(self) -> BleakClient:
        """Establish a robust connection using bleak-retry-connector.

        Callers must hold `self._device_lock` for the whole connect-write-disconnect
        cycle so two commands never fight over the same BLE slot/proxy at once.
        """
        _LOGGER.info(
            "Solem %s - Looking up device on Bluetooth...", self.mac_address)
        ble_device = await self._resolve_ble_device()
        # Which adapter/proxy will be used, and how well it hears the device,
        # is the single most useful thing to know when connections fail.
        service_info = bluetooth.async_last_service_info(
            self.hass, self.mac_address.upper(), connectable=True
        )
        if service_info is not None:
            _LOGGER.info(
                "Solem %s - Device found via %s (RSSI %s dBm), opening connection...",
                self.mac_address,
                service_info.source,
                service_info.rssi,
            )
        else:
            _LOGGER.info(
                "Solem %s - Device found, opening connection...", self.mac_address
            )
        try:
            # A single attempt here: retries are handled one level up in
            # `_write_and_commit`, which retries the whole connect-write-commit
            # cycle. Combining both layers used to multiply worst-case wait time
            # (3 internal attempts x 2 outer attempts x timeout).
            client = await establish_connection(
                BleakClient,
                ble_device,
                name=f"Solem - {self.mac_address}",
                timeout=self.bluetooth_timeout,
                max_attempts=1,
            )
            _LOGGER.info("Solem %s - Connection established.",
                         self.mac_address)
            return client
        except BleakOutOfConnectionSlotsError as exc:
            _LOGGER.warning(
                "Solem %s - Bluetooth adapter/proxy has no free connection slots "
                "(device may be busy or another command is already connecting).",
                self.mac_address,
            )
            raise APIConnectionError(
                "Bluetooth adapter/proxy out of connection slots or device busy/unreachable"
            ) from exc
        except TimeoutError as exc:
            _LOGGER.warning("Solem %s - Connection timeout.", self.mac_address)
            raise APIConnectionError("Timeout connecting to device") from exc
        except (BleakDBusError, OSError) as exc:
            _LOGGER.warning("Solem %s - Bluetooth transport error: %s", self.mac_address, exc)
            raise APIConnectionError("Bluetooth transport error connecting to device") from exc
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "Solem %s - Unexpected error while connecting: %s", self.mac_address, exc
            )
            raise APIConnectionError(
                "Unexpected BLE connection error") from exc

    async def _disconnect_client(self, client: BleakClient) -> None:
        """Disconnect and log the outcome in human-readable form."""
        try:
            await client.disconnect()
            _LOGGER.info("Solem %s - Connection closed.", self.mac_address)
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "Solem %s - Error while disconnecting (device may already be gone).",
                self.mac_address,
                exc_info=True,
            )

    async def list_characteristics(self) -> dict:
        """Return discovered services/characteristics (debug helper)."""
        async with self._device_lock:
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
                    inner = getattr(client, "_client", None) or getattr(
                        client, "_bleak_client", None)
                    if inner is not None and hasattr(inner, "get_services"):
                        services = await inner.get_services()
                    else:
                        raise APIConnectionError(
                            "Services not available on this platform/client")
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
                await self._disconnect_client(client)

    async def _write_with_auth_retry(self, client: BleakClient, payload: bytes) -> None:
        """Write once: a transport error does not prove the device ignored the command."""
        if not client.is_connected:
            raise APIConnectionError("Client not connected")
        await client.write_gatt_char(self.characteristic_uuid, payload, response=False)

    async def _subscribe_ack(self, client: BleakClient) -> _NotifyCollector:
        """Require working notifications before sending a device-changing command."""
        collector = _NotifyCollector()
        try:
            await asyncio.wait_for(
                client.start_notify(NOTIFY_CHARACTERISTIC_UUID, collector.on_notify),
                timeout=NOTIFY_SUBSCRIBE_TIMEOUT,
            )
        except Exception as exc:
            raise APIConnectionError(
                "Cannot enable controller notifications; no command was sent"
            ) from exc
        return collector

    async def _await_ack(
        self,
        client: BleakClient,
        collector: _NotifyCollector,
        collect_seconds: float = 0.0,
        command: bytes = b"",
    ) -> None:
        """Wait for a valid matching status, using existing BLE notifications only."""
        opcode = command[2] if len(command) >= 7 else None
        station = command[3] if opcode == 0x12 else None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + COMMAND_ACK_TIMEOUT
        last_status = None
        try:
            while True:
                status = protocol.decode_status(collector.frames)
                if status is not None:
                    last_status = status
                    if (
                        (station is None or (status.is_watering and status.active_station == station))
                        and (opcode != 0x15 or not status.is_watering)
                    ):
                        if collect_seconds > 0:
                            await asyncio.sleep(collect_seconds)
                        return
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                collector.event.clear()
                try:
                    await asyncio.wait_for(collector.event.wait(), remaining)
                except asyncio.TimeoutError:
                    break

            if last_status is None:
                if not collector.frames:
                    raise APIConnectionError("No acknowledgement; command outcome is unknown")
                raise APIConnectionError("No valid controller status response; command outcome unknown")
            if station is not None:
                raise APIConnectionError("Controller did not report the requested watering station")
            if opcode == 0x15:
                raise APIConnectionError("Controller still reports watering after stop")
            raise APIConnectionError("Controller status not confirmed; outcome unknown")
        finally:
            try:
                await client.stop_notify(NOTIFY_CHARACTERISTIC_UUID)
            except Exception:  # noqa: BLE001
                pass

    async def _write_and_commit(
        self,
        command: bytes,
        description: str,
        *,
        commit: bool = True,
        collect_seconds: float = 0.0,
    ) -> list[bytes]:
        """Retry connecting before a write, never replay an uncertain command."""
        async with self._device_lock:
            last_error: APIConnectionError | None = None
            client = None
            for attempt in range(1, COMMAND_MAX_ATTEMPTS + 1):
                try:
                    client = await self._connect_client()
                    break
                except APIConnectionError as exc:
                    last_error = exc
                    if attempt < COMMAND_MAX_ATTEMPTS:
                        await asyncio.sleep(RETRY_BACKOFF_SECONDS)
            if client is None:
                raise last_error or APIConnectionError("Unable to connect")

            try:
                if not client.is_connected:
                    raise APIConnectionError("Failed connecting!")
                collector = await self._subscribe_ack(client)
                # Once the first write is attempted, an error or missing reply
                # leaves the physical outcome uncertain: never retry blindly.
                await self._write_with_auth_retry(client, command)
                if commit:
                    await self._write_with_auth_retry(client, protocol.STATUS_REQUEST)
                await self._await_ack(client, collector, collect_seconds, command)
                frames = list(collector.frames)
                self._log_status(frames, description)
                return frames
            except asyncio.CancelledError:
                raise
            except APIConnectionError:
                raise
            except Exception as exc:
                raise APIConnectionError(
                    f"Command '{description}' could not be confirmed; outcome unknown"
                ) from exc
            finally:
                await self._disconnect_client(client)

    def _log_status(self, frames: list[bytes], description: str) -> None:
        """Decode the device reply and write it to the log.

        Never raises: this runs after the command has already succeeded, and a
        decoding problem must not turn a confirmed command into a failure. The
        payload layout was reconstructed from one capture of one firmware
        version, so being wrong about it is a realistic outcome - and the right
        price for being wrong is a missing log line, nothing more.
        """
        if not frames:
            return

        try:
            status = protocol.decode_status(frames)
            if status is None:
                # Normal for replies we don't decode yet (station names,
                # programs, device info), hence debug rather than warning.
                _LOGGER.debug(
                    "Solem %s - No status in the reply to '%s': %s",
                    self.mac_address,
                    description,
                    [frame.hex() for frame in frames],
                )
                return

            self.last_status = status
            _LOGGER.info(
                "Solem %s - Device reports: %s",
                self.mac_address,
                protocol.describe_status(status),
            )
            # The raw payload is what makes a surprising log line checkable
            # months later, when the only thing left of the event is the log.
            _LOGGER.debug(
                "Solem %s - Status payload after '%s': %s",
                self.mac_address,
                description,
                status.payload_hex,
            )
            self._fire_status_event(status)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug(
                "Solem %s - Could not decode the reply to '%s' (%s): %s",
                self.mac_address,
                description,
                exc,
                [frame.hex() for frame in frames],
            )

    def _fire_status_event(self, status: protocol.SolemStatus) -> None:
        """Publish the reading on the event bus.

        This is what makes the status visible outside the log: an integration
        without a config entry has no entity platform, so a dashboard gets the
        value through a trigger-based template sensor listening for this event
        (see DEPLOY.md). It fires after *every* command too, not just an
        explicit read, because the device sends its status unprompted - so the
        dashboard stays current at no cost in radio time.

        Failures are swallowed for the same reason as the rest of _log_status:
        a command that reached the device must not be reported as failed
        because a notification about it could not be published.
        """
        data = protocol.as_event_data(status)
        data["device_mac"] = self.mac_address
        try:
            self.hass.bus.async_fire(EVENT_DEVICE_STATUS, data)
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "Solem %s - Could not fire the %s event.",
                self.mac_address,
                EVENT_DEVICE_STATUS,
                exc_info=True,
            )

    async def turn_on(self) -> None:
        """Turn on controller (enable watering)."""
        command = struct.pack(">HBBBH", 0x3105, 0xA0, 0x00, 0x01, 0x0000)
        await self._write_and_commit(command, "turn controller on")

    async def turn_off_permanent(self) -> None:
        """Disable watering permanently."""
        command = struct.pack(">HBBBH", 0x3105, 0xC0, 0x00, 0x00, 0x0000)
        await self._write_and_commit(command, "turn controller off permanently")

    async def turn_off_x_days(self, days: int) -> None:
        """Disable watering for X days (device supports up to 15 days)."""
        days = max(0, min(days, 15))
        command = struct.pack(">HBBBH", 0x3105, 0xC0, 0x00, days, 0x0000)
        await self._write_and_commit(command, f"turn controller off for {days} day(s)")

    async def sprinkle_station_x_for_y_minutes(self, station: int, minutes: int) -> None:
        """Manually water a station for Y minutes."""
        station = max(1, min(station, 16))
        minutes = max(1, min(minutes, 240))
        seconds = minutes * 60
        command = struct.pack(">HBBBH", 0x3105, 0x12, station, 0x00, seconds)
        await self._write_and_commit(
            command, f"sprinkle station {station} for {minutes} minute(s)"
        )

    async def sprinkle_all_stations_for_y_minutes(self, minutes: int) -> None:
        """Manually water all stations for Y minutes each."""
        minutes = max(1, min(minutes, 240))
        seconds = minutes * 60
        command = struct.pack(">HBBBH", 0x3105, 0x11, 0x00, 0x00, seconds)
        await self._write_and_commit(
            command, f"sprinkle all stations for {minutes} minute(s)"
        )

    async def run_program_x(self, program: int) -> None:
        """Run a controller program by id (1-3 on most devices)."""
        program = max(1, min(program, 3))
        command = struct.pack(">HBBBH", 0x3105, 0x14, 0x00, program, 0x0000)
        await self._write_and_commit(command, f"run program {program}")

    async def stop_manual_sprinkle(self) -> None:
        """Stop any running manual watering session."""
        command = struct.pack(">HBBBH", 0x3105, 0x15, 0x00, 0xFF, 0x0000)
        await self._write_and_commit(command, "stop manual sprinkle")

    async def read_status(self) -> protocol.SolemStatus | None:
        """Ask the device what it is doing and return its answer.

        This sends only the status request (`0x3B 0x00`) - the same two bytes
        the command path already writes as a commit, and that the MySolem app
        sends on its own as a pure read. It changes nothing on the device, so
        `commit=False`: committing a commit would be meaningless.

        Returns None if the device answered but the reply carried no status
        (never observed, but a firmware we haven't seen is free to do it).
        """
        frames = await self._write_and_commit(
            protocol.STATUS_REQUEST,
            "read status",
            commit=False,
            collect_seconds=STATUS_COLLECT_SECONDS,
        )
        # `_write_and_commit` has already decoded and logged this; decoding
        # again keeps the return value honest even if a caller of the future
        # bypasses `last_status`.
        return protocol.decode_status(frames)
