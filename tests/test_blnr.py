"""Experimental BLNR BLE regression tests; hardware validation is still required."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.solem_toolkit.api import APIConnectionError
from custom_components.solem_toolkit.blnr import (
    BLNRAPI,
    BLNR_COMMIT,
    BLNR_OFF_ONE_DAY,
    BLNR_ON,
    BLNR_NOTIFY_UUID,
    BLNR_WRITE_UUID,
    is_valid_blnr_frame,
)


def reply(opcode):
    """Generate a minimal valid BLNR reply for mocked transport."""
    start = bytes((opcode, 0, 0))
    return start + bytes((sum(start) & 0xFF, 0))


def test_captured_blnr_control_frames_and_checksum():
    assert BLNR_ON.hex() == "050400c0000000c900"
    assert BLNR_OFF_ONE_DAY.hex() == "050400a0010000aa00"
    assert BLNR_COMMIT.hex() == "0d00000d00"
    assert all(is_valid_blnr_frame(frame, frame[0]) for frame in
               (BLNR_ON, BLNR_OFF_ONE_DAY, BLNR_COMMIT))
    assert is_valid_blnr_frame(reply(0x06), 0x06)
    assert not is_valid_blnr_frame(b"\x06\x00\x00\xff\x00", 0x06)
    assert not is_valid_blnr_frame(reply(0x07), 0x06)


@pytest.fixture
def setup_api(monkeypatch):
    api = BLNRAPI(SimpleNamespace(), "AA:BB:CC:DD:EE:FF")
    client = SimpleNamespace(
        is_connected=True,
        start_notify=AsyncMock(),
        write_gatt_char=AsyncMock(),
        stop_notify=AsyncMock(),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr(api, "_connect_client", AsyncMock(return_value=client))
    return api, client


@pytest.mark.asyncio
@pytest.mark.parametrize("method,expected", [
    ("turn_on", BLNR_ON),
    ("turn_off_one_day", BLNR_OFF_ONE_DAY),
])
async def test_blnr_commands_are_isolated_and_acknowledged(setup_api, method, expected):
    api, client = setup_api

    async def write(uuid, payload, response):
        assert uuid == BLNR_WRITE_UUID
        assert response is False
        if payload == BLNR_COMMIT:
            notify = client.start_notify.call_args.args[1]
            notify(0, bytearray(reply(0x06)))

    client.write_gatt_char.side_effect = write
    await getattr(api, method)()
    assert client.start_notify.call_args.args[0] == BLNR_NOTIFY_UUID
    assert [c.args[1] for c in client.write_gatt_char.await_args_list] == [
        expected, BLNR_COMMIT,
    ]
    client.stop_notify.assert_awaited_once()
    client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_subscription_failure_sends_no_command(setup_api):
    api, client = setup_api
    client.start_notify.side_effect = OSError("no subscription")
    with pytest.raises(APIConnectionError, match="nothing sent"):
        await api.turn_on()
    client.write_gatt_char.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_write_is_never_replayed(setup_api):
    api, client = setup_api
    client.write_gatt_char.side_effect = OSError("transport lost")
    with pytest.raises(APIConnectionError, match="outcome unknown"):
        await api.turn_on()
    assert client.write_gatt_char.await_count == 1


@pytest.mark.asyncio
async def test_unrelated_notification_does_not_confirm_command(setup_api, monkeypatch):
    api, client = setup_api
    monkeypatch.setattr(
        "custom_components.solem_toolkit.blnr.BLNR_REPLY_TIMEOUT", 0.01
    )

    async def start_notify(_uuid, callback):
        callback(0, bytearray(reply(0x02)))

    client.start_notify.side_effect = start_notify
    with pytest.raises(APIConnectionError, match="acknowledgement missing"):
        await api.turn_on()
    assert client.write_gatt_char.await_count == 2



@pytest.mark.asyncio
@pytest.mark.parametrize("opcode,index,expected,reply_opcode", [
    (0x01, 1, "010100010300", 0x02),
    (0x09, 2, "090100020c00", 0x0A),
    (0xF7, None, "f70000f700", 0xF8),
])
async def test_read_queries_use_notify_without_commit(setup_api, opcode, index, expected, reply_opcode):
    api, client = setup_api

    async def write(uuid, payload, response):
        callback = client.start_notify.call_args.args[1]
        header = bytes((reply_opcode, 0, 0))
        callback(0, bytearray(header + bytes((sum(header) & 255, 0))))

    client.write_gatt_char.side_effect = write
    result = await api.read_raw(opcode, index)
    assert result["request_hex"] == expected
    assert result["response_opcode"] == f"0x{reply_opcode:02x}"
    assert result["valid_frame"] is True
    assert client.write_gatt_char.await_count == 1
    assert client.write_gatt_char.await_args.args[1] != BLNR_COMMIT
    client.stop_notify.assert_awaited_once()
    client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_wrong_reply_opcode_times_out(setup_api, monkeypatch):
    api, client = setup_api
    monkeypatch.setattr("custom_components.solem_toolkit.blnr.BLNR_REPLY_TIMEOUT", 0.01)

    async def write(uuid, payload, response):
        callback = client.start_notify.call_args.args[1]
        callback(0, bytearray.fromhex("0600000600"))

    client.write_gatt_char.side_effect = write
    with pytest.raises(APIConnectionError, match="timed out"):
        await api.read_raw(0x01, 1)


@pytest.mark.asyncio
async def test_raw_unverified_frame_is_returned_not_decoded(setup_api):
    api, client = setup_api

    async def write(uuid, payload, response):
        callback = client.start_notify.call_args.args[1]
        callback(0, bytearray.fromhex("f80300000000"))

    client.write_gatt_char.side_effect = write
    result = await api.read_raw(0xF7)
    assert result["response_hex"] == "f80300000000"
    assert result["valid_frame"] is False


@pytest.mark.parametrize("opcode,index", [(0x03, None), (0x01, None), (0x09, 256), (0xF7, 1)])
@pytest.mark.asyncio
async def test_invalid_queries_never_connect(setup_api, opcode, index):
    api, client = setup_api
    with pytest.raises(ValueError):
        await api.read_raw(opcode, index)
    client.write_gatt_char.assert_not_awaited()


# Frames captured from a physical BL-NR during the 1.0.4b2 beta.
REAL_READ_REPLIES = (
    (0x02, "020f01424c4e522d44334637303100000000c202", "02"),
    (0x0A, "0a0c0253746174696f6e20310000004b03", "03"),
    (0xF8, "f80300000000fb00", "00"),
)


@pytest.mark.parametrize("opcode,reply_hex,trailer", REAL_READ_REPLIES)
def test_physical_blnr_read_frames_validate(opcode, reply_hex, trailer):
    frame = bytes.fromhex(reply_hex)
    assert len(frame) == frame[1] + 5
    assert frame[-2] == sum(frame[:-2]) & 0xFF
    assert frame[-1:].hex() == trailer
    assert is_valid_blnr_frame(frame, opcode)


@pytest.mark.parametrize("opcode,reply_hex,_trailer", REAL_READ_REPLIES)
def test_physical_read_frames_reject_corrupted_checksum_and_length(opcode, reply_hex, _trailer):
    frame = bytes.fromhex(reply_hex)
    bad_checksum = frame[:-2] + bytes((frame[-2] ^ 1, frame[-1]))
    assert not is_valid_blnr_frame(bad_checksum, opcode)
    bad_length = bytearray(frame)
    bad_length[1] += 1
    assert not is_valid_blnr_frame(bad_length, opcode)
    assert not is_valid_blnr_frame(frame, opcode ^ 1)


def test_control_ack_still_requires_zero_terminator():
    assert is_valid_blnr_frame(reply(0x06), 0x06)
    assert not is_valid_blnr_frame(reply(0x06)[:-1] + bytes((2,)), 0x06)


@pytest.mark.asyncio
@pytest.mark.parametrize("request_opcode,index,reply_opcode,reply_hex,_trailer", [
    (0x01, 1, *REAL_READ_REPLIES[0]),
    (0x09, 2, *REAL_READ_REPLIES[1]),
    (0xF7, None, *REAL_READ_REPLIES[2]),
])
async def test_real_captured_reply_is_accepted_end_to_end(
    setup_api, request_opcode, index, reply_opcode, reply_hex, _trailer
):
    api, client = setup_api

    async def write(_uuid, _payload, response):
        assert response is False
        callback = client.start_notify.call_args.args[1]
        callback(0, bytearray.fromhex(reply_hex))

    client.write_gatt_char.side_effect = write
    result = await api.read_raw(request_opcode, index)
    assert result["response_opcode"] == f"0x{reply_opcode:02x}"
    assert result["response_hex"] == reply_hex
    assert result["valid_frame"] is True
    assert result["trailer_hex"] == _trailer
    assert client.write_gatt_char.await_count == 1
    client.stop_notify.assert_awaited_once()
    client.disconnect.assert_awaited_once()



# Sanitized app HCI capture, 2026-10-27: start one minute, stop 10.947 s later.
MANUAL_CAPTURE = (
    ("start", "0504000a0001001400", "06060242240001600fe400", "0e060042240001600fea00"),
    ("stop", "050400900000009900", "060602400000016010bf00", "0e060040000001600fc400"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("action,command_hex,ack_hex,commit_hex", MANUAL_CAPTURE)
async def test_manual_commands_match_real_capture(setup_api, action, command_hex, ack_hex, commit_hex):
    api, client = setup_api

    async def write(uuid, payload, response):
        assert uuid == BLNR_WRITE_UUID
        assert response is False
        callback = client.start_notify.call_args.args[1]
        callback(0, bytearray.fromhex(commit_hex if payload == BLNR_COMMIT else ack_hex))

    client.write_gatt_char.side_effect = write
    if action == "start":
        await api.sprinkle_station_x_for_y_minutes(1, 1)
    else:
        await api.stop_manual_sprinkle()
    assert [c.args[1].hex() for c in client.write_gatt_char.await_args_list] == [command_hex, BLNR_COMMIT.hex()]
    client.stop_notify.assert_awaited_once()
    client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("minutes,expected", [(2, "0504000a0002001500"), (10, "0504000a000a001d00"), (255, "0504000a00ff001200")])
async def test_configurable_duration_encoding_is_experimental(setup_api, minutes, expected):
    api, _client = setup_api
    api._send_blnr_command = AsyncMock()
    await api.sprinkle_station_x_for_y_minutes(1, minutes)
    assert api._send_blnr_command.await_args.args[0].hex() == expected
    assert api._send_blnr_command.await_args.kwargs["verify_commit"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("days,expected", [(1, "050400a0010000aa00"), (10, "050400a00a0000b300"), (255, "050400a0ff0000a800")])
async def test_configurable_disable_days_encoding(setup_api, days, expected):
    api, _client = setup_api
    api._send_blnr_command = AsyncMock()
    await api.turn_off_x_days(days)
    assert api._send_blnr_command.await_args.args[0].hex() == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("method,args", [
    ("sprinkle_station_x_for_y_minutes", (2, 1)),
    ("sprinkle_station_x_for_y_minutes", (1, 0)),
    ("sprinkle_station_x_for_y_minutes", (1, 256)),
    ("sprinkle_station_x_for_y_minutes", (1, 1.5)),
    ("turn_off_x_days", (0,)), ("turn_off_x_days", (256,)),
])
async def test_invalid_parameters_never_connect(setup_api, method, args):
    api, client = setup_api
    with pytest.raises(APIConnectionError):
        await getattr(api, method)(*args)
    api._connect_client.assert_not_awaited()
    client.write_gatt_char.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing_control", "missing_commit", "bad_control", "bad_commit", "write_failed"])
async def test_manual_failure_is_never_replayed(setup_api, monkeypatch, failure):
    api, client = setup_api
    monkeypatch.setattr("custom_components.solem_toolkit.blnr.BLNR_REPLY_TIMEOUT", 0.01)

    async def write(_uuid, payload, response):
        if failure == "write_failed":
            raise OSError("lost transport")
        commit = payload == BLNR_COMMIT
        if failure == ("missing_commit" if commit else "missing_control"):
            return
        callback = client.start_notify.call_args.args[1]
        frame = bytes.fromhex(MANUAL_CAPTURE[0][3 if commit else 2])
        if failure == ("bad_commit" if commit else "bad_control"):
            frame = frame[:-2] + bytes((frame[-2] ^ 1, 0))
        callback(0, bytearray(frame))

    client.write_gatt_char.side_effect = write
    with pytest.raises(APIConnectionError, match="outcome unknown"):
        await api.sprinkle_station_x_for_y_minutes(1, 1)
    assert client.write_gatt_char.await_count == (2 if failure in ("missing_commit", "bad_commit") else 1)
    client.disconnect.assert_awaited_once()
