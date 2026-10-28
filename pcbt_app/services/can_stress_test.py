from __future__ import annotations

from time import monotonic, sleep

from pcbt_app.protocol.mit_firmware import (
    CAN_STRESS_DEFAULT_INTERVAL_MS,
    CAN_STRESS_DEFAULT_TOTAL_MESSAGES,
    CAN_INDEX_TEST_ACTIVE,
    CAN_INDEX_TEST_CAN_LAST_SEQUENCE,
    CAN_INDEX_TEST_CAN_LATE_COUNT,
    CAN_INDEX_TEST_CAN_TX_ATTEMPT_COUNT,
    CAN_INDEX_TEST_CAN_TX_FAIL_COUNT,
    CAN_INDEX_TEST_CAN_TX_SUCCESS_COUNT,
    CAN_INDEX_TEST_ELAPSED_MS,
    CAN_INDEX_TEST_STATUS,
    CAN_INDEX_TEST_STOP_REASON,
    MotorTestStatus,
    build_can_stress_config_frames,
    build_can_stress_start_frame,
    build_can_stress_stop_frame,
    build_openloop_unlock_frame,
    status_label,
)
from pcbt_app.transport.base import CanTransportError


def run_can_stress_test(
    service,
    timeout_ms: int,
    driver_id: int | None = None,
    interval_ms: int = CAN_STRESS_DEFAULT_INTERVAL_MS,
    total_messages: int = CAN_STRESS_DEFAULT_TOTAL_MESSAGES,
):
    self = service
    start = monotonic()
    events: list[str] = []

    if driver_id is None:
        return self._result(
            "CAN Stress Test",
            False,
            "Driver ID is required; run Detect ID first",
            start,
            events,
        )
    if not 0 <= driver_id <= 0xFF:
        return self._result("CAN Stress Test", False, f"Driver ID out of range: {driver_id}", start, events)
    if not 1 <= interval_ms <= 1000:
        return self._result("CAN Stress Test", False, "CAN stress interval must be 1..1000 ms", start, events)
    if not 1 <= total_messages <= 1_000_000:
        return self._result("CAN Stress Test", False, "CAN stress total messages must be 1..1000000", start, events)

    drain_result = self._drain_before_command("CAN Stress Test")
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
    stress_tracker: dict[str, object] = {
        "sequences": set(),
        "duplicate_count": 0,
        "out_of_range_count": 0,
        "last_elapsed_ms": None,
    }
    saw_status_response = False
    saw_active_status = False

    try:
        for frame in build_can_stress_config_frames(driver_id, interval_ms, total_messages):
            index = frame.data[0] | (frame.data[1] << 8)
            self._send_diagnostic_command(frame, f"write 0x{index:04X}", deadline)

        self._send_diagnostic_command(build_openloop_unlock_frame(driver_id), "unlock diagnostic test", deadline)
        events.append("wrote 0x7204 = 0x54455354")
        self._send_diagnostic_command(build_can_stress_start_frame(driver_id), "start CAN stress", deadline)
        events.append("wrote 0x7200 = 2")

        while monotonic() < deadline:
            active_test = self._read_u32(
                driver_id,
                CAN_INDEX_TEST_ACTIVE,
                deadline,
                events,
                report_counters=counters,
                stress_tracker=stress_tracker,
                total_messages=total_messages,
            )
            status = self._read_u32(
                driver_id,
                CAN_INDEX_TEST_STATUS,
                deadline,
                events,
                report_counters=counters,
                stress_tracker=stress_tracker,
                total_messages=total_messages,
            )
            stop_reason = self._read_u32(
                driver_id,
                CAN_INDEX_TEST_STOP_REASON,
                deadline,
                events,
                report_counters=counters,
                stress_tracker=stress_tracker,
                total_messages=total_messages,
            )
            elapsed_ms = self._read_u32(
                driver_id,
                CAN_INDEX_TEST_ELAPSED_MS,
                deadline,
                events,
                report_counters=counters,
                stress_tracker=stress_tracker,
                total_messages=total_messages,
            )
            counters["tx_attempt_count"] = self._read_u32(
                driver_id,
                CAN_INDEX_TEST_CAN_TX_ATTEMPT_COUNT,
                deadline,
                events,
                report_counters=counters,
                stress_tracker=stress_tracker,
                total_messages=total_messages,
            )
            counters["tx_success_count"] = self._read_u32(
                driver_id,
                CAN_INDEX_TEST_CAN_TX_SUCCESS_COUNT,
                deadline,
                events,
                report_counters=counters,
                stress_tracker=stress_tracker,
                total_messages=total_messages,
            )
            counters["tx_fail_count"] = self._read_u32(
                driver_id,
                CAN_INDEX_TEST_CAN_TX_FAIL_COUNT,
                deadline,
                events,
                report_counters=counters,
                stress_tracker=stress_tracker,
                total_messages=total_messages,
            )
            counters["last_sequence"] = self._read_u32(
                driver_id,
                CAN_INDEX_TEST_CAN_LAST_SEQUENCE,
                deadline,
                events,
                report_counters=counters,
                stress_tracker=stress_tracker,
                total_messages=total_messages,
            )
            counters["late_count"] = self._read_u32(
                driver_id,
                CAN_INDEX_TEST_CAN_LATE_COUNT,
                deadline,
                events,
                report_counters=counters,
                stress_tracker=stress_tracker,
                total_messages=total_messages,
            )

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

            if status == MotorTestStatus.FINISHED:
                self._collect_can_stress_frames(
                    driver_id,
                    min(deadline, monotonic() + 0.200),
                    events,
                    stress_tracker,
                    total_messages,
                )
                return self._finish_can_stress_result(
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

            if self.poll_interval_s > 0:
                sleep(min(self.poll_interval_s, max(0.0, deadline - monotonic())))
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
            self._send(build_can_stress_stop_frame(driver_id), "stop CAN stress")
        except CanTransportError as exc:
            events.append(f"failed to send stop command: {exc}")
        return self._finish_can_stress_result(
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
        return self._can_stress_fail(str(exc), start, events, driver_id, None, None, None, counters)
