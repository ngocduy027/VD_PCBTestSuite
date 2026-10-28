"""Service facade that runs PCB tests over an :class:`AbstractCanTransport`.

``PcbTestService`` owns the transport, the raw-frame event log and the timing
knobs shared by every test. Each test's workflow lives in its own module
(``can_id_test``, ``openloop_test``, ``fct_workflows`` ...) and is called with
the service instance so it can reuse the send/receive helpers defined here.

Two firmware protocols are supported:

* **Legacy MIT diagnostic** (``use_fct=False``): parameters are written with
  single-write frames and progress is polled through ``0x72xx`` indices.
* **FCT** (``use_fct=True``): a dedicated request/response protocol handled by
  :class:`~pcbt_app.services.fct_client.FctClient`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from time import monotonic, sleep
from typing import Any

from pcbt_app.logging_store import EventLog, RawFrameEvent
from pcbt_app.models import RawCanFrame, TestVerdict
from pcbt_app.protocol.mit_firmware import (
    CAN_STRESS_DEFAULT_INTERVAL_MS,
    CAN_STRESS_DEFAULT_TOTAL_MESSAGES,
    CAN_STRESS_MIN_SUCCESS_PERCENT,
    DIAG_REPORT_TEST_IDS,
    GONGWANG_DEFAULT_DURATION_MS,
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
)
from pcbt_app.transport.base import AbstractCanTransport, CanTransportError
from pcbt_app.transport.rs485_serial import SerialRs485Transport

# Granularity of receive polling inside wait loops.
RECV_SLICE_S = 0.01

# Metric name -> value, forwarded into TestVerdict fields of the same name.
Counters = dict[str, int | float | None]


@dataclass(slots=True)
class ServiceResult:
    """Outcome of one test run: the verdict plus any transport-level warning."""

    verdict: TestVerdict
    transport_warning: str | None = None


@dataclass(slots=True)
class CanStressTracker:
    """Accumulates legacy CAN-stress frames received from the board."""

    sequences: set[int] = field(default_factory=set)
    duplicate_count: int = 0
    out_of_range_count: int = 0
    last_elapsed_ms: int | None = None

    def record(self, sequence: int, elapsed_ms: int, total_messages: int) -> None:
        """Count one received sequence number (new, duplicate or out of range)."""
        if not 0 <= sequence < total_messages:
            self.out_of_range_count += 1
        elif sequence in self.sequences:
            self.duplicate_count += 1
        else:
            self.sequences.add(sequence)
        self.last_elapsed_ms = elapsed_ms


class PcbTestService:
    """Runs PCB tests and turns firmware responses into :class:`TestVerdict` objects.

    Timing parameters exist mainly so tests can run with zero delays; production
    callers use the defaults.
    """

    def __init__(
        self,
        transport: AbstractCanTransport,
        event_log: EventLog,
        *,
        scan_interval_s: float = 0.008,
        openloop_command_interval_s: float = 0.008,
        poll_interval_s: float = 0.100,
        response_timeout_s: float = 0.500,
        use_fct: bool = False,
    ) -> None:
        self.transport = transport
        self.event_log = event_log
        self.scan_interval_s = scan_interval_s
        # Gap after each legacy diagnostic write so the firmware can apply it.
        self.openloop_command_interval_s = openloop_command_interval_s
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

    # ------------------------------------------------------------------ tests
    # Workflow modules are imported lazily: they import this module for
    # ServiceResult, so top-level imports would be circular.

    def run_can_test(self, timeout_ms: int) -> ServiceResult:
        """Scan the bus and report the single responding driver ID."""
        from pcbt_app.services.can_id_test import run_can_test

        return run_can_test(self, timeout_ms)

    def run_openloop_test(
        self,
        timeout_ms: int,
        driver_id: int | None = None,
        theta_step: float = OPEN_LOOP_DEFAULT_THETA_STEP,
        duration_ms: int = OPEN_LOOP_DEFAULT_DURATION_MS,
    ) -> ServiceResult:
        """Spin the motor open-loop for ``duration_ms`` and check for faults."""
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
        """Have the board stream ``total_messages`` frames and measure loss."""
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
        """Start the FCT RS485 stream and validate it on ``rs485_transport``."""
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
        """Run the on-board GongWang encoder read test and collect its statistics."""
        if self.use_fct:
            from pcbt_app.services.fct_workflows import run_fct_gongwang_test

            return run_fct_gongwang_test(self, duration_ms, driver_id, period_ticks, app_timeout_ms)
        from pcbt_app.services.gongwang_encoder_test import run_gongwang_encoder_test

        return run_gongwang_encoder_test(self, duration_ms, driver_id)

    # -------------------------------------------------------- transport helpers

    def _drain_before_command(self, test_name: str) -> ServiceResult | None:
        """Discard stale RX frames before a test; returns a FAIL result on transport error."""
        try:
            stale_frames = self.transport.drain()
        except CanTransportError as exc:
            return ServiceResult(TestVerdict(test_name=test_name, passed=False, reason=str(exc)))
        for stale in stale_frames:
            self.event_log.add(RawFrameEvent("rx", datetime.now(), stale, note="drained before command"))
        # Serial-based adapters also hold partially parsed bytes; drop those too.
        reset_stream = getattr(self.transport, "reset_stream", None)
        if callable(reset_stream):
            reset_stream()
        return None

    def _send(self, frame: RawCanFrame, note: str) -> None:
        self.transport.send(frame)
        self.event_log.add(RawFrameEvent("tx", datetime.now(), frame, note=note))

    def _send_diagnostic_command(self, frame: RawCanFrame, note: str, deadline: float) -> None:
        """Send a legacy diagnostic write, then pause so the firmware can apply it."""
        self._send(frame, note)
        if self.openloop_command_interval_s > 0:
            sleep(min(self.openloop_command_interval_s, max(0.0, deadline - monotonic())))

    def _poll_sleep(self, deadline: float) -> None:
        """Wait one poll interval between status reads, never past ``deadline``."""
        if self.poll_interval_s > 0:
            sleep(min(self.poll_interval_s, max(0.0, deadline - monotonic())))

    def _recv_once(self, timeout_s: float = 0.0) -> RawCanFrame | None:
        frame = self.transport.recv(timeout_s=max(0.0, timeout_s))
        if frame is not None:
            self.event_log.add(RawFrameEvent("rx", datetime.now(), frame))
        return frame

    def _iter_frames_until(self, deadline: float) -> Iterator[RawCanFrame]:
        """Yield received frames until ``deadline``.

        Always polls at least once, so a deadline already in the past still
        returns one frame that is immediately available.
        """
        first = True
        while first or monotonic() < deadline:
            first = False
            timeout_s = max(0.0, min(RECV_SLICE_S, deadline - monotonic()))
            frame = self._recv_once(timeout_s)
            if frame is None:
                if timeout_s <= 0:
                    return
                continue
            yield frame

    def _collect_scan_responses(self, deadline: float, found_ids: set[int], events: list[str]) -> None:
        for frame in self._iter_frames_until(deadline):
            driver_id = parse_scan_response(frame)
            if driver_id is None:
                events.append(f"ignored frame 0x{frame.arbitration_id:08X}")
                continue
            found_ids.add(driver_id)
            events.append(f"received scan response from driver ID {driver_id}")

    # ------------------------------------------------- legacy parameter reads

    def _read_u32(
        self,
        driver_id: int,
        index: int,
        deadline: float,
        events: list[str],
        report_counters: Counters | None = None,
        stress_tracker: CanStressTracker | None = None,
        total_messages: int | None = None,
    ) -> int | None:
        """Read a uint32 parameter; returns ``None`` on timeout."""
        self._send(build_single_read_frame(driver_id, index), f"read 0x{index:04X}")
        frame = self._wait_for(
            lambda candidate: read_param_value_u32(candidate, driver_id) is not None,
            min(deadline, monotonic() + self.response_timeout_s),
            events,
            report_driver_id=driver_id,
            report_counters=report_counters,
            stress_tracker=stress_tracker,
            total_messages=total_messages,
        )
        return None if frame is None else read_param_value_u32(frame, driver_id)

    def _read_float(
        self,
        driver_id: int,
        index: int,
        deadline: float,
        events: list[str],
        report_counters: Counters | None = None,
    ) -> float | None:
        """Read a float32 parameter; returns ``None`` on timeout."""
        self._send(build_single_read_frame(driver_id, index), f"read 0x{index:04X}")
        frame = self._wait_for(
            lambda candidate: read_param_value_float(candidate, driver_id) is not None,
            min(deadline, monotonic() + self.response_timeout_s),
            events,
            report_driver_id=driver_id,
            report_counters=report_counters,
        )
        return None if frame is None else read_param_value_float(frame, driver_id)

    def _wait_for(
        self,
        predicate: Callable[[RawCanFrame], bool],
        deadline: float,
        events: list[str],
        report_driver_id: int | None = None,
        report_counters: Counters | None = None,
        stress_tracker: CanStressTracker | None = None,
        total_messages: int | None = None,
    ) -> RawCanFrame | None:
        """Return the first frame matching ``predicate`` before ``deadline``.

        Asynchronous traffic that arrives meanwhile is not lost: diagnostic
        reports update ``report_counters``, CAN-stress frames feed
        ``stress_tracker``, and everything else is noted in ``events``.
        """
        for frame in self._iter_frames_until(deadline):
            if predicate(frame):
                return frame
            report = parse_diagnostic_test_report(frame, report_driver_id)
            if report is not None and report.test_id in DIAG_REPORT_TEST_IDS:
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
                    stress_tracker.record(stress_frame.sequence, stress_frame.elapsed_ms, total_messages)
                events.append(f"CAN stress frame sequence={stress_frame.sequence} elapsed_ms={stress_frame.elapsed_ms}")
                continue
            stress_report = parse_can_stress_report(frame, report_driver_id)
            if stress_report is not None:
                if report_counters is not None:
                    report_counters["report_sequence"] = stress_report.sequence
                    report_counters["report_elapsed_ms"] = stress_report.elapsed_ms
                events.append(
                    f"CAN stress report sequence={stress_report.sequence} elapsed_ms={stress_report.elapsed_ms}"
                )
                continue
            events.append(f"ignored frame 0x{frame.arbitration_id:08X}")
        return None

    # ------------------------------------------------------ legacy CAN stress

    def _collect_can_stress_frames(
        self,
        driver_id: int,
        deadline: float,
        events: list[str],
        stress_tracker: CanStressTracker,
        total_messages: int,
    ) -> None:
        """Gather trailing stress frames that arrive after the board reports FINISHED."""
        while monotonic() < deadline:
            frame = self._recv_once(min(RECV_SLICE_S, max(0.0, deadline - monotonic())))
            if frame is None:
                return
            stress_frame = parse_can_stress_frame(frame, driver_id)
            if stress_frame is None:
                events.append(f"ignored frame 0x{frame.arbitration_id:08X}")
                continue
            stress_tracker.record(stress_frame.sequence, stress_frame.elapsed_ms, total_messages)
            events.append(f"CAN stress frame sequence={stress_frame.sequence} elapsed_ms={stress_frame.elapsed_ms}")

    @staticmethod
    def _finalize_can_stress_counters(
        counters: Counters,
        stress_tracker: CanStressTracker,
        total_messages: int,
        elapsed_ms: int | None,
    ) -> None:
        """Derive received/missing/duplicate counts, success rate and receive rate."""
        received_count = len(stress_tracker.sequences)
        rate_elapsed_ms = elapsed_ms or stress_tracker.last_elapsed_ms
        counters["received_message_count"] = received_count
        counters["missing_message_count"] = max(0, total_messages - received_count)
        counters["duplicate_message_count"] = stress_tracker.duplicate_count + stress_tracker.out_of_range_count
        counters["success_rate_percent"] = received_count * 100.0 / total_messages if total_messages > 0 else 0.0
        counters["receive_rate_hz"] = (
            received_count * 1000.0 / rate_elapsed_ms if rate_elapsed_ms and rate_elapsed_ms > 0 else 0.0
        )

    def _finish_can_stress_result(
        self,
        start: float,
        events: list[str],
        driver_id: int,
        status: int | None,
        stop_reason: int | None,
        elapsed_ms: int | None,
        counters: Counters,
        stress_tracker: CanStressTracker,
        total_messages: int,
    ) -> ServiceResult:
        """Final CAN stress verdict: PASS above ``CAN_STRESS_MIN_SUCCESS_PERCENT``."""
        self._finalize_can_stress_counters(counters, stress_tracker, total_messages, elapsed_ms)
        received_count = int(counters["received_message_count"] or 0)
        missing_count = int(counters["missing_message_count"] or 0)
        duplicate_count = int(counters["duplicate_message_count"] or 0)
        success_rate = float(counters["success_rate_percent"] or 0.0)
        receive_rate_hz = float(counters["receive_rate_hz"] or 0.0)
        passed = success_rate > CAN_STRESS_MIN_SUCCESS_PERCENT
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

    # ------------------------------------------------------- result builders

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
        """Best-effort stop of the motor, then return a FAIL verdict."""
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
        counters: Counters,
    ) -> ServiceResult:
        """Best-effort stop of the stress stream, then return a FAIL verdict."""
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
        elapsed_override_ms: int | None = None,
        **metrics: Any,
    ) -> ServiceResult:
        """Build a :class:`ServiceResult`.

        ``metrics`` are forwarded to :class:`TestVerdict` as keyword fields, so
        an unknown name raises ``TypeError`` rather than being silently dropped.
        Elapsed time defaults to the wall time since ``start``.
        """
        elapsed_ms = elapsed_override_ms
        if elapsed_ms is None:
            elapsed_ms = int((monotonic() - start) * 1000)
        return ServiceResult(
            verdict=TestVerdict(
                test_name=test_name,
                passed=passed,
                reason=reason,
                elapsed_ms=elapsed_ms,
                events=events,
                **metrics,
            ),
            transport_warning=self._take_transport_warning(),
        )

    def _take_transport_warning(self) -> str | None:
        """Return and reset the serial parser's error summary, if the transport has one."""
        take_parser_stats = getattr(self.transport, "take_parser_stats", None)
        if not callable(take_parser_stats):
            return None
        summary = getattr(take_parser_stats(), "summary", None)
        return summary() if callable(summary) else None
