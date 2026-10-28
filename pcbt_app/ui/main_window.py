from __future__ import annotations

from collections import deque

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QLineEdit,
    QMainWindow,
    QMessageBox,
)

from pcbt_app.config import (
    DEFAULT_PCAN_CHANNEL,
    DEFAULT_MODEL_B_CHANNEL,
    DEFAULT_WAVESHARE_CHANNEL,
    WAVESHARE_BACKEND,
    WAVESHARE_MODEL_B_BACKEND,
)
from pcbt_app.services.pcb_test_service import ServiceResult
from pcbt_app.protocol.gw_calibration import describe_encoder_error
from pcbt_app.services.gw_calibration import GwCalibrationUpdate
from pcbt_app.protocol.mit_firmware import (
    CAN_STRESS_DEFAULT_INTERVAL_MS,
    CAN_STRESS_DEFAULT_TOTAL_MESSAGES,
    GONGWANG_DEFAULT_DURATION_MS,
    OPEN_LOOP_DEFAULT_DURATION_MS,
    OPEN_LOOP_DEFAULT_THETA_STEP,
    RS485_STRESS_DEFAULT_DURATION_MS,
    RS485_STRESS_DEFAULT_PERIOD_TICKS,
)
from pcbt_app.transport.rs485_serial import list_rs485_com_ports
from pcbt_app.transport.waveshare_usbcan_a import list_waveshare_com_ports
from pcbt_app.ui.amplitude_range import AmplitudeRange, AmplitudeRangeDialog
from pcbt_app.ui.layout import build_main_window_ui
from pcbt_app.ui.presets import (
    CAN_STRESS_APP_TIMEOUT_DEFAULT_MS,
    DETECT_ID_DEFAULT_TIMEOUT_MS,
    GONGWANG_APP_TIMEOUT_DEFAULT_MS,
    MAX_TEST_TIMEOUT_MS,
    OPENLOOP_APP_TIMEOUT_DEFAULT_MS,
    RS485_STRESS_DEFAULT_TIMEOUT_MS,
    bitrate_value_from_label,
    format_waveshare_config_packet,
    serial_baud_value_from_label,
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
    amplitude_value_stylesheet,
    banner_stylesheet,
    main_window_stylesheet,
)
from pcbt_app.ui.worker import CanWorker, ConnectionParams, Rs485ConnectionParams, TestOutcome
from pcbt_app.protocol.fct_can import (
    FCT_MAX_DURATION_MS,
    FCT_MAX_PERIOD_TICKS,
    FCT_OPENLOOP_MAX_THETA_STEP_RAD,
    FCT_OPENLOOP_MIN_THETA_STEP_RAD,
)


class MainWindow(QMainWindow):
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
        self.setWindowTitle("VD_PCBTestSuite_V1.2")
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

    def _amplitude_stylesheet(self, value_mv):
        return amplitude_value_stylesheet(value_mv, self._amplitude_limits)

    def _show_amplitude_range_dialog(self):
        dialog = AmplitudeRangeDialog(self._amplitude_limits, self)
        dialog.range_applied.connect(self._apply_amplitude_range)
        dialog.exec()

    def _apply_amplitude_range(self, limits):
        self._amplitude_limits = limits
        limits.save(self._settings)
        self.amplitude_range_legend.setText(limits.legend())
        for ring_fields in self.gw_live_fields.values():
            for key in ("amplitude_mv", "amplitude_min_mv", "amplitude_max_mv"):
                field = ring_fields[key]
                text = field.text().removeprefix("Min: ").removeprefix("Max: ")
                value = None if text == "-" else float(text)
                field.setStyleSheet(self._amplitude_stylesheet(value))
        for ring_fields in self.gw_calibration_fields.values():
            for key, field in ring_fields.items():
                if key.endswith("_amplitude_v"):
                    value = None if field.text() == "-" else float(field.text()) * 1000.0
                    field.setStyleSheet(self._amplitude_stylesheet(value))

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
        self.request_stop_gw_live.emit()
        self.request_disconnect.emit()
        self.request_rs485_disconnect.emit()
        self._worker_thread.quit()
        self._worker_thread.wait(3000)
        super().closeEvent(event)

    def _connect_clicked(self) -> None:
        if self.backend.currentText() == WAVESHARE_MODEL_B_BACKEND and (self._model_b_scanning or self.channel.currentData() is None):
            self._append_console("Refresh devices and select an available Model B adapter first.")
            return
        self._append_console(f"Opening {self._current_channel()}...")
        self._set_ui_state("opening")
        params = ConnectionParams(
            backend=self.backend.currentText(),
            channel=self._current_channel(),
            bitrate=self._selected_bitrate(),
            serial_baud=self._selected_serial_baud(),
        )
        self.request_connect.emit(params)

    def _close_clicked(self) -> None:
        self._append_console("Closing COM...")
        self.request_disconnect.emit()

    def _toggle_com(self) -> None:
        if self._connected:
            self._close_clicked()
            return
        self._connect_clicked()

    def _current_rs485_channel(self) -> str:
        data = self.rs485_channel.currentData()
        if data:
            return str(data)
        text = self.rs485_channel.currentText().strip()
        return text.split(" ", 1)[0] if text else "COM1"

    def _selected_rs485_baud(self) -> int:
        value = self.rs485_baud.currentData()
        if value is not None:
            return int(value)
        return int(self.rs485_baud.currentText().strip())

    def _set_rs485_baud_value(self, baudrate: int) -> None:
        for index in range(self.rs485_baud.count()):
            if self.rs485_baud.itemData(index) == baudrate:
                self.rs485_baud.setCurrentIndex(index)
                return
        self.rs485_baud.addItem(str(baudrate), baudrate)
        self.rs485_baud.setCurrentIndex(self.rs485_baud.count() - 1)

    def _refresh_rs485_ports(self) -> None:
        self.rs485_channel.clear()
        try:
            ports = list_rs485_com_ports()
        except Exception:
            ports = []
        if not ports:
            self.rs485_channel.addItem("COM1", "COM1")
            return
        for device, description in ports:
            label = f"{device} - {description}" if description else device
            self.rs485_channel.addItem(label, device)
        self._append_console("Refreshed RS485 COM.")

    def _toggle_rs485_com(self) -> None:
        if self._rs485_connected:
            self._append_console("Closing RS485 COM...")
            self.request_rs485_disconnect.emit()
            return
        self._append_console(f"Opening RS485 {self._current_rs485_channel()}...")
        self.rs485_toggle_button.setEnabled(False)
        params = Rs485ConnectionParams(
            channel=self._current_rs485_channel(),
            baudrate=self._selected_rs485_baud(),
        )
        self.request_rs485_connect.emit(params)

    def _set_and_start_can(self) -> None:
        if not self._connected:
            self._append_console("Open COM before Set and Start.")
            return
        self._append_console("Set and Start CAN config...")
        self.request_configure_adapter.emit(self._selected_bitrate())

    def _set_com_toggle_visual(self, text: str, object_name: str) -> None:
        self.com_toggle_button.setText(text)
        if self.com_toggle_button.objectName() == object_name:
            return
        self.com_toggle_button.setObjectName(object_name)
        self.com_toggle_button.style().unpolish(self.com_toggle_button)
        self.com_toggle_button.style().polish(self.com_toggle_button)

    def _set_rs485_toggle_visual(self, text: str, object_name: str) -> None:
        self.rs485_toggle_button.setText(text)
        if self.rs485_toggle_button.objectName() == object_name:
            return
        self.rs485_toggle_button.setObjectName(object_name)
        self.rs485_toggle_button.style().unpolish(self.rs485_toggle_button)
        self.rs485_toggle_button.style().polish(self.rs485_toggle_button)

    def _format_driver_id(self, driver_id: int | None) -> str:
        return "-" if driver_id is None else str(driver_id)

    def _current_channel(self) -> str:
        if self.backend.currentText() == WAVESHARE_MODEL_B_BACKEND:
            return self._model_b_channel_name()
        data = self.channel.currentData()
        if data:
            return str(data)
        text = self.channel.currentText().strip()
        return text.split(" ", 1)[0] if text else DEFAULT_WAVESHARE_CHANNEL

    def _selected_bitrate(self) -> int:
        value = self.bitrate.currentData()
        if value is None:
            return bitrate_value_from_label(self.bitrate.currentText())
        return int(value)

    def _selected_serial_baud(self) -> int:
        value = self.serial_baud.currentData()
        if value is None:
            return serial_baud_value_from_label(self.serial_baud.currentText())
        return int(value)

    def _set_bitrate_value(self, bitrate: int) -> None:
        for index in range(self.bitrate.count()):
            if self.bitrate.itemData(index) == bitrate:
                self.bitrate.setCurrentIndex(index)
                return
        self.bitrate.addItem(str(bitrate), bitrate)
        self.bitrate.setCurrentIndex(self.bitrate.count() - 1)

    def _set_serial_baud_value(self, baudrate: int) -> None:
        for index in range(self.serial_baud.count()):
            if self.serial_baud.itemData(index) == baudrate:
                self.serial_baud.setCurrentIndex(index)
                return
        self.serial_baud.addItem(str(baudrate), baudrate)
        self.serial_baud.setCurrentIndex(self.serial_baud.count() - 1)

    def _backend_changed(self, backend: str) -> None:
        self.adapter_model.blockSignals(True)
        self.adapter_model.setCurrentIndex(self.adapter_model.findData(backend))
        self.adapter_model.blockSignals(False)
        if self._settings is not None and backend in (WAVESHARE_BACKEND, WAVESHARE_MODEL_B_BACKEND):
            self._settings.setValue("adapter/backend", backend)
        is_model_a = backend == WAVESHARE_BACKEND
        is_model_b = backend == WAVESHARE_MODEL_B_BACKEND
        for widget in (self.model_b_device_label, self.model_b_channel):
            widget.setVisible(is_model_b)
        from pcbt_app.ui.presets import BITRATE_PRESETS
        selected_bitrate = self._selected_bitrate()
        self.bitrate.blockSignals(True)
        self.bitrate.clear()
        for label, value in BITRATE_PRESETS:
            if not is_model_b or value in (1_000_000, 800_000, 500_000, 250_000, 125_000, 100_000):
                self.bitrate.addItem(label, value)
        index = self.bitrate.findData(selected_bitrate)
        self.bitrate.setCurrentIndex(max(0, index))
        self.bitrate.blockSignals(False)
        self.can_group.setTitle("Model B CAN Configure" if is_model_b else "CAN Configure")
        self.config_summary_label.setText("Channel / Mode" if is_model_b else "Data Send")
        self.filter_start_label.setText("Filter Start" if is_model_b else "Filter ID")
        self.filter_end_label.setText("Filter End" if is_model_b else "Mask ID")
        self.mask_id.setText("1FFFFFFF" if is_model_b else "00000000")
        self.can_group.setToolTip(
            "OPEN applies settings automatically. Set and Start reapplies the selected bitrate. "
            "Fixed for PCB tests: normal mode, extended classic CAN, full-range filter. "
            "The controller uses ISO CAN-FD with data bitrate equal to arbitration bitrate; no FD/BRS frames are sent."
            if is_model_b else ""
        )
        controls_available = self._ui_state == "closed"
        self.channel.setEnabled(controls_available)
        self.serial_baud.setEnabled(controls_available and is_model_a)
        self.refresh_ports_button.setEnabled(controls_available)
        self.refresh_ports_button.setText("Refresh Devices" if is_model_b else "Refresh COM")
        if backend == WAVESHARE_BACKEND:
            self._refresh_ports()
        elif backend == WAVESHARE_MODEL_B_BACKEND:
            self.channel.setEditable(False)
            self._refresh_ports()
        else:
            self.channel.clear()
            self.channel.addItem(DEFAULT_PCAN_CHANNEL if backend == "pcan" else "")
            self.channel.setEditable(True)
        self._update_config_packet()

    def _refresh_ports(self) -> None:
        if self.backend.currentText() == WAVESHARE_MODEL_B_BACKEND:
            if self._ui_state != "closed" or self._model_b_scanning:
                return
            self._model_b_scanning = True
            self.channel.clear()
            self.channel.addItem("Scanning devices...", None)
            self._set_ui_state("closed")
            self.request_scan_model_b.emit()
            return
        self.channel.setEditable(True)
        self.channel.clear()
        try:
            ports = list_waveshare_com_ports()
        except Exception:
            ports = []
        if not ports:
            self.channel.addItem(DEFAULT_WAVESHARE_CHANNEL, DEFAULT_WAVESHARE_CHANNEL)
            return
        for device, description in ports:
            label = f"{device} - {description}" if description else device
            self.channel.addItem(label, device)
        self._append_console("Refreshed COM.")

    def _update_config_packet(self) -> None:
        if self.backend.currentText() == WAVESHARE_MODEL_B_BACKEND:
            device = self.channel.currentData()
            self.data_send.setText(f"Device {device} / {self.model_b_channel.currentText()} / Normal / Classic CAN" if device is not None else "No Model B device selected")
            return
        try:
            self.data_send.setText(format_waveshare_config_packet(self._selected_bitrate()))
        except Exception as exc:
            self.data_send.setText(f"Unsupported bitrate: {exc}")

    def _model_b_channel_name(self) -> str:
        return f"USB-CAN-FD-B Device {self.channel.currentData()} / {self.model_b_channel.currentText()}"

    def _model_b_selection_changed(self) -> None:
        if self.backend.currentText() == WAVESHARE_MODEL_B_BACKEND:
            device = self.channel.currentData()
            if device is not None:
                self._preferred_model_b_device = int(device)
            if self._settings is not None:
                self._settings.setValue("adapter/device_index", self._preferred_model_b_device)
                self._settings.setValue("adapter/channel_index", self.model_b_channel.currentIndex())
            self._update_config_packet()

    def _on_model_b_devices_scanned(self, devices, error: str) -> None:
        self._model_b_scanning = False
        if self.backend.currentText() != WAVESHARE_MODEL_B_BACKEND:
            return
        self.channel.blockSignals(True)
        self.channel.clear()
        for index, label in devices:
            self.channel.addItem(label, index)
        if not devices:
            self.channel.addItem("No available Model B devices", None)
        selected = self.channel.findData(self._preferred_model_b_device)
        self.channel.setCurrentIndex(max(0, selected))
        self.channel.blockSignals(False)
        self._model_b_selection_changed()
        self._append_console(f"Model B scan failed: {error}" if error else f"Model B scan: {len(devices)} available device(s).")
        self._set_ui_state(self._ui_state)

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
        timeout_ms = (
            DETECT_ID_DEFAULT_TIMEOUT_MS
            if use_defaults
            else self._parse_can_stress_int(
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
        if use_defaults:
            app_timeout_ms = OPENLOOP_APP_TIMEOUT_DEFAULT_MS
            theta_step = OPEN_LOOP_DEFAULT_THETA_STEP
            duration_ms = OPEN_LOOP_DEFAULT_DURATION_MS
        else:
            app_timeout_ms = self._parse_can_stress_int(
                self.gw_live_openloop_app_timeout if use_live_fields else self.openloop_app_timeout,
                1, MAX_TEST_TIMEOUT_MS, "Open-loop app timeout"
            )
            theta_step = self._parse_float(
                self.openloop_theta_step,
                FCT_OPENLOOP_MIN_THETA_STEP_RAD,
                FCT_OPENLOOP_MAX_THETA_STEP_RAD,
                "Open-loop theta step",
            )
            duration_ms = self._parse_can_stress_int(
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
        if use_defaults:
            app_timeout_ms = CAN_STRESS_APP_TIMEOUT_DEFAULT_MS
            interval_ms = CAN_STRESS_DEFAULT_INTERVAL_MS
            total_messages = CAN_STRESS_DEFAULT_TOTAL_MESSAGES
        else:
            app_timeout_ms = self._parse_can_stress_int(
                self.can_stress_app_timeout, 1, MAX_TEST_TIMEOUT_MS, "CAN stress app timeout"
            )
            interval_ms = self._parse_can_stress_int(
                self.can_stress_interval, 1, 1000, "CAN stress interval"
            )
            total_messages = self._parse_can_stress_int(
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
        if use_defaults:
            period_ticks = RS485_STRESS_DEFAULT_PERIOD_TICKS
            duration_ms = RS485_STRESS_DEFAULT_DURATION_MS
            timeout_ms = RS485_STRESS_DEFAULT_TIMEOUT_MS
        else:
            period_ticks = self._parse_can_stress_int(
                self.rs485_stress_interval, 1, FCT_MAX_PERIOD_TICKS, "RS485 period", "ticks"
            )
            duration_ms = self._parse_can_stress_int(
                self.rs485_stress_total, 1, FCT_MAX_DURATION_MS, "RS485 duration"
            )
            timeout_ms = self._parse_can_stress_int(
                self.rs485_stress_timeout, 1, MAX_TEST_TIMEOUT_MS, "RS485 timeout"
            )
        if period_ticks is None or duration_ms is None or timeout_ms is None:
            return
        max_duration_ms = (0xFFFFF * period_ticks) // 20
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
        if use_defaults:
            app_timeout_ms = GONGWANG_APP_TIMEOUT_DEFAULT_MS
            duration_ms = GONGWANG_DEFAULT_DURATION_MS
            period_ticks = 1
        else:
            app_timeout_ms = self._parse_can_stress_int(
                self.gongwang_app_timeout, 1, MAX_TEST_TIMEOUT_MS, "GongWang app timeout"
            )
            duration_ms = self._parse_can_stress_int(
                self.gongwang_duration,
                1,
                FCT_MAX_DURATION_MS,
                "GongWang duration",
            )
            period_ticks = self._parse_can_stress_int(
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

    def _toggle_gw_live(self) -> None:
        if self._gw_live_running:
            self.gw_live_button.setEnabled(False)
            self.gw_live_state.setText("Stopping...")
            self.request_stop_gw_live.emit()
            return
        self._reset_gw_amplitude_ranges()
        self._gw_live_sample_target = None
        self.gw_live_sample_count.setText("-")
        self.gw_live_button.setEnabled(False)
        self.gw_live_state.setText("Starting...")
        self.request_start_gw_live.emit(self.gw_live_frequency.value())

    def _read_500_gw_live_samples(self) -> None:
        sample_count = 500
        self._clear_gw_live_data()
        self._gw_live_sample_target = sample_count
        self.gw_live_sample_count.setText(f"0 / {sample_count}")
        self.gw_live_state.setText(f"Reading 0/{sample_count}")
        self.gw_live_button.setEnabled(False)
        self.gw_live_read_500_button.setEnabled(False)
        self.gw_live_clear_button.setEnabled(False)
        self.request_read_gw_live_samples.emit(self.gw_live_frequency.value(), sample_count)

    def _on_gw_live_progress(self, received_samples: int, target_samples: int) -> None:
        self.gw_live_sample_count.setText(f"{received_samples} / {target_samples}")
        self.gw_live_state.setText(f"Reading {received_samples}/{target_samples}")

    def _on_gw_live_state_changed(self, running: bool, message: str) -> None:
        self._gw_live_running = running
        self.gw_live_state.setText(message or ("Running" if running else "Stopped"))
        if not running:
            self._gw_live_sample_target = None
        self._set_ui_state(self._ui_state)
        if not running and message and message != "Stopped":
            self._append_console(f"GW Encoder: {message}")

    def _on_gw_live_updated(self, cycle: object) -> None:
        self._update_gw_ring("motor", getattr(cycle, "motor", None))
        self._update_gw_ring("output", getattr(cycle, "output", None))

    def _start_gw_calibration(self) -> None:
        if self._gw_live_running or self._active_tests:
            self._append_console("Stop active tests and Live Amplitude before calibration.")
            return
        self._clear_gw_calibration_data()
        self._gw_calibration_active = True
        self._gw_calibration_operation = "calibration"
        self.gw_calibration_state.setText("STARTING")
        self.gw_calibration_message.setText(
            "Sending calibration START. Closing the app will not stop firmware calibration."
        )
        self._set_ui_state(self._ui_state)
        self.request_start_gw_calibration.emit()

    def _read_gw_calibration_result(self) -> None:
        if self._gw_live_running or self._active_tests:
            self._append_console("Stop active tests and Live Amplitude before reading calibration results.")
            return
        self._clear_gw_calibration_data()
        self._gw_calibration_active = True
        self._gw_calibration_operation = "read"
        self.gw_calibration_state.setText("READING")
        self.gw_calibration_message.setText("Requesting stored calibration result.")
        self._set_ui_state(self._ui_state)
        self.request_read_gw_calibration_result.emit()

    def _on_gw_calibration_updated(self, update: object) -> None:
        if not isinstance(update, GwCalibrationUpdate):
            return
        self.gw_calibration_state.setText(update.state)
        self.gw_calibration_message.setText(update.message)
        if update.step is not None:
            self.gw_calibration_step.setText(f"0x{update.step:02X}")
        if update.encoder_status is not None:
            self.gw_calibration_encoder_status.setText(f"0x{update.encoder_status:02X}")

        result = update.result
        if result is not None:
            if result.motor is not None:
                self._set_gw_calibration_record("motor", result.motor)
            if result.output is not None:
                self._set_gw_calibration_record("output", result.output)
            if result.encoder_error is not None:
                error = result.encoder_error
                self.gw_calibration_message.setText(
                    "Phía motor: "
                    + describe_encoder_error(error.motor_code, error.motor_value)
                    + " | Phía output: "
                    + describe_encoder_error(error.output_code, error.output_value)
                )

        if update.terminal:
            self._gw_calibration_active = False
            self._gw_calibration_operation = None
            self._append_console(f"GW Calibration {update.state}: {self.gw_calibration_message.text()}")
            self._set_ui_state(self._ui_state)

    def _set_gw_calibration_record(self, ring_key: str, record: object) -> None:
        fields = self.gw_calibration_fields[ring_key]
        for key in (
            "master_sin_amplitude_v",
            "master_sin_center_v",
            "master_cos_amplitude_v",
            "master_cos_center_v",
            "master_sin_cos_percent",
            "slave_sin_amplitude_v",
            "slave_sin_center_v",
            "slave_cos_amplitude_v",
            "slave_cos_center_v",
            "slave_sin_cos_percent",
            "relative_deviation_deg",
        ):
            value = getattr(record, key)
            display_text = f"{value:.3f}"
            fields[key].setText(display_text)
            if key.endswith("_amplitude_v"):
                fields[key].setStyleSheet(
                    self._amplitude_stylesheet(float(display_text) * 1000.0)
                )
        fields["status"].setText(f"0x{record.status:02X}")

    def _clear_gw_calibration_data(self) -> None:
        for ring_fields in self.gw_calibration_fields.values():
            for key, field in ring_fields.items():
                field.setText("-")
                if key.endswith("_amplitude_v"):
                    field.setStyleSheet(self._amplitude_stylesheet(None))
        self.gw_calibration_step.setText("-")
        self.gw_calibration_encoder_status.setText("-")
        if not self._gw_calibration_active:
            self.gw_calibration_state.setText("IDLE")
            self.gw_calibration_message.setText("Ready to start calibration.")

    def _reset_gw_amplitude_ranges(self) -> None:
        for ring_key in self._gw_amplitude_ranges:
            self._gw_amplitude_ranges[ring_key] = None
            self._gw_amplitude_samples[ring_key].clear()
            if hasattr(self, "gw_live_fields"):
                self.gw_live_fields[ring_key]["amplitude_min_mv"].setText("Min: -")
                self.gw_live_fields[ring_key]["amplitude_max_mv"].setText("Max: -")
                self.gw_live_fields[ring_key]["amplitude_min_mv"].setStyleSheet(
                    self._amplitude_stylesheet(None)
                )
                self.gw_live_fields[ring_key]["amplitude_max_mv"].setStyleSheet(
                    self._amplitude_stylesheet(None)
                )

    def _clear_gw_live_data(self) -> None:
        self._reset_gw_amplitude_ranges()
        self.gw_live_sample_count.setText("-")
        for ring_fields in self.gw_live_fields.values():
            for key, field in ring_fields.items():
                if key == "amplitude_min_mv":
                    field.setText("Min: -")
                elif key == "amplitude_max_mv":
                    field.setText("Max: -")
                else:
                    field.setText("-")
                if key.startswith("amplitude_"):
                    field.setStyleSheet(self._amplitude_stylesheet(None))

    def _copy_gw_live_amplitudes(self, ring_key: str) -> None:
        fields = self.gw_live_fields[ring_key]
        values = (
            fields["amplitude_mv"].text(),
            fields["amplitude_min_mv"].text().removeprefix("Min: "),
            fields["amplitude_max_mv"].text().removeprefix("Max: "),
        )
        QApplication.clipboard().setText("\t".join(values))
        self._append_console(
            f"Copied {ring_key} amplitude Avg, Min, Max to clipboard."
        )

    def _update_gw_ring(self, ring_key: str, sample: object | None) -> None:
        fields = self.gw_live_fields[ring_key]
        if sample is None:
            return

        fields["resolution"].setText(str(sample.resolution))
        fields["sin_raw"].setText(str(sample.sin_raw))
        fields["cos_raw"].setText(str(sample.cos_raw))
        fields["count"].setText(str(sample.count))
        if not sample.xor_valid:
            return

        fields["sin_mv"].setText(f"{sample.sin_mv:.2f}")
        fields["cos_mv"].setText(f"{sample.cos_mv:.2f}")
        amplitude_samples = self._gw_amplitude_samples[ring_key]
        amplitude_samples.append(sample.amplitude_mv)
        amplitude_average = sum(amplitude_samples) / len(amplitude_samples)
        amplitude_average_text = f"{amplitude_average:.2f}"
        fields["amplitude_mv"].setText(amplitude_average_text)
        fields["amplitude_mv"].setStyleSheet(
            self._amplitude_stylesheet(float(amplitude_average_text))
        )
        amplitude_range = self._gw_amplitude_ranges[ring_key]
        if amplitude_range is None:
            amplitude_min = amplitude_max = sample.amplitude_mv
        else:
            amplitude_min = min(amplitude_range[0], sample.amplitude_mv)
            amplitude_max = max(amplitude_range[1], sample.amplitude_mv)
        self._gw_amplitude_ranges[ring_key] = (amplitude_min, amplitude_max)
        amplitude_min_text = f"{amplitude_min:.2f}"
        amplitude_max_text = f"{amplitude_max:.2f}"
        fields["amplitude_min_mv"].setText(f"Min: {amplitude_min_text}")
        fields["amplitude_max_mv"].setText(f"Max: {amplitude_max_text}")
        fields["amplitude_min_mv"].setStyleSheet(
            self._amplitude_stylesheet(float(amplitude_min_text))
        )
        fields["amplitude_max_mv"].setStyleSheet(
            self._amplitude_stylesheet(float(amplitude_max_text))
        )
        fields["angle_deg"].setText(f"{sample.angle_deg:.2f}")

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

    def _parse_can_stress_int(
        self,
        field: QLineEdit,
        minimum: int,
        maximum: int,
        name: str,
        unit: str = "ms",
    ) -> int | None:
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

    def _on_connection_changed(self, connected: bool, message: str) -> None:
        self._connected = connected
        if connected:
            self._append_console(f"Opened {self._current_channel()}.")
            self._set_ui_state("open")
        else:
            if message and message != "Disconnected":
                self._append_console(f"Open failed: {message}")
            else:
                self._append_console("Closed COM!")
            self._id_detected = False
            self.driver_id_display.clear()
            self.gw_live_driver_id.clear()
            self.gw_calibration_driver_id.clear()
            self._gw_calibration_active = False
            self._gw_calibration_operation = None
            self._set_ui_state("closed")
            self._apply_banner_state("idle")

    def _on_rs485_connection_changed(self, connected: bool, message: str) -> None:
        self._rs485_connected = connected
        if connected:
            self._append_console(f"Opened RS485 {self._current_rs485_channel()}.")
            self._set_rs485_toggle_visual("CLOSE", "CloseButton")
        else:
            if message and message != "Disconnected":
                self._append_console(f"Open RS485 failed: {message}")
            else:
                self._append_console("Closed RS485 COM!")
            self._set_rs485_toggle_visual("OPEN", "OpenButton")
        self._set_ui_state(self._ui_state)

    def _on_adapter_configured(self, ok: bool, message: str) -> None:
        if ok:
            if self.backend.currentText() == WAVESHARE_MODEL_B_BACKEND:
                self._update_config_packet()
                self._append_console(f"{self._model_b_channel_name()} started: {self._selected_bitrate()} bit/s, normal mode, extended classic CAN, filter 00000000-1FFFFFFF.")
                return
            self.data_send.setText(message)
            spaced = " ".join(message[index : index + 2] for index in range(0, len(message), 2))
            self._append_console(f"-> Data to send config: {spaced}")
            self._append_console("Configure CAN Ok!")
            return
        self._append_console(f"Configure CAN Fail: {message}")

    def _on_test_finished(self, payload: object) -> None:
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
                self._test_run_inputs.get(test_key, {}),
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


    def _format_optional_int(self, value: int | None) -> str:
        return optional_int_text(value)
