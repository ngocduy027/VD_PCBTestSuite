from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


class TestId(IntEnum):
    OPEN_LOOP = 1
    CAN = 2
    GONGWANG_ENCODER = 4


class ResultCode(IntEnum):
    NOT_RUN = 0
    RUNNING = 1
    PASS = 2
    FAIL = 3


@dataclass(slots=True, frozen=True)
class RawCanFrame:
    arbitration_id: int
    data: bytes
    is_extended_id: bool = True
    timestamp: float = 0.0
    channel: str | None = None

    def __post_init__(self) -> None:
        if len(self.data) > 8:
            raise ValueError("Classic CAN payload must be 0 to 8 bytes")


@dataclass(slots=True, frozen=True)
class CanIdFields:
    raw_id: int
    function_code: int
    state_byte: int
    driver_id: int
    host_id: int


@dataclass(slots=True, frozen=True)
class PcbTestResultFrame:
    raw_frame: RawCanFrame
    id_fields: CanIdFields
    test_id: int
    result: int
    fail_mask: int
    actual_return_id: int
    fsr1: int
    fsr2: int


@dataclass(slots=True, frozen=True)
class CanIdResponseFrame:
    raw_frame: RawCanFrame
    id_fields: CanIdFields
    data: bytes


@dataclass(slots=True, frozen=True)
class ParsedFrame:
    raw_frame: RawCanFrame
    kind: str
    can_id_response: Optional[CanIdResponseFrame] = None
    pcb_result: Optional[PcbTestResultFrame] = None
    id_fields: Optional[CanIdFields] = None


@dataclass(slots=True, frozen=True)
class CanStressReportFrame:
    raw_frame: RawCanFrame
    sequence: int
    elapsed_ms: int


@dataclass(slots=True, frozen=True)
class DiagnosticTestReportFrame:
    raw_frame: RawCanFrame
    test_id: int
    status: int
    stop_reason: int
    elapsed_ms: int


@dataclass(slots=True, frozen=True)
class GongwangResultSet:
    duration_ms: Optional[int] = None
    schema_version: Optional[int] = None
    target_hz: Optional[int] = None
    rx_mode: Optional[int] = None
    command: Optional[int] = None
    response_length: Optional[int] = None
    scheduled_count: Optional[int] = None
    started_count: Optional[int] = None
    response_count: Optional[int] = None
    valid_count: Optional[int] = None
    actual_request_hz: Optional[int] = None
    actual_response_hz: Optional[int] = None
    valid_response_hz: Optional[int] = None
    success_percent: Optional[float] = None
    received_byte_count: Optional[int] = None
    received_byte_hz: Optional[int] = None
    discarded_byte_count: Optional[int] = None
    ring_overrun_count: Optional[int] = None
    missed_tx_slot_count: Optional[int] = None
    busy_deadline_count: Optional[int] = None
    timeout_count: Optional[int] = None
    crc_error_count: Optional[int] = None
    header_error_count: Optional[int] = None
    uart_error_count: Optional[int] = None
    decode_queue_overflow_count: Optional[int] = None
    fault_response_count: Optional[int] = None
    latest_status: Optional[int] = None
    input_single_turn_raw: Optional[int] = None
    output_single_turn_raw: Optional[int] = None
    output_multi_turn_raw: Optional[int] = None
    fault_code: Optional[int] = None
    input_single_turn_deg: Optional[float] = None
    output_single_turn_deg: Optional[float] = None
    output_total_turns: Optional[float] = None
    output_total_deg: Optional[float] = None

    @property
    def has_valid_position(self) -> bool:
        return self.valid_count is not None and self.valid_count > 0


@dataclass(slots=True, frozen=True)
class FailureDetail:
    bit: int
    name: str
    description: str


@dataclass(slots=True)
class TestVerdict:
    test_name: str
    passed: bool
    reason: str
    returned_driver_id: Optional[int] = None
    fail_mask: Optional[int] = None
    fsr1: Optional[int] = None
    fsr2: Optional[int] = None
    firmware_status: Optional[int] = None
    stop_reason: Optional[int] = None
    elapsed_ms: Optional[int] = None
    fct_state: Optional[int] = None
    fct_pass_count: Optional[int] = None
    fct_fail_count: Optional[int] = None
    tx_attempt_count: Optional[int] = None
    tx_success_count: Optional[int] = None
    tx_fail_count: Optional[int] = None
    last_sequence: Optional[int] = None
    late_count: Optional[int] = None
    report_sequence: Optional[int] = None
    report_elapsed_ms: Optional[int] = None
    received_message_count: Optional[int] = None
    actual_response_count: Optional[int] = None
    expected_message_count: Optional[int] = None
    missing_message_count: Optional[int] = None
    duplicate_message_count: Optional[int] = None
    success_rate_percent: Optional[float] = None
    receive_rate_hz: Optional[float] = None
    received_byte_count: Optional[int] = None
    bytes_per_message: Optional[int] = None
    requested_duration_ms: Optional[int] = None
    period_ticks: Optional[int] = None
    requested_frequency_hz: Optional[float] = None
    malformed_count: Optional[int] = None
    sequence_error_count: Optional[int] = None
    first_sequence: Optional[int] = None
    parser_buffer_length: Optional[int] = None
    gongwang_results: Optional[GongwangResultSet] = None
    events: list[str] = field(default_factory=list)

    @property
    def status_text(self) -> str:
        return "PASS" if self.passed else "FAIL"
