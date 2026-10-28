"""CAN-adapter and RS485 port controls of the main window.

Mixed into :class:`~pcbt_app.ui.main_window.MainWindow`; widgets are created
by :func:`~pcbt_app.ui.layout.build_main_window_ui` and connection work runs on
the :class:`~pcbt_app.ui.worker.CanWorker` thread via the window's signals.
"""

from __future__ import annotations

import logging

from pcbt_app.config import (
    DEFAULT_PCAN_CHANNEL,
    DEFAULT_WAVESHARE_CHANNEL,
    WAVESHARE_BACKEND,
    WAVESHARE_MODEL_B_BACKEND,
)
from pcbt_app.transport.base import CanTransportError
from pcbt_app.transport.rs485_serial import list_rs485_com_ports
from pcbt_app.transport.waveshare_usbcan_a import list_waveshare_com_ports
from pcbt_app.ui.presets import (
    BITRATE_PRESETS,
    MODEL_B_BITRATES,
    bitrate_value_from_label,
    format_waveshare_config_packet,
    serial_baud_value_from_label,
)
from pcbt_app.ui.worker import ConnectionParams, Rs485ConnectionParams

logger = logging.getLogger(__name__)


class AdapterPanelMixin:
    """Port selection, open/close and adapter configuration for CAN and RS485."""

    _ui_state: str
    _model_b_scanning: bool
    _gw_calibration_operation: str | None

    def _connect_clicked(self) -> None:
        """Ask the worker to open the selected adapter."""
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
            logger.exception("Listing RS485 COM ports failed")
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
        """Adapt the CAN panel to the chosen adapter model (labels, bitrates, port list) and remember it."""
        self.adapter_model.blockSignals(True)
        self.adapter_model.setCurrentIndex(self.adapter_model.findData(backend))
        self.adapter_model.blockSignals(False)
        if self._settings is not None and backend in (WAVESHARE_BACKEND, WAVESHARE_MODEL_B_BACKEND):
            self._settings.setValue("adapter/backend", backend)
        is_model_a = backend == WAVESHARE_BACKEND
        is_model_b = backend == WAVESHARE_MODEL_B_BACKEND
        for widget in (self.model_b_device_label, self.model_b_channel):
            widget.setVisible(is_model_b)
        selected_bitrate = self._selected_bitrate()
        self.bitrate.blockSignals(True)
        self.bitrate.clear()
        for label, value in BITRATE_PRESETS:
            if not is_model_b or value in MODEL_B_BITRATES:
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
        """Model A: list COM ports. Model B: ask the worker to scan devices (result arrives asynchronously)."""
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
            logger.exception("Listing Waveshare COM ports failed")
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
        except (CanTransportError, ValueError) as exc:
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
        """Fill the port list with scanned Model B devices, preferring the last used index."""
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

    def _on_connection_changed(self, connected: bool, message: str) -> None:
        """Update state after open/close; closing also clears the detected driver ID."""
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
