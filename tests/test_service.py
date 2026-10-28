import struct

import pytest

from pcbt_app.logging_store import EventLog
from pcbt_app.models import RawCanFrame
from pcbt_app.protocol.mit_firmware import (
    CAN_INDEX_TEST_ACTIVE,
    CAN_INDEX_TEST_CAN_INTERVAL_MS,
    CAN_INDEX_TEST_CAN_TOTAL_MESSAGES,
    CAN_STRESS_DEFAULT_TOTAL_MESSAGES,
    MotorTestStatus,
    MotorTestStopReason,
)
from pcbt_app.services.pcb_test_service import PcbTestService
from pcbt_app.transport.fake_bus import FakeCanTransport
from pcbt_app.transport.waveshare_usbcan_a import WaveshareParserStats


def make_service(
    transport: FakeCanTransport,
    *,
    poll_interval_s: float = 0.0,
    response_timeout_s: float = 0.0,
) -> PcbTestService:
    return PcbTestService(
        transport,
        EventLog(),
        scan_interval_s=0.0,
        openloop_command_interval_s=0.0,
        poll_interval_s=poll_interval_s,
        response_timeout_s=response_timeout_s,
    )


def feedback(driver_id: int = 0x34) -> RawCanFrame:
    return RawCanFrame(arbitration_id=0x020000FD | (driver_id << 8), data=b"\x00" * 8)


def scan_response(driver_id: int = 0x34) -> RawCanFrame:
    return RawCanFrame(arbitration_id=0x000000FE | (driver_id << 8), data=b"\x00" * 8)


def read_response(value: int, driver_id: int = 0x34) -> RawCanFrame:
    return RawCanFrame(
        arbitration_id=0x110000FD | (driver_id << 8),
        data=b"\x00\x00\x00\x00" + value.to_bytes(4, "little"),
    )


class DiagnosticFakeCanTransport(FakeCanTransport):
    def take_parser_stats(self) -> WaveshareParserStats:
        return WaveshareParserStats(
            valid_packets=3,
            decoded_frames=2,
            malformed_candidates=2,
            discarded_bytes=5,
            checksum_errors=1,
            framing_errors=1,
        )


def can_stress_frame(sequence: int = 0, elapsed_ms: int = 0, driver_id: int = 0x34) -> RawCanFrame:
    return RawCanFrame(
        arbitration_id=0x1C0000FE | (driver_id << 8),
        data=sequence.to_bytes(4, "little") + elapsed_ms.to_bytes(4, "little"),
    )


def diagnostic_report(
    test_id: int,
    status: int = MotorTestStatus.FINISHED,
    stop_reason: int = MotorTestStopReason.COMPLETED,
    elapsed_ms: int = 3,
    driver_id: int = 0x34,
) -> RawCanFrame:
    return RawCanFrame(
        arbitration_id=0x1B0000FD | (driver_id << 8),
        data=(
            test_id.to_bytes(2, "little")
            + bytes([status, stop_reason])
            + elapsed_ms.to_bytes(4, "little")
        ),
    )


def can_stress_final_reads(
    *,
    status: int = MotorTestStatus.FINISHED,
    stop_reason: int = MotorTestStopReason.COMPLETED,
    total_messages: int = 3,
    tx_fail_count: int = 0,
    late_count: int = 0,
) -> list[RawCanFrame]:
    return [
        read_response(0),
        read_response(status),
        read_response(stop_reason),
        read_response(3),
        read_response(total_messages),
        read_response(total_messages - tx_fail_count),
        read_response(tx_fail_count),
        read_response(total_messages - 1),
        read_response(late_count),
    ]


def test_service_attaches_transport_recovery_summary_to_result() -> None:
    service = PcbTestService(DiagnosticFakeCanTransport(), EventLog())

    result = service._result("CAN Test", True, "PASS", 0.0, [])

    assert result.transport_warning is not None
    assert "2 malformed candidates" in result.transport_warning
    assert "checksum=1" in result.transport_warning


def test_can_test_get_id_passes_when_one_id_found() -> None:
    transport = FakeCanTransport()
    transport.connect()
    transport.queued_frames.append(scan_response(0x34))
    result = make_service(transport).run_can_test(timeout_ms=3000)
    assert result.verdict.passed is True
    assert result.verdict.returned_driver_id == 0x34
    assert len(transport.sent_frames) == 1
    assert transport.sent_frames[0].arbitration_id == 0x00000000
    assert transport.sent_frames[0].data == b"\x00"


def test_can_test_get_id_fails_when_no_motor_found() -> None:
    transport = FakeCanTransport()
    transport.connect()
    result = make_service(transport).run_can_test(timeout_ms=3000)
    assert result.verdict.passed is False
    assert "no motor" in result.verdict.reason.lower()


def test_can_test_get_id_fails_when_multiple_ids_found() -> None:
    transport = FakeCanTransport()
    transport.connect()
    transport.queued_frames.extend([scan_response(0x34), scan_response(0x35)])
    result = make_service(transport).run_can_test(timeout_ms=3000)
    assert result.verdict.passed is False
    assert "multiple" in result.verdict.reason.lower()
    assert "52" in result.verdict.reason
    assert "53" in result.verdict.reason


def test_openloop_passes_when_status_finishes_completed() -> None:
    transport = FakeCanTransport()
    transport.connect()
    transport.queued_frames.extend(
        [
            read_response(0),
            read_response(MotorTestStatus.FINISHED),
            read_response(MotorTestStopReason.COMPLETED),
            read_response(5000),
        ]
    )
    result = make_service(transport).run_openloop_test(timeout_ms=9000, driver_id=0x34)
    assert result.verdict.passed is True
    assert result.verdict.returned_driver_id == 0x34
    assert result.verdict.firmware_status == MotorTestStatus.FINISHED
    assert result.verdict.stop_reason == MotorTestStopReason.COMPLETED
    assert result.verdict.elapsed_ms == 5000
    assert transport.sent_frames[0].arbitration_id == 0x12000034
    assert transport.sent_frames[0].data[:2] == bytes.fromhex("10 72")
    assert any(frame.data == bytes.fromhex("04 72 00 00 54 53 45 54") for frame in transport.sent_frames)
    assert any(frame.data == bytes.fromhex("00 72 00 00 01 00 00 00") for frame in transport.sent_frames)
    assert transport.sent_frames[-3].arbitration_id == 0x11000034


def test_openloop_uses_selected_theta_and_duration() -> None:
    transport = FakeCanTransport()
    transport.connect()
    transport.queued_frames.extend(
        [
            read_response(0),
            read_response(MotorTestStatus.FINISHED),
            read_response(MotorTestStopReason.COMPLETED),
            read_response(12345),
        ]
    )
    result = make_service(transport).run_openloop_test(
        timeout_ms=9000,
        driver_id=0x34,
        theta_step=-0.0005,
        duration_ms=12345,
    )
    assert result.verdict.passed is True
    assert transport.sent_frames[2].data == bytes.fromhex("12 72 00 00") + struct.pack("<f", -0.0005)
    assert transport.sent_frames[3].data == bytes.fromhex("13 72 00 00 39 30 00 00")


def test_openloop_rejects_invalid_theta_and_duration_before_sending() -> None:
    transport = FakeCanTransport()
    transport.connect()
    result = make_service(transport).run_openloop_test(
        timeout_ms=9000,
        driver_id=0x34,
        theta_step=0.003,
    )
    assert result.verdict.passed is False
    assert "theta" in result.verdict.reason.lower()
    assert transport.sent_frames == []

    result = make_service(transport).run_openloop_test(
        timeout_ms=9000,
        driver_id=0x34,
        duration_ms=99,
    )
    assert result.verdict.passed is False
    assert "duration" in result.verdict.reason.lower()
    assert transport.sent_frames == []


def test_openloop_fails_on_blocked_status_and_sends_stop() -> None:
    transport = FakeCanTransport()
    transport.connect()
    transport.queued_frames.extend(
        [
            read_response(1),
            read_response(MotorTestStatus.BLOCKED_FAULT),
            read_response(MotorTestStopReason.MIT_DISABLE),
            read_response(12),
        ]
    )
    result = make_service(transport).run_openloop_test(timeout_ms=9000, driver_id=0x34)
    assert result.verdict.passed is False
    assert "blocked" in result.verdict.reason.lower()
    assert transport.sent_frames[-1].data == bytes([CAN_INDEX_TEST_ACTIVE & 0xFF, CAN_INDEX_TEST_ACTIVE >> 8, 0, 0, 0, 0, 0, 0])


def test_openloop_does_not_require_enable_or_write_feedback() -> None:
    transport = FakeCanTransport()
    transport.connect()
    transport.queued_frames.extend(
        [
            read_response(0),
            read_response(MotorTestStatus.FINISHED),
            read_response(MotorTestStopReason.COMPLETED),
            read_response(5000),
        ]
    )
    result = make_service(transport).run_openloop_test(timeout_ms=9000, driver_id=0x34)
    assert result.verdict.passed is True


def test_openloop_keeps_waiting_when_initial_status_is_idle() -> None:
    transport = FakeCanTransport()
    transport.connect()
    transport.queued_frames.extend(
        [
            read_response(0),
            read_response(MotorTestStatus.IDLE),
            read_response(MotorTestStopReason.NONE),
            read_response(0),
            read_response(1),
            read_response(MotorTestStatus.RUNNING),
            read_response(MotorTestStopReason.NONE),
            read_response(120),
            read_response(0),
            read_response(MotorTestStatus.FINISHED),
            read_response(MotorTestStopReason.COMPLETED),
            read_response(5000),
        ]
    )
    result = make_service(transport).run_openloop_test(timeout_ms=9000, driver_id=0x34)
    assert result.verdict.passed is True
    stop_payload = bytes([CAN_INDEX_TEST_ACTIVE & 0xFF, CAN_INDEX_TEST_ACTIVE >> 8, 0, 0, 0, 0, 0, 0])
    assert transport.sent_frames[-1].data != stop_payload


def test_openloop_fails_without_status_read_response() -> None:
    transport = FakeCanTransport()
    transport.connect()
    result = make_service(transport, poll_interval_s=0.0).run_openloop_test(timeout_ms=1, driver_id=0x34)
    assert result.verdict.passed is False
    assert "status" in result.verdict.reason.lower()
    assert transport.sent_frames[-1].data == bytes([CAN_INDEX_TEST_ACTIVE & 0xFF, CAN_INDEX_TEST_ACTIVE >> 8, 0, 0, 0, 0, 0, 0])


def test_openloop_timeout_sends_stop() -> None:
    transport = FakeCanTransport()
    transport.connect()
    transport.queued_frames.extend(
        [
            read_response(1),
            read_response(MotorTestStatus.RUNNING),
            read_response(MotorTestStopReason.NONE),
            read_response(0),
        ]
    )
    result = make_service(transport, poll_interval_s=0.01).run_openloop_test(timeout_ms=1, driver_id=0x34)
    assert result.verdict.passed is False
    assert "timeout" in result.verdict.reason.lower()
    assert transport.sent_frames[-1].data == bytes([CAN_INDEX_TEST_ACTIVE & 0xFF, CAN_INDEX_TEST_ACTIVE >> 8, 0, 0, 0, 0, 0, 0])


def test_openloop_passes_with_final_active_id_zero() -> None:
    transport = FakeCanTransport()
    transport.connect()
    transport.queued_frames.extend(
        [
            read_response(0),
            read_response(MotorTestStatus.FINISHED),
            read_response(MotorTestStopReason.COMPLETED),
            read_response(5000),
        ]
    )
    result = make_service(transport).run_openloop_test(timeout_ms=9000, driver_id=0x34)
    assert result.verdict.passed is True


def test_openloop_start_not_accepted_times_out_when_status_stays_idle() -> None:
    transport = FakeCanTransport()
    transport.connect()
    transport.queued_frames.extend(
        [
            read_response(0),
            read_response(MotorTestStatus.IDLE),
            read_response(MotorTestStopReason.NONE),
            read_response(0),
        ]
    )
    result = make_service(transport, poll_interval_s=0.01).run_openloop_test(timeout_ms=1, driver_id=0x34)
    assert result.verdict.passed is False
    assert "accepted" in result.verdict.reason.lower()
    assert transport.sent_frames[-1].data == bytes([CAN_INDEX_TEST_ACTIVE & 0xFF, CAN_INDEX_TEST_ACTIVE >> 8, 0, 0, 0, 0, 0, 0])


def test_can_stress_passes_when_finished_completed_and_all_messages_received() -> None:
    transport = FakeCanTransport()
    transport.connect()
    transport.queued_frames.extend(
        [
            can_stress_frame(0, 1),
            can_stress_frame(1, 2),
            can_stress_frame(2, 3),
            *can_stress_final_reads(total_messages=3),
        ]
    )
    result = make_service(transport, response_timeout_s=0.01).run_can_stress_test(
        timeout_ms=8000,
        driver_id=0x34,
        interval_ms=1,
        total_messages=3,
    )
    assert result.verdict.passed is True
    assert result.verdict.tx_attempt_count == 3
    assert result.verdict.tx_success_count == 3
    assert result.verdict.tx_fail_count == 0
    assert result.verdict.last_sequence == 2
    assert result.verdict.late_count == 0
    assert result.verdict.received_message_count == 3
    assert result.verdict.missing_message_count == 0
    assert result.verdict.duplicate_message_count == 0
    assert result.verdict.success_rate_percent == 100.0
    assert result.verdict.receive_rate_hz == 1000.0
    assert "Received 3/3 messages (100.0%)" in result.verdict.reason
    assert transport.sent_frames[0].data == bytes([CAN_INDEX_TEST_CAN_INTERVAL_MS & 0xFF, CAN_INDEX_TEST_CAN_INTERVAL_MS >> 8, 0, 0, 1, 0, 0, 0])
    assert transport.sent_frames[1].data == bytes([CAN_INDEX_TEST_CAN_TOTAL_MESSAGES & 0xFF, CAN_INDEX_TEST_CAN_TOTAL_MESSAGES >> 8, 0, 0]) + bytes.fromhex("03 00 00 00")
    assert any(frame.data == bytes.fromhex("00 72 00 00 02 00 00 00") for frame in transport.sent_frames)


def test_can_stress_handles_async_diagnostic_report_during_counter_reads() -> None:
    transport = FakeCanTransport()
    transport.connect()
    transport.queued_frames.extend(
        [
            can_stress_frame(0, 1),
            can_stress_frame(1, 2),
            can_stress_frame(2, 3),
            diagnostic_report(2),
            *can_stress_final_reads(total_messages=3),
        ]
    )
    result = make_service(transport, response_timeout_s=0.01).run_can_stress_test(
        timeout_ms=8000,
        driver_id=0x34,
        interval_ms=1,
        total_messages=3,
    )
    assert result.verdict.passed is True
    assert result.verdict.report_elapsed_ms == 3


def test_can_stress_passes_when_success_rate_above_95_percent() -> None:
    transport = FakeCanTransport()
    transport.connect()
    transport.queued_frames.extend(
        [
            can_stress_frame(0, 1),
            can_stress_frame(2, 3),
            *can_stress_final_reads(total_messages=3),
        ]
    )
    result = make_service(transport, response_timeout_s=0.01).run_can_stress_test(
        timeout_ms=8000,
        driver_id=0x34,
        interval_ms=1,
        total_messages=3,
    )
    assert result.verdict.passed is False
    assert result.verdict.success_rate_percent == pytest.approx(66.6666667)
    assert "missing=1" in result.verdict.reason


def test_can_stress_passes_with_small_loss_above_threshold() -> None:
    transport = FakeCanTransport()
    transport.connect()
    frames = [can_stress_frame(sequence, sequence + 1) for sequence in range(96)]
    transport.queued_frames.extend([*frames, *can_stress_final_reads(total_messages=100)])
    result = make_service(transport, response_timeout_s=0.01).run_can_stress_test(
        timeout_ms=8000,
        driver_id=0x34,
        interval_ms=1,
        total_messages=100,
    )
    assert result.verdict.passed is True
    assert result.verdict.success_rate_percent == 96.0
    assert "Received 96/100 messages (96.0%)" in result.verdict.reason


def test_can_stress_fails_when_duplicate_message_seen() -> None:
    transport = FakeCanTransport()
    transport.connect()
    transport.queued_frames.extend(
        [
            can_stress_frame(0, 1),
            can_stress_frame(1, 2),
            can_stress_frame(1, 3),
            *can_stress_final_reads(total_messages=3),
        ]
    )
    result = make_service(transport, response_timeout_s=0.01).run_can_stress_test(
        timeout_ms=8000,
        driver_id=0x34,
        interval_ms=1,
        total_messages=3,
    )
    assert result.verdict.passed is False
    assert result.verdict.success_rate_percent == pytest.approx(66.6666667)
    assert "missing=1" in result.verdict.reason
    assert "duplicate=1" in result.verdict.reason


def test_can_stress_invalid_interval_and_duration_fail_before_sending() -> None:
    transport = FakeCanTransport()
    transport.connect()
    result = make_service(transport).run_can_stress_test(timeout_ms=8000, driver_id=0x34, interval_ms=0)
    assert result.verdict.passed is False
    assert "interval" in result.verdict.reason.lower()
    assert transport.sent_frames == []

    result = make_service(transport).run_can_stress_test(timeout_ms=8000, driver_id=0x34, total_messages=0)
    assert result.verdict.passed is False
    assert "total messages" in result.verdict.reason.lower()
    assert transport.sent_frames == []


def test_can_stress_timeout_sends_stop() -> None:
    transport = FakeCanTransport()
    transport.connect()
    transport.queued_frames.extend(
        [
            read_response(2),
            read_response(MotorTestStatus.RUNNING),
            read_response(MotorTestStopReason.NONE),
            read_response(10),
            read_response(1),
            read_response(1),
            read_response(0),
            read_response(0),
            read_response(0),
        ]
    )
    result = make_service(transport, poll_interval_s=0.01).run_can_stress_test(timeout_ms=1, driver_id=0x34)
    assert result.verdict.passed is False
    assert result.verdict.success_rate_percent == 0.0
    assert f"Received 0/{CAN_STRESS_DEFAULT_TOTAL_MESSAGES} messages" in result.verdict.reason
    assert transport.sent_frames[-1].data == bytes.fromhex("00 72 00 00 00 00 00 00")
