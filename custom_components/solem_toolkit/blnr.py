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
BLNR_REPLY_TIMEOUT = 4.0


def is_valid_blnr_frame(frame: bytes, opcode: int) -> bool:
    """Validate opcode, declared payload length, checksum and terminator."""
    if len(frame) < 5 or frame[0] != opcode or frame[-1] != 0:
        return False
    payload_length = frame[1]
    if len(frame) != payload_length + 5:
        return False
    return (sum(frame[:-2]) & 0xFF) == frame[-2]


class BLNRAPI(SolemAPI):
    """Experimental BLNR protocol; never use standard BL-IP services."""

    async def _send_blnr_command(self, command: bytes, label: str) -> None:
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
                await client.write_gatt_char(BLNR_WRITE_UUID, command, response=False)
                await client.write_gatt_char(BLNR_WRITE_UUID, BLNR_COMMIT, response=False)

                loop = asyncio.get_running_loop()
                deadline = loop.time() + BLNR_REPLY_TIMEOUT
                while True:
                    # Only matching validated protocol frames count as replies.
                    # BLNR status doesn't use the BL-IP status decoder.
                    if any(is_valid_blnr_frame(f, 0x06) for f in notifications):
                        _LOGGER.info(
                            "BLNR %s - %s sent; control reply received (physical state not independently verified)",
                            self.mac_address, label,
                        )
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

    async def turn_off_one_day(self) -> None:
        """Disable BLNR controller for one day; other durations unverified."""
        await self._send_blnr_command(BLNR_OFF_ONE_DAY, "disable for one day")
