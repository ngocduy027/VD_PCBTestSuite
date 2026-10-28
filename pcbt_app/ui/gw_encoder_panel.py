"""GongWang encoder tab of the main window: live amplitude and calibration.

Mixed into :class:`~pcbt_app.ui.main_window.MainWindow`. Amplitudes are shown
in millivolts and coloured against the operator-configured
:class:`~pcbt_app.ui.amplitude_range.AmplitudeRange` limits.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from pcbt_app.protocol.gw_calibration import describe_encoder_error
from pcbt_app.services.gw_calibration import GwCalibrationUpdate
from pcbt_app.ui.amplitude_range import AmplitudeRangeDialog
from pcbt_app.ui.styles import amplitude_value_stylesheet

# Samples requested by the "Read 500" button.
GW_LIVE_READ_SAMPLE_COUNT = 500

CALIBRATION_RECORD_FIELDS = (
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
)


class GwEncoderPanelMixin:
    """Live sin/cos amplitude display and encoder calibration workflow."""

    _ui_state: str
    _gw_live_sample_target: int | None
    _gw_calibration_operation: str | None

    def _amplitude_stylesheet(self, value_mv):
        return amplitude_value_stylesheet(value_mv, self._amplitude_limits)

    def _show_amplitude_range_dialog(self):
        dialog = AmplitudeRangeDialog(self._amplitude_limits, self)
        dialog.range_applied.connect(self._apply_amplitude_range)
        dialog.exec()

    def _apply_amplitude_range(self, limits):
        """Save new colour limits and recolour every amplitude field already shown."""
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
        sample_count = GW_LIVE_READ_SAMPLE_COUNT
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
        """Start calibration; refused while tests or live polling are running."""
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
        """Show calibration progress, result records or the encoder error message."""
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
        for key in CALIBRATION_RECORD_FIELDS:
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
        """Show one live sample for a ring.

        Raw values are always shown; mV, amplitude and angle only when the XOR is
        valid. The displayed amplitude is the mean of the last 500 valid samples,
        with running min/max since the last reset.
        """
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
