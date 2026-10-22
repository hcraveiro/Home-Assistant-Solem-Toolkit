"""Decoding of the Solem BLE reply frames.

Deliberately free of Home Assistant imports so it can be imported and unit
tested outside HA (see `solem-bluetooth-analysis/ble-checks/check_ble_fix.py`),
which is the only part of this integration that can be.

Everything here was reconstructed from a HCI capture of a session with the
official MySolem app (2026-09-22, `bt_mysolem.json` in the analysis repo), by
diffing an idle reply against one taken while station 1 was watering for 60
seconds. Fields confirmed by that diff are decoded; fields that are merely
plausible are exposed raw and named `*_raw`, so a later reading can settle them
without this module having to pretend it already knows.

Request/reply convention: a request opcode `X` is answered with opcode `X + 1`.
Long replies are split into chunks of 15 payload bytes, each prefixed with
`[opcode][length][index]`, where `index` counts *down* to zero. Concatenating
the chunks in order of arrival (descending index) gives the logical payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Status request. The command path already writes this after every command as a
# "commit", and the MySolem app also sends it on its own as a pure read (frames
# 29.965, 48.171 and 68.582 of the capture), so it is safe to send alone: it
# changes nothing on the device.
STATUS_REQUEST = bytes((0x3B, 0x00))

# Opcodes that carry a status payload: 0x3C answers the standalone status
# request, 0x32 answers a 0x31 command. Both were observed with an identical
# layout, which is why one decoder serves both.
STATUS_REPLY_OPCODES = (0x3C, 0x32)

# Offsets into the reassembled status payload.
_OFF_STATE = 0
_OFF_ACTIVE_STATION = 6
_OFF_BATTERY = 7
_OFF_STATIONS = 9

# Twelve stations of three bytes each, which is exactly the 36 bytes that follow
# the 9-byte header in the 45-byte payload (3 chunks x 15). Twelve also matches
# the station-name dump (opcode 0x35/0x36), which returns indices 0x00-0x0B.
_STATION_COUNT = 12
_STATION_STRIDE = 3

# Bits of the state byte. Observed: 0x40 idle, 0x42 while watering. The "on"
# bit is a reading of 0x40 being present in both, not a confirmed meaning: the
# capture never had the programmer switched off, so `is_on` is a hypothesis
# while `is_watering` is a measurement.
_STATE_BIT_ON = 0x40
_STATE_BIT_WATERING = 0x02


@dataclass(frozen=True)
class StationStatus:
    """Per-station slice of the status payload."""

    number: int
    flag_raw: int
    remaining_seconds: int


@dataclass(frozen=True)
class SolemStatus:
    """Decoded device status.

    `battery_raw_byte` is the battery in **decivolts**. This was an open
    question - the capture value 0x5C was equally consistent with 92 decivolts
    (9.2 V) and with the low half of a 12-bit ADC count (0x0E5C = 3676, i.e.
    3676/4096 of a ~10.3 V scale, which is also ~9.2 V) - and it was settled by
    eighteen days of readings on this 9 V block:

        01/10  byte7=92  byte8=14       09/10  byte7=91  byte8=10
        11/10  byte7=90  byte8=9        14/10  byte7=90  byte8=8
        17/10  byte7=90  byte8=9        22/10  byte7=90  byte8=7, then 9
                                               (09:34 and 11:41, same morning)

    Byte 7 falls in unit steps - 9.2, 9.1, 9.0 V - exactly the curve an alkaline
    block draws. Byte 8 has no trend at all: 14, 10, 9, 8, 9, 7, 9, and on 22/10
    it read 7 and then 9 two hours apart while byte 7 sat still at 90 in both.
    A battery voltage does not do that, so byte 8 is not part of the value and
    the 16-bit reading was wrong.

    `unknown_byte` is that byte, still published because it moves and tracking
    it is how it will eventually be identified. It is deliberately not called
    anything with "battery" in it: the previous name invited graphing it as a
    second opinion on the charge, which is what made a wrong reading look
    corroborated. A temperature hypothesis was tested against two outdoor
    sensors over nine paired points and rejected (byte 8 of 8-10 against 13-27
    degrees, no relation).
    """

    state_raw: int
    active_station: int
    battery_raw_byte: int
    unknown_byte: int
    stations: tuple[StationStatus, ...] = field(default_factory=tuple)
    payload_hex: str = ""

    @property
    def battery_volts(self) -> float:
        """Battery voltage. Decivolts, confirmed by the drain curve above."""
        return self.battery_raw_byte / 10

    @property
    def is_watering(self) -> bool:
        """True when the device reports a station running (measured)."""
        return bool(self.state_raw & _STATE_BIT_WATERING)

    @property
    def is_on(self) -> bool:
        """True when the device reports itself enabled (hypothesis, see above)."""
        return bool(self.state_raw & _STATE_BIT_ON)

    @property
    def remaining_seconds(self) -> int:
        """Seconds left on the station the device says is running."""
        for station in self.stations:
            if station.number == self.active_station:
                return station.remaining_seconds
        return 0


def iter_status_chunks(frames):
    """Yield (index, payload) for the frames that look like a status reply.

    Frames from other replies (station names, programs, device info) are
    ignored rather than rejected: a single command can produce several kinds of
    notification, and a decoder that threw on the first unrelated frame would
    be useless in the command path.
    """
    for frame in frames:
        data = bytes(frame or b"")
        if len(data) < 4 or data[0] not in STATUS_REPLY_OPCODES:
            continue

        declared = data[1]
        index = data[2]
        payload = data[3:]
        # `length` counts the index byte plus the payload. A frame truncated by
        # a short MTU would otherwise be silently reassembled with a hole in it.
        if declared != len(payload) + 1:
            continue
        yield index, payload


def decode_status(frames) -> SolemStatus | None:
    """Reassemble and decode a status reply, or None if there isn't one.

    Returns a result as soon as the header chunk is present, without requiring
    all three: in the command path the connection is closed as soon as the
    device acknowledges, so the tail can legitimately be missing. The fields
    that matter most - state, active station, battery - all live in the first
    chunk.
    """
    chunks = {}
    for index, payload in iter_status_chunks(frames):
        # Highest index arrives first; a repeated index means the status was
        # read twice in one session, so the later copy is the current one.
        chunks[index] = payload

    if not chunks:
        return None

    payload = b"".join(chunks[i] for i in sorted(chunks, reverse=True))
    if len(payload) <= _OFF_BATTERY + 1:
        return None

    stations = []
    for i in range(_STATION_COUNT):
        start = _OFF_STATIONS + i * _STATION_STRIDE
        if start + _STATION_STRIDE > len(payload):
            break
        flag = payload[start]
        remaining = int.from_bytes(payload[start + 1:start + 3], "big")
        stations.append(StationStatus(i + 1, flag, remaining))

    return SolemStatus(
        state_raw=payload[_OFF_STATE],
        active_station=payload[_OFF_ACTIVE_STATION],
        battery_raw_byte=payload[_OFF_BATTERY],
        unknown_byte=payload[_OFF_BATTERY + 1],
        stations=tuple(stations),
        payload_hex=payload.hex(),
    )


def as_event_data(status: SolemStatus) -> dict:
    """Flat, JSON-safe view of a status, for the event bus and service replies.

    Flat and primitive on purpose: this ends up in a Home Assistant event, read
    from a Jinja template as `trigger.event.data.<field>`, so nested objects
    would only make the template harder to write.

    `battery_volts` no longer carries a disclaimer in its name: the unit is
    established (see SolemStatus). `battery_volts_if_decivolts` is kept as an
    alias of it because template sensors in the wild are reading that key, and
    an attribute that silently stops rendering is a bad way to announce good
    news. It can go once the dashboards have moved.
    """
    return {
        "is_watering": status.is_watering,
        "is_on": status.is_on,
        "active_station": status.active_station,
        "remaining_seconds": status.remaining_seconds,
        "battery_raw": status.battery_raw_byte,
        "battery_volts": round(status.battery_volts, 1),
        "battery_volts_if_decivolts": round(status.battery_volts, 1),
        "unknown_byte": status.unknown_byte,
        "state_raw": f"0x{status.state_raw:02x}",
        "payload": status.payload_hex,
    }


def describe_status(status: SolemStatus) -> str:
    """One log line, written to be readable in Home Assistant's log viewer.

    The unknown byte is printed next to the battery because the log is the
    dataset that will eventually identify it: a line per command, over a season.
    """
    if status.is_watering and status.active_station:
        activity = (
            f"watering station {status.active_station}, "
            f"{status.remaining_seconds}s left"
        )
    elif status.is_watering:
        activity = "watering (station not reported)"
    else:
        activity = "idle"

    return (
        f"{activity}; battery {status.battery_volts:.1f} V"
        f" (raw={status.battery_raw_byte});"
        f" unknown byte={status.unknown_byte};"
        f" state=0x{status.state_raw:02x}"
    )
