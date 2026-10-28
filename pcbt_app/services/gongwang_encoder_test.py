"""Legacy GongWang encoder test (firmware diagnostic test ID 4).

The firmware drives the encoder UART at 20 kHz for the requested duration and
then reports completion; the PC reads the ``0x7240..0x7262`` result block and
judges it against :class:`GongwangAcceptanceCriteria`.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from time import monotonic
from typing import TYPE_CHECKING, Any

from pcbt_app.models import DiagnosticTestReportFrame, GongwangResultSet
from pcbt_app.protocol.mit_firmware import (
    CAN_INDEX_TEST_GONGWANG_ACTUAL_REQUEST_HZ,
    CAN_INDEX_TEST_GONGWANG_ACTUAL_RESPONSE_HZ,
    CAN_INDEX_TEST_GONGWANG_BUSY_DEADLINE_COUNT,
    CAN_INDEX_TEST_GONGWANG_COMMAND,
    CAN_INDEX_TEST_GONGWANG_CRC_ERROR_COUNT,
    CAN_INDEX_TEST_GONGWANG_DECODE_QUEUE_OVERFLOW_COUNT,
    CAN_INDEX_TEST_GONGWANG_DISCARDED_BYTE_COUNT,
    CAN_INDEX_TEST_GONGWANG_DURATION_MS,
    CAN_INDEX_TEST_GONGWANG_FAULT_CODE,
    CAN_INDEX_TEST_GONGWANG_FAULT_RESPONSE_COUNT,
    CAN_INDEX_TEST_GONGWANG_HEADER_ERROR_COUNT,
    CAN_INDEX_TEST_GONGWANG_INPUT_SINGLE_TURN_DEG,
    CAN_INDEX_TEST_GONGWANG_INPUT_SINGLE_TURN_RAW,
    CAN_INDEX_TEST_GONGWANG_LATEST_STATUS,
    CAN_INDEX_TEST_GONGWANG_MISSED_TX_SLOT_COUNT,
    CAN_INDEX_TEST_GONGWANG_OUTPUT_MULTI_TURN_RAW,
    CAN_INDEX_TEST_GONGWANG_OUTPUT_SINGLE_TURN_DEG,
    CAN_INDEX_TEST_GONGWANG_OUTPUT_SINGLE_TURN_RAW,
    CAN_INDEX_TEST_GONGWANG_OUTPUT_TOTAL_DEG,
    CAN_INDEX_TEST_GONGWANG_OUTPUT_TOTAL_TURNS,
    CAN_INDEX_TEST_GONGWANG_RECEIVED_BYTE_COUNT,
    CAN_INDEX_TEST_GONGWANG_RECEIVED_BYTE_HZ,
    CAN_INDEX_TEST_GONGWANG_RESPONSE_COUNT,
    CAN_INDEX_TEST_GONGWANG_RESPONSE_LENGTH,
    CAN_INDEX_TEST_GONGWANG_RING_OVERRUN_COUNT,
    CAN_INDEX_TEST_GONGWANG_RX_MODE,
    CAN_INDEX_TEST_GONGWANG_SCHEDULED_COUNT,
    CAN_INDEX_TEST_GONGWANG_SCHEMA_VERSION,
    CAN_INDEX_TEST_GONGWANG_STARTED_COUNT,
    CAN_INDEX_TEST_GONGWANG_SUCCESS_PERCENT,
    CAN_INDEX_TEST_GONGWANG_TARGET_HZ,
    CAN_INDEX_TEST_GONGWANG_TIMEOUT_COUNT,
    CAN_INDEX_TEST_GONGWANG_UART_ERROR_COUNT,
    CAN_INDEX_TEST_GONGWANG_VALID_COUNT,
    CAN_INDEX_TEST_GONGWANG_VALID_RESPONSE_HZ,
    GONGWANG_DEFAULT_DURATION_MS,
    GONGWANG_EXPECTED_COMMAND,
    GONGWANG_EXPECTED_RESPONSE_LENGTH,
    GONGWANG_EXPECTED_RX_MODE,
    GONGWANG_EXPECTED_SCHEMA_VERSION,
    GONGWANG_EXPECTED_TARGET_HZ,
    GONGWANG_MAX_DURATION_MS,
    GONGWANG_MIN_DURATION_MS,
    GONGWANG_MIN_RATE_PERCENT,
    GONGWANG_MIN_SUCCESS_PERCENT,
    GONGWANG_TIMEOUT_MARGIN_MS,
    GONGWANG_TIMER_HZ,
    MotorTestId,
    MotorTestStatus,
    MotorTestStopReason,
    build_gongwang_config_frames,
    build_gongwang_start_frame,
    build_gongwang_stop_frame,
    build_openloop_unlock_frame,
    parse_diagnostic_test_report,
    status_label,
    stop_reason_label,
)
from pcbt_app.transport.base import CanTransportError

if TYPE_CHECKING:
    from pcbt_app.services.pcb_test_service import PcbTestService, ServiceResult


@dataclass(slots=True, frozen=True)
class GongwangAcceptanceCriteria:
    """Pass criteria for the legacy GongWang test.

    Metadata fields must equal the expected firmware schema (version 1, 20 kHz,
    circular-ring RX, command ``0x9A``, 10-byte response). Success percentage and
    the request/valid-response rates (relative to the rate achievable with the
    10 MHz timer) must reach the minimum percentages.
    """

    schema_version: int = GONGWANG_EXPECTED_SCHEMA_VERSION
    target_hz: int = GONGWANG_EXPECTED_TARGET_HZ
    rx_mode: int = GONGWANG_EXPECTED_RX_MODE
    command: int = GONGWANG_EXPECTED_COMMAND
    response_length: int = GONGWANG_EXPECTED_RESPONSE_LENGTH
    minimum_success_percent: float = GONGWANG_MIN_SUCCESS_PERCENT
    minimum_rate_percent: float = GONGWANG_MIN_RATE_PERCENT
    timer_hz: int = GONGWANG_TIMER_HZ


DEFAULT_GONGWANG_CRITERIA = GongwangAcceptanceCriteria()

# Attribute name, firmware index, wire type, required for verdict.
GONGWANG_RESULT_FIELDS: tuple[tuple[str, int, str, bool], ...] = (
    ("duration_ms", CAN_INDEX_TEST_GONGWANG_DURATION_MS, "u32", False),
    ("schema_version", CAN_INDEX_TEST_GONGWANG_SCHEMA_VERSION, "u32", True),
    ("target_hz", CAN_INDEX_TEST_GONGWANG_TARGET_HZ, "u32", True),
    ("rx_mode", CAN_INDEX_TEST_GONGWANG_RX_MODE, "u32", True),
    ("command", CAN_INDEX_TEST_GONGWANG_COMMAND, "u32", True),
    ("response_length", CAN_INDEX_TEST_GONGWANG_RESPONSE_LENGTH, "u32", True),
    ("scheduled_count", CAN_INDEX_TEST_GONGWANG_SCHEDULED_COUNT, "u32", False),
    ("started_count", CAN_INDEX_TEST_GONGWANG_STARTED_COUNT, "u32", False),
    ("response_count", CAN_INDEX_TEST_GONGWANG_RESPONSE_COUNT, "u32", False),
    ("valid_count", CAN_INDEX_TEST_GONGWANG_VALID_COUNT, "u32", False),
    ("actual_request_hz", CAN_INDEX_TEST_GONGWANG_ACTUAL_REQUEST_HZ, "u32", True),
    ("actual_response_hz", CAN_INDEX_TEST_GONGWANG_ACTUAL_RESPONSE_HZ, "u32", False),
    ("valid_response_hz", CAN_INDEX_TEST_GONGWANG_VALID_RESPONSE_HZ, "u32", True),
    ("success_percent", CAN_INDEX_TEST_GONGWANG_SUCCESS_PERCENT, "float", True),
    ("received_byte_count", CAN_INDEX_TEST_GONGWANG_RECEIVED_BYTE_COUNT, "u32", False),
    ("received_byte_hz", CAN_INDEX_TEST_GONGWANG_RECEIVED_BYTE_HZ, "u32", False),
    ("discarded_byte_count", CAN_INDEX_TEST_GONGWANG_DISCARDED_BYTE_COUNT, "u32", False),
    ("ring_overrun_count", CAN_INDEX_TEST_GONGWANG_RING_OVERRUN_COUNT, "u32", False),
    ("missed_tx_slot_count", CAN_INDEX_TEST_GONGWANG_MISSED_TX_SLOT_COUNT, "u32", False),
    ("busy_deadline_count", CAN_INDEX_TEST_GONGWANG_BUSY_DEADLINE_COUNT, "u32", False),
    ("timeout_count", CAN_INDEX_TEST_GONGWANG_TIMEOUT_COUNT, "u32", False),
    ("crc_error_count", CAN_INDEX_TEST_GONGWANG_CRC_ERROR_COUNT, "u32", False),
    ("header_error_count", CAN_INDEX_TEST_GONGWANG_HEADER_ERROR_COUNT, "u32", False),
    ("uart_error_count", CAN_INDEX_TEST_GONGWANG_UART_ERROR_COUNT, "u32", False),
    ("decode_queue_overflow_count", CAN_INDEX_TEST_GONGWANG_DECODE_QUEUE_OVERFLOW_COUNT, "u32", False),
    ("fault_response_count", CAN_INDEX_TEST_GONGWANG_FAULT_RESPONSE_COUNT, "u32", False),
    ("latest_status", CAN_INDEX_TEST_GONGWANG_LATEST_STATUS, "u32", False),
    ("input_single_turn_raw", CAN_INDEX_TEST_GONGWANG_INPUT_SINGLE_TURN_RAW, "u32", False),
    ("output_single_turn_raw", CAN_INDEX_TEST_GONGWANG_OUTPUT_SINGLE_TURN_RAW, "u32", False),
    ("output_multi_turn_raw", CAN_INDEX_TEST_GONGWANG_OUTPUT_MULTI_TURN_RAW, "u32", False),
    ("fault_code", CAN_INDEX_TEST_GONGWANG_FAULT_CODE, "u32", False),
    ("input_single_turn_deg", CAN_INDEX_TEST_GONGWANG_INPUT_SINGLE_TURN_DEG, "float", False),
    ("output_single_turn_deg", CAN_INDEX_TEST_GONGWANG_OUTPUT_SINGLE_TURN_DEG, "float", False),
    ("output_total_turns", CAN_INDEX_TEST_GONGWANG_OUTPUT_TOTAL_TURNS, "float", False),
    ("output_total_deg", CAN_INDEX_TEST_GONGWANG_OUTPUT_TOTAL_DEG, "float", False),
)


def run_gongwang_encoder_test(
    service: PcbTestService,
    duration_ms: int = GONGWANG_DEFAULT_DURATION_MS,
    driver_id: int | None = None,
    criteria: GongwangAcceptanceCriteria = DEFAULT_GONGWANG_CRITERIA,
) -> ServiceResult:
    """Run the legacy GongWang test and read back its full result block.

    Waits up to ``duration_ms + GONGWANG_TIMEOUT_MARGIN_MS`` for the completion
    report, then reads every field in ``GONGWANG_RESULT_FIELDS`` one index at a
    time and judges them with ``criteria``.
    """
    start = monotonic()
    events: list[str] = []

    if driver_id is None:
        return service._result("GongWang Encoder Test", False, "Driver ID is required; run Detect ID first", start, events)
    if not 0 <= driver_id <= 0xFF:
        return service._result("GongWang Encoder Test", False, f"Driver ID out of range: {driver_id}", start, events)
    if not GONGWANG_MIN_DURATION_MS <= duration_ms <= GONGWANG_MAX_DURATION_MS:
        return service._result(
            "GongWang Encoder Test",
            False,
            f"GongWang duration must be {GONGWANG_MIN_DURATION_MS}..{GONGWANG_MAX_DURATION_MS} ms",
            start,
            events,
        )

    drain_result = service._drain_before_command("GongWang Encoder Test")
    if drain_result is not None:
        return drain_result

    deadline = start + (duration_ms + GONGWANG_TIMEOUT_MARGIN_MS) / 1000.0
    report = None
    values: dict[str, Any] = {name: None for name, _, _, _ in GONGWANG_RESULT_FIELDS}
    report_state: dict[str, int | float | None] = {
        "report_test_id": None,
        "report_status": None,
        "report_stop_reason": None,
        "report_elapsed_ms": None,
    }

    try:
        for config_frame in build_gongwang_config_frames(driver_id, duration_ms):
            service._send_diagnostic_command(config_frame, "write 0x7240 duration", deadline)
        service._send_diagnostic_command(build_openloop_unlock_frame(driver_id), "unlock diagnostic test", deadline)
        events.append("wrote 0x7204 = 0x54455354")
        service._send_diagnostic_command(build_gongwang_start_frame(driver_id), "start GongWang encoder test", deadline)
        events.append("wrote 0x7200 = 4")

        while monotonic() < deadline:
            frame = service._recv_once(min(0.01, max(0.0, deadline - monotonic())))
            if frame is None:
                continue
            candidate = parse_diagnostic_test_report(frame, driver_id, test_id=MotorTestId.GONGWANG_ENCODER)
            if candidate is None:
                events.append(f"ignored frame 0x{frame.arbitration_id:08X}")
                continue
            report = candidate
            events.append(
                f"diagnostic report test={report.test_id} status={report.status} "
                f"stop_reason={report.stop_reason} elapsed_ms={report.elapsed_ms}"
            )
            break

        if report is None:
            reason = "Timeout waiting for GongWang encoder completion report"
            events.append(reason)
            _cleanup(service, driver_id, events)
            return service._result(
                "GongWang Encoder Test", False, reason, start, events, returned_driver_id=driver_id
            )

        report_state.update(
            report_test_id=report.test_id,
            report_status=report.status,
            report_stop_reason=report.stop_reason,
            report_elapsed_ms=report.elapsed_ms,
        )
        for name, index, wire_type, _required in GONGWANG_RESULT_FIELDS:
            if wire_type == "float":
                values[name] = service._read_float(driver_id, index, deadline, events, report_state)
            else:
                values[name] = service._read_u32(driver_id, index, deadline, events, report_counters=report_state)
    except CanTransportError as exc:
        events.append(str(exc))
        _cleanup(service, driver_id, events)
        result_set = GongwangResultSet(**values)
        return service._result(
            "GongWang Encoder Test",
            False,
            str(exc),
            start,
            events,
            returned_driver_id=driver_id,
            firmware_status=None if report is None else report.status,
            stop_reason=None if report is None else report.stop_reason,
            elapsed_override_ms=None if report is None else report.elapsed_ms,
            gongwang_results=result_set,
        )

    result_set = GongwangResultSet(**values)
    return _finish_result(service, start, events, driver_id, report, result_set, criteria)


def _cleanup(service: PcbTestService, driver_id: int, events: list[str]) -> None:
    """Best-effort stop of the GongWang test after a failure."""
    try:
        service._send(build_gongwang_stop_frame(driver_id), "stop GongWang encoder test")
        events.append("wrote 0x7200 = 0")
    except CanTransportError as exc:
        events.append(f"failed to send stop command: {exc}")


def _finish_result(
    service: PcbTestService,
    start: float,
    events: list[str],
    driver_id: int,
    report: DiagnosticTestReportFrame,
    results: GongwangResultSet,
    criteria: GongwangAcceptanceCriteria,
) -> ServiceResult:
    """Judge the GongWang results.

    PASS requires: all required fields read, a clean completion report
    (FINISHED/COMPLETED), matching metadata, enough successful responses, and
    request/valid-response rates close enough to the achievable rate.
    """
    required_missing = [
        name for name, _index, _wire_type, required in GONGWANG_RESULT_FIELDS
        if required and getattr(results, name) is None
    ]
    informational_missing = [
        name for name, _index, _wire_type, required in GONGWANG_RESULT_FIELDS
        if not required and getattr(results, name) is None
    ]
    if informational_missing:
        events.append("warning: missing informational results: " + ", ".join(informational_missing))

    firmware_ok = (
        report.test_id == MotorTestId.GONGWANG_ENCODER
        and report.status == MotorTestStatus.FINISHED
        and report.stop_reason == MotorTestStopReason.COMPLETED
    )
    metadata_ok = (
        results.schema_version == criteria.schema_version
        and results.target_hz == criteria.target_hz
        and results.rx_mode == criteria.rx_mode
        and results.command == criteria.command
        and results.response_length == criteria.response_length
    )
    success_ok = (
        results.success_percent is not None
        and isfinite(results.success_percent)
        and results.success_percent >= criteria.minimum_success_percent
    )

    achievable_hz = 0.0
    request_rate_percent = 0.0
    valid_rate_percent = 0.0
    if results.target_hz is not None and 0 < results.target_hz <= criteria.timer_hz:
        period_ticks = criteria.timer_hz // results.target_hz
        if period_ticks > 0:
            achievable_hz = criteria.timer_hz / period_ticks
            if results.actual_request_hz is not None:
                request_rate_percent = results.actual_request_hz * 100.0 / achievable_hz
            if results.valid_response_hz is not None:
                valid_rate_percent = results.valid_response_hz * 100.0 / achievable_hz
    rates_ok = (
        request_rate_percent >= criteria.minimum_rate_percent
        and valid_rate_percent >= criteria.minimum_rate_percent
    )

    passed = not required_missing and firmware_ok and metadata_ok and success_ok and rates_ok
    reason = (
        f"success={_percent(results.success_percent)}; achievable={achievable_hz:.1f} Hz; "
        f"request={_rate(results.actual_request_hz, request_rate_percent)}; "
        f"complete={_hz(results.actual_response_hz)}; valid={_rate(results.valid_response_hz, valid_rate_percent)}"
    )
    if not firmware_ok:
        reason = (
            f"GongWang ended with status {status_label(report.status)} and "
            f"stop reason {stop_reason_label(report.stop_reason)}; {reason}"
        )
    if not metadata_ok:
        reason = f"Result metadata does not match schema 1 / 20 kHz circular-ring protocol; {reason}"
    if required_missing:
        reason = f"Missing required results: {', '.join(required_missing)}; {reason}"
    if informational_missing:
        reason += f"; warning: missing informational results: {', '.join(informational_missing)}"

    return service._result(
        "GongWang Encoder Test",
        passed,
        reason,
        start,
        events,
        returned_driver_id=driver_id,
        firmware_status=report.status,
        stop_reason=report.stop_reason,
        elapsed_override_ms=report.elapsed_ms,
        success_rate_percent=results.success_percent,
        receive_rate_hz=None if results.valid_response_hz is None else float(results.valid_response_hz),
        received_byte_count=results.received_byte_count,
        gongwang_results=results,
    )


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"


def _hz(value: int | None) -> str:
    return "-" if value is None else f"{value} Hz"


def _rate(value: int | None, percent: float) -> str:
    return "-" if value is None else f"{value} Hz ({percent:.1f}% achievable)"
