"""Legacy open-loop test: spin the motor with a fixed voltage vector.

Writes Vd/Vq/theta-step/duration, unlocks and starts the test, then polls
status until FINISHED. PASS only when the stop reason is COMPLETED; any other
ending (fault, timeout) sends a stop command and FAILs.
"""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING

from pcbt_app.protocol.mit_firmware import (
    CAN_INDEX_TEST_ACTIVE,
    CAN_INDEX_TEST_ELAPSED_MS,
    CAN_INDEX_TEST_STATUS,
    CAN_INDEX_TEST_STOP_REASON,
    CAN_INDEX_TEST_UNLOCK,
    DIAG_TEST_UNLOCK_KEY,
    MOTOR_TEST_ACTIVE_STATUSES,
    OPEN_LOOP_DEFAULT_DURATION_MS,
    OPEN_LOOP_DEFAULT_THETA_STEP,
    OPEN_LOOP_MAX_ABS_THETA_STEP,
    OPEN_LOOP_MAX_DURATION_MS,
    OPEN_LOOP_MIN_DURATION_MS,
    MotorTestId,
    MotorTestStatus,
    MotorTestStopReason,
    build_openloop_default_write_frames,
    build_openloop_start_frame,
    build_openloop_unlock_frame,
    status_label,
    stop_reason_label,
)
from pcbt_app.transport.base import CanTransportError

if TYPE_CHECKING:
    from pcbt_app.services.pcb_test_service import PcbTestService, ServiceResult


def run_openloop_test(
    service: PcbTestService,
    timeout_ms: int,
    driver_id: int | None = None,
    theta_step: float = OPEN_LOOP_DEFAULT_THETA_STEP,
    duration_ms: int = OPEN_LOOP_DEFAULT_DURATION_MS,
) -> ServiceResult:
    """Run the legacy open-loop test; see the module docstring for the sequence."""
    start = monotonic()
    events: list[str] = []

    if driver_id is None:
        return service._result(
            "Open-loop Test",
            False,
            "Driver ID is required; run Detect ID first",
            start,
            events,
        )
    if not 0 <= driver_id <= 0xFF:
        return service._result("Open-loop Test", False, f"Driver ID out of range: {driver_id}", start, events)
    if not -OPEN_LOOP_MAX_ABS_THETA_STEP <= theta_step <= OPEN_LOOP_MAX_ABS_THETA_STEP:
        return service._result(
            "Open-loop Test",
            False,
            f"Open-loop theta step must be -{OPEN_LOOP_MAX_ABS_THETA_STEP}..{OPEN_LOOP_MAX_ABS_THETA_STEP}",
            start,
            events,
        )
    if not OPEN_LOOP_MIN_DURATION_MS <= duration_ms <= OPEN_LOOP_MAX_DURATION_MS:
        return service._result(
            "Open-loop Test",
            False,
            f"Open-loop duration must be {OPEN_LOOP_MIN_DURATION_MS}..{OPEN_LOOP_MAX_DURATION_MS} ms",
            start,
            events,
        )

    drain_result = service._drain_before_command("Open-loop Test")
    if drain_result is not None:
        return drain_result

    deadline = start + (timeout_ms / 1000.0)
    try:
        for frame in build_openloop_default_write_frames(driver_id, theta_step, duration_ms):
            index = frame.data[0] | (frame.data[1] << 8)
            service._send_diagnostic_command(frame, f"write 0x{index:04X}", deadline)

        service._send_diagnostic_command(build_openloop_unlock_frame(driver_id), "unlock diagnostic test", deadline)
        events.append(f"wrote 0x{CAN_INDEX_TEST_UNLOCK:04X} = 0x{DIAG_TEST_UNLOCK_KEY:08X}")
        service._send_diagnostic_command(build_openloop_start_frame(driver_id), "start open-loop", deadline)
        events.append(f"wrote 0x{CAN_INDEX_TEST_ACTIVE:04X} = {MotorTestId.OPEN_LOOP:d}")

        saw_status_response = False
        saw_active_status = False
        latest_status: int | None = None
        latest_stop_reason: int | None = None
        latest_elapsed_ms: int | None = None
        while monotonic() < deadline:
            active_test = service._read_u32(driver_id, CAN_INDEX_TEST_ACTIVE, deadline, events)
            status = service._read_u32(driver_id, CAN_INDEX_TEST_STATUS, deadline, events)
            stop_reason = service._read_u32(driver_id, CAN_INDEX_TEST_STOP_REASON, deadline, events)
            elapsed_ms = service._read_u32(driver_id, CAN_INDEX_TEST_ELAPSED_MS, deadline, events)

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

            if status == MotorTestStatus.FINISHED and stop_reason == MotorTestStopReason.COMPLETED:
                return service._result(
                    "Open-loop Test",
                    True,
                    "PASS",
                    start,
                    events,
                    returned_driver_id=driver_id,
                    firmware_status=status,
                    stop_reason=stop_reason,
                    elapsed_override_ms=elapsed_ms,
                )

            reason = (
                f"Open-loop ended with status {status_label(status)} "
                f"and stop reason {stop_reason_label(stop_reason)}"
            )
            return service._openloop_fail(reason, start, events, driver_id, status, stop_reason, elapsed_ms)

        reason = (
            "Timeout waiting for open-loop completion"
            if saw_active_status
            else (
                "Diagnostic open-loop start was not accepted before timeout"
                if saw_status_response
                else "No read response for open-loop status before timeout"
            )
        )
        return service._openloop_fail(
            reason,
            start,
            events,
            driver_id,
            latest_status,
            latest_stop_reason,
            latest_elapsed_ms,
        )
    except CanTransportError as exc:
        return service._openloop_fail(str(exc), start, events, driver_id, None, None)
