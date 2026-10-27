"""Experimental, deliberately limited Solem BLNR BLE commands.

Protocol sourced from the physical capture documented in legacy issue #19.
BLNR frames, GATT UUIDs and commit are NOT compatible with BL-IP.
"""

from __future__ import annotations

import asyncio
import logging

from .api import APIConnectionError, SolemAPI

_LOGGER = logging.getLogger(__name__)

BLNR_WRITE_UUID = "a3eb0002-8c8c-b87a-827d-9bf3de08084a"
BLNR_NOTIFY_UUID = "a3eb0003-8c8c-b87a-827d-9bf3de08084a"
BLNR_COMMIT = bytes.fromhex("0d00000d00")
BLNR_ON = bytes.fromhex("050400c0000000c900")
BLNR_OFF_ONE_DAY = bytes.fromhex("050400a0010000aa00")
BLNR_STOP_MANUAL = bytes.fromhex("050400900000009900")
BLNR_REPLY_TIMEOUT = 4.0


def _control_frame(mode: int, parameter: int) -> bytes:
    """Build a control frame with the 16-bit little-endian additive checksum."""
    prefix = bytes((0x05, 0x04, 0x00, mode, parameter, 0x00, 0x00))
    return prefix + (sum(prefix) & 0xFFFF).to_bytes(2, "little")


def _validate_parameter(value: int, name: str) -> None:
    """Keep inferred parameter values within a single byte pending captures."""
    if type(value) is not int or not 1 <= value <= 255:
        raise APIConnectionError(f"BLNR {name} must be an integer from 1 to 255")


def is_valid_blnr_frame(frame: bytes, opcode: int) -> bool:
    """Validate the complete 16-bit checksum confirmed by app HCI captures."""
    if len(frame) < 5 or frame[0] != opcode:
        return False
    if len(frame) != frame[1] + 5:
        return False
    return (sum(frame[:-2]) & 0xFFFF) == int.from_bytes(frame[-2:], "little")


class BLNRAPI(SolemAPI):
    """Experimental BLNR protocol; never use standard BL-IP services."""

    async def _send_blnr_command(
        self, command: bytes, label: str, *, verify_commit: bool = False
    ) -> None:
        async with self._device_lock:
            client = await self._connect_client()
            notifications: list[bytes] = []
            reply_event = asyncio.Event()

            def on_notify(_sender: object, data: bytearray) -> None:
                notifications.append(bytes(data))
                reply_event.set()

            subscribed = False
            command_sent = False
            try:
                if not client.is_connected:
                    raise APIConnectionError("BLNR is not connected")
                await asyncio.wait_for(
                    client.start_notify(BLNR_NOTIFY_UUID, on_notify),
                    timeout=5.0,
                )
                subscribed = True
                # No command retry: after a BLE write its physical outcome
                # could be uncertain even when the connection disappears.
                command_sent = True
                async def wait_for_reply(opcode: int) -> None:
                    loop = asyncio.get_running_loop()
                    deadline = loop.time() + BLNR_REPLY_TIMEOUT
                    while True:
                        if any(is_valid_blnr_frame(f, opcode) for f in notifications):
                            return
                        remaining = deadline - loop.time()
                        if remaining <= 0:
                            raise APIConnectionError(
                                "BLNR acknowledgement missing; command outcome unknown"
                            )
                        reply_event.clear()
                        try:
                            await asyncio.wait_for(reply_event.wait(), remaining)
                        except asyncio.TimeoutError as exc:
                            raise APIConnectionError(
                                "BLNR acknowledgement missing; command outcome unknown"
                            ) from exc

                await client.write_gatt_char(BLNR_WRITE_UUID, command, response=False)
                if verify_commit:
                    # Match the app capture: control reply, commit, commit reply.
                    await wait_for_reply(0x06)
                    notifications.clear()
                await client.write_gatt_char(BLNR_WRITE_UUID, BLNR_COMMIT, response=False)
                await wait_for_reply(0x0E if verify_commit else 0x06)
                _LOGGER.info(
                    "BLNR %s - %s acknowledged (physical state not independently verified)",
                    self.mac_address, label,
                )
                return
            except asyncio.CancelledError:
                raise
            except APIConnectionError:
                raise
            except Exception as exc:
                phase = "outcome unknown" if command_sent else "nothing sent"
                raise APIConnectionError(
                    f"BLNR {label} failed ({phase})"
                ) from exc
            finally:
                if subscribed:
                    try:
                        await client.stop_notify(BLNR_NOTIFY_UUID)
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("BLNR notification cleanup failed", exc_info=True)
                await self._disconnect_client(client)

    async def turn_on(self) -> None:
        """Enable BLNR controller; not equivalent to watering a station."""
        await self._send_blnr_command(BLNR_ON, "enable controller")

    async def turn_off_permanent(self) -> None:
        """Disable scheduled irrigation indefinitely using the app-captured zero-day mode."""
        await self._send_blnr_command(
            _control_frame(0xA0, 0), "disable permanently", verify_commit=True
        )

    async def turn_off_one_day(self) -> None:
        """Retain the hardware-validated one-day operation."""
        await self.turn_off_x_days(1)

    async def turn_off_x_days(self, days: int) -> None:
        """Suspend scheduled watering; one and ten days have app capture evidence."""
        _validate_parameter(days, "days")
        await self._send_blnr_command(
            _control_frame(0xA0, days), f"disable for {days} days"
        )

    async def sprinkle_station_x_for_y_minutes(self, station: int, minutes: int) -> None:
        """Start the single BL-NR station; 1, 2 and 20 minutes have capture evidence."""
        if type(station) is not int or station != 1:
            raise APIConnectionError("BLNR supports only station 1")
        _validate_parameter(minutes, "minutes")
        # The app's one-minute frame is 05 04 00 0a 00 01 00 14 00.
        # Keep the upper duration byte zero until its semantics are captured.
        prefix = bytes((0x05, 0x04, 0x00, 0x0A, 0x00, minutes, 0x00))
        command = prefix + (sum(prefix) & 0xFFFF).to_bytes(2, "little")
        await self._send_blnr_command(
            command, f"water station 1 for {minutes} minutes", verify_commit=True
        )

    async def stop_manual_sprinkle(self) -> None:
        """Stop manual watering using the hardware-captured BL-NR frame."""
        await self._send_blnr_command(
            BLNR_STOP_MANUAL, "stop manual watering", verify_commit=True
        )

    async def read_raw(self, opcode: int, index: int | None = None) -> dict:
        """Read a BLNR block without altering controller settings."""
        if opcode not in (0x01, 0x09, 0xF7):
            raise ValueError("Unsupported BLNR read opcode")
        if opcode == 0xF7:
            if index is not None:
                raise ValueError("Status query has no index")
            request = bytes.fromhex("f70000f700")
        else:
            if not isinstance(index, int) or not 0 <= index <= 255:
                raise ValueError("BLNR index must be between 0 and 255")
            prefix = bytes((opcode, 1, 0, index))
            request = prefix + (sum(prefix) & 0xFFFF).to_bytes(2, "little")

        expected_opcode = (opcode + 1) & 0xFF
        async with self._device_lock:
            client = await self._connect_client()
            frames: list[bytes] = []
            event = asyncio.Event()

            def on_notify(_sender: object, data: bytearray) -> None:
                frames.append(bytes(data))
                event.set()

            subscribed = False
            try:
                if not client.is_connected:
                    raise APIConnectionError("BLNR is not connected")
                await asyncio.wait_for(client.start_notify(BLNR_NOTIFY_UUID, on_notify), 5.0)
                subscribed = True
                await client.write_gatt_char(BLNR_WRITE_UUID, request, response=False)
                loop = asyncio.get_running_loop()
                deadline = loop.time() + BLNR_REPLY_TIMEOUT
                while True:
                    for frame in frames:
                        if frame and frame[0] == expected_opcode:
                            valid = is_valid_blnr_frame(frame, expected_opcode)
                            _LOGGER.info(
                                "BLNR %s read opcode=0x%02x index=%s response=%s valid_frame=%s",
                                self.mac_address, opcode, index, frame.hex(), valid,
                            )
                            return {
                                "device_mac": self.mac_address,
                                "model": "bl_nr",
                                "request_opcode": f"0x{opcode:02x}",
                                "response_opcode": f"0x{expected_opcode:02x}",
                                "index": index,
                                "request_hex": request.hex(),
                                "response_hex": frame.hex(),
                                "trailer_hex": f"{frame[-1]:02x}",
                                "checksum_hex": frame[-2:].hex(),
                                "valid_frame": valid,
                            }
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise APIConnectionError(
                            f"BLNR read 0x{opcode:02x} timed out waiting for response"
                        )
                    event.clear()
                    try:
                        await asyncio.wait_for(event.wait(), remaining)
                    except asyncio.TimeoutError as exc:
                        raise APIConnectionError(
                            f"BLNR read 0x{opcode:02x} timed out waiting for response"
                        ) from exc
            except asyncio.CancelledError:
                raise
            except APIConnectionError:
                raise
            except Exception as exc:
                raise APIConnectionError(
                    f"BLNR read 0x{opcode:02x} failed"
                ) from exc
            finally:
                if subscribed:
                    try:
                        await client.stop_notify(BLNR_NOTIFY_UUID)
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("BLNR notification cleanup failed", exc_info=True)
                await self._disconnect_client(client)

