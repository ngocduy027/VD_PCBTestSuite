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
from pcbt_app.protocol.mit_firmware import MitCommand
from pcbt_app.services.pcb_test_service import PcbTestService
from pcbt_app.transport.base import AbstractCanTransport

DRIVER_ID = 0x34


@pytest.fixture(autouse=True)
def skip_scheduled_waits(monkeypatch):
    monkeypatch.setattr("pcbt_app.services.fct_workflows.sleep", lambda _seconds: None)


def fct_response(opcode, test_id, state, result=FctCommandResult.OK, passed=0, failed=0):
    assert 0 <= passed <= 0xFFFFF
    assert 0 <= failed <= 0xFFFFF
    counter_msb = ((failed >> 16) << 4) | (passed >> 16)
    return RawCanFrame(
        build_fct_id(FCT_RESPONSE_FUNCTION, opcode, DRIVER_ID, FCT_HOST_ID),
        bytes((result, test_id, state, counter_msb))
        + (passed & 0xFFFF).to_bytes(2, "little")
        + (failed & 0xFFFF).to_bytes(2, "little"),
    )


class WorkflowTransport(AbstractCanTransport):
    def __init__(self, *, terminal=FctState.PASS, start_result=FctCommandResult.OK):
        self.connected = True
        self.queued = deque()
        self.sent_frames = []
        self.terminal = terminal
        self.start_result = start_result
        self.passive_count = 0
        self.status_count = 0

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    def send(self, frame):
        self.sent_frames.append(frame)
        fields = parse_fct_id(frame.arbitration_id)
        if fields.function == 0x1E:
            test_id = frame.data[0]
            if fields.opcode == FctOpcode.START:
                state = FctState.RUNNING if self.start_result is FctCommandResult.OK else FctState.IDLE
                self.queued.append(fct_response(fields.opcode, test_id, state, self.start_result))
            elif fields.opcode == FctOpcode.STATUS:
                self.status_count += 1
                passed = self.passive_count if test_id == FctTestId.CAN_STRESS else 1
                failed = 0 if self.terminal is FctState.PASS else 1
                self.queued.append(fct_response(fields.opcode, test_id, self.terminal, passed=passed, failed=failed))
            elif fields.opcode == FctOpcode.STOP:
                self.queued.append(fct_response(fields.opcode, test_id, FctState.ABORTED))
        elif fields.function == MitCommand.PASSIVE_REPORT:
            self.passive_count += 1
            self.queued.append(
                RawCanFrame((MitCommand.FEEDBACK << 24) | (DRIVER_ID << 8) | FCT_HOST_ID, b"\x00" * 8)
            )

    def recv(self, timeout_s):
        return self.queued.popleft() if self.queued else None

    def drain(self):
        return []


class DroppedStatusTransport(WorkflowTransport):
    def __init__(self, drops: int):
        super().__init__()
        self.status_drops_remaining = drops

    def send(self, frame):
        super().send(frame)
        fields = parse_fct_id(frame.arbitration_id)
        if (
            fields.function == 0x1E
            and fields.opcode == FctOpcode.STATUS
            and self.status_drops_remaining > 0
        ):
            self.status_drops_remaining -= 1
            self.queued.pop()


class DroppedPassiveFeedbackTransport(WorkflowTransport):
    def send(self, frame):
        super().send(frame)
        fields = parse_fct_id(frame.arbitration_id)
        if fields.function == MitCommand.PASSIVE_REPORT:
            self.queued.pop()


def service(transport):
    return PcbTestService(
        transport,
        EventLog(),
        use_fct=True,
        poll_interval_s=0,
        response_timeout_s=0.01,
    )


def test_fct_openloop_starts_then_polls_to_pass() -> None:
    transport = WorkflowTransport()
    result = service(transport).run_openloop_test(10, DRIVER_ID, -0.00083, 1)
    assert result.verdict.passed
    assert result.verdict.fct_state == FctState.PASS
    assert [parse_fct_id(frame.arbitration_id).opcode for frame in transport.sent_frames] == [
        FctOpcode.START,
        FctOpcode.STATUS,
    ]
    assert transport.sent_frames[0].data == bytes.fromhex("01 00 01 00 00 C2 FC FF")


def test_fct_status_timeout_is_retried_without_stopping() -> None:
    transport = DroppedStatusTransport(drops=1)
    result = service(transport).run_openloop_test(100, DRIVER_ID, -0.00083, 1)

    assert result.verdict.passed
    opcodes = [parse_fct_id(frame.arbitration_id).opcode for frame in transport.sent_frames]
    assert opcodes == [FctOpcode.START, FctOpcode.STATUS, FctOpcode.STATUS]
    assert any("response timeout; retrying" in event for event in result.verdict.events)


def test_fct_stop_is_sent_only_after_app_timeout_expires() -> None:
    transport = DroppedStatusTransport(drops=100)
    result = service(transport).run_openloop_test(25, DRIVER_ID, -0.00083, 1)

    assert not result.verdict.passed
    assert result.verdict.fct_state == FctState.ABORTED
    opcodes = [parse_fct_id(frame.arbitration_id).opcode for frame in transport.sent_frames]
    assert opcodes[-1] == FctOpcode.STOP
    assert opcodes.count(FctOpcode.STATUS) >= 2
    assert "App timeout expired" in result.verdict.reason


def test_fct_openloop_converts_positive_theta_step_to_microradians() -> None:
    transport = WorkflowTransport()
    result = service(transport).run_openloop_test(10, DRIVER_ID, 0.0004, 1)
    assert result.verdict.passed
    assert transport.sent_frames[0].data == bytes.fromhex("01 00 01 00 00 90 01 00")


def test_fct_openloop_zero_selects_firmware_default() -> None:
    transport = WorkflowTransport()
    result = service(transport).run_openloop_test(10, DRIVER_ID, 0.0, 1)
    assert result.verdict.passed
    assert transport.sent_frames[0].data == bytes.fromhex("01 00 01 00 00 00 00 00")


def test_fct_openloop_rejects_theta_outside_firmware_range() -> None:
    transport = WorkflowTransport()
    result = service(transport).run_openloop_test(10, DRIVER_ID, 0.001571, 1)
    assert not result.verdict.passed
    assert "-0.00157..0.00157" in result.verdict.reason
    assert transport.sent_frames == []


def test_fct_start_rejection_is_reported_distinctly() -> None:
    transport = WorkflowTransport(start_result=FctCommandResult.SAFETY_BLOCKED)
    result = service(transport).run_openloop_test(10, DRIVER_ID, -0.00083, 100)
    assert not result.verdict.passed
    assert "SAFETY_BLOCKED" in result.verdict.reason
    assert transport.status_count == 0


def test_fct_terminal_fail_uses_firmware_state_and_counters() -> None:
    transport = WorkflowTransport(terminal=FctState.FAIL)
    result = service(transport).run_gongwang_encoder_test(100, DRIVER_ID)
    assert not result.verdict.passed
    assert result.verdict.fct_state == FctState.FAIL
    assert result.verdict.fct_fail_count == 1
    assert transport.sent_frames[0].data == bytes.fromhex("00 01 64 00 00 00 00 00")


def test_gongwang_waits_for_firmware_duration_before_final_status(monkeypatch) -> None:
    waits = []
    monkeypatch.setattr("pcbt_app.services.fct_workflows.sleep", waits.append)
    transport = WorkflowTransport()

    result = service(transport).run_gongwang_encoder_test(
        100, DRIVER_ID, app_timeout_ms=1000
    )

    assert result.verdict.passed
    assert len(waits) == 1
    assert 0.05 <= waits[0] <= 0.1
    assert any("before final FCT STATUS GONGWANG_ENCODER" in event for event in result.verdict.events)


def test_can_stress_starts_observer_before_generating_passive_traffic() -> None:
    transport = WorkflowTransport()
    progress = []
    result = service(transport).run_can_stress_test(
        10,
        DRIVER_ID,
        interval_ms=1,
        total_messages=3,
        response_count_callback=progress.append,
    )
    assert result.verdict.passed
    assert result.verdict.tx_attempt_count == 3
    assert result.verdict.received_message_count == 3
    assert result.verdict.actual_response_count == 3
    assert progress[0] == 0
    assert progress[-1] == 3
    functions = [parse_fct_id(frame.arbitration_id).function for frame in transport.sent_frames]
    assert functions[0] == 0x1E
    assert functions[1:4] == [MitCommand.PASSIVE_REPORT] * 3
    assert functions[-1] == 0x1E
    assert transport.sent_frames[0].data[5:8] == b"\x00\x00\x00"


def test_can_stress_actual_response_count_does_not_control_firmware_verdict() -> None:
    transport = DroppedPassiveFeedbackTransport()
    result = service(transport).run_can_stress_test(
        10, DRIVER_ID, interval_ms=1, total_messages=3
    )

    assert result.verdict.passed
    assert result.verdict.received_message_count == 3
    assert result.verdict.actual_response_count == 0
