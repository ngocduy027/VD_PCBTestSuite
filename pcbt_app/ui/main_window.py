"""Main window: test orchestration, result cards and UI state machine.

Port/adapter handling lives in :mod:`pcbt_app.ui.adapter_panel` and the
GongWang encoder tab in :mod:`pcbt_app.ui.gw_encoder_panel`; both are mixed
into :class:`MainWindow`. All hardware I/O runs on a :class:`CanWorker` in a
separate ``QThread``; the window only emits request signals and reacts to the
worker's result signals.

UI states (``self._ui_state``): ``closed`` -> ``opening`` -> ``open`` ->
``detecting`` -> ``ready``/``failed``.
"""

from __future__ import annotations

import logging
from collections import deque

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QLineEdit,
    QMainWindow,
    QMessageBox,
)

from pcbt_app import APP_TITLE
from pcbt_app.config import (
    WAVESHARE_BACKEND,
    WAVESHARE_MODEL_B_BACKEND,
)
from pcbt_app.protocol.fct_can import (
    FCT_MAX_DURATION_MS,
    FCT_MAX_PERIOD_TICKS,
    FCT_OPENLOOP_MAX_THETA_STEP_RAD,
    FCT_OPENLOOP_MIN_THETA_STEP_RAD,
    rs485_max_duration_ms,
)
from pcbt_app.protocol.mit_firmware import (
    CAN_STRESS_DEFAULT_INTERVAL_MS,
    CAN_STRESS_DEFAULT_TOTAL_MESSAGES,
    GONGWANG_DEFAULT_DURATION_MS,
    OPEN_LOOP_DEFAULT_DURATION_MS,
    OPEN_LOOP_DEFAULT_THETA_STEP,
    RS485_STRESS_DEFAULT_DURATION_MS,
    RS485_STRESS_DEFAULT_PERIOD_TICKS,
)
from pcbt_app.services.pcb_test_service import ServiceResult
from pcbt_app.ui.adapter_panel import AdapterPanelMixin
from pcbt_app.ui.amplitude_range import AmplitudeRange
from pcbt_app.ui.gw_encoder_panel import GwEncoderPanelMixin
from pcbt_app.ui.layout import build_main_window_ui
from pcbt_app.ui.presets import (
    CAN_STRESS_APP_TIMEOUT_DEFAULT_MS,
    DETECT_ID_DEFAULT_TIMEOUT_MS,
    GONGWANG_APP_TIMEOUT_DEFAULT_MS,
    MAX_TEST_TIMEOUT_MS,
    OPENLOOP_APP_TIMEOUT_DEFAULT_MS,
    RS485_STRESS_DEFAULT_TIMEOUT_MS,
)
from pcbt_app.ui.result_report import (
    gongwang_fault_log_lines,
    reset_result_card,
    set_result_card,
    set_result_card_error,
    set_result_card_running,
)
from pcbt_app.ui.styles import (
    BANNER_STYLES,
    banner_stylesheet,
    main_window_stylesheet,
)
from pcbt_app.ui.worker import CanWorker, TestOutcome

logger = logging.getLogger(__name__)


class MainWindow(AdapterPanelMixin, GwEncoderPanelMixin, QMainWindow):
    """Top-level operator window for running PCB tests."""

    request_connect = Signal(object)
    request_scan_model_b = Signal()
    request_disconnect = Signal()
    request_configure_adapter = Signal(int)
    request_can_test = Signal(int)
    request_openloop_test = Signal(int, float, int)
    request_can_stress_test = Signal(int, int, int)
    request_gongwang_test = Signal(int, int, int)
    request_rs485_connect = Signal(object)
    request_rs485_disconnect = Signal()
    request_rs485_stress_test = Signal(int, int, int)
    request_start_gw_live = Signal(int)
    request_read_gw_live_samples = Signal(int, int)
    request_stop_gw_live = Signal()
    request_start_gw_calibration = Signal()
    request_read_gw_calibration_result = Signal()

    def __init__(self, settings=None) -> None:
        super().__init__()
        self._settings = settings
        self._amplitude_limits = AmplitudeRange.load(settings)
        self._model_b_scanning = False
        self._preferred_model_b_device = int(settings.value("adapter/device_index", 0)) if settings is not None else 0
        saved_backend = settings.value("adapter/backend", "") if settings is not None else ""
        self.setWindowTitle(APP_TITLE)
        self.resize(1280, 720)
        self.setMinimumSize(1120, 720)

        self._worker_thread = QThread(self)
        self._worker = CanWorker()
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.start()

        self.request_connect.connect(self._worker.connect_bus)
        self.request_scan_model_b.connect(self._worker.scan_model_b_devices)
        self._worker.model_b_devices_scanned.connect(self._on_model_b_devices_scanned)
        self.request_disconnect.connect(self._worker.disconnect_bus)
        self.request_configure_adapter.connect(self._worker.configure_adapter)
        self.request_can_test.connect(self._worker.run_can_test)
        self.request_openloop_test.connect(self._worker.run_openloop_test)
        self.request_can_stress_test.connect(self._worker.run_can_stress_test)
        self.request_gongwang_test.connect(self._worker.run_gongwang_test)
        self.request_rs485_connect.connect(self._worker.connect_rs485)
        self.request_rs485_disconnect.connect(self._worker.disconnect_rs485)
        self.request_rs485_stress_test.connect(self._worker.run_rs485_stress_test)
        self.request_start_gw_live.connect(self._worker.start_gw_live)
        self.request_read_gw_live_samples.connect(self._worker.read_gw_live_samples)
        self.request_stop_gw_live.connect(self._worker.stop_gw_live)
        self.request_start_gw_calibration.connect(self._worker.start_gw_calibration)
        self.request_read_gw_calibration_result.connect(self._worker.read_gw_calibration_result)
        self._worker.connection_changed.connect(self._on_connection_changed)
        self._worker.rs485_connection_changed.connect(self._on_rs485_connection_changed)
        self._worker.adapter_configured.connect(self._on_adapter_configured)
        self._worker.test_finished.connect(self._on_test_finished)
        self._worker.test_progress.connect(self._on_test_progress)
        self._worker.log_updated.connect(self._on_log_updated)
        self._worker.gw_live_updated.connect(self._on_gw_live_updated)
        self._worker.gw_live_progress.connect(self._on_gw_live_progress)
        self._worker.gw_live_state_changed.connect(self._on_gw_live_state_changed)
        self._worker.gw_calibration_updated.connect(self._on_gw_calibration_updated)

        self._connected = False
        self._rs485_connected = False
        self._id_detected = False
        self._last_log_count = 0
        self._active_tests: set[str] = set()
        self._gw_live_running = False
        self._gw_live_sample_target: int | None = None
        self._gw_calibration_active = False
        self._gw_calibration_operation: str | None = None
        self._gw_amplitude_ranges: dict[str, tuple[float, float] | None] = {
            "motor": None,
            "output": None,
        }
        self._gw_amplitude_samples: dict[str, deque[float]] = {
            "motor": deque(maxlen=500),
            "output": deque(maxlen=500),
        }
        self._test_run_inputs: dict[str, dict[str, object]] = {}
        self._ui_state = "closed"
        self._build_ui()
        if self._settings is not None:
            saved_channel_index = int(self._settings.value("adapter/channel_index", 1))
            self.model_b_channel.setCurrentIndex(saved_channel_index if saved_channel_index in (0, 1) else 1)
            if saved_backend in (WAVESHARE_BACKEND, WAVESHARE_MODEL_B_BACKEND):
                self.backend.setCurrentText(saved_backend)
        self._apply_app_style()
        self._set_ui_state("closed")
        self._append_console("Closed COM!")

    def _build_ui(self) -> None:
        build_main_window_ui(self)

    def _group(self, title: str) -> QGroupBox:
        group = QGroupBox(title)
        group.setObjectName("ClassicGroup")
        return group

    def _read_only_value(self, text: str) -> QLineEdit:
        field = QLineEdit(text)
        field.setReadOnly(True)
        field.setObjectName("ReadOnlyField")
        return field

    def _apply_app_style(self) -> None:
        self.setStyleSheet(main_window_stylesheet())

    def closeEvent(self, event) -> None:  # noqa: N802
        """Stop live polling, close both ports and wait up to 3 s for the worker thread."""
        self.request_stop_gw_live.emit()
        self.request_disconnect.emit()
        self.request_rs485_disconnect.emit()
        self._worker_thread.quit()
        self._worker_thread.wait(3000)
        super().closeEvent(event)

    def _format_driver_id(self, driver_id: int | None) -> str:
        return "-" if driver_id is None else str(driver_id)

    def _run_default_test(self, test_key: str) -> None:
        handlers = {
            "detect_id": self._run_detect_id,
            "openloop": self._run_openloop_test,
            "can_stress": self._run_can_stress_test,
            "rs485_stress": self._run_rs485_stress_test,
            "gongwang": self._run_gongwang_test,
        }
        handlers[test_key](use_defaults=True)

    def _run_detect_id(self, *, use_defaults: bool = False) -> None:
        """Validate the timeout and ask the worker to detect the driver ID."""
        timeout_ms = (
            DETECT_ID_DEFAULT_TIMEOUT_MS
            if use_defaults
            else self._parse_int_field(
                self.detect_id_timeout, 1, MAX_TEST_TIMEOUT_MS, "Detect ID timeout"
            )
        )
        if timeout_ms is None:
            return
        self._clear_completed_result_cards()
        self._append_console("Detecting ID...")
        self._apply_banner_state("running")
        self._set_ui_state("detecting")
        self.request_can_test.emit(timeout_ms)

    def _run_openloop_test(
        self, *, use_defaults: bool = False, use_live_fields: bool = False
    ) -> None:
        """Read/validate open-loop inputs (or defaults) and start the test."""
        app_timeout_ms: int | None
        theta_step: float | None
        duration_ms: int | None
        if use_defaults:
            app_timeout_ms = OPENLOOP_APP_TIMEOUT_DEFAULT_MS
            theta_step = OPEN_LOOP_DEFAULT_THETA_STEP
            duration_ms = OPEN_LOOP_DEFAULT_DURATION_MS
        else:
            app_timeout_ms = self._parse_int_field(
                self.gw_live_openloop_app_timeout if use_live_fields else self.openloop_app_timeout,
                1, MAX_TEST_TIMEOUT_MS, "Open-loop app timeout"
            )
            theta_step = self._parse_float(
                self.openloop_theta_step,
                FCT_OPENLOOP_MIN_THETA_STEP_RAD,
                FCT_OPENLOOP_MAX_THETA_STEP_RAD,
                "Open-loop theta step",
            )
            duration_ms = self._parse_int_field(
                self.gw_live_openloop_duration if use_live_fields else self.openloop_duration,
                1, FCT_MAX_DURATION_MS, "Open-loop duration"
            )
        if app_timeout_ms is None or theta_step is None or duration_ms is None:
            return
        self._test_run_inputs["openloop"] = {
            "app_timeout_ms": app_timeout_ms,
            "theta_step": theta_step,
            "duration_ms": duration_ms,
        }
        self._append_console("Running open-loop test...")
        self._mark_test_started("openloop")
        self.request_openloop_test.emit(app_timeout_ms, theta_step, duration_ms)

    def _run_can_stress_test(self, *, use_defaults: bool = False) -> None:
        """Read/validate CAN stress inputs (or defaults) and start the test."""
        app_timeout_ms: int | None
        interval_ms: int | None
        total_messages: int | None
        if use_defaults:
            app_timeout_ms = CAN_STRESS_APP_TIMEOUT_DEFAULT_MS
            interval_ms = CAN_STRESS_DEFAULT_INTERVAL_MS
            total_messages = CAN_STRESS_DEFAULT_TOTAL_MESSAGES
        else:
            app_timeout_ms = self._parse_int_field(
                self.can_stress_app_timeout, 1, MAX_TEST_TIMEOUT_MS, "CAN stress app timeout"
            )
            interval_ms = self._parse_int_field(
                self.can_stress_interval, 1, 1000, "CAN stress interval"
            )
            total_messages = self._parse_int_field(
                self.can_stress_duration, 1, 1_000_000, "CAN stress total messages", ""
            )
        if app_timeout_ms is None or interval_ms is None or total_messages is None:
            return
        self._test_run_inputs["can_stress"] = {
            "app_timeout_ms": app_timeout_ms,
            "interval_ms": interval_ms,
            "total_messages": total_messages,
        }
        self.can_stress_received_display.setText("0")
        self._append_console("Running CAN stress test...")
        self._mark_test_started("can_stress")
        self.request_can_stress_test.emit(app_timeout_ms, interval_ms, total_messages)

    def _on_test_progress(self, test_key: str, value: int) -> None:
        if value < 0:
            return
        text = str(value)
        if test_key == "can_stress":
            self.can_stress_received_display.setText(text)
            card = self.result_cards["can_stress"]
            card["fields"]["actual_response_count"].setText(text)
        elif test_key == "rs485_stress":
            self.rs485_stress_received_display.setText(text)
            card = self.result_cards["rs485_stress"]
            card["fields"]["received_message_count"].setText(text)

    def _run_rs485_stress_test(self, *, use_defaults: bool = False) -> None:
        """Read/validate RS485 inputs (or defaults) and start the test."""
        period_ticks: int | None
        duration_ms: int | None
        timeout_ms: int | None
        if use_defaults:
            period_ticks = RS485_STRESS_DEFAULT_PERIOD_TICKS
            duration_ms = RS485_STRESS_DEFAULT_DURATION_MS
            timeout_ms = RS485_STRESS_DEFAULT_TIMEOUT_MS
        else:
            period_ticks = self._parse_int_field(
                self.rs485_stress_interval, 1, FCT_MAX_PERIOD_TICKS, "RS485 period", "ticks"
            )
            duration_ms = self._parse_int_field(
                self.rs485_stress_total, 1, FCT_MAX_DURATION_MS, "RS485 duration"
            )
            timeout_ms = self._parse_int_field(
                self.rs485_stress_timeout, 1, MAX_TEST_TIMEOUT_MS, "RS485 timeout"
            )
        if period_ticks is None or duration_ms is None or timeout_ms is None:
            return
        max_duration_ms = rs485_max_duration_ms(period_ticks)
        if duration_ms > max_duration_ms:
            self._append_console(
                f"RS485 duration must be 1..{max_duration_ms} ms for period {period_ticks} ticks."
            )
            return
        self._test_run_inputs["rs485_stress"] = {
            "timeout_ms": timeout_ms,
            "duration_ms": duration_ms,
            "period_ticks": period_ticks,
        }
        self.rs485_stress_received_display.setText("0")
        self._append_console("Running RS485 stress test...")
        self._mark_test_started("rs485_stress")
        self.request_rs485_stress_test.emit(timeout_ms, duration_ms, period_ticks)

    def _run_gongwang_test(self, *, use_defaults: bool = False) -> None:
        """Read/validate GongWang inputs (or defaults) and start the test."""
        app_timeout_ms: int | None
        duration_ms: int | None
        period_ticks: int | None
        if use_defaults:
            app_timeout_ms = GONGWANG_APP_TIMEOUT_DEFAULT_MS
            duration_ms = GONGWANG_DEFAULT_DURATION_MS
            period_ticks = 1
        else:
            app_timeout_ms = self._parse_int_field(
                self.gongwang_app_timeout, 1, MAX_TEST_TIMEOUT_MS, "GongWang app timeout"
            )
            duration_ms = self._parse_int_field(
                self.gongwang_duration,
                1,
                FCT_MAX_DURATION_MS,
                "GongWang duration",
            )
            period_ticks = self._parse_int_field(
                self.gongwang_period, 1, FCT_MAX_PERIOD_TICKS, "GongWang period", "ticks"
            )
        if app_timeout_ms is None or duration_ms is None or period_ticks is None:
            return
        self._test_run_inputs["gongwang"] = {
            "app_timeout_ms": app_timeout_ms,
            "duration_ms": duration_ms,
            "period_ticks": period_ticks,
        }
        self._append_console("Running GongWang encoder test...")
        self._mark_test_started("gongwang")
        self.request_gongwang_test.emit(app_timeout_ms, duration_ms, period_ticks)

    def _clear_completed_result_cards(self, _index: int | None = None) -> None:
        for test_key, card in self.result_cards.items():
            if test_key in self._active_tests:
                continue
            reset_result_card(card)
            self._test_run_inputs.pop(test_key, None)
        if self._active_tests:
            self._apply_banner_state("running")
            self.overall_status.setText(f"RUNNING ({len(self._active_tests)})")
        else:
            self._apply_banner_state("idle")

    def _mark_test_started(self, test_key: str) -> None:
        self._active_tests.add(test_key)
        if test_key in self.result_cards:
            set_result_card_running(self.result_cards[test_key])
        self._apply_banner_state("running")
        self.overall_status.setText(f"RUNNING ({len(self._active_tests)})")
        self._set_ui_state(self._ui_state)

    def _parse_int_field(
        self,
        field: QLineEdit,
        minimum: int,
        maximum: int,
        name: str,
        unit: str = "ms",
    ) -> int | None:
        """Parse an integer input in ``minimum..maximum``; logs a message and returns ``None`` if invalid."""
        text = field.text().strip()
        suffix = f" {unit}" if unit else ""
        try:
            value = int(text)
        except ValueError:
            self._append_console(f"{name} must be {minimum}..{maximum}{suffix}.")
            return None
        if not minimum <= value <= maximum:
            self._append_console(f"{name} must be {minimum}..{maximum}{suffix}.")
            return None
        return value

    def _parse_float(self, field: QLineEdit, minimum: float, maximum: float, name: str) -> float | None:
        """Parse a float input in ``minimum..maximum``; logs a message and returns ``None`` if invalid."""
        text = field.text().strip()
        try:
            value = float(text)
        except ValueError:
            self._append_console(f"{name} must be {minimum:g}..{maximum:g}.")
            return None
        if not minimum <= value <= maximum:
            self._append_console(f"{name} must be {minimum:g}..{maximum:g}.")
            return None
        return value

    def _on_test_finished(self, payload: object) -> None:
        """Handle a finished test: update its result card, the banner and the console.

        A passing Detect ID stores the driver ID, which enables the other tests.
        """
        test_key = None
        if isinstance(payload, TestOutcome):
            test_key = payload.test_key
            payload = payload.payload
            self._active_tests.discard(test_key)
        if isinstance(payload, Exception):
            self._append_console(f"FAIL: {payload}")
            if test_key in self.result_cards:
                set_result_card_error(self.result_cards[test_key], str(payload))
            self._set_ui_state("failed")
            if self._active_tests:
                self._apply_banner_state("running")
                self.overall_status.setText(f"RUNNING ({len(self._active_tests)})")
            else:
                self._apply_banner_state("fail")
            QMessageBox.critical(self, "Test Failed", str(payload))
            return

        result = payload
        assert isinstance(result, ServiceResult)
        if result.transport_warning:
            self._append_console(result.transport_warning)
        verdict = result.verdict
        if test_key is None:
            test_key = {
                "Open-loop Test": "openloop",
                "CAN Stress Test": "can_stress",
                "GongWang Encoder Test": "gongwang",
                "RS485 Stress Test": "rs485_stress",
                "CAN Test": "can",
            }.get(verdict.test_name)
            if test_key is not None:
                self._active_tests.discard(test_key)
        is_detect_id = verdict.test_name == "CAN Test"
        display_test_name = "Detect ID" if is_detect_id else verdict.test_name
        if not is_detect_id and test_key in self.result_cards:
            set_result_card(
                self.result_cards[test_key],
                verdict,
                self._test_run_inputs.get(test_key, {}) if test_key else {},
                self._format_driver_id,
            )
        if self._active_tests:
            self._apply_banner_state("running")
            self.overall_status.setText(f"RUNNING ({len(self._active_tests)})")
        else:
            self._apply_banner_state("pass" if verdict.passed else "fail")
        if verdict.gongwang_results is not None:
            for line in gongwang_fault_log_lines(verdict.gongwang_results.fault_code):
                self._append_console(line)

        if verdict.passed:
            if is_detect_id:
                driver_id = self._format_driver_id(verdict.returned_driver_id)
                self._id_detected = True
                self.driver_id_display.setText(driver_id)
                self.gw_live_driver_id.setText(driver_id)
                self.gw_calibration_driver_id.setText(driver_id)
                self._append_console(f"Driver ID: {driver_id}")
            self._append_console(f"PASS: {display_test_name}")
            self._set_ui_state("ready")
            return

        if is_detect_id:
            self._id_detected = False
            self.driver_id_display.clear()
            self.gw_live_driver_id.clear()
            self.gw_calibration_driver_id.clear()
            if "no motor" in verdict.reason.lower():
                self._append_console("No Motor Found! Please check connection and try again.")
            else:
                self._append_console(f"Detect ID failed: {verdict.reason}")
        self._append_console(f"FAIL: {verdict.reason}")
        self._set_ui_state("failed")

    def _on_log_updated(self, lines: list[str]) -> None:
        if not lines:
            return
        new_lines = lines[self._last_log_count :]
        self._last_log_count = len(lines)
        if new_lines:
            self._append_console("time         dir  can id      payload                       note")
            for line in new_lines:
                self._append_console(line)

    def _set_ui_state(self, state: str) -> None:
        """Recompute every control's enabled state and button text.

        ``state`` is the connection/test phase; the rest comes from window fields
        (active tests, calibration, live polling, RS485 connection). Call it after
        any change to those fields.
        """
        self._ui_state = state
        closed = state == "closed"
        self.adapter_model.setEnabled(closed and not self._model_b_scanning)
        self.model_b_channel.setEnabled(closed)
        working = state in {"opening", "detecting", "running_test"}
        opened = state in {"open", "ready", "failed"}
        fct_running = bool(self._active_tests)
        calibration_active = self._gw_calibration_active

        is_model_a = self.backend.currentText() == WAVESHARE_BACKEND
        is_model_b = self.backend.currentText() == WAVESHARE_MODEL_B_BACKEND
        self.channel.setEnabled(closed and not self._model_b_scanning)
        self.serial_baud.setEnabled(closed and is_model_a)
        self.bitrate.setEnabled(closed or (is_model_b and opened and not working and not fct_running and not calibration_active and not self._gw_live_running))
        self.refresh_ports_button.setEnabled(closed and not self._model_b_scanning and (is_model_a or is_model_b))
        self.set_and_start_button.setEnabled(opened and not working and not fct_running and not calibration_active and not self._gw_live_running)
        self.com_toggle_button.setEnabled(closed or (opened and not working and not fct_running and not calibration_active))
        if closed and is_model_b:
            self.com_toggle_button.setEnabled(not self._model_b_scanning and self.channel.currentData() is not None)
        if closed or state == "opening":
            self._set_com_toggle_visual("OPEN", "OpenButton")
        else:
            self._set_com_toggle_visual("CLOSE", "CloseButton")
        self.detect_id_button.setEnabled(
            opened
            and not working
            and not fct_running
            and not calibration_active
            and not self._gw_live_running
        )
        self.openloop_button.setEnabled(self._id_detected and opened and not working and not calibration_active and "openloop" not in self._active_tests)
        self.can_stress_button.setEnabled(self._id_detected and opened and not working and not calibration_active and "can_stress" not in self._active_tests)
        self.gongwang_button.setEnabled(self._id_detected and opened and not working and not calibration_active and "gongwang" not in self._active_tests)
        self.rs485_stress_button.setEnabled(self._id_detected and self._rs485_connected and opened and not working and not calibration_active and "rs485_stress" not in self._active_tests)
        self.detect_id_timeout.setEnabled(opened and not working and not fct_running)
        self.openloop_theta_step.setEnabled(opened and not working and "openloop" not in self._active_tests)
        self.openloop_duration.setEnabled(opened and not working and "openloop" not in self._active_tests)
        self.openloop_app_timeout.setEnabled(opened and not working and "openloop" not in self._active_tests)
        self.can_stress_interval.setEnabled(opened and not working and "can_stress" not in self._active_tests)
        self.can_stress_duration.setEnabled(opened and not working and "can_stress" not in self._active_tests)
        self.can_stress_app_timeout.setEnabled(opened and not working and "can_stress" not in self._active_tests)
        self.rs485_stress_interval.setEnabled(opened and not working and not calibration_active and "rs485_stress" not in self._active_tests)
        self.rs485_stress_total.setEnabled(opened and not working and not calibration_active and "rs485_stress" not in self._active_tests)
        self.rs485_stress_timeout.setEnabled(opened and not working and not calibration_active and "rs485_stress" not in self._active_tests)
        self.gongwang_duration.setEnabled(opened and not working and not calibration_active and "gongwang" not in self._active_tests)
        self.gongwang_period.setEnabled(opened and not working and not calibration_active and "gongwang" not in self._active_tests)
        self.gongwang_app_timeout.setEnabled(opened and not working and not calibration_active and "gongwang" not in self._active_tests)
        self.rs485_channel.setEnabled(not self._rs485_connected and not working and not fct_running)
        self.rs485_baud.setEnabled(not self._rs485_connected and not working and not fct_running)
        self.refresh_rs485_ports_button.setEnabled(not self._rs485_connected and not working and not fct_running)
        self.rs485_toggle_button.setEnabled(not working and not fct_running and not calibration_active)
        self.gw_live_frequency.setEnabled(self._connected and not self._gw_live_running and not calibration_active)
        self.gw_live_button.setEnabled(
            not calibration_active and (self._gw_live_running or (self._connected and self._id_detected))
        )
        self.gw_live_button.setText("Stop" if self._gw_live_running else "Start")
        self.gw_live_read_500_button.setEnabled(
            not calibration_active
            and not self._gw_live_running
            and self._connected
            and self._id_detected
        )
        self.gw_live_clear_button.setEnabled(
            not (self._gw_live_running and self._gw_live_sample_target is not None)
        )
        self.gw_calibration_button.setEnabled(
            self._connected
            and self._id_detected
            and not self._gw_live_running
            and not fct_running
            and not working
            and not calibration_active
        )
        self.gw_calibration_button.setText(
            "Calibration Running..."
            if calibration_active and self._gw_calibration_operation == "calibration"
            else "Start Calibration"
        )
        self.gw_calibration_read_button.setEnabled(
            self._connected
            and self._id_detected
            and not self._gw_live_running
            and not fct_running
            and not working
            and not calibration_active
        )
        self.gw_calibration_read_button.setText(
            "Reading..."
            if calibration_active and self._gw_calibration_operation == "read"
            else "Read Result"
        )
        self.openloop_button.setText("Open-loop Running..." if "openloop" in self._active_tests else "Start Open-loop Test")
        self.can_stress_button.setText("CAN Stress Running..." if "can_stress" in self._active_tests else "Start CAN Stress Test")
        self.rs485_stress_button.setText("RS485 Stress Running..." if "rs485_stress" in self._active_tests else "RS485 Stress Test")
        self.gongwang_button.setText("GongWang Running..." if "gongwang" in self._active_tests else "Start GongWang Encoder Test")
        for buttons in self._test_button_copies.values():
            source = buttons[0]
            for copy in buttons[1:]:
                copy.setEnabled(source.isEnabled())
                copy.setText(source.text())
        for field_groups in self._test_field_copies.values():
            for source, copies in field_groups.items():
                for copy in copies[1:]:
                    copy.setEnabled(source.isEnabled())
        if self._rs485_connected:
            self._set_rs485_toggle_visual("CLOSE", "CloseButton")
        else:
            self._set_rs485_toggle_visual("OPEN", "OpenButton")
        self.clear_console_button.setEnabled(True)

    def _append_console(self, text: str) -> None:
        self.console_box.appendPlainText(text)

    def _apply_banner_state(self, state: str) -> None:
        style = BANNER_STYLES[state]
        text, background, color = style
        self.overall_status.setText(text)
        self.overall_status.setStyleSheet(banner_stylesheet(background, color))
