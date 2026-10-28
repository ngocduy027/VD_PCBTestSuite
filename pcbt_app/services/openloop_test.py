from __future__ import annotations

from time import monotonic, sleep

from pcbt_app.protocol.mit_firmware import (
    CAN_INDEX_TEST_ACTIVE,
    CAN_INDEX_TEST_ELAPSED_MS,
    CAN_INDEX_TEST_STATUS,
    CAN_INDEX_TEST_STOP_REASON,
    MotorTestStatus,
    MotorTestStopReason,
    OPEN_LOOP_DEFAULT_DURATION_MS,
    OPEN_LOOP_DEFAULT_THETA_STEP,
    build_openloop_default_write_frames,
    build_openloop_start_frame,
    build_openloop_unlock_frame,
    status_label,
    stop_reason_label,
)
from pcbt_app.transport.base import CanTransportError


def run_openloop_test(
    service,
    timeout_ms: int,
    driver_id: int | None = None,
    theta_step: float = OPEN_LOOP_DEFAULT_THETA_STEP,
    duration_ms: int = OPEN_LOOP_DEFAULT_DURATION_MS,
):
    self = service
    start = monotonic()
    events: list[str] = []

    if driver_id is None:
        return self._result(
            "Open-loop Test",
            False,
            "Driver ID is required; run Detect ID first",
            start,
            events,
        )
    if not 0 <= driver_id <= 0xFF:
        return self._result("Open-loop Test", False, f"Driver ID out of range: {driver_id}", start, events)
    if not -0.002 <= theta_step <= 0.002:
        return self._result("Open-loop Test", False, "Open-loop theta step must be -0.002..0.002", start, events)
    if not 100 <= duration_ms <= 60_000:
        return self._result("Open-loop Test", False, "Open-loop duration must be 100..60000 ms", start, events)

    drain_result = self._drain_before_command("Open-loop Test")
    if drain_result is not None:
        return drain_result

    deadline = start + (timeout_ms / 1000.0)
    try:
        for frame in build_openloop_default_write_frames(driver_id, theta_step, duration_ms):
            index = frame.data[0] | (frame.data[1] << 8)
            self._send_openloop_command(frame, f"write 0x{index:04X}", deadline)

        self._send_openloop_command(build_openloop_unlock_frame(driver_id), "unlock diagnostic test", deadline)
        events.append("wrote 0x7204 = 0x54455354")
        self._send_openloop_command(build_openloop_start_frame(driver_id), "start open-loop", deadline)
        events.append("wrote 0x7200 = 1")

        saw_status_response = False
        saw_active_status = False
        latest_status: int | None = None
        latest_stop_reason: int | None = None
        latest_elapsed_ms: int | None = None
        while monotonic() < deadline:
            active_test = self._read_u32(driver_id, CAN_INDEX_TEST_ACTIVE, deadline, events)
            status = self._read_u32(driver_id, CAN_INDEX_TEST_STATUS, deadline, events)
            stop_reason = self._read_u32(driver_id, CAN_INDEX_TEST_STOP_REASON, deadline, events)
            elapsed_ms = self._read_u32(driver_id, CAN_INDEX_TEST_ELAPSED_MS, deadline, events)

            if active_test is not None:
                events.append(f"active test {active_test}")
                if active_test != 0:
                    saw_active_status = True
            if status is None:
                if self.poll_interval_s > 0:
                    sleep(min(self.poll_interval_s, max(0.0, deadline - monotonic())))
                continue

            saw_status_response = True
            latest_status = status
            latest_stop_reason = stop_reason
            latest_elapsed_ms = elapsed_ms
            events.append(f"status {status_label(status)}")
            if status == MotorTestStatus.IDLE and not saw_active_status:
                if self.poll_interval_s > 0:
                    sleep(min(self.poll_interval_s, max(0.0, deadline - monotonic())))
                continue

            if status in (
                MotorTestStatus.REQUESTED,
                MotorTestStatus.WAITING_DRIVER,
                MotorTestStatus.STARTUP_GUARD,
                MotorTestStatus.RUNNING,
            ):
                saw_active_status = True
                if self.poll_interval_s > 0:
                    sleep(min(self.poll_interval_s, max(0.0, deadline - monotonic())))
                continue

            if status == MotorTestStatus.FINISHED and stop_reason == MotorTestStopReason.COMPLETED:
                return self._result(
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
            return self._openloop_fail(reason, start, events, driver_id, status, stop_reason, elapsed_ms)

        reason = (
            "Timeout waiting for open-loop completion"
            if saw_active_status
            else (
                "Diagnostic open-loop start was not accepted before timeout"
                if saw_status_response
                else "No read response for open-loop status before timeout"
            )
        )
        return self._openloop_fail(
            reason,
            start,
            events,
            driver_id,
            latest_status,
            latest_stop_reason,
            latest_elapsed_ms,
        )
    except CanTransportError as exc:
        return self._openloop_fail(str(exc), start, events, driver_id, None, None)
