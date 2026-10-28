from __future__ import annotations

from collections import deque

import pytest

from pcbt_app.logging_store import EventLog
from pcbt_app.models import RawCanFrame
from pcbt_app.protocol.fct_can import (
    FCT_HOST_ID,
    FCT_RESPONSE_FUNCTION,
    FctCommandResult,
    FctOpcode,
    FctState,
    FctTestId,
    build_fct_id,
    parse_fct_id,
)
from pcbt_app.services.pcb_test_service import PcbTestService
from pcbt_app.transport.base import AbstractCanTransport
from pcbt_app.transport.rs485_serial import FctRs485StreamParser, Rs485ParserSnapshot

DRIVER_ID = 0x34


def rs485_frame(sequence: int) -> bytes:
    body = b"\x9A" + sequence.to_bytes(3, "little") + b"\xAA" * 5
    checksum = 0
    for byte in body:
        checksum ^= byte
    return body + bytes((checksum,))


def parser_snapshot(data_parts: list[bytes], *, finalize: bool = False) -> Rs485ParserSnapshot:
    parser = FctRs485StreamParser()
    for part in data_parts:
        parser.feed(part)
    if finalize:
        parser.finalize()
    return parser.snapshot()


def test_parser_accepts_one_complete_valid_frame() -> None:
    snapshot = parser_snapshot([rs485_frame(0)])
    assert snapshot.valid_frame_count == 1
    assert snapshot.first_sequence == snapshot.last_sequence == 0
    assert snapshot.malformed_count == snapshot.sequence_error_count == 0


def test_parser_handles_frame_divided_across_reads() -> None:
    frame = rs485_frame(0)
    snapshot = parser_snapshot([frame[:2], frame[2:7], frame[7:]])
    assert snapshot.valid_frame_count == 1
    assert snapshot.buffer_length == 0


def test_parser_handles_several_frames_in_one_read() -> None:
    snapshot = parser_snapshot([rs485_frame(0) + rs485_frame(1) + rs485_frame(2)])
    assert snapshot.valid_frame_count == 3
    assert snapshot.last_sequence == 2


def test_parser_handles_one_second_of_max_rate_input_in_one_chunk() -> None:
    snapshot = parser_snapshot(
        [b"".join(rs485_frame(sequence) for sequence in range(20_000))]
    )
    assert snapshot.valid_frame_count == 20_000
    assert snapshot.last_sequence == 19_999
    assert snapshot.malformed_count == snapshot.sequence_error_count == 0


def test_parser_resynchronizes_after_leading_and_interframe_garbage() -> None:
    snapshot = parser_snapshot([b"\x01\x02" + rs485_frame(0) + b"\x03" + rs485_frame(1)])
    assert snapshot.valid_frame_count == 2
    assert snapshot.malformed_count == 2


@pytest.mark.parametrize("corruption", ["command", "pattern", "checksum"])
def test_parser_rejects_bad_structure_and_recovers(corruption: str) -> None:
    invalid = bytearray(rs485_frame(0))
    if corruption == "command":
        invalid[0] = 0x99
    elif corruption == "pattern":
        invalid[5] = 0x00
    else:
        invalid[9] ^= 0x01
    snapshot = parser_snapshot([bytes(invalid) + rs485_frame(0)])
    assert snapshot.valid_frame_count == 1
    assert snapshot.malformed_count > 0


@pytest.mark.parametrize(
    ("sequences", "first_error"),
    [
        ([0, 0], (1, 0)),
        ([0, 2], (1, 2)),
        ([0, 2, 1], (1, 2)),
    ],
)
def test_parser_reports_duplicate_missing_and_out_of_order_sequences(
    sequences: list[int], first_error: tuple[int, int]
) -> None:
    snapshot = parser_snapshot([b"".join(rs485_frame(value) for value in sequences)])
    assert snapshot.sequence_error_count >= 1
    assert snapshot.first_sequence_error == first_error


def test_parser_marks_incomplete_final_frame_as_malformed() -> None:
    snapshot = parser_snapshot([rs485_frame(0)[:6]], finalize=True)
    assert snapshot.valid_frame_count == 0
    assert snapshot.malformed_count == 1
    assert snapshot.buffer_length == 0


def fct_response(opcode, state, *, passed=0, failed=0):
    counter_msb = ((failed >> 16) << 4) | (passed >> 16)
    return RawCanFrame(
        build_fct_id(FCT_RESPONSE_FUNCTION, opcode, DRIVER_ID, FCT_HOST_ID),
        bytes((FctCommandResult.OK, FctTestId.RS485, state, counter_msb))
        + (passed & 0xFFFF).to_bytes(2, "little")
        + (failed & 0xFFFF).to_bytes(2, "little"),
    )


class Rs485FctTransport(AbstractCanTransport):
    def __init__(self, terminal: FctState, *, passed: int, failed: int = 0) -> None:
        self.terminal = terminal
        self.passed = passed
        self.failed = failed
        self.sent_frames: list[RawCanFrame] = []
        self.queued: deque[RawCanFrame] = deque()

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def is_connected(self) -> bool:
        return True

    def send(self, frame: RawCanFrame) -> None:
        self.sent_frames.append(frame)
        fields = parse_fct_id(frame.arbitration_id)
        if fields.opcode == FctOpcode.START:
            self.queued.append(fct_response(fields.opcode, FctState.RUNNING))
        elif fields.opcode == FctOpcode.STATUS:
            self.queued.append(
                fct_response(fields.opcode, self.terminal, passed=self.passed, failed=self.failed)
            )
        elif fields.opcode == FctOpcode.STOP:
            self.queued.append(
                fct_response(fields.opcode, FctState.ABORTED, passed=self.passed, failed=self.failed)
            )

    def recv(self, timeout_s: float) -> RawCanFrame | None:
        return self.queued.popleft() if self.queued else None

    def drain(self) -> list[RawCanFrame]:
        return []


class FakeRs485Capture:
    def __init__(self, snapshot: Rs485ParserSnapshot) -> None:
        self.snapshot = snapshot
        self.started = False
        self.finished = False

    def is_connected(self) -> bool:
        return True

    def begin_capture(self) -> None:
        self.started = True

    def finish_capture(self) -> Rs485ParserSnapshot:
        self.finished = True
        return self.snapshot

    def capture_snapshot(self) -> Rs485ParserSnapshot:
        return self.snapshot


def snapshot(valid: int, **changes) -> Rs485ParserSnapshot:
    values = dict(
        valid_frame_count=valid,
        malformed_count=0,
        sequence_error_count=0,
        first_sequence=0 if valid else None,
        last_sequence=valid - 1 if valid else None,
        expected_sequence=valid,
        first_sequence_error=None,
        buffer_length=0,
        received_byte_count=valid * 10,
        transport_error=None,
    )
    values.update(changes)
    return Rs485ParserSnapshot(**values)


def service(can_transport) -> PcbTestService:
    return PcbTestService(
        can_transport,
        EventLog(),
        use_fct=True,
        poll_interval_s=0,
        response_timeout_s=0.01,
    )


@pytest.fixture(autouse=True)
def skip_drain_wait(monkeypatch):
    monkeypatch.setattr("pcbt_app.services.rs485_stress_test.sleep", lambda _seconds: None)


def run_case(
    terminal=FctState.PASS,
    *,
    firmware_pass=3,
    firmware_fail=0,
    capture=None,
    app_timeout_ms=1000,
    response_count_callback=None,
):
    can = Rs485FctTransport(terminal, passed=firmware_pass, failed=firmware_fail)
    serial_capture = FakeRs485Capture(capture or snapshot(firmware_pass))
    result = service(can).run_rs485_stress_test(
        app_timeout_ms,
        DRIVER_ID,
        serial_capture,
        duration_ms=100,
        period_ticks=1,
        response_count_callback=response_count_callback,
    )
    return result, can, serial_capture


def test_rs485_workflow_uses_new_fct_start_and_strict_pass() -> None:
    result, can, serial_capture = run_case()
    assert result.verdict.passed
    assert serial_capture.started and serial_capture.finished
    assert can.sent_frames[0].data == bytes.fromhex("03 01 64 00 00 00 00 00")
    assert result.verdict.fct_pass_count == 3
    assert result.verdict.received_message_count == 3
    assert result.verdict.requested_frequency_hz == 20_000.0


def test_rs485_workflow_publishes_actual_pc_receive_count() -> None:
    progress: list[int] = []
    result, _, _ = run_case(response_count_callback=progress.append)
    assert result.verdict.passed
    assert progress == [0, 3]


def test_pc_firmware_count_mismatch_is_informational_only() -> None:
    result, _, _ = run_case(firmware_pass=3, capture=snapshot(4))
    assert result.verdict.passed
    assert result.verdict.received_message_count == 4
    assert result.verdict.fct_pass_count == 3
    assert "PC received 4 valid RS485 frames" in result.verdict.reason
    assert any("count mismatch" in event for event in result.verdict.events)


@pytest.mark.parametrize(
    "capture",
    [
        snapshot(0),
        snapshot(3, malformed_count=2),
        snapshot(3, sequence_error_count=1, first_sequence_error=(1, 2)),
        snapshot(3, buffer_length=4),
        snapshot(3, transport_error="serial receive failed"),
    ],
)
def test_pc_capture_diagnostics_do_not_override_firmware_pass(capture) -> None:
    result, _, _ = run_case(firmware_pass=3, capture=capture)
    assert result.verdict.passed
    assert result.verdict.received_message_count == capture.valid_frame_count
    assert any("informational only" in event for event in result.verdict.events)


def test_firmware_send_failure_fails_even_when_received_count_matches() -> None:
    result, _, _ = run_case(firmware_pass=3, firmware_fail=1)
    assert not result.verdict.passed
    assert "fail_count=1" in result.verdict.reason


@pytest.mark.parametrize("state", [FctState.FAIL, FctState.ABORTED])
def test_firmware_fail_and_aborted_states_fail(state: FctState) -> None:
    result, _, _ = run_case(state)
    assert not result.verdict.passed
    assert f"state is {state.name}" in result.verdict.reason


def test_terminal_status_is_followed_by_bounded_serial_drain(monkeypatch) -> None:
    waits: list[float] = []
    monkeypatch.setattr("pcbt_app.services.rs485_stress_test.sleep", waits.append)
    result, _, _ = run_case()
    assert result.verdict.passed
    assert waits == [0.100]


def test_timeout_sends_stop_and_reconciles_terminal_status() -> None:
    result, can, _ = run_case(FctState.RUNNING, app_timeout_ms=1)
    opcodes = [parse_fct_id(frame.arbitration_id).opcode for frame in can.sent_frames]
    assert not result.verdict.passed
    assert FctOpcode.STOP in opcodes
    assert result.verdict.fct_state == FctState.ABORTED
    assert "App timeout expired" in result.verdict.reason
