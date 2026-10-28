from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from time import monotonic, sleep
from typing import Callable

from pcbt_app.logging_store import EventLog, RawFrameEvent
from pcbt_app.models import GongwangResultSet, RawCanFrame, TestVerdict
from pcbt_app.protocol.mit_firmware import (
    CAN_STRESS_DEFAULT_INTERVAL_MS,
    CAN_STRESS_DEFAULT_TOTAL_MESSAGES,
    GONGWANG_DEFAULT_DURATION_MS,
    MotorTestStatus,
    MotorTestStopReason,
    OPEN_LOOP_DEFAULT_DURATION_MS,
    OPEN_LOOP_DEFAULT_THETA_STEP,
    RS485_STRESS_DEFAULT_DURATION_MS,
    RS485_STRESS_DEFAULT_PERIOD_TICKS,
    build_can_stress_stop_frame,
    build_openloop_stop_frame,
    build_single_read_frame,
    parse_can_stress_frame,
    parse_can_stress_report,
    parse_diagnostic_test_report,
    parse_scan_response,
    read_param_value_float,
    read_param_value_u32,
    status_label,
    stop_reason_label,
)
from pcbt_app.transport.base import AbstractCanTransport, CanTransportError
from pcbt_app.transport.rs485_serial import SerialRs485Transport


@dataclass(slots=True)
class ServiceResult:
    verdict: TestVerdict
    parsed_frames: list[object] = field(default_factory=list)
    transport_warning: str | None = None


class PcbTestService:
    def __init__(
        self,
        transport: AbstractCanTransport,
        event_log: EventLog,
        command_id: int | None = None,
        *,
        scan_interval_s: float = 0.008,
        openloop_command_interval_s: float = 0.008,
        openloop_ready_wait_s: float = 0.350,
        poll_interval_s: float = 0.100,
        response_timeout_s: float = 0.500,
        use_fct: bool = False,
    ) -> None:
        self.transport = transport
        self.event_log = event_log
        self.command_id = command_id
        self.scan_interval_s = scan_interval_s
        self.openloop_command_interval_s = openloop_command_interval_s
        self.openloop_ready_wait_s = openloop_ready_wait_s
        self.poll_interval_s = poll_interval_s
        self.response_timeout_s = response_timeout_s
        self.use_fct = use_fct
        self.fct_client = None
        if use_fct:
            from pcbt_app.services.fct_client import FctClient

            self.fct_client = FctClient(
                transport,
                response_timeout_s=response_timeout_s,
                event_log=event_log,
            )

    def run_can_test(self, timeout_ms: int) -> ServiceResult:
        from pcbt_app.services.can_id_test import run_can_test

        return run_can_test(self, timeout_ms)

    def run_openloop_test(
        self,
        timeout_ms: int,
        driver_id: int | None = None,
        theta_step: float = OPEN_LOOP_DEFAULT_THETA_STEP,
        duration_ms: int = OPEN_LOOP_DEFAULT_DURATION_MS,
    ) -> ServiceResult:
        if self.use_fct:
            from pcbt_app.services.fct_workflows import run_fct_openloop_test

            return run_fct_openloop_test(self, timeout_ms, driver_id, theta_step, duration_ms)
        from pcbt_app.services.openloop_test import run_openloop_test

        return run_openloop_test(self, timeout_ms, driver_id, theta_step, duration_ms)

    def run_can_stress_test(
        self,
        timeout_ms: int,
        driver_id: int | None = None,
        interval_ms: int = CAN_STRESS_DEFAULT_INTERVAL_MS,
        total_messages: int = CAN_STRESS_DEFAULT_TOTAL_MESSAGES,
        response_count_callback: Callable[[int], None] | None = None,
    ) -> ServiceResult:
        if self.use_fct:
            from pcbt_app.services.fct_workflows import run_fct_can_stress_test

            return run_fct_can_stress_test(
                self,
                timeout_ms,
                driver_id,
                interval_ms,
                total_messages,
                response_count_callback,
            )
        from pcbt_app.services.can_stress_test import run_can_stress_test

        return run_can_stress_test(self, timeout_ms, driver_id, interval_ms, total_messages)

    def run_rs485_stress_test(
        self,
        app_timeout_ms: int,
        driver_id: int | None = None,
        rs485_transport: SerialRs485Transport | None = None,
        duration_ms: int = RS485_STRESS_DEFAULT_DURATION_MS,
        period_ticks: int = RS485_STRESS_DEFAULT_PERIOD_TICKS,
        response_count_callback: Callable[[int], None] | None = None,
    ) -> ServiceResult:
        from pcbt_app.services.rs485_stress_test import run_rs485_stress_test

        return run_rs485_stress_test(
            self,
            app_timeout_ms,
            driver_id,
            rs485_transport,
            duration_ms,
            period_ticks,
            response_count_callback,
        )

    def run_gongwang_encoder_test(
        self,
        duration_ms: int = GONGWANG_DEFAULT_DURATION_MS,
        driver_id: int | None = None,
        period_ticks: int = 1,
        app_timeout_ms: int | None = None,
    ) -> ServiceResult:
        if self.use_fct:
            from pcbt_app.services.fct_workflows import run_fct_gongwang_test

            return run_fct_gongwang_test(
                self,
                duration_ms,
                driver_id,
                period_ticks,
                duration_ms + 1500 if app_timeout_ms is None else app_timeout_ms,
            )
        from pcbt_app.services.gongwang_encoder_test import run_gongwang_encoder_test

        return run_gongwang_encoder_test(self, duration_ms, driver_id)

    def _drain_before_command(self, test_name: str) -> ServiceResult | None:
        try:
            stale_frames = self.transport.drain()
        except CanTransportError as exc:
            return ServiceResult(TestVerdict(test_name=test_name, passed=False, reason=str(exc)))
        for stale in stale_frames:
            self.event_log.add(RawFrameEvent("rx", datetime.now(), stale, note="drained before command"))
        reset_stream = getattr(self.transport, "reset_stream", None)
        if callable(reset_stream):
            reset_stream()
        return None

    def _send(self, frame: RawCanFrame, note: str) -> None:
        self.transport.send(frame)
        self.event_log.add(RawFrameEvent("tx", datetime.now(), frame, note=note))

    def _send_openloop_command(self, frame: RawCanFrame, note: str, deadline: float) -> None:
        self._send_diagnostic_command(frame, note, deadline)

    def _send_diagnostic_command(self, frame: RawCanFrame, note: str, deadline: float) -> None:
        self._send(frame, note)
        if self.openloop_command_interval_s > 0:
            sleep(min(self.openloop_command_interval_s, max(0.0, deadline - monotonic())))

    def _recv_once(self, timeout_s: float = 0.0) -> RawCanFrame | None:
        frame = self.transport.recv(timeout_s=max(0.0, timeout_s))
        if frame is not None:
            self.event_log.add(RawFrameEvent("rx", datetime.now(), frame))
        return frame

    def _collect_scan_responses(self, deadline: float, found_ids: set[int], events: list[str]) -> None:
        first = True
        while first or monotonic() < deadline:
            first = False
            timeout_s = max(0.0, min(0.01, deadline - monotonic()))
            frame = self._recv_once(timeout_s)
            if frame is None:
                if timeout_s <= 0:
                    return
                continue
            driver_id = parse_scan_response(frame)
            if driver_id is None:
                events.append(f"ignored frame 0x{frame.arbitration_id:08X}")
                continue
            found_ids.add(driver_id)
            events.append(f"received scan response from driver ID {driver_id}")

    def _read_u32(
        self,
        driver_id: int,
        index: int,
        deadline: float,
        events: list[str],
        report_counters: dict[str, int | float | None] | None = None,
        stress_tracker: dict[str, object] | None = None,
        total_messages: int | None = None,
        frame_handler: Callable[[RawCanFrame], bool] | None = None,
    ) -> int | None:
        self._send(build_single_read_frame(driver_id, index), f"read 0x{index:04X}")

        def parse(candidate: RawCanFrame) -> bool:
            return read_param_value_u32(candidate, driver_id) is not None

        frame = self._wait_for(
            parse,
            min(deadline, monotonic() + self.response_timeout_s),
            events,
            report_driver_id=driver_id,
            report_counters=report_counters,
            stress_tracker=stress_tracker,
            total_messages=total_messages,
            frame_handler=frame_handler,
        )
        if frame is None:
            return None
        return read_param_value_u32(frame, driver_id)

    def _read_float(
        self,
        driver_id: int,
        index: int,
        deadline: float,
        events: list[str],
        report_counters: dict[str, int | float | None] | None = None,
    ) -> float | None:
        self._send(build_single_read_frame(driver_id, index), f"read 0x{index:04X}")

        def parse(candidate: RawCanFrame) -> bool:
            return read_param_value_float(candidate, driver_id) is not None

        frame = self._wait_for(
            parse,
            min(deadline, monotonic() + self.response_timeout_s),
            events,
            report_driver_id=driver_id,
            report_counters=report_counters,
        )
        if frame is None:
            return None
        return read_param_value_float(frame, driver_id)

    def _wait_for(
        self,
        predicate: Callable[[RawCanFrame], bool],
        deadline: float,
        events: list[str],
        report_driver_id: int | None = None,
        report_counters: dict[str, int | float | None] | None = None,
        stress_tracker: dict[str, object] | None = None,
        total_messages: int | None = None,
        frame_handler: Callable[[RawCanFrame], bool] | None = None,
    ) -> RawCanFrame | None:
        first = True
        while first or monotonic() < deadline:
            first = False
            timeout_s = max(0.0, min(0.01, deadline - monotonic()))
            frame = self._recv_once(timeout_s)
            if frame is None:
                if timeout_s <= 0:
                    return None
                continue
            if predicate(frame):
                return frame
            if frame_handler is not None and frame_handler(frame):
                continue
            report = parse_diagnostic_test_report(frame, report_driver_id)
            if report is not None and report.test_id in (1, 2, 3, 4):
                if report_counters is not None:
                    report_values = {
                        "report_test_id": report.test_id,
                        "report_status": report.status,
                        "report_stop_reason": report.stop_reason,
                        "report_elapsed_ms": report.elapsed_ms,
                    }
                    for key, value in report_values.items():
                        if key in report_counters:
                            report_counters[key] = value
                events.append(
                    f"diagnostic report test={report.test_id} status={report.status} "
                    f"stop_reason={report.stop_reason} elapsed_ms={report.elapsed_ms}"
                )
                continue
            stress_frame = parse_can_stress_frame(frame, report_driver_id)
            if stress_frame is not None:
                if stress_tracker is not None and total_messages is not None:
                    self._record_can_stress_frame(stress_tracker, stress_frame.sequence, stress_frame.elapsed_ms, total_messages)
                events.append(f"CAN stress frame sequence={stress_frame.sequence} elapsed_ms={stress_frame.elapsed_ms}")
                continue
            report = parse_can_stress_report(frame, report_driver_id)
            if report is not None:
                if report_counters is not None:
                    report_counters["report_sequence"] = report.sequence
                    report_counters["report_elapsed_ms"] = report.elapsed_ms
                events.append(f"CAN stress report sequence={report.sequence} elapsed_ms={report.elapsed_ms}")
                continue
            events.append(f"ignored frame 0x{frame.arbitration_id:08X}")
        return None

    def _collect_can_stress_frames(
        self,
        driver_id: int,
        deadline: float,
        events: list[str],
        stress_tracker: dict[str, object],
        total_messages: int,
    ) -> None:
        while monotonic() < deadline:
            frame = self._recv_once(min(0.01, max(0.0, deadline - monotonic())))
            if frame is None:
                return
            stress_frame = parse_can_stress_frame(frame, driver_id)
            if stress_frame is None:
                events.append(f"ignored frame 0x{frame.arbitration_id:08X}")
                continue
            self._record_can_stress_frame(
                stress_tracker,
                stress_frame.sequence,
                stress_frame.elapsed_ms,
                total_messages,
            )
            events.append(f"CAN stress frame sequence={stress_frame.sequence} elapsed_ms={stress_frame.elapsed_ms}")

    def _record_can_stress_frame(
        self,
        stress_tracker: dict[str, object],
        sequence: int,
        elapsed_ms: int,
        total_messages: int,
    ) -> None:
        sequences = stress_tracker["sequences"]
        assert isinstance(sequences, set)
        if not 0 <= sequence < total_messages:
            stress_tracker["out_of_range_count"] = int(stress_tracker["out_of_range_count"]) + 1
        elif sequence in sequences:
            stress_tracker["duplicate_count"] = int(stress_tracker["duplicate_count"]) + 1
        else:
            sequences.add(sequence)
        stress_tracker["last_elapsed_ms"] = elapsed_ms

    def _finalize_can_stress_counters(
        self,
        counters: dict[str, int | float | None],
        stress_tracker: dict[str, object],
        total_messages: int,
        elapsed_ms: int | None,
    ) -> None:
        sequences = stress_tracker["sequences"]
        assert isinstance(sequences, set)
        received_count = len(sequences)
        duplicate_count = int(stress_tracker["duplicate_count"]) + int(stress_tracker["out_of_range_count"])
        missing_count = max(0, total_messages - received_count)
        rate_elapsed_ms = elapsed_ms or stress_tracker["last_elapsed_ms"]
        rate_hz = 0.0
        if isinstance(rate_elapsed_ms, int) and rate_elapsed_ms > 0:
            rate_hz = received_count * 1000.0 / rate_elapsed_ms
        counters["received_message_count"] = received_count
        counters["missing_message_count"] = missing_count
        counters["duplicate_message_count"] = duplicate_count
        counters["success_rate_percent"] = received_count * 100.0 / total_messages if total_messages > 0 else 0.0
        counters["receive_rate_hz"] = rate_hz

    def _finish_can_stress_result(
        self,
        start: float,
        events: list[str],
        driver_id: int,
        status: int | None,
        stop_reason: int | None,
        elapsed_ms: int | None,
        counters: dict[str, int | float | None],
        stress_tracker: dict[str, object],
        total_messages: int,
    ) -> ServiceResult:
        self._finalize_can_stress_counters(counters, stress_tracker, total_messages, elapsed_ms)
        received_count = int(counters["received_message_count"] or 0)
        missing_count = int(counters["missing_message_count"] or 0)
        duplicate_count = int(counters["duplicate_message_count"] or 0)
        success_rate = float(counters["success_rate_percent"] or 0.0)
        receive_rate_hz = float(counters["receive_rate_hz"] or 0.0)
        passed = success_rate > 95.0
        reason = (
            f"Received {received_count}/{total_messages} messages "
            f"({success_rate:.1f}%) at {receive_rate_hz:.1f} msg/s; "
            f"missing={missing_count} duplicate={duplicate_count}"
        )
        return self._result(
            "CAN Stress Test",
            passed,
            reason,
            start,
            events,
            returned_driver_id=driver_id,
            firmware_status=status,
            stop_reason=stop_reason,
            elapsed_override_ms=elapsed_ms,
            **counters,
        )

    def _openloop_fail(
        self,
        reason: str,
        start: float,
        events: list[str],
        driver_id: int,
        status: int | None,
        stop_reason: int | None,
        elapsed_ms: int | None = None,
    ) -> ServiceResult:
        try:
            self._send(build_openloop_stop_frame(driver_id), "stop open-loop")
        except CanTransportError as exc:
            events.append(f"failed to send stop command: {exc}")
        return self._result(
            "Open-loop Test",
            False,
            reason,
            start,
            events,
            returned_driver_id=driver_id,
            firmware_status=status,
            stop_reason=stop_reason,
            elapsed_override_ms=elapsed_ms,
        )

    def _can_stress_fail(
        self,
        reason: str,
        start: float,
        events: list[str],
        driver_id: int,
        status: int | None,
        stop_reason: int | None,
        elapsed_ms: int | None,
        counters: dict[str, int | float | None],
    ) -> ServiceResult:
        try:
            self._send(build_can_stress_stop_frame(driver_id), "stop CAN stress")
        except CanTransportError as exc:
            events.append(f"failed to send stop command: {exc}")
        return self._result(
            "CAN Stress Test",
            False,
            reason,
            start,
            events,
            returned_driver_id=driver_id,
            firmware_status=status,
            stop_reason=stop_reason,
            elapsed_override_ms=elapsed_ms,
            **counters,
        )

    def _result(
        self,
        test_name: str,
        passed: bool,
        reason: str,
        start: float,
        events: list[str],
        *,
        returned_driver_id: int | None = None,
        firmware_status: int | None = None,
        stop_reason: int | None = None,
        elapsed_override_ms: int | None = None,
        tx_attempt_count: int | None = None,
        tx_success_count: int | None = None,
        tx_fail_count: int | None = None,
        last_sequence: int | None = None,
        late_count: int | None = None,
        report_sequence: int | None = None,
        report_elapsed_ms: int | None = None,
        received_message_count: int | None = None,
        actual_response_count: int | None = None,
        expected_message_count: int | None = None,
        missing_message_count: int | None = None,
        duplicate_message_count: int | None = None,
        success_rate_percent: float | None = None,
        receive_rate_hz: float | None = None,
        received_byte_count: int | None = None,
        bytes_per_message: int | None = None,
        requested_duration_ms: int | None = None,
        period_ticks: int | None = None,
        requested_frequency_hz: float | None = None,
        malformed_count: int | None = None,
        sequence_error_count: int | None = None,
        first_sequence: int | None = None,
        parser_buffer_length: int | None = None,
        gongwang_results: GongwangResultSet | None = None,
        fct_state: int | None = None,
        fct_pass_count: int | None = None,
        fct_fail_count: int | None = None,
    ) -> ServiceResult:
        elapsed_ms = elapsed_override_ms
        if elapsed_ms is None:
            elapsed_ms = int((monotonic() - start) * 1000)
        transport_warning = None
        take_parser_stats = getattr(self.transport, "take_parser_stats", None)
        if callable(take_parser_stats):
            stats = take_parser_stats()
            summary = getattr(stats, "summary", None)
            if callable(summary):
                transport_warning = summary()
        return ServiceResult(
            verdict=TestVerdict(
                test_name=test_name,
                passed=passed,
                reason=reason,
                returned_driver_id=returned_driver_id,
                firmware_status=firmware_status,
                stop_reason=stop_reason,
                elapsed_ms=elapsed_ms,
                fct_state=fct_state,
                fct_pass_count=fct_pass_count,
                fct_fail_count=fct_fail_count,
                tx_attempt_count=tx_attempt_count,
                tx_success_count=tx_success_count,
                tx_fail_count=tx_fail_count,
                last_sequence=last_sequence,
                late_count=late_count,
                report_sequence=report_sequence,
                report_elapsed_ms=report_elapsed_ms,
                received_message_count=received_message_count,
                actual_response_count=actual_response_count,
                expected_message_count=expected_message_count,
                missing_message_count=missing_message_count,
                duplicate_message_count=duplicate_message_count,
                success_rate_percent=success_rate_percent,
                receive_rate_hz=receive_rate_hz,
                received_byte_count=received_byte_count,
                bytes_per_message=bytes_per_message,
                requested_duration_ms=requested_duration_ms,
                period_ticks=period_ticks,
                requested_frequency_hz=requested_frequency_hz,
                malformed_count=malformed_count,
                sequence_error_count=sequence_error_count,
                first_sequence=first_sequence,
                parser_buffer_length=parser_buffer_length,
                gongwang_results=gongwang_results,
                events=events,
            ),
            transport_warning=transport_warning,
        )

    @staticmethod
    def format_failure_mask(mask: int | None) -> str:
        return "-" if mask is None else f"0x{mask:02X}"

    @staticmethod
    def format_firmware_status(status: int | None) -> str:
        return status_label(status)

    @staticmethod
    def format_stop_reason(reason: int | None) -> str:
        return stop_reason_label(reason)

    @staticmethod
    def summarize_parsed_frame(parsed: object) -> str:
        return str(parsed)
