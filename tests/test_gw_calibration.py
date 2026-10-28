from __future__ import annotations

import pytest

from pcbt_app.models import RawCanFrame
from pcbt_app.protocol.fct_can import FCT_HOST_ID, build_fct_id
from pcbt_app.protocol.gw_calibration import (
    ERROR_CODE_MESSAGES,
    GW_MOTOR_RESOLUTION,
    GW_OUTPUT_RESOLUTION,
    GwCalibrationAssembler,
    GwCalibrationOutcome,
    GwCalibrationProtocolError,
    build_gw_calibration_read_result,
    build_gw_calibration_start,
    decode_gw_calibration_record,
    describe_encoder_error,
    parse_gw_calibration_fragment,
    parse_gw_calibration_progress,
)
from pcbt_app.services.gw_calibration import GwCalibrationRunner
from pcbt_app.transport.fake_bus import FakeCanTransport

DRIVER_ID = 0x34


def response(opcode: int, data: bytes) -> RawCanFrame:
    return RawCanFrame(build_fct_id(0x1F, opcode, DRIVER_ID, FCT_HOST_ID), data)


def with_xor(prefix: bytes) -> bytes:
    value = 0
    for item in prefix:
        value ^= item
    return prefix + bytes((value,))


def result_record(request: int, values: tuple[int, ...], status: int = 0) -> bytes:
    return with_xor(bytes((0x76, request)) + b"".join(value.to_bytes(2, "little", signed=True) for value in values) + bytes((status,)))


def result_fragments(outcome: int, record_type: int, raw: bytes) -> list[RawCanFrame]:
    padded = raw + b"\x00" * ((5 - len(raw) % 5) % 5)
    return [
        response(0x13, bytes((outcome, record_type, index)) + padded[offset : offset + 5])
        for index, offset in enumerate(range(0, len(padded), 5))
    ]


def test_builds_documented_calibration_start() -> None:
    frame = build_gw_calibration_start(driver_id=DRIVER_ID)
    assert frame.arbitration_id == 0x1E10FD34
    assert frame.data == b"\x00" * 8
    assert frame.is_extended_id


def test_builds_documented_read_result_request() -> None:
    frame = build_gw_calibration_read_result(driver_id=DRIVER_ID)
    assert frame.arbitration_id == 0x1E13FD34
    assert frame.data == b"\x00" * 8
    assert frame.is_extended_id


def test_progress_requires_raw_command_request_and_xor() -> None:
    good = response(0x12, with_xor(bytes((0x76, 0x01, 0x11, 0x00))) + b"\x00" * 3)
    parsed = parse_gw_calibration_progress(good, driver_id=DRIVER_ID)
    assert parsed is not None
    assert (parsed.request, parsed.step, parsed.encoder_status) == (1, 0x11, 0)

    bad = RawCanFrame(good.arbitration_id, good.data[:4] + bytes((good.data[4] ^ 1,)) + good.data[5:])
    with pytest.raises(GwCalibrationProtocolError, match="XOR"):
        parse_gw_calibration_progress(bad, driver_id=DRIVER_ID)
    assert parse_gw_calibration_progress(good, driver_id=DRIVER_ID + 1) is None


def test_signed_result_decoding_and_ring_specific_deviation() -> None:
    values = (4000, -300, -2000, 250, 1000, -800, 500, 700, -256)
    motor = decode_gw_calibration_record(result_record(0x02, values), resolution=GW_MOTOR_RESOLUTION)
    output = decode_gw_calibration_record(result_record(0x04, values), resolution=GW_OUTPUT_RESOLUTION)

    assert motor.master_sin_amplitude_v == pytest.approx(4000 * 0.5 * 3.657 / 8192)
    assert motor.master_sin_center_v == pytest.approx(-300 * 3.657 / 8192)
    assert motor.master_sin_cos_percent == pytest.approx(50.0)
    assert motor.slave_sin_cos_percent == pytest.approx(50.0)
    assert motor.relative_deviation_deg == pytest.approx(-256 * 360 / 131072)
    assert output.relative_deviation_deg == pytest.approx(-256 * 360 / 524288)


def test_pass_reassembly_requires_motor_then_output_with_all_fragments() -> None:
    motor_raw = result_record(0x02, (1, 2, 3, 4, 5, 6, 7, 8, 9))
    output_raw = result_record(0x04, (-1, -2, -3, -4, -5, -6, -7, -8, -9))
    assembler = GwCalibrationAssembler()
    result = None
    for frame in result_fragments(0x01, 0x01, motor_raw) + result_fragments(0x01, 0x02, output_raw):
        fragment = parse_gw_calibration_fragment(frame, driver_id=DRIVER_ID)
        assert fragment is not None
        result = assembler.add(fragment)

    assert result is not None and result.outcome is GwCalibrationOutcome.PASS
    assert result.motor is not None and result.motor.status == 0
    assert result.output is not None and result.output.master_sin_amplitude_v < 0


def test_out_of_order_fragment_reports_incomplete_result() -> None:
    raw = result_record(0x02, (1, 2, 3, 4, 5, 6, 7, 8, 9))
    frames = result_fragments(0x01, 0x01, raw)
    assembler = GwCalibrationAssembler()
    first = parse_gw_calibration_fragment(frames[0], driver_id=DRIVER_ID)
    third = parse_gw_calibration_fragment(frames[2], driver_id=DRIVER_ID)
    assert first is not None and third is not None
    assembler.add(first)
    with pytest.raises(GwCalibrationProtocolError, match="Incomplete calibration result"):
        assembler.add(third)


def test_encoder_failure_reassembles_and_decodes_both_sides() -> None:
    prefix = bytes((0x76, 0x0A, 0xE3)) + (123).to_bytes(2, "little") + bytes((0xE7,)) + (456).to_bytes(2, "little")
    raw = with_xor(prefix)
    assembler = GwCalibrationAssembler()
    result = None
    for frame in result_fragments(0x02, 0x03, raw):
        fragment = parse_gw_calibration_fragment(frame, driver_id=DRIVER_ID)
        assert fragment is not None
        result = assembler.add(fragment)
    assert result is not None and result.encoder_error is not None
    assert (result.encoder_error.motor_code, result.encoder_error.motor_value) == (0xE3, 123)
    assert (result.encoder_error.output_code, result.encoder_error.output_value) == (0xE7, 456)


@pytest.mark.parametrize(
    ("code", "value", "expected"),
    [
        (0xE1, 4096, "biên độ: 914.25 mV"),
        (0xE2, 8192, "biên độ: 1828.50 mV"),
        (0xE3, 73, "tỉ lệ: 73%"),
        (0xE4, 12345, "độ lệch: 12.345°"),
        (0xE5, 9, "chưa rõ cách quy đổi"),
        (0xE6, 10, "chưa rõ cách quy đổi"),
        (0xE7, 15000, "thời gian: 15000 ms"),
    ],
)
def test_encoder_error_description_uses_documented_vietnamese_conversion(
    code: int, value: int, expected: str
) -> None:
    description = describe_encoder_error(code, value)
    assert ERROR_CODE_MESSAGES[code] in description
    assert expected in description
    assert f"raw: {value}" in description


@pytest.mark.parametrize("outcome", (0x03, 0x04))
def test_internal_failure_finishes_from_one_status_frame(outcome: int) -> None:
    assembler = GwCalibrationAssembler()
    fragment = parse_gw_calibration_fragment(
        response(0x13, bytes((outcome, 0x00, 0x00)) + b"\x00" * 5),
        driver_id=DRIVER_ID,
    )
    assert fragment is not None
    result = assembler.add(fragment)
    assert result is not None and int(result.outcome) == outcome
    assert result.motor is None and result.output is None and result.encoder_error is None


def test_runner_receives_ack_progress_and_pushed_pass_result() -> None:
    motor_raw = result_record(0x02, (100, 20, 90, -10, 80, 5, 70, -5, 12))
    output_raw = result_record(0x04, (110, 21, 95, -11, 85, 6, 75, -6, -13))
    transport = FakeCanTransport(connected=True)
    transport.queued_frames.extend(
        [
            response(0x10, bytes((0x00, 0x01)) + b"\x00" * 6),
            response(0x12, with_xor(bytes((0x76, 0x01, 0x10, 0x00))) + b"\x00" * 3),
            *result_fragments(0x01, 0x01, motor_raw),
            *result_fragments(0x01, 0x02, output_raw),
        ]
    )
    updates = []

    result = GwCalibrationRunner(transport).run(DRIVER_ID, on_update=updates.append)

    assert transport.sent_frames == [build_gw_calibration_start(driver_id=DRIVER_ID)]
    assert any(update.step == 0x10 for update in updates)
    assert updates[-1].terminal and updates[-1].passed
    assert result.motor is not None and result.output is not None


def test_runner_reads_stored_result_without_starting_calibration() -> None:
    motor_raw = result_record(0x02, (100, 20, 90, -10, 80, 5, 70, -5, 12))
    output_raw = result_record(0x04, (110, 21, 95, -11, 85, 6, 75, -6, -13))
    transport = FakeCanTransport(connected=True)
    transport.queued_frames.extend(
        result_fragments(0x01, 0x01, motor_raw) + result_fragments(0x01, 0x02, output_raw)
    )
    updates = []

    result = GwCalibrationRunner(transport).read_result(DRIVER_ID, on_update=updates.append)

    assert transport.sent_frames == [build_gw_calibration_read_result(driver_id=DRIVER_ID)]
    assert updates[0].state == "READING"
    assert updates[-1].terminal and updates[-1].passed
    assert result.motor is not None and result.output is not None
