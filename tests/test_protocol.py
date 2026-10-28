import struct

from pcbt_app.models import RawCanFrame
from pcbt_app.protocol.mit_firmware import (
    CAN_INDEX_TEST_ACTIVE,
    CAN_INDEX_TEST_CAN_INTERVAL_MS,
    CAN_INDEX_TEST_CAN_TOTAL_MESSAGES,
    CAN_INDEX_TEST_UNLOCK,
    CAN_INDEX_TEST_OPEN_LOOP_DURATION_MS,
    CAN_INDEX_TEST_OPEN_LOOP_THETA_STEP,
    CAN_INDEX_TEST_OPEN_LOOP_VD,
    CAN_INDEX_TEST_OPEN_LOOP_VQ,
    CAN_INDEX_TEST_STATUS,
    DIAG_TEST_UNLOCK_KEY,
    MotorTestStatus,
    MotorTestStopReason,
    OPEN_LOOP_DEFAULT_DURATION_MS,
    OPEN_LOOP_DEFAULT_THETA_STEP,
    build_enable_frame,
    build_can_stress_config_frames,
    build_can_stress_start_frame,
    build_can_stress_stop_frame,
    build_get_id_frame,
    build_openloop_default_write_frames,
    build_openloop_start_frame,
    build_openloop_unlock_frame,
    build_scan_id_frame,
    build_single_read_frame,
    parse_can_stress_frame,
    parse_can_stress_report,
    parse_scan_response,
    status_label,
    stop_reason_label,
)
from pcbt_app.protocol.ids import build_extended_id, parse_extended_id


def test_parse_extended_id() -> None:
    fields = parse_extended_id(0x1C12AAFE)
    assert fields.function_code == 0x1C
    assert fields.state_byte == 0x12
    assert fields.driver_id == 0xAA
    assert fields.host_id == 0xFE


def test_build_extended_id() -> None:
    assert build_extended_id(0x1C, 0x12, 0xAA, 0xFE) == 0x1C12AAFE


def test_scan_frame_for_driver_id_52() -> None:
    frame = build_scan_id_frame(52)
    assert frame.arbitration_id == 0x00000034
    assert frame.data == b"\x00"


def test_get_id_frame_matches_firmware_guide() -> None:
    frame = build_get_id_frame()
    assert frame.arbitration_id == 0x00000000
    assert frame.data == b"\x00"


def test_scan_response_parses_driver_id_52() -> None:
    frame = RawCanFrame(arbitration_id=0x000034FE, data=b"\x00" * 8)
    assert parse_scan_response(frame) == 52


def test_enable_frame_for_driver_id_52() -> None:
    frame = build_enable_frame(52)
    assert frame.arbitration_id == 0x03000034
    assert frame.data == b"\x00" * 8


def test_openloop_default_write_payloads() -> None:
    frames = build_openloop_default_write_frames(52)
    expected = [
        (CAN_INDEX_TEST_OPEN_LOOP_VD, bytes.fromhex("00 00 00 40")),
        (CAN_INDEX_TEST_OPEN_LOOP_VQ, bytes.fromhex("00 00 00 00")),
        (CAN_INDEX_TEST_OPEN_LOOP_THETA_STEP, struct.pack("<f", OPEN_LOOP_DEFAULT_THETA_STEP)),
        (CAN_INDEX_TEST_OPEN_LOOP_DURATION_MS, OPEN_LOOP_DEFAULT_DURATION_MS.to_bytes(4, "little")),
    ]
    assert len(frames) == len(expected)
    for frame, (index, value) in zip(frames, expected, strict=True):
        assert frame.arbitration_id == 0x12000034
        assert frame.data == bytes([index & 0xFF, index >> 8, 0x00, 0x00]) + value


def test_openloop_start_frame() -> None:
    frame = build_openloop_start_frame(52)
    assert frame.arbitration_id == 0x12000034
    assert frame.data == bytes([CAN_INDEX_TEST_ACTIVE & 0xFF, CAN_INDEX_TEST_ACTIVE >> 8, 0, 0, 1, 0, 0, 0])


def test_openloop_custom_theta_and_duration_payloads() -> None:
    frames = build_openloop_default_write_frames(52, theta_step=-0.0005, duration_ms=12345)
    theta = frames[2]
    duration = frames[3]
    assert theta.data == bytes([CAN_INDEX_TEST_OPEN_LOOP_THETA_STEP & 0xFF, CAN_INDEX_TEST_OPEN_LOOP_THETA_STEP >> 8, 0, 0]) + struct.pack("<f", -0.0005)
    assert duration.data == bytes([CAN_INDEX_TEST_OPEN_LOOP_DURATION_MS & 0xFF, CAN_INDEX_TEST_OPEN_LOOP_DURATION_MS >> 8, 0, 0]) + (12345).to_bytes(4, "little")


def test_openloop_unlock_frame() -> None:
    frame = build_openloop_unlock_frame(52)
    assert frame.arbitration_id == 0x12000034
    assert frame.data == bytes([CAN_INDEX_TEST_UNLOCK & 0xFF, CAN_INDEX_TEST_UNLOCK >> 8, 0, 0]) + DIAG_TEST_UNLOCK_KEY.to_bytes(4, "little")


def test_can_stress_config_start_stop_frames() -> None:
    config_frames = build_can_stress_config_frames(52, interval_ms=1, total_messages=1000)
    assert config_frames[0].arbitration_id == 0x12000034
    assert config_frames[0].data == bytes([CAN_INDEX_TEST_CAN_INTERVAL_MS & 0xFF, CAN_INDEX_TEST_CAN_INTERVAL_MS >> 8, 0, 0, 1, 0, 0, 0])
    assert config_frames[1].arbitration_id == 0x12000034
    assert config_frames[1].data == bytes([CAN_INDEX_TEST_CAN_TOTAL_MESSAGES & 0xFF, CAN_INDEX_TEST_CAN_TOTAL_MESSAGES >> 8, 0, 0]) + bytes.fromhex("E8 03 00 00")

    start = build_can_stress_start_frame(52)
    assert start.arbitration_id == 0x12000034
    assert start.data == bytes.fromhex("00 72 00 00 02 00 00 00")

    stop = build_can_stress_stop_frame(52)
    assert stop.arbitration_id == 0x12000034
    assert stop.data == bytes.fromhex("00 72 00 00 00 00 00 00")


def test_can_stress_frame_parses_sequence_and_elapsed() -> None:
    frame = RawCanFrame(arbitration_id=0x1C0034FE, data=bytes.fromhex("2A 00 00 00 88 13 00 00"))
    report = parse_can_stress_frame(frame, driver_id=52)
    assert report is not None
    assert report.sequence == 42
    assert report.elapsed_ms == 5000


def test_can_stress_final_report_parses_command_0x1b() -> None:
    frame = RawCanFrame(arbitration_id=0x1B0034FE, data=bytes.fromhex("2A 00 00 00 88 13 00 00"))
    report = parse_can_stress_report(frame, driver_id=52)
    assert report is not None
    assert report.sequence == 42
    assert report.elapsed_ms == 5000


def test_single_read_status_frame() -> None:
    frame = build_single_read_frame(52, CAN_INDEX_TEST_STATUS)
    assert frame.arbitration_id == 0x11000034
    assert frame.data == bytes.fromhex("01 72 00 00 00 00 00 00")


def test_status_and_stop_reason_labels() -> None:
    assert status_label(MotorTestStatus.FINISHED) == "FINISHED"
    assert stop_reason_label(MotorTestStopReason.COMPLETED) == "COMPLETED"
    assert status_label(99) == "UNKNOWN(99)"
    assert stop_reason_label(99) == "UNKNOWN(99)"
