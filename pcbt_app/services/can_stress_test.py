"""Legacy CAN stress test: the board streams numbered frames and the PC counts them.

PASS when more than ``CAN_STRESS_MIN_SUCCESS_PERCENT`` of the expected
sequence numbers arrive (duplicates and out-of-range numbers do not count).
"""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING

from pcbt_app.protocol.mit_firmware import (
    CAN_INDEX_TEST_ACTIVE,
    CAN_INDEX_TEST_CAN_LAST_SEQUENCE,
    CAN_INDEX_TEST_CAN_LATE_COUNT,
    CAN_INDEX_TEST_CAN_TX_ATTEMPT_COUNT,
    CAN_INDEX_TEST_CAN_TX_FAIL_COUNT,
    CAN_INDEX_TEST_CAN_TX_SUCCESS_COUNT,
    CAN_INDEX_TEST_ELAPSED_MS,
    CAN_INDEX_TEST_STATUS,
    CAN_INDEX_TEST_STOP_REASON,
    CAN_INDEX_TEST_UNLOCK,
    CAN_STRESS_DEFAULT_INTERVAL_MS,
    CAN_STRESS_DEFAULT_TOTAL_MESSAGES,
    DIAG_TEST_UNLOCK_KEY,
    MOTOR_TEST_ACTIVE_STATUSES,
    MotorTestId,
    MotorTestStatus,
    build_can_stress_config_frames,
    build_can_stress_start_frame,
    build_can_stress_stop_frame,
    build_openloop_unlock_frame,
    status_label,
)
from pcbt_app.services.pcb_test_service import CanStressTracker
from pcbt_app.transport.base import CanTransportError

if TYPE_CHECKING:
    from pcbt_app.services.pcb_test_service import PcbTestService, ServiceResult

# Board-side transmit counters read on every poll, as (TestVerdict field, index).
TX_COUNTER_INDICES = (
    ("tx_attempt_count", CAN_INDEX_TEST_CAN_TX_ATTEMPT_COUNT),
    ("tx_success_count", CAN_INDEX_TEST_CAN_TX_SUCCESS_COUNT),
    ("tx_fail_count", CAN_INDEX_TEST_CAN_TX_FAIL_COUNT),
    ("last_sequence", CAN_INDEX_TEST_CAN_LAST_SEQUENCE),
    ("late_count", CAN_INDEX_TEST_CAN_LATE_COUNT),
)


def run_can_stress_test(
    service: PcbTestService,
    timeout_ms: int,
    driver_id: int | None = None,
    interval_ms: int = CAN_STRESS_DEFAULT_INTERVAL_MS,
    total_messages: int = CAN_STRESS_DEFAULT_TOTAL_MESSAGES,
) -> ServiceResult:
    """Configure, unlock and start the legacy CAN stress test, then poll until FINISHED or timeout."""
    start = monotonic()
    events: list[str] = []

    if driver_id is None:
        return service._result(
            "CAN Stress Test",
            False,
            "Driver ID is required; run Detect ID first",
            start,
            events,
        )
    if not 0 <= driver_id <= 0xFF:
        return service._result("CAN Stress Test", False, f"Driver ID out of range: {driver_id}", start, events)
    if not 1 <= interval_ms <= 1000:
        return service._result("CAN Stress Test", False, "CAN stress interval must be 1..1000 ms", start, events)
    if not 1 <= total_messages <= 1_000_000:
        return service._result("CAN Stress Test", False, "CAN stress total messages must be 1..1000000", start, events)

    drain_result = service._drain_before_command("CAN Stress Test")
    if drain_result is not None:
        return drain_result

    deadline = start + (timeout_ms / 1000.0)
    latest_status: int | None = None
    latest_stop_reason: int | None = None
    latest_elapsed_ms: int | None = None
    counters: dict[str, int | float | None] = {
        "tx_attempt_count": None,
        "tx_success_count": None,
        "tx_fail_count": None,
        "last_sequence": None,
        "late_count": None,
        "report_sequence": None,
        "report_elapsed_ms": None,
        "received_message_count": None,
        "missing_message_count": None,
        "duplicate_message_count": None,
        "success_rate_percent": None,
        "receive_rate_hz": None,
    }
    stress_tracker = CanStressTracker()

    def read(index: int) -> int | None:
        return service._read_u32(
            driver_id,
            index,
            deadline,
            events,
            report_counters=counters,
            stress_tracker=stress_tracker,
            total_messages=total_messages,
        )

    saw_status_response = False
    saw_active_status = False

    try:
        for frame in build_can_stress_config_frames(driver_id, interval_ms, total_messages):
            index = frame.data[0] | (frame.data[1] << 8)
            service._send_diagnostic_command(frame, f"write 0x{index:04X}", deadline)

        service._send_diagnostic_command(build_openloop_unlock_frame(driver_id), "unlock diagnostic test", deadline)
        events.append(f"wrote 0x{CAN_INDEX_TEST_UNLOCK:04X} = 0x{DIAG_TEST_UNLOCK_KEY:08X}")
        service._send_diagnostic_command(build_can_stress_start_frame(driver_id), "start CAN stress", deadline)
        events.append(f"wrote 0x{CAN_INDEX_TEST_ACTIVE:04X} = {MotorTestId.CAN_STRESS:d}")

        while monotonic() < deadline:
            active_test = read(CAN_INDEX_TEST_ACTIVE)
            status = read(CAN_INDEX_TEST_STATUS)
            stop_reason = read(CAN_INDEX_TEST_STOP_REASON)
            elapsed_ms = read(CAN_INDEX_TEST_ELAPSED_MS)
            for counter_name, index in TX_COUNTER_INDICES:
                counters[counter_name] = read(index)

            if active_test is not None:
                events.append(f"active test {active_test}")
                if active_test != MotorTestId.NONE:
                    saw_active_status = True
            if status is None:
                service._poll_sleep(deadline)
                continue

            saw_status_response = True
            latest_status = status
            latest_stop_reason = stop_reason
            latest_elapsed_ms = elapsed_ms
            events.append(f"status {status_label(status)}")

            if status == MotorTestStatus.IDLE and not saw_active_status:
                service._poll_sleep(deadline)
                continue

            if status in MOTOR_TEST_ACTIVE_STATUSES:
                saw_active_status = True
                service._poll_sleep(deadline)
                continue

            if status == MotorTestStatus.FINISHED:
                service._collect_can_stress_frames(
                    driver_id,
                    min(deadline, monotonic() + 0.200),
                    events,
                    stress_tracker,
                    total_messages,
                )
                return service._finish_can_stress_result(
                    start,
                    events,
                    driver_id,
                    status,
                    stop_reason,
                    elapsed_ms,
                    counters,
                    stress_tracker,
                    total_messages,
                )

            service._poll_sleep(deadline)
            continue

        reason = (
            "Timeout waiting for CAN stress completion"
            if saw_active_status
            else (
                "Diagnostic CAN stress start was not accepted before timeout"
                if saw_status_response
                else "No read response for CAN stress status before timeout"
            )
        )
        events.append(reason)
        try:
            service._send(build_can_stress_stop_frame(driver_id), "stop CAN stress")
        except CanTransportError as exc:
            events.append(f"failed to send stop command: {exc}")
        return service._finish_can_stress_result(
            start,
            events,
            driver_id,
            latest_status,
            latest_stop_reason,
            latest_elapsed_ms,
            counters,
            stress_tracker,
            total_messages,
        )
    except CanTransportError as exc:
        return service._can_stress_fail(str(exc), start, events, driver_id, None, None, None, counters)
