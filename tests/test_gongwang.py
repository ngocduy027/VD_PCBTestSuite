from __future__ import annotations

import struct

import pytest

from pcbt_app.logging_store import EventLog
from pcbt_app.models import RawCanFrame
from pcbt_app.protocol.mit_firmware import (
    CAN_INDEX_TEST_ACTIVE,
    CAN_INDEX_TEST_GONGWANG_DURATION_MS,
    CAN_INDEX_TEST_UNLOCK,
    DIAG_TEST_UNLOCK_KEY,
    GONGWANG_DEFAULT_DURATION_MS,
    MOTOR_TEST_GONGWANG_ENCODER,
    MotorTestStatus,
    MotorTestStopReason,
    build_gongwang_config_frames,
    build_gongwang_start_frame,
    build_gongwang_stop_frame,
    build_single_write_u32_frame,
    read_param_value_float,
    read_param_value_u32,
)
from pcbt_app.services.gongwang_encoder_test import GONGWANG_RESULT_FIELDS
from pcbt_app.services.pcb_test_service import PcbTestService
from pcbt_app.transport.fake_bus import FakeCanTransport

DRIVER_ID = 0x34
HOST_ID = 0xA5


def report_frame(
    *,
    status: int = MotorTestStatus.FINISHED,
    reason: int = MotorTestStopReason.COMPLETED,
    elapsed_ms: int = GONGWANG_DEFAULT_DURATION_MS,
) -> RawCanFrame:
    payload = bytes([4, 0, int(status), int(reason)]) + elapsed_ms.to_bytes(4, "little")
    return RawCanFrame(0x1B000000 | (DRIVER_ID << 8) | HOST_ID, payload)


def read_response(value: int | float, wire_type: str, *, host_id: int = HOST_ID) -> RawCanFrame:
    packed = struct.pack("<f", value) if wire_type == "float" else int(value).to_bytes(4, "little")
    return RawCanFrame(0x11000000 | (DRIVER_ID << 8) | host_id, b"\x00" * 4 + packed)


def passing_values() -> dict[str, int | float]:
    return {
        "duration_ms": 5_000,
        "schema_version": 1,
        "target_hz": 20_000,
        "rx_mode": 1,
        "command": 0x9A,
        "response_length": 10,
        "scheduled_count": 100_000,
        "started_count": 100_000,
        "response_count": 97_000,
        "valid_count": 95_000,
        "actual_request_hz": 19_000,
        "actual_response_hz": 19_000,
        "valid_response_hz": 19_000,
        "success_percent": 95.0,
        "received_byte_count": 970_000,
        "received_byte_hz": 194_000,
        "discarded_byte_count": 7,
        "ring_overrun_count": 2,
        "missed_tx_slot_count": 3,
        "busy_deadline_count": 4,
        "timeout_count": 5,
        "crc_error_count": 6,
        "header_error_count": 7,
        "uart_error_count": 8,
        "decode_queue_overflow_count": 9,
        "fault_response_count": 10,
        "latest_status": 11,
        "input_single_turn_raw": 12,
        "output_single_turn_raw": 13,
        "output_multi_turn_raw": 14,
        "fault_code": 15,
        "input_single_turn_deg": 16.25,
        "output_single_turn_deg": 17.5,
        "output_total_turns": 18.75,
        "output_total_deg": 19.125,
    }


def make_service(frames: list[RawCanFrame]) -> tuple[PcbTestService, FakeCanTransport]:
    transport = FakeCanTransport()
    transport.connect()
    transport.queued_frames.extend(frames)
    service = PcbTestService(
        transport,
        EventLog(),
        openloop_command_interval_s=0.0,
        poll_interval_s=0.0,
        response_timeout_s=0.001,
    )
    return service, transport


def result_frames(values: dict[str, int | float], *, stop_after: str | None = None) -> list[RawCanFrame]:
    frames: list[RawCanFrame] = []
    for name, _index, wire_type, _required in GONGWANG_RESULT_FIELDS:
        if stop_after == name:
            break
        frames.append(read_response(values[name], wire_type))
    return frames


def test_gongwang_schema_is_complete_and_typed() -> None:
    assert len(GONGWANG_RESULT_FIELDS) == 35
    assert [index for _name, index, _type, _required in GONGWANG_RESULT_FIELDS] == list(range(0x7240, 0x7263))
    assert [name for name, _index, wire_type, _required in GONGWANG_RESULT_FIELDS if wire_type == "float"] == [
        "success_percent",
        "input_single_turn_deg",
        "output_single_turn_deg",
        "output_total_turns",
        "output_total_deg",
    ]


def test_gongwang_builders_and_read_decoders_use_little_endian_payload() -> None:
    config = build_gongwang_config_frames(DRIVER_ID, 12_345)
    assert config == [build_single_write_u32_frame(DRIVER_ID, CAN_INDEX_TEST_GONGWANG_DURATION_MS, 12_345)]
    assert build_gongwang_start_frame(DRIVER_ID) == build_single_write_u32_frame(
        DRIVER_ID, CAN_INDEX_TEST_ACTIVE, MOTOR_TEST_GONGWANG_ENCODER
    )
    assert build_gongwang_stop_frame(DRIVER_ID) == build_single_write_u32_frame(DRIVER_ID, CAN_INDEX_TEST_ACTIVE, 0)
    assert read_param_value_u32(read_response(0x12345678, "u32"), DRIVER_ID) == 0x12345678
    assert read_param_value_float(read_response(12.5, "float"), DRIVER_ID) == pytest.approx(12.5)


def test_gongwang_service_passes_at_exact_thresholds_and_reads_serially() -> None:
    values = passing_values()
    unrelated = RawCanFrame(0x020034A5, b"\x00" * 8)
    service, transport = make_service([unrelated, report_frame(), *result_frames(values)])

    result = service.run_gongwang_encoder_test(5_000, DRIVER_ID)

    assert result.verdict.passed is True
    assert result.verdict.gongwang_results is not None
    assert result.verdict.gongwang_results.success_percent == pytest.approx(95.0)
    assert result.verdict.gongwang_results.input_single_turn_deg == pytest.approx(16.25)
    assert "request=19000 Hz (95.0% achievable)" in result.verdict.reason
    assert "valid=19000 Hz (95.0% achievable)" in result.verdict.reason

    sent_payloads = [frame.data for frame in transport.sent_frames]
    assert sent_payloads[0] == build_single_write_u32_frame(
        DRIVER_ID, CAN_INDEX_TEST_GONGWANG_DURATION_MS, 5_000
    ).data
    assert sent_payloads[1] == build_single_write_u32_frame(DRIVER_ID, CAN_INDEX_TEST_UNLOCK, DIAG_TEST_UNLOCK_KEY).data
    assert sent_payloads[2] == build_gongwang_start_frame(DRIVER_ID).data
    read_indexes = [payload[0] | (payload[1] << 8) for payload in sent_payloads[3:]]
    assert read_indexes == list(range(0x7240, 0x7263))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("actual_request_hz", 15_999),
        ("valid_response_hz", 15_999),
        ("success_percent", 79.999),
    ],
)
def test_gongwang_service_fails_below_each_acceptance_threshold(field: str, value: int | float) -> None:
    values = passing_values()
    values[field] = value
    service, _transport = make_service([report_frame(), *result_frames(values)])

    result = service.run_gongwang_encoder_test(5_000, DRIVER_ID)

    assert result.verdict.passed is False


def test_gongwang_service_passes_at_80_percent_thresholds() -> None:
    values = passing_values()
    values["actual_request_hz"] = 16_000
    values["valid_response_hz"] = 16_000
    values["success_percent"] = 80.0
    service, _transport = make_service([report_frame(), *result_frames(values)])

    result = service.run_gongwang_encoder_test(5_000, DRIVER_ID)

    assert result.verdict.passed is True


def test_gongwang_service_fails_wrong_metadata_or_abnormal_completion() -> None:
    values = passing_values()
    values["schema_version"] = 2
    service, _transport = make_service([report_frame(), *result_frames(values)])
    assert service.run_gongwang_encoder_test(5_000, DRIVER_ID).verdict.passed is False

    values["schema_version"] = 1
    service, _transport = make_service(
        [
            report_frame(status=MotorTestStatus.STOPPED, reason=MotorTestStopReason.CAN_STOP),
            *result_frames(values),
        ]
    )
    assert service.run_gongwang_encoder_test(5_000, DRIVER_ID).verdict.passed is False


def test_gongwang_nonzero_error_and_fault_values_are_informational() -> None:
    values = passing_values()
    service, _transport = make_service([report_frame(), *result_frames(values)])

    result = service.run_gongwang_encoder_test(5_000, DRIVER_ID)

    assert result.verdict.passed is True
    assert result.verdict.gongwang_results is not None
    assert result.verdict.gongwang_results.crc_error_count == 6
    assert result.verdict.gongwang_results.fault_code == 15


def test_missing_informational_result_passes_with_warning() -> None:
    values = passing_values()
    frames = [report_frame(), *result_frames(values, stop_after="output_total_deg")]
    service, _transport = make_service(frames)

    result = service.run_gongwang_encoder_test(5_000, DRIVER_ID)

    assert result.verdict.passed is True
    assert "warning: missing informational results: output_total_deg" in result.verdict.reason
    assert result.verdict.gongwang_results is not None
    assert result.verdict.gongwang_results.output_total_deg is None


def test_missing_required_result_fails() -> None:
    values = passing_values()
    frames = [report_frame(), *result_frames(values, stop_after="success_percent")]
    service, _transport = make_service(frames)

    result = service.run_gongwang_encoder_test(5_000, DRIVER_ID)

    assert result.verdict.passed is False
    assert "Missing required results: success_percent" in result.verdict.reason


def test_completion_timeout_sends_cleanup_stop(monkeypatch) -> None:
    class Clock:
        now = 0.0

        def __call__(self) -> float:
            self.now += 1.0
            return self.now

    monkeypatch.setattr("pcbt_app.services.gongwang_encoder_test.monotonic", Clock())
    service, transport = make_service([])

    result = service.run_gongwang_encoder_test(100, DRIVER_ID)

    assert result.verdict.passed is False
    assert "completion report" in result.verdict.reason
    assert transport.sent_frames[-1].data == build_gongwang_stop_frame(DRIVER_ID).data
