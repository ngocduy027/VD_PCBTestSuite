"""Plain data types shared by the transport, protocol, service and UI layers.

``TestVerdict`` is deliberately flat: every test fills only the fields it
measures and leaves the rest ``None``; the result cards decide what to show.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class RawCanFrame:
    """A classic CAN frame as sent/received by every transport (payload 0..8 bytes)."""

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
    """Fields of the legacy 29-bit ID; see :func:`pcbt_app.protocol.ids.parse_extended_id`."""

    raw_id: int
    function_code: int
    state_byte: int
    driver_id: int
    host_id: int


@dataclass(slots=True, frozen=True)
class CanStressReportFrame:
    """Sequence number and board-side elapsed time from a CAN stress frame or report."""

    raw_frame: RawCanFrame
    sequence: int
    elapsed_ms: int


@dataclass(slots=True, frozen=True)
class DiagnosticTestReportFrame:
    """Completion report the legacy firmware sends when a diagnostic test ends."""

    raw_frame: RawCanFrame
    test_id: int
    status: int
    stop_reason: int
    elapsed_ms: int


@dataclass(slots=True, frozen=True)
class GongwangResultSet:
    """Statistics read back after a GongWang encoder test.

    Field names mirror the firmware's ``0x7240..0x7262`` result block (see
    ``GONGWANG_RESULT_FIELDS``); a field is ``None`` when it could not be read.
    """

    duration_ms: int | None = None
    schema_version: int | None = None
    target_hz: int | None = None
    rx_mode: int | None = None
    command: int | None = None
    response_length: int | None = None
    scheduled_count: int | None = None
    started_count: int | None = None
    response_count: int | None = None
    valid_count: int | None = None
    actual_request_hz: int | None = None
    actual_response_hz: int | None = None
    valid_response_hz: int | None = None
    success_percent: float | None = None
    received_byte_count: int | None = None
    received_byte_hz: int | None = None
    discarded_byte_count: int | None = None
    ring_overrun_count: int | None = None
    missed_tx_slot_count: int | None = None
    busy_deadline_count: int | None = None
    timeout_count: int | None = None
    crc_error_count: int | None = None
    header_error_count: int | None = None
    uart_error_count: int | None = None
    decode_queue_overflow_count: int | None = None
    fault_response_count: int | None = None
    latest_status: int | None = None
    input_single_turn_raw: int | None = None
    output_single_turn_raw: int | None = None
    output_multi_turn_raw: int | None = None
    fault_code: int | None = None
    input_single_turn_deg: float | None = None
    output_single_turn_deg: float | None = None
    output_total_turns: float | None = None
    output_total_deg: float | None = None

    @property
    def has_valid_position(self) -> bool:
        """True when at least one decoded encoder response was valid."""
        return self.valid_count is not None and self.valid_count > 0


@dataclass(slots=True)
class TestVerdict:
    """PASS/FAIL outcome of one test, plus whatever metrics that test measured.

    ``reason`` is the operator-facing explanation; ``events`` is a step-by-step
    trace for the console. Unused metric fields stay ``None``.
    """

    test_name: str
    passed: bool
    reason: str
    returned_driver_id: int | None = None
    fail_mask: int | None = None
    fsr1: int | None = None
    fsr2: int | None = None
    firmware_status: int | None = None
    stop_reason: int | None = None
    elapsed_ms: int | None = None
    fct_state: int | None = None
    fct_pass_count: int | None = None
    fct_fail_count: int | None = None
    tx_attempt_count: int | None = None
    tx_success_count: int | None = None
    tx_fail_count: int | None = None
    last_sequence: int | None = None
    late_count: int | None = None
    report_sequence: int | None = None
    report_elapsed_ms: int | None = None
    received_message_count: int | None = None
    actual_response_count: int | None = None
    expected_message_count: int | None = None
    missing_message_count: int | None = None
    duplicate_message_count: int | None = None
    success_rate_percent: float | None = None
    receive_rate_hz: float | None = None
    received_byte_count: int | None = None
    bytes_per_message: int | None = None
    requested_duration_ms: int | None = None
    period_ticks: int | None = None
    requested_frequency_hz: float | None = None
    malformed_count: int | None = None
    sequence_error_count: int | None = None
    first_sequence: int | None = None
    parser_buffer_length: int | None = None
    gongwang_results: GongwangResultSet | None = None
    events: list[str] = field(default_factory=list)

    @property
    def status_text(self) -> str:
        """``"PASS"`` or ``"FAIL"``."""
        return "PASS" if self.passed else "FAIL"
