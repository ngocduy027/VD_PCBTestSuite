from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QApplication, QGroupBox

from pcbt_app.config import (
    DEFAULT_BITRATE,
    DEFAULT_MODEL_B_CHANNEL,
    DEFAULT_SERIAL_BAUD,
    WAVESHARE_BACKEND,
    WAVESHARE_MODEL_B_BACKEND,
)
from pcbt_app.models import GongwangResultSet, TestVerdict as Verdict
from pcbt_app.protocol.mit_firmware import (
    CAN_STRESS_DEFAULT_INTERVAL_MS,
    CAN_STRESS_DEFAULT_TOTAL_MESSAGES,
    GONGWANG_DEFAULT_DURATION_MS,
    MotorTestStatus,
    MotorTestStopReason,
    OPEN_LOOP_DEFAULT_DURATION_MS,
    OPEN_LOOP_DEFAULT_THETA_STEP,
    RS485_STRESS_DEFAULT_DURATION_MS,
)
from pcbt_app.services.pcb_test_service import ServiceResult
from pcbt_app.transport.waveshare_usbcan_a import build_waveshare_config_packet
from pcbt_app.ui.main_window import (
    MainWindow,
    MAX_TEST_TIMEOUT_MS,
    RS485_STRESS_DEFAULT_TIMEOUT_MS,
    bitrate_value_from_label,
    format_waveshare_config_packet,
    serial_baud_value_from_label,
)
from pcbt_app.ui.presets import (
    CAN_STRESS_APP_TIMEOUT_DEFAULT_MS,
    DETECT_ID_DEFAULT_TIMEOUT_MS,
    GONGWANG_APP_TIMEOUT_DEFAULT_MS,
    OPENLOOP_APP_TIMEOUT_DEFAULT_MS,
)
from pcbt_app.ui.styles import amplitude_value_stylesheet


def widget_rect_in_window(window: MainWindow, widget) -> QRect:
    return QRect(widget.mapTo(window, widget.rect().topLeft()), widget.size())


def assert_widgets_do_not_overlap(window: MainWindow, widgets: dict[str, object]) -> None:
    rects = {name: widget_rect_in_window(window, widget) for name, widget in widgets.items()}
    for first_name, first_rect in rects.items():
        for second_name, second_rect in rects.items():
            if first_name >= second_name:
                continue
            assert not first_rect.intersects(second_rect), (
                f"{first_name} overlaps {second_name}: "
                f"{first_rect.getRect()} intersects {second_rect.getRect()}"
            )


def disconnect_worker_slots(window: MainWindow) -> None:
    for signal, slot in (
        (window.request_connect, window._worker.connect_bus),
        (window.request_disconnect, window._worker.disconnect_bus),
        (window.request_configure_adapter, window._worker.configure_adapter),
        (window.request_can_test, window._worker.run_can_test),
        (window.request_openloop_test, window._worker.run_openloop_test),
        (window.request_can_stress_test, window._worker.run_can_stress_test),
        (window.request_gongwang_test, window._worker.run_gongwang_test),
        (window.request_rs485_connect, window._worker.connect_rs485),
        (window.request_rs485_disconnect, window._worker.disconnect_rs485),
        (window.request_rs485_stress_test, window._worker.run_rs485_stress_test),
        (window.request_start_gw_live, window._worker.start_gw_live),
        (window.request_read_gw_live_samples, window._worker.read_gw_live_samples),
        (window.request_stop_gw_live, window._worker.stop_gw_live),
        (window.request_start_gw_calibration, window._worker.start_gw_calibration),
        (window.request_read_gw_calibration_result, window._worker.read_gw_calibration_result),
    ):
        try:
            signal.disconnect(slot)
        except RuntimeError:
            pass


def test_bitrate_and_serial_label_mapping() -> None:
    assert DEFAULT_BITRATE == 1_000_000
    assert DEFAULT_SERIAL_BAUD == 2_000_000
    assert bitrate_value_from_label("1M") == 1_000_000
    assert bitrate_value_from_label("500K") == 500_000
    assert serial_baud_value_from_label("2000000") == 2_000_000
    with pytest.raises(ValueError):
        bitrate_value_from_label("333K")


def test_config_packet_display_matches_transport_packet() -> None:
    assert format_waveshare_config_packet(1_000_000) == build_waveshare_config_packet(1_000_000).hex().upper()


def test_gui_contains_reference_style_sections(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        titles = {group.title() for group in window.findChildren(QGroupBox)}
        assert {"CAN Port", "RS485 Port", "CAN Configure", "Test Status", "Console", "Test"} <= titles
        assert "Motors Configure" not in titles
        assert "WorkSpace" not in titles
        assert window.left_container.minimumWidth() == 340
        assert window.left_container.maximumWidth() == 340
        assert window.left_column_layout.stretch(2) == 1
        assert window.right_column_layout.stretch(1) == 1
    finally:
        window.close()


def test_gui_has_c1_c2_and_advanced_test_tabs(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        assert [window.test_tabs.tabText(index) for index in range(window.test_tabs.count())] == [
            "C1",
            "C2",
            "Advanced",
        ]
        assert window.test_tabs.currentIndex() == 0
        assert len(window._test_button_copies["detect_id"]) == 5
        assert len(window._test_button_copies["openloop"]) == 4
        assert len(window._test_button_copies["can_stress"]) == 3
        assert len(window._test_button_copies["rs485_stress"]) == 2
        assert len(window._test_button_copies["gongwang"]) == 2
        assert set(window.result_cards) == {"openloop", "can_stress", "rs485_stress", "gongwang"}
        assert "detect_id" not in window.result_cards
        assert all(not card["details_button"].isChecked() for card in window.result_cards.values())
    finally:
        window.close()


def test_gui_has_fct_and_gw_encoder_workspace_tabs(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        assert [
            window.workspace_tabs.tabText(index)
            for index in range(window.workspace_tabs.count())
        ] == ["FCT PCB", "GW Encoder"]
        assert window.workspace_tabs.currentIndex() == 0
        assert [window.gw_tabs.tabText(index) for index in range(window.gw_tabs.count())] == [
            "Calibration",
            "Live Amplitude",
        ]
        assert window.gw_tabs.currentIndex() == 0
        assert window.gw_calibration_button.text() == "Start Calibration"
        assert not window.gw_calibration_button.isEnabled()
        assert window.gw_calibration_read_button.text() == "Read Result"
        assert not window.gw_calibration_read_button.isEnabled()
        assert window.gw_live_frequency.minimum() == 1
        assert window.gw_live_frequency.maximum() == 50
        assert window.gw_live_frequency.value() == 50
        assert window.gw_live_button.text() == "Start"
        assert window.gw_live_read_500_button.text() == "Read 500 Samples"
        assert window.gw_live_sample_count.text() == "-"
        assert window.gw_live_clear_button.text() == "Clear"
        assert not window.gw_live_button.isEnabled()
        assert not window.gw_live_read_500_button.isEnabled()
        assert set(window.gw_live_fields) == {"motor", "output"}
        for copy_button in window.gw_live_copy_buttons.values():
            assert copy_button.text() == "⧉"
            assert copy_button.size().width() == 22
            assert copy_button.size().height() == 22
    finally:
        window.close()


def test_gw_live_start_stop_and_independent_ring_updates(qtbot) -> None:
    from pcbt_app.protocol.gw_sincos import parse_gw_sincos_response
    from pcbt_app.services.gw_live_reader import GwLiveCycle
    from pcbt_app.models import RawCanFrame

    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._on_connection_changed(True, "Connected")
        window._id_detected = True
        window.driver_id_display.setText("1")
        window.gw_live_driver_id.setText("1")
        window._set_ui_state("ready")
        assert window.gw_live_button.isEnabled()

        with qtbot.waitSignal(window.request_start_gw_live, timeout=1000) as start:
            window.gw_live_button.click()
        assert start.args == [50]
        window._on_gw_live_state_changed(True, "Running")
        assert window.gw_live_button.text() == "Stop"
        assert not window.gw_live_frequency.isEnabled()
        assert not window.gw_live_detect_id_button.isEnabled()
        assert not window.gw_calibration_detect_id_button.isEnabled()
        assert window.openloop_button.isEnabled()
        assert window.gw_live_openloop_button.isEnabled()

        sample = parse_gw_sincos_response(
            RawCanFrame(0x1D5401FD, bytes.fromhex("95 01 C4 F3 52 75 01 D1")),
            driver_id=1,
        )
        window._on_gw_live_updated(GwLiveCycle(motor=sample))
        motor = window.gw_live_fields["motor"]
        assert motor["sin_raw"].text() == "405"
        assert motor["cos_raw"].text() == "-3132"
        assert motor["amplitude_mv"].text() == "1409.80"
        assert motor["amplitude_min_mv"].text() == "Min: 1409.80"
        assert motor["amplitude_max_mv"].text() == "Max: 1409.80"

        lower_amplitude = parse_gw_sincos_response(
            RawCanFrame(0x1D5401FD, bytes.fromhex("64 00 00 00 01 00 00 31")),
            driver_id=1,
        )
        window._on_gw_live_updated(GwLiveCycle(motor=lower_amplitude))
        assert motor["amplitude_mv"].text() == "727.22"
        assert motor["amplitude_min_mv"].text() == "Min: 44.64"
        assert motor["amplitude_max_mv"].text() == "Max: 1409.80"

        window.gw_live_clear_button.click()
        assert window.gw_live_button.text() == "Stop"
        assert motor["sin_raw"].text() == "-"
        assert motor["amplitude_mv"].text() == "-"
        assert motor["amplitude_min_mv"].text() == "Min: -"
        assert motor["amplitude_max_mv"].text() == "Max: -"

        window._on_gw_live_updated(GwLiveCycle(motor=lower_amplitude))
        assert motor["amplitude_min_mv"].text() == "Min: 44.64"
        assert motor["amplitude_max_mv"].text() == "Max: 44.64"

        previous_mv = motor["sin_mv"].text()
        invalid = parse_gw_sincos_response(
            RawCanFrame(0x1D5401FD, bytes.fromhex("95 01 C4 F3 52 75 01 D0")),
            driver_id=1,
        )
        window._on_gw_live_updated(GwLiveCycle(motor=invalid))
        assert motor["sin_mv"].text() == previous_mv

        with qtbot.waitSignal(window.request_stop_gw_live, timeout=1000):
            window.gw_live_button.click()
        window._on_gw_live_state_changed(False, "Stopped")
        assert window.gw_live_button.text() == "Start"
        assert window.gw_live_frequency.isEnabled()
        assert window.gw_live_detect_id_button.isEnabled()
        assert window.gw_calibration_detect_id_button.isEnabled()
        assert motor["sin_raw"].text() == "405"

        with qtbot.waitSignal(window.request_start_gw_live, timeout=1000):
            window.gw_live_button.click()
        assert motor["amplitude_min_mv"].text() == "Min: -"
        assert motor["amplitude_max_mv"].text() == "Max: -"
    finally:
        window.close()


def test_gw_live_read_500_tracks_complete_received_pairs(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._on_connection_changed(True, "Connected")
        window._id_detected = True
        window.gw_live_driver_id.setText("1")
        window._set_ui_state("ready")

        with qtbot.waitSignal(window.request_read_gw_live_samples, timeout=1000) as start:
            window.gw_live_read_500_button.click()
        assert start.args == [50, 500]
        assert window.gw_live_sample_count.text() == "0 / 500"
        assert not window.gw_live_clear_button.isEnabled()

        window._on_gw_live_state_changed(True, "Reading 0/500")
        assert window.gw_live_button.text() == "Stop"
        assert not window.gw_live_read_500_button.isEnabled()
        assert not window.gw_live_clear_button.isEnabled()

        window._on_gw_live_progress(237, 500)
        assert window.gw_live_sample_count.text() == "237 / 500"
        assert window.gw_live_state.text() == "Reading 237/500"

        window._on_gw_live_progress(500, 500)
        window._on_gw_live_state_changed(False, "Completed 500/500")
        assert window.gw_live_sample_count.text() == "500 / 500"
        assert window.gw_live_state.text() == "Completed 500/500"
        assert window.gw_live_button.text() == "Start"
        assert window.gw_live_read_500_button.isEnabled()
        assert window.gw_live_clear_button.isEnabled()
        window.gw_live_clear_button.click()
        assert window.gw_live_sample_count.text() == "-"
    finally:
        window.close()


def test_gw_live_fields_do_not_overlap_at_target_sizes(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        for size in ((1366, 768), (1120, 720)):
            window.resize(*size)
            window.show()
            window.workspace_tabs.setCurrentIndex(1)
            window.gw_tabs.setCurrentIndex(1)
            qtbot.wait(10)
            fields = {
                f"{ring}_{key}": field
                for ring, ring_fields in window.gw_live_fields.items()
                for key, field in ring_fields.items()
            }
            fields.update(
                {
                    "live_detect_id": window.gw_live_detect_id_button,
                    "live_openloop_timeout": window.gw_live_openloop_app_timeout,
                    "live_openloop_theta": window.gw_live_openloop_theta_step,
                    "live_openloop_duration": window.gw_live_openloop_duration,
                    "live_openloop_button": window.gw_live_openloop_button,
                    "live_sample_count": window.gw_live_sample_count,
                    "live_read_500": window.gw_live_read_500_button,
                    "motor_copy": window.gw_live_copy_buttons["motor"],
                    "output_copy": window.gw_live_copy_buttons["output"],
                }
            )
            assert_widgets_do_not_overlap(window, fields)
    finally:
        window.close()


def test_gw_calibration_start_result_and_resource_ownership(qtbot) -> None:
    from pcbt_app.protocol.gw_calibration import (
        GW_MOTOR_RESOLUTION,
        GW_OUTPUT_RESOLUTION,
        GwCalibrationOutcome,
        GwCalibrationResult,
        decode_gw_calibration_record,
    )
    from pcbt_app.services.gw_calibration import GwCalibrationUpdate

    def record(request: int, value: int) -> bytes:
        prefix = bytes((0x76, request)) + b"".join(
            item.to_bytes(2, "little", signed=True)
            for item in (value, 10, value - 5, -10, value - 20, 20, value - 25, -20, 30)
        ) + b"\x00"
        xor_value = 0
        for item in prefix:
            xor_value ^= item
        return prefix + bytes((xor_value,))

    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._on_connection_changed(True, "Connected")
        window._id_detected = True
        window.gw_calibration_driver_id.setText("52")
        window._set_ui_state("ready")
        assert window.gw_calibration_button.isEnabled()

        with qtbot.waitSignal(window.request_start_gw_calibration, timeout=1000):
            window.gw_calibration_button.click()
        assert not window.gw_calibration_button.isEnabled()
        assert not window.gw_live_button.isEnabled()
        assert not window.gongwang_button.isEnabled()
        assert not window.gongwang_duration.isEnabled()
        assert not window.rs485_stress_button.isEnabled()
        assert not window.rs485_stress_interval.isEnabled()

        window._on_gw_calibration_updated(
            GwCalibrationUpdate("RUNNING", "Calibration step 0x0A", step=0x0A, encoder_status=0)
        )
        assert window.gw_calibration_step.text() == "0x0A"
        assert window.gw_calibration_encoder_status.text() == "0x00"

        result = GwCalibrationResult(
            GwCalibrationOutcome.PASS,
            motor=decode_gw_calibration_record(record(0x02, 1000), resolution=GW_MOTOR_RESOLUTION),
            output=decode_gw_calibration_record(record(0x04, 1100), resolution=GW_OUTPUT_RESOLUTION),
        )
        window._on_gw_calibration_updated(
            GwCalibrationUpdate("PASS", "Calibration completed successfully", result=result, terminal=True, passed=True)
        )
        assert window.gw_calibration_fields["motor"]["master_sin_amplitude_v"].text() == "0.223"
        assert window.gw_calibration_fields["output"]["relative_deviation_deg"].text() == "0.021"
        assert window.gw_calibration_button.isEnabled()
        assert window.gw_live_button.isEnabled()
    finally:
        window.close()


def test_gw_calibration_fields_do_not_overlap_at_target_sizes(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        for size in ((1366, 768), (1120, 720)):
            window.resize(*size)
            window.show()
            window.workspace_tabs.setCurrentIndex(1)
            window.gw_tabs.setCurrentIndex(0)
            qtbot.wait(10)
            fields = {
                f"{ring}_{key}": field
                for ring, ring_fields in window.gw_calibration_fields.items()
                for key, field in ring_fields.items()
            }
            fields["calibration_detect_id"] = window.gw_calibration_detect_id_button
            assert_widgets_do_not_overlap(window, fields)
    finally:
        window.close()


def test_gw_calibration_error_is_displayed_in_vietnamese_with_converted_value(qtbot) -> None:
    from pcbt_app.protocol.gw_calibration import (
        GwCalibrationEncoderError,
        GwCalibrationOutcome,
        GwCalibrationResult,
    )
    from pcbt_app.services.gw_calibration import GwCalibrationUpdate

    window = MainWindow()
    qtbot.addWidget(window)
    try:
        result = GwCalibrationResult(
            GwCalibrationOutcome.ENCODER_FAILURE,
            encoder_error=GwCalibrationEncoderError(0xE4, 12345, 0xE7, 15000),
        )
        window._on_gw_calibration_updated(
            GwCalibrationUpdate("FAIL", "Encoder error", result=result, terminal=True)
        )

        message = window.gw_calibration_message.text()
        assert "Phía motor" in message
        assert "Độ lệch tương đối giữa kênh chính và kênh phụ quá lớn" in message
        assert "độ lệch: 12.345°" in message
        assert "Phía output" in message
        assert "Calibration bị timeout" in message
        assert "thời gian: 15000 ms" in message
    finally:
        window.close()


def test_gw_calibration_read_result_uses_same_result_panels(qtbot) -> None:
    from pcbt_app.protocol.gw_calibration import (
        GW_MOTOR_RESOLUTION,
        GW_OUTPUT_RESOLUTION,
        GwCalibrationOutcome,
        GwCalibrationResult,
        decode_gw_calibration_record,
    )
    from pcbt_app.services.gw_calibration import GwCalibrationUpdate

    def record(request: int, value: int) -> bytes:
        prefix = bytes((0x76, request)) + b"".join(
            item.to_bytes(2, "little", signed=True)
            for item in (value, 10, value - 5, -10, value - 20, 20, value - 25, -20, 30)
        ) + b"\x00"
        xor_value = 0
        for item in prefix:
            xor_value ^= item
        return prefix + bytes((xor_value,))

    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._on_connection_changed(True, "Connected")
        window._id_detected = True
        window.gw_calibration_driver_id.setText("52")
        window._set_ui_state("ready")

        with qtbot.waitSignal(window.request_read_gw_calibration_result, timeout=1000):
            window.gw_calibration_read_button.click()
        assert window.gw_calibration_state.text() == "READING"
        assert window.gw_calibration_read_button.text() == "Reading..."
        assert not window.gw_calibration_button.isEnabled()

        result = GwCalibrationResult(
            GwCalibrationOutcome.PASS,
            motor=decode_gw_calibration_record(record(0x02, 1000), resolution=GW_MOTOR_RESOLUTION),
            output=decode_gw_calibration_record(record(0x04, 1100), resolution=GW_OUTPUT_RESOLUTION),
        )
        window._on_gw_calibration_updated(
            GwCalibrationUpdate(
                "PASS",
                "Stored calibration result read successfully",
                result=result,
                terminal=True,
                passed=True,
            )
        )

        assert window.gw_calibration_fields["motor"]["master_sin_amplitude_v"].text() == "0.223"
        assert window.gw_calibration_fields["output"]["master_sin_amplitude_v"].text() == "0.246"
        assert window.gw_calibration_read_button.isEnabled()
        assert window.gw_calibration_read_button.text() == "Read Result"
    finally:
        window.close()


def test_gw_amplitude_uses_latest_500_valid_samples(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        for amplitude in range(1, 502):
            window._update_gw_ring(
                "motor",
                SimpleNamespace(
                    resolution=131072,
                    sin_raw=0,
                    cos_raw=0,
                    count=0,
                    xor_valid=True,
                    sin_mv=0.0,
                    cos_mv=0.0,
                    amplitude_mv=float(amplitude),
                    angle_deg=0.0,
                ),
            )

        fields = window.gw_live_fields["motor"]
        assert len(window._gw_amplitude_samples["motor"]) == 500
        assert fields["amplitude_mv"].text() == "251.50"
        assert fields["amplitude_min_mv"].text() == "Min: 1.00"
        assert fields["amplitude_max_mv"].text() == "Max: 501.00"

        window._clear_gw_live_data()
        assert len(window._gw_amplitude_samples["motor"]) == 0
        assert fields["amplitude_mv"].text() == "-"
    finally:
        window.close()


@pytest.mark.parametrize(
    ("value_mv", "background"),
    [
        (None, "#F3F4F6"),
        (599.99, "#FDE8E5"),
        (600.0, "#FFF4CC"),
        (999.99, "#FFF4CC"),
        (1000.0, "#DCFCE7"),
        (1800.0, "#DCFCE7"),
        (1800.01, "#FDE8E5"),
    ],
)
def test_amplitude_threshold_boundaries(value_mv, background) -> None:
    assert background in amplitude_value_stylesheet(value_mv)


def test_live_amplitude_average_min_and_max_are_colored_independently(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        def sample(amplitude_mv: float):
            return SimpleNamespace(
                resolution=131072,
                sin_raw=0,
                cos_raw=0,
                count=0,
                xor_valid=True,
                sin_mv=0.0,
                cos_mv=0.0,
                amplitude_mv=amplitude_mv,
                angle_deg=0.0,
            )

        window._update_gw_ring("motor", sample(400.0))
        window._update_gw_ring("motor", sample(1200.0))
        fields = window.gw_live_fields["motor"]
        assert fields["amplitude_mv"].text() == "800.00"
        assert "#FFF4CC" in fields["amplitude_mv"].styleSheet()
        assert "#FDE8E5" in fields["amplitude_min_mv"].styleSheet()
        assert "#DCFCE7" in fields["amplitude_max_mv"].styleSheet()

        window._clear_gw_live_data()
        assert "#F3F4F6" in fields["amplitude_mv"].styleSheet()
        assert "#F3F4F6" in fields["amplitude_min_mv"].styleSheet()
        assert "#F3F4F6" in fields["amplitude_max_mv"].styleSheet()
    finally:
        window.close()


def test_live_amplitude_copy_uses_excel_tab_separators(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.resize(1120, 720)
        window.show()
        window.workspace_tabs.setCurrentIndex(1)
        window.gw_tabs.setCurrentIndex(1)
        qtbot.wait(10)
        fields = window.gw_live_fields["motor"]
        fields["amplitude_mv"].setText("1234.56")
        fields["amplitude_min_mv"].setText("Min: 1000.00")
        fields["amplitude_max_mv"].setText("Max: 1500.00")

        window.gw_live_copy_buttons["motor"].click()

        assert QApplication.clipboard().text() == "1234.56\t1000.00\t1500.00"
        combined_width = (
            sum(fields[key].width() for key in ("amplitude_mv", "amplitude_min_mv", "amplitude_max_mv"))
            + window.gw_live_copy_buttons["motor"].width()
            + 9
        )
        assert combined_width == pytest.approx(fields["sin_raw"].width(), abs=2)
    finally:
        window.close()


def test_calibration_amplitude_colors_convert_volts_to_millivolts(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        record = SimpleNamespace(
            master_sin_amplitude_v=0.599,
            master_sin_center_v=0.0,
            master_cos_amplitude_v=0.600,
            master_cos_center_v=0.0,
            master_sin_cos_percent=0.0,
            slave_sin_amplitude_v=1.000,
            slave_sin_center_v=0.0,
            slave_cos_amplitude_v=1.801,
            slave_cos_center_v=0.0,
            slave_sin_cos_percent=0.0,
            relative_deviation_deg=0.0,
            status=0,
        )
        window._set_gw_calibration_record("motor", record)
        fields = window.gw_calibration_fields["motor"]
        assert "#FDE8E5" in fields["master_sin_amplitude_v"].styleSheet()
        assert "#FFF4CC" in fields["master_cos_amplitude_v"].styleSheet()
        assert "#DCFCE7" in fields["slave_sin_amplitude_v"].styleSheet()
        assert "#FDE8E5" in fields["slave_cos_amplitude_v"].styleSheet()
        assert fields["master_sin_center_v"].styleSheet() == ""

        window._clear_gw_calibration_data()
        assert "#F3F4F6" in fields["master_sin_amplitude_v"].styleSheet()
    finally:
        window.close()


def test_encoder_tab_controls_reuse_detect_id_and_openloop_signals(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._on_connection_changed(True, "Connected")
        with qtbot.waitSignal(window.request_can_test, timeout=1000) as detect:
            window.gw_calibration_detect_id_button.click()
        assert detect.args == [DETECT_ID_DEFAULT_TIMEOUT_MS]

        window._on_test_finished(
            ServiceResult(
                Verdict(test_name="CAN Test", passed=True, reason="PASS", returned_driver_id=0x34)
            )
        )
        assert window.gw_live_openloop_app_timeout.text() == "30000"
        assert window.gw_live_openloop_duration.text() == "20000"
        with qtbot.waitSignal(window.request_openloop_test, timeout=1000) as defaults:
            window.gw_live_openloop_button.click()
        assert defaults.args == [30000, OPEN_LOOP_DEFAULT_THETA_STEP, 20000]
        window._on_test_finished(
            ServiceResult(Verdict(test_name="Open-loop Test", passed=True, reason="PASS"))
        )
        window.gw_live_openloop_app_timeout.setText("13000")
        window.gw_live_openloop_theta_step.setText("-0.00050")
        window.gw_live_openloop_duration.setText("6000")
        assert window.openloop_app_timeout.text() == str(OPENLOOP_APP_TIMEOUT_DEFAULT_MS)
        assert window.openloop_theta_step.text() == "-0.00050"
        assert window.openloop_duration.text() == str(OPEN_LOOP_DEFAULT_DURATION_MS)
        with qtbot.waitSignal(window.request_openloop_test, timeout=1000) as openloop:
            window.gw_live_openloop_button.click()
        assert openloop.args == [13000, -0.00050, 6000]
    finally:
        window.close()


def test_c1_c2_configs_are_read_only_defaults_and_run_independently_of_advanced(
    qtbot, monkeypatch
) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._on_connection_changed(True, "Connected")
        window._on_rs485_connection_changed(True, "Connected")

        config_sources = (
            window.detect_id_timeout,
            window.openloop_app_timeout,
            window.openloop_theta_step,
            window.openloop_duration,
            window.can_stress_app_timeout,
            window.can_stress_interval,
            window.can_stress_duration,
            window.rs485_stress_timeout,
            window.rs485_stress_interval,
            window.rs485_stress_total,
            window.gongwang_app_timeout,
            window.gongwang_period,
            window.gongwang_duration,
        )
        quick_defaults = {
            source: source.text()
            for source in config_sources
        }
        for source, default_text in quick_defaults.items():
            for quick_field in window._test_field_copies[
                next(
                    key
                    for key, field_copies in window._test_field_copies.items()
                    if source in field_copies
                )
            ][source][1:3]:
                assert quick_field.isReadOnly()
                assert quick_field.text() == default_text

        window.detect_id_timeout.setText("2222")
        window.openloop_app_timeout.setText("17000")
        window.openloop_theta_step.setText("-0.00050")
        window.openloop_duration.setText("12000")
        window.can_stress_app_timeout.setText("11000")
        window.can_stress_interval.setText("2")
        window.can_stress_duration.setText("9000")
        window.rs485_stress_timeout.setText("12000")
        window.rs485_stress_interval.setText("2")
        window.rs485_stress_total.setText("9000")
        window.gongwang_app_timeout.setText("12000")
        window.gongwang_period.setText("2")
        window.gongwang_duration.setText("9000")

        quick_timeout = window._test_field_copies["detect_id"][window.detect_id_timeout][1]
        assert quick_timeout.text() == str(DETECT_ID_DEFAULT_TIMEOUT_MS)
        with qtbot.waitSignal(window.request_can_test, timeout=1000) as blocker:
            window._test_button_copies["detect_id"][1].click()
        assert blocker.args == [DETECT_ID_DEFAULT_TIMEOUT_MS]

        window._on_test_finished(
            ServiceResult(
                Verdict(test_name="CAN Test", passed=True, reason="PASS", returned_driver_id=0x34)
            )
        )
        emitted = {"openloop": [], "can": [], "rs485": [], "gongwang": []}
        window.request_openloop_test.connect(lambda *args: emitted["openloop"].append(args))
        window.request_can_stress_test.connect(lambda *args: emitted["can"].append(args))
        window.request_rs485_stress_test.connect(lambda *args: emitted["rs485"].append(args))
        window.request_gongwang_test.connect(lambda *args: emitted["gongwang"].append(args))
        window._test_button_copies["openloop"][1].click()
        window._test_button_copies["can_stress"][1].click()
        window._test_button_copies["rs485_stress"][1].click()
        window._test_button_copies["gongwang"][1].click()

        assert emitted["openloop"] == [
            (
                OPENLOOP_APP_TIMEOUT_DEFAULT_MS,
                OPEN_LOOP_DEFAULT_THETA_STEP,
                OPEN_LOOP_DEFAULT_DURATION_MS,
            )
        ]
        assert emitted["can"] == [
            (
                CAN_STRESS_APP_TIMEOUT_DEFAULT_MS,
                CAN_STRESS_DEFAULT_INTERVAL_MS,
                CAN_STRESS_DEFAULT_TOTAL_MESSAGES,
            )
        ]
        assert emitted["rs485"] == [
            (RS485_STRESS_DEFAULT_TIMEOUT_MS, RS485_STRESS_DEFAULT_DURATION_MS, 1)
        ]
        assert emitted["gongwang"] == [
            (GONGWANG_APP_TIMEOUT_DEFAULT_MS, GONGWANG_DEFAULT_DURATION_MS, 1)
        ]

        assert window.openloop_duration.text() == "12000"
        assert window.can_stress_duration.text() == "9000"
        assert window.rs485_stress_total.text() == "9000"
        assert window.gongwang_duration.text() == "9000"
    finally:
        window.close()


def test_gui_test_panel_does_not_overlap_at_target_sizes(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_rs485_com_ports",
        lambda: [("COM14", "USB-SERIAL RS485")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    widgets = {
        "overall_status": window.overall_status,
        "detect_id_button": window.detect_id_button,
        "driver_id_display": window.driver_id_display,
        "openloop_button": window.openloop_button,
        "can_stress_button": window.can_stress_button,
        "rs485_stress_button": window.rs485_stress_button,
        "gongwang_button": window.gongwang_button,
        "detect_id_timeout": window.detect_id_timeout,
        "openloop_theta_step": window.openloop_theta_step,
        "openloop_duration": window.openloop_duration,
        "openloop_app_timeout": window.openloop_app_timeout,
        "can_stress_interval": window.can_stress_interval,
        "can_stress_duration": window.can_stress_duration,
        "can_stress_app_timeout": window.can_stress_app_timeout,
        "can_stress_received_display": window.can_stress_received_display,
        "rs485_stress_interval": window.rs485_stress_interval,
        "rs485_stress_total": window.rs485_stress_total,
        "rs485_stress_timeout": window.rs485_stress_timeout,
        "rs485_stress_received_display": window.rs485_stress_received_display,
        "gongwang_duration": window.gongwang_duration,
        "gongwang_period": window.gongwang_period,
        "gongwang_app_timeout": window.gongwang_app_timeout,
    }
    for card_key, card in window.result_cards.items():
        widgets[f"{card_key}_status"] = card["status"]
        for field_key, field in card["fields"].items():
            widgets[f"{card_key}_{field_key}"] = field
    try:
        for size in ((1366, 768), (1120, 720)):
            window.resize(*size)
            window.show()
            qtbot.waitExposed(window)
            window.test_tabs.setCurrentIndex(2)
            for card in window.result_cards.values():
                card["details_button"].setChecked(True)
            qtbot.wait(10)
            assert_widgets_do_not_overlap(window, widgets)
            assert window.test_controls_scroll.geometry().right() < window.results_scroll.geometry().left()
            assert window.can_group.geometry().right() < window.status_group.geometry().left()
            assert window.status_group.width() == 200
            assert window.results_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
            for card in window.result_cards.values():
                for label in card["fields"].values():
                    assert label.height() >= label.fontMetrics().height()
    finally:
        window.close()


def test_gui_refresh_ports_populates_detected_ports(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window._refresh_ports()
        assert window.channel.currentData() == "COM13"
        assert window.channel.currentText() == "COM13 - USB-SERIAL CH340"
    finally:
        window.close()


def test_gui_model_b_uses_native_usb_device_without_com_settings(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.backend.setCurrentText(WAVESHARE_MODEL_B_BACKEND)

        assert window.channel.currentData() == 0
        assert window.channel.currentText() == "Device 0 / SN TEST0"
        assert not window.channel.isEditable()
        assert window.channel.isEnabled()
        assert not window.serial_baud.isEnabled()
        assert window.refresh_ports_button.isEnabled()
    finally:
        window.close()


def test_gui_refresh_rs485_ports_populates_detected_ports(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_rs485_com_ports",
        lambda: [("COM14", "USB-SERIAL RS485")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window._refresh_rs485_ports()
        assert window.rs485_channel.currentData() == "COM14"
        assert window.rs485_channel.currentText() == "COM14 - USB-SERIAL RS485"
        assert window.rs485_baud.currentText() == "2500000"
    finally:
        window.close()


def test_open_emits_connection_request(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._refresh_ports()
        assert window.com_toggle_button.text() == "OPEN"
        with qtbot.waitSignal(window.request_connect, timeout=1000) as blocker:
            window.com_toggle_button.click()
        params = blocker.args[0]
        assert params.backend == WAVESHARE_BACKEND
        assert params.channel == "COM13"
        assert params.serial_baud == 2_000_000
        assert params.bitrate == 1_000_000
    finally:
        window.close()


def test_com_toggle_closes_after_successful_open(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._on_connection_changed(True, "Connected")
        assert window.com_toggle_button.text() == "CLOSE"
        with qtbot.waitSignal(window.request_disconnect, timeout=1000):
            window.com_toggle_button.click()
    finally:
        window.close()


def test_detect_id_flow_enables_openloop_after_pass(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        assert not window.detect_id_button.isEnabled()
        assert not window.openloop_button.isEnabled()
        assert not window.can_stress_button.isEnabled()
        assert not window.rs485_stress_button.isEnabled()

        window._on_connection_changed(True, "Connected")
        assert window.detect_id_button.isEnabled()
        assert not window.openloop_button.isEnabled()
        assert "Opened COM13" in window.console_box.toPlainText()

        with qtbot.waitSignal(window.request_can_test, timeout=1000):
            window.detect_id_button.click()
        assert "Detecting ID..." in window.console_box.toPlainText()

        verdict = Verdict(
            test_name="CAN Test",
            passed=True,
            reason="PASS",
            returned_driver_id=0x34,
        )
        window._on_test_finished(ServiceResult(verdict=verdict))
        assert window.driver_id_display.text() == "52"
        assert all(card["status"].text() == "NOT RUN" for card in window.result_cards.values())
        assert window.openloop_button.isEnabled()
        assert window.can_stress_button.isEnabled()
        assert not window.rs485_stress_button.isEnabled()
        assert "Driver ID: 52" in window.console_box.toPlainText()
    finally:
        window.close()


def test_gui_current_fct_fields_emit_typed_values(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._on_connection_changed(True, "Connected")
        window.detect_id_timeout.setText("1234")
        with qtbot.waitSignal(window.request_can_test, timeout=1000) as can_blocker:
            window.detect_id_button.click()
        assert can_blocker.args == [1234]

        window._on_test_finished(ServiceResult(Verdict(test_name="CAN Test", passed=True, reason="PASS", returned_driver_id=0x34)))
        window.openloop_theta_step.setText("-0.00050")
        window.openloop_duration.setText("12345")
        with qtbot.waitSignal(window.request_openloop_test, timeout=1000) as openloop_blocker:
            window.openloop_button.click()
        assert openloop_blocker.args == [OPENLOOP_APP_TIMEOUT_DEFAULT_MS, -0.00050, 12345]
        window._on_test_finished(
            ServiceResult(
                Verdict(
                    test_name="Open-loop Test",
                    passed=True,
                    reason="PASS",
                    returned_driver_id=0x34,
                    elapsed_ms=12345,
                    fct_state=2,
                    fct_pass_count=1,
                    fct_fail_count=0,
                )
            )
        )
        details = window.result_cards["openloop"]["fields"]
        assert details["duration"].text() == "12345 ms"
        assert details["theta_step"].text() == "-0.00050 rad/tick"
    finally:
        window.close()


def test_gui_openloop_parameter_defaults_and_validation(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        assert window.openloop_theta_step.text() == "-0.00083"
        assert window.openloop_duration.text() == str(OPEN_LOOP_DEFAULT_DURATION_MS)
        assert window.openloop_app_timeout.text() == str(OPENLOOP_APP_TIMEOUT_DEFAULT_MS)

        window._on_connection_changed(True, "Connected")
        window._on_test_finished(ServiceResult(Verdict(test_name="CAN Test", passed=True, reason="PASS", returned_driver_id=0x34)))
        window.openloop_theta_step.setText("0.003")
        emitted = []
        window.request_openloop_test.connect(lambda *args: emitted.append(args))
        window.openloop_button.click()
        assert emitted == []
        assert "Open-loop theta step must be -0.00157..0.00157." in window.console_box.toPlainText()

        window.openloop_theta_step.setText("-0.00083")
        window.openloop_duration.setText("0")
        window.openloop_button.click()
        assert emitted == []
        assert "Open-loop duration must be 1..16777215 ms." in window.console_box.toPlainText()
    finally:
        window.close()


def test_gui_invalid_timeout_does_not_emit(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._on_connection_changed(True, "Connected")
        window.detect_id_timeout.setText("0")
        emitted = []
        window.request_can_test.connect(lambda *args: emitted.append(args))
        window.detect_id_button.click()
        assert emitted == []
        assert "Detect ID timeout must be 1.." in window.console_box.toPlainText()
    finally:
        window.close()


def test_fct_fields_allow_other_compatible_tests_while_one_runs(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        assert not window.detect_id_timeout.isEnabled()
        assert not window.openloop_theta_step.isEnabled()
        assert not window.openloop_duration.isEnabled()

        window._on_connection_changed(True, "Connected")
        window._on_rs485_connection_changed(True, "Connected")
        window._on_test_finished(
            ServiceResult(Verdict(test_name="CAN Test", passed=True, reason="PASS", returned_driver_id=0x34))
        )
        assert window.detect_id_timeout.isEnabled()
        assert window.openloop_theta_step.isEnabled()
        assert window.openloop_duration.isEnabled()

        window._mark_test_started("openloop")
        assert not window.detect_id_timeout.isEnabled()
        assert not window.openloop_theta_step.isEnabled()
        assert not window.openloop_duration.isEnabled()
        assert window.can_stress_button.isEnabled()
        assert window.gongwang_button.isEnabled()
        assert window.rs485_stress_button.isEnabled()
        assert not window.openloop_button.isEnabled()

        window._on_test_finished(
            ServiceResult(Verdict(test_name="Open-loop Test", passed=True, reason="PASS"))
        )
        window._mark_test_started("rs485_stress")
        assert window.openloop_button.isEnabled()
        assert window.can_stress_button.isEnabled()
        assert window.gongwang_button.isEnabled()
        assert not window.rs485_stress_button.isEnabled()
        assert not window.rs485_stress_interval.isEnabled()
        assert not window.rs485_stress_total.isEnabled()
        assert not window.rs485_stress_timeout.isEnabled()
        assert window.rs485_stress_button.text() == "RS485 Stress Running..."
    finally:
        window.close()


def test_rs485_open_and_stress_signal(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_rs485_com_ports",
        lambda: [("COM14", "USB-SERIAL RS485")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._refresh_rs485_ports()
        with qtbot.waitSignal(window.request_rs485_connect, timeout=1000) as open_blocker:
            window.rs485_toggle_button.click()
        params = open_blocker.args[0]
        assert params.channel == "COM14"
        assert params.baudrate == 2_500_000

        window._on_connection_changed(True, "Connected")
        window._on_rs485_connection_changed(True, "Connected")
        window._on_test_finished(ServiceResult(Verdict(test_name="CAN Test", passed=True, reason="PASS", returned_driver_id=0x34)))
        assert window.rs485_stress_button.isEnabled()
        assert window.rs485_stress_interval.text() == "1"
        assert window.rs485_stress_total.text() == str(RS485_STRESS_DEFAULT_DURATION_MS)
        assert window.rs485_stress_timeout.text() == str(RS485_STRESS_DEFAULT_TIMEOUT_MS)
        assert window.rs485_stress_received_display.text() == "0"
        window.rs485_stress_timeout.setText("45000")

        with qtbot.waitSignal(window.request_rs485_stress_test, timeout=1000) as test_blocker:
            window.rs485_stress_button.click()
        assert test_blocker.args == [45000, RS485_STRESS_DEFAULT_DURATION_MS, 1]
        window._on_test_progress("rs485_stress", 456)
        assert window.rs485_stress_received_display.text() == "456"
        assert window.result_cards["rs485_stress"]["fields"]["received_message_count"].text() == "456"
        assert "Running RS485 stress test..." in window.console_box.toPlainText()
    finally:
        window.close()


def test_rs485_stress_invalid_timeout_does_not_emit(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._on_connection_changed(True, "Connected")
        window._on_rs485_connection_changed(True, "Connected")
        verdict = Verdict(test_name="CAN Test", passed=True, reason="PASS", returned_driver_id=0x34)
        window._on_test_finished(ServiceResult(verdict=verdict))
        window.rs485_stress_timeout.setText("0")

        emitted = []
        window.request_rs485_stress_test.connect(lambda *args: emitted.append(args))
        window.rs485_stress_button.click()
        assert emitted == []
        assert f"RS485 timeout must be 1..{MAX_TEST_TIMEOUT_MS} ms." in window.console_box.toPlainText()
    finally:
        window.close()


def test_gui_shows_waveshare_config_defaults(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        assert window.bitrate.currentText() == "1M"
        assert window._selected_bitrate() == 1_000_000
        assert window.serial_baud.currentText() == "2000000"
        assert window.frame_type.text() == "Extended Frame"
        assert window.filter_id.text() == "00000000"
        assert window.mask_id.text() == "00000000"
        assert window.data_send.text() == format_waveshare_config_packet(1_000_000)
        assert window.set_and_start_button.text() == "Set and Start"
        assert not window.set_and_start_button.isEnabled()
        assert window.com_toggle_button.text() == "OPEN"
    finally:
        window.close()


def test_set_and_start_enabled_after_open_and_updates_console(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._on_connection_changed(True, "Connected")
        assert window.set_and_start_button.isEnabled()
        with qtbot.waitSignal(window.request_configure_adapter, timeout=1000):
            window.set_and_start_button.click()

        packet = format_waveshare_config_packet(1_000_000)
        window._on_adapter_configured(True, packet)
        assert window.data_send.text() == packet
        assert "Configure CAN Ok!" in window.console_box.toPlainText()
    finally:
        window.close()


def test_gui_shows_current_fct_summary(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        verdict = Verdict(
            test_name="Open-loop Test",
            passed=True,
            reason="PASS",
            returned_driver_id=0x34,
            firmware_status=MotorTestStatus.FINISHED,
            stop_reason=MotorTestStopReason.COMPLETED,
            elapsed_ms=5000,
            fct_state=2,
            fct_pass_count=1,
            fct_fail_count=0,
        )
        window._on_test_finished(ServiceResult(verdict=verdict))
        card = window.result_cards["openloop"]
        assert card["status"].text() == "PASS"
        assert card["fields"]["returned_driver_id"].text() == "52"
        assert card["fields"]["fct_state"].text() == "PASS"
        assert card["fields"]["fct_pass_count"].text() == "1"
        assert card["fields"]["fct_fail_count"].text() == "0"
        assert card["fields"]["elapsed"].text() == "5000 ms"
        assert card["details_widget"].isHidden()
        card["details_button"].setChecked(True)
        assert not card["details_widget"].isHidden()
    finally:
        window.close()


def test_tab_switches_clear_completed_cards_and_preserve_running_cards(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        assert not window.result_cards["openloop"]["widget"].isHidden()
        assert not window.result_cards["can_stress"]["widget"].isHidden()
        assert not window.result_cards["rs485_stress"]["widget"].isHidden()
        assert window.result_cards["gongwang"]["widget"].isHidden()

        openloop = Verdict(
            test_name="Open-loop Test",
            passed=True,
            reason="PASS",
            fct_state=2,
            fct_pass_count=1,
            fct_fail_count=0,
        )
        window._on_test_finished(ServiceResult(verdict=openloop))
        window._mark_test_started("can_stress")
        assert window.result_cards["openloop"]["status"].text() == "PASS"
        assert window.result_cards["can_stress"]["status"].text() == "RUNNING"

        window.test_tabs.setCurrentIndex(1)
        assert window.result_cards["rs485_stress"]["widget"].isHidden()
        assert not window.result_cards["gongwang"]["widget"].isHidden()
        assert window.result_cards["openloop"]["status"].text() == "NOT RUN"
        assert window.result_cards["can_stress"]["status"].text() == "RUNNING"
        assert window.overall_status.text() == "RUNNING (1)"

        window.test_tabs.setCurrentIndex(2)
        assert all(not card["widget"].isHidden() for card in window.result_cards.values())
    finally:
        window.close()


def test_detect_id_clears_completed_result_cards(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._on_connection_changed(True, "Connected")
        window._id_detected = True
        window._set_ui_state("ready")
        window._test_run_inputs["openloop"] = {"duration_ms": 5000}
        window._on_test_finished(
            ServiceResult(
                Verdict(
                    test_name="Open-loop Test",
                    passed=False,
                    reason="old failure",
                    fct_state=3,
                )
            )
        )
        card = window.result_cards["openloop"]
        card["details_button"].setChecked(True)
        assert card["status"].text() == "FAIL"
        assert not card["failure"].isHidden()

        with qtbot.waitSignal(window.request_can_test, timeout=1000):
            window.detect_id_button.click()
        assert card["status"].text() == "NOT RUN"
        assert all(field.text() == "-" for field in card["fields"].values())
        assert card["failure"].isHidden()
        assert not card["details_button"].isChecked()
        assert "openloop" not in window._test_run_inputs
    finally:
        window.close()


def test_workspace_and_encoder_tab_switches_clear_completed_cards(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        verdict = Verdict(test_name="Open-loop Test", passed=True, reason="PASS", fct_state=2)

        window._on_test_finished(ServiceResult(verdict=verdict))
        assert window.result_cards["openloop"]["status"].text() == "PASS"
        window.workspace_tabs.setCurrentIndex(1)
        assert window.result_cards["openloop"]["status"].text() == "NOT RUN"

        window._on_test_finished(ServiceResult(verdict=verdict))
        assert window.result_cards["openloop"]["status"].text() == "PASS"
        window.gw_tabs.setCurrentIndex(1)
        assert window.result_cards["openloop"]["status"].text() == "NOT RUN"
    finally:
        window.close()


def test_gui_can_stress_defaults_and_signal(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._on_connection_changed(True, "Connected")
        verdict = Verdict(test_name="CAN Test", passed=True, reason="PASS", returned_driver_id=0x34)
        window._on_test_finished(ServiceResult(verdict=verdict))

        assert window.can_stress_interval.text() == str(CAN_STRESS_DEFAULT_INTERVAL_MS)
        assert window.can_stress_duration.text() == str(CAN_STRESS_DEFAULT_TOTAL_MESSAGES)
        assert window.can_stress_app_timeout.text() == str(CAN_STRESS_APP_TIMEOUT_DEFAULT_MS)
        assert window.can_stress_received_display.text() == "0"
        assert window.detect_id_timeout.text() == str(DETECT_ID_DEFAULT_TIMEOUT_MS)
        with qtbot.waitSignal(window.request_can_stress_test, timeout=1000) as blocker:
            window.can_stress_button.click()
        assert blocker.args == [
            CAN_STRESS_APP_TIMEOUT_DEFAULT_MS,
            CAN_STRESS_DEFAULT_INTERVAL_MS,
            CAN_STRESS_DEFAULT_TOTAL_MESSAGES,
        ]
        window._on_test_progress("can_stress", 123)
        assert window.can_stress_received_display.text() == "123"
        assert window.result_cards["can_stress"]["fields"]["actual_response_count"].text() == "123"
        assert "Running CAN stress test..." in window.console_box.toPlainText()
    finally:
        window.close()


def test_gui_can_stress_invalid_input_does_not_emit(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._on_connection_changed(True, "Connected")
        verdict = Verdict(test_name="CAN Test", passed=True, reason="PASS", returned_driver_id=0x34)
        window._on_test_finished(ServiceResult(verdict=verdict))
        window.can_stress_interval.setText("0")

        emitted = []
        window.request_can_stress_test.connect(lambda *args: emitted.append(args))
        window.can_stress_button.click()
        assert emitted == []
        assert "CAN stress interval must be 1..1000 ms." in window.console_box.toPlainText()

        window.can_stress_interval.setText("2")
        window.can_stress_duration.setText("0")
        window.can_stress_button.click()
        assert emitted == []
        assert "CAN stress total messages must be 1..1000000." in window.console_box.toPlainText()
    finally:
        window.close()


def test_gui_gongwang_defaults_and_signal(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window._on_connection_changed(True, "Connected")
        verdict = Verdict(test_name="CAN Test", passed=True, reason="PASS", returned_driver_id=0x34)
        window._on_test_finished(ServiceResult(verdict=verdict))

        assert window.gongwang_duration.text() == str(GONGWANG_DEFAULT_DURATION_MS)
        assert window.gongwang_period.text() == "1"
        assert window.gongwang_app_timeout.text() == str(GONGWANG_APP_TIMEOUT_DEFAULT_MS)
        assert window.gongwang_button.isEnabled()
        with qtbot.waitSignal(window.request_gongwang_test, timeout=1000) as blocker:
            window.gongwang_button.click()
        assert blocker.args == [GONGWANG_APP_TIMEOUT_DEFAULT_MS, GONGWANG_DEFAULT_DURATION_MS, 1]
        assert "Running GongWang encoder test..." in window.console_box.toPlainText()
    finally:
        window.close()


def test_gui_shows_compact_gongwang_fct_result(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        results = GongwangResultSet(
            duration_ms=5000,
            schema_version=1,
            target_hz=20000,
            rx_mode=1,
            command=0x9A,
            response_length=10,
            scheduled_count=100000,
            started_count=99000,
            response_count=98000,
            valid_count=0,
            actual_request_hz=19800,
            actual_response_hz=19600,
            valid_response_hz=19000,
            success_percent=95.0,
            crc_error_count=2,
            fault_code=3,
            input_single_turn_raw=123,
            input_single_turn_deg=12.5,
        )
        verdict = Verdict(
            test_name="GongWang Encoder Test",
            passed=True,
            reason="PASS",
            gongwang_results=results,
            fct_state=2,
            fct_pass_count=98000,
            fct_fail_count=2,
        )
        window._on_test_finished(ServiceResult(verdict=verdict))
        card = window.result_cards["gongwang"]
        assert card["status"].text() == "PASS"
        assert card["fields"]["fct_state"].text() == "PASS"
        assert card["fields"]["fct_pass_count"].text() == "98000"
        assert card["fields"]["fct_fail_count"].text() == "2"
    finally:
        window.close()


def test_gui_logs_gongwang_encoder_fault_bits_from_lsb(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        verdict = Verdict(
            test_name="GongWang Encoder Test",
            passed=True,
            reason="PASS",
            gongwang_results=GongwangResultSet(
                valid_count=1,
                fault_code=(1 << 4) | (1 << 5),
            ),
        )

        window._on_test_finished(ServiceResult(verdict=verdict))

        console = window.console_box.toPlainText()
        assert "Mã lỗi ALMC bộ mã hóa: 48 (0b00110000)" in console
        assert "Lỗi bộ mã hóa bit 4 (0x10): Cảnh báo khe hở phía động cơ" in console
        assert "Lỗi bộ mã hóa bit 5 (0x20): Cảnh báo khe hở phía hộp giảm tốc" in console
        assert "0b00001100" not in console
    finally:
        window.close()


def test_gui_shows_can_stress_counters(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        verdict = Verdict(
            test_name="CAN Stress Test",
            passed=True,
            reason="PASS",
            returned_driver_id=0x34,
            firmware_status=MotorTestStatus.FINISHED,
            stop_reason=MotorTestStopReason.COMPLETED,
            elapsed_ms=5000,
            tx_attempt_count=500,
            tx_success_count=500,
            tx_fail_count=0,
            last_sequence=499,
            late_count=0,
            received_message_count=500,
            actual_response_count=498,
            missing_message_count=0,
            duplicate_message_count=0,
            success_rate_percent=100.0,
            receive_rate_hz=100.0,
            fct_pass_count=500,
            fct_fail_count=0,
        )
        window._on_test_finished(ServiceResult(verdict=verdict))
        fields = window.result_cards["can_stress"]["fields"]
        assert fields["tx_attempt_count"].text() == "500"
        assert fields["fct_pass_count"].text() == "500"
        assert fields["fct_fail_count"].text() == "0"
        assert fields["actual_response_count"].text() == "498"
        assert fields["success_rate"].text() == "100.0%"
        assert fields["receive_rate"].text() == "100.0 msg/s"
    finally:
        window.close()


def test_gui_shows_rs485_stress_counters(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "pcbt_app.ui.main_window.list_waveshare_com_ports",
        lambda: [("COM13", "USB-SERIAL CH340")],
    )
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        verdict = Verdict(
            test_name="RS485 Stress Test",
            passed=True,
            reason="PASS",
            returned_driver_id=0x34,
            fct_state=2,
            fct_pass_count=1000,
            fct_fail_count=0,
            elapsed_ms=5000,
            last_sequence=999,
            first_sequence=0,
            received_message_count=1000,
            expected_message_count=1000,
            missing_message_count=0,
            malformed_count=0,
            sequence_error_count=0,
            requested_frequency_hz=20000.0,
            received_byte_count=10000,
            bytes_per_message=10,
        )
        window._on_test_finished(ServiceResult(verdict=verdict))
        fields = window.result_cards["rs485_stress"]["fields"]
        assert fields["received_message_count"].text() == "1000"
        assert fields["fct_pass_count"].text() == "1000"
        assert fields["fct_fail_count"].text() == "0"
        assert fields["malformed_count"].text() == "0"
        assert fields["sequence_error_count"].text() == "0"
        assert fields["first_sequence"].text() == "0x000000"
        assert fields["last_sequence"].text() == "0x0003E7"
        assert fields["requested_frequency"].text() == "20000.0 frame/s"
    finally:
        window.close()


def test_gui_state_styles_are_distinct(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window._apply_banner_state("running")
        assert window.overall_status.text() == "RUNNING"
        assert "#D18A1D" in window.overall_status.styleSheet()

        window._apply_banner_state("pass")
        assert window.overall_status.text() == "PASS"
        assert "#36A853" in window.overall_status.styleSheet()

        window._apply_banner_state("fail")
        assert window.overall_status.text() == "FAIL"
        assert "#E03616" in window.overall_status.styleSheet()
    finally:
        window.close()

def test_model_b_native_configuration_controls(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window.backend.setCurrentText(WAVESHARE_MODEL_B_BACKEND)
        assert window.filter_start_label.text() == "Filter Start"
        assert window.filter_end_label.text() == "Filter End"
        assert window.mask_id.text() == "1FFFFFFF"
        assert window.bitrate.findData(400_000) == -1
        window._on_connection_changed(True, "Connected")
        assert window.bitrate.isEnabled()
        window._set_bitrate_value(500_000)
        with qtbot.waitSignal(window.request_configure_adapter, timeout=1000) as signal:
            window.set_and_start_button.click()
        assert signal.args == [500_000]
        window._on_adapter_configured(True, format_waveshare_config_packet(500_000))
        assert "AA55" not in window.data_send.text()
        assert "500000 bit/s" in window.console_box.toPlainText()
        window._gw_live_running = True
        window._set_ui_state("open")
        assert not window.set_and_start_button.isEnabled()
        assert not window.bitrate.isEnabled()
    finally:
        window.close()

def test_adapter_selector_switches_and_remembers_model(qtbot, tmp_path):
    from PySide6.QtCore import QSettings
    settings = QSettings(str(tmp_path / "adapter.ini"), QSettings.IniFormat)
    window = MainWindow(settings=settings)
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window.adapter_model.setCurrentIndex(1)
        assert window.backend.currentText() == WAVESHARE_MODEL_B_BACKEND
        assert window.channel.isEnabled()
        assert window.filter_end_label.text() == "Filter End"
        window._on_connection_changed(True, "Connected")
        assert not window.adapter_model.isEnabled()
        window._on_connection_changed(False, "Disconnected")
        assert window.adapter_model.isEnabled()
    finally:
        window.close()
    restored = MainWindow(settings=settings)
    qtbot.addWidget(restored)
    try:
        assert restored.adapter_model.currentData() == WAVESHARE_MODEL_B_BACKEND
        restored.adapter_model.setCurrentIndex(0)
        assert restored.backend.currentText() == WAVESHARE_BACKEND
        assert restored.channel.isEnabled()
        assert restored.mask_id.text() == "00000000"
        assert settings.value("adapter/backend") == WAVESHARE_BACKEND
    finally:
        restored.close()

def test_model_b_device_channel_selection(qtbot, tmp_path):
    from PySide6.QtCore import QSettings
    settings = QSettings(str(tmp_path / 'selection.ini'), QSettings.IniFormat)
    settings.setValue('adapter/backend', WAVESHARE_MODEL_B_BACKEND)
    settings.setValue('adapter/device_index', 2)
    settings.setValue('adapter/channel_index', 0)
    window = MainWindow(settings=settings)
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        assert window._current_channel() == 'USB-CAN-FD-B Device 2 / CAN1'
        assert settings.value('adapter/channel_index', type=int) == 0
        with qtbot.waitSignal(window.request_connect) as signal:
            window._connect_clicked()
        assert signal.args[0].channel == 'USB-CAN-FD-B Device 2 / CAN1'
        assert not window.channel.isEnabled()
        assert not window.model_b_channel.isEnabled()
        window._on_connection_changed(False, 'Disconnected')
        window.channel.setCurrentIndex(window.channel.findData(3))
        window.model_b_channel.setCurrentIndex(1)
        assert window._current_channel() == 'USB-CAN-FD-B Device 3 / CAN2'
        assert settings.value('adapter/device_index', type=int) == 3
    finally:
        window.close()


@pytest.fixture(autouse=True)
def fake_model_b_discovery(monkeypatch):
    original = MainWindow._refresh_ports
    def refresh(window):
        if window.backend.currentText() == WAVESHARE_MODEL_B_BACKEND:
            window._on_model_b_devices_scanned([(0, "Device 0 / SN TEST0"), (2, "Device 2 / SN TEST2"), (3, "Device 3 / SN TEST3")], "")
        else:
            original(window)
    monkeypatch.setattr(MainWindow, "_refresh_ports", refresh)


def test_model_b_empty_scan_blocks_open(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    disconnect_worker_slots(window)
    try:
        window.adapter_model.setCurrentIndex(1)
        window._on_model_b_devices_scanned([], "")
        assert window.channel.currentData() is None
        assert not window.com_toggle_button.isEnabled()
        assert window.refresh_ports_button.isEnabled()
        assert window.model_b_channel.isEnabled()
        window._on_model_b_devices_scanned([(4, "Device 4 / SN CONNECTED")], "")
        assert window.com_toggle_button.isEnabled()
        assert window._current_channel() == "USB-CAN-FD-B Device 4 / CAN2"
    finally:
        window.close()


@pytest.mark.parametrize("value, color", [
    (None, "#F3F4F6"), (399.99, "#FDE8E5"), (400, "#FFF4CC"),
    (799.99, "#FFF4CC"), (800, "#DCFCE7"), (1600, "#DCFCE7"),
    (1600.01, "#FDE8E5"),
])
def test_custom_amplitude_boundaries(value, color):
    from pcbt_app.ui.amplitude_range import AmplitudeRange
    assert color in amplitude_value_stylesheet(value, AmplitudeRange(400, 800, 1600))


def test_amplitude_range_dialog_apply_cancel_restore_and_persistence(qtbot, tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QDialogButtonBox
    from pcbt_app.ui.amplitude_range import AmplitudeRange, AmplitudeRangeDialog
    path = str(tmp_path / "amplitude.ini")
    settings = QSettings(path, QSettings.IniFormat)
    window = MainWindow(settings=settings)
    qtbot.addWidget(window)
    try:
        for fields in window.gw_live_fields.values():
            fields["amplitude_mv"].setText("800.00")
            fields["amplitude_min_mv"].setText("Min: 400.00")
            fields["amplitude_max_mv"].setText("Max: 1600.01")
        for fields in window.gw_calibration_fields.values():
            fields["master_sin_amplitude_v"].setText("0.800")
        dialog = AmplitudeRangeDialog(window._amplitude_limits, window)
        dialog.range_applied.connect(window._apply_amplitude_range)
        dialog.fields[0].setValue(1200)
        dialog.buttons.button(QDialogButtonBox.Apply).click()
        assert dialog.error.text()
        assert window._amplitude_limits == AmplitudeRange()
        for field, value in zip(dialog.fields, (400, 800, 1600)):
            field.setValue(value)
        dialog.buttons.button(QDialogButtonBox.Apply).click()
        for fields in window.gw_live_fields.values():
            assert "#DCFCE7" in fields["amplitude_mv"].styleSheet()
            assert "#FFF4CC" in fields["amplitude_min_mv"].styleSheet()
            assert "#FDE8E5" in fields["amplitude_max_mv"].styleSheet()
            assert fields["amplitude_mv"].text() == "800.00"
        for fields in window.gw_calibration_fields.values():
            assert "#DCFCE7" in fields["master_sin_amplitude_v"].styleSheet()
            assert "#F3F4F6" in fields["slave_cos_amplitude_v"].styleSheet()
        assert "800" in window.amplitude_range_legend.text()
        dialog = AmplitudeRangeDialog(window._amplitude_limits, window)
        dialog.range_applied.connect(window._apply_amplitude_range)
        dialog.buttons.button(QDialogButtonBox.RestoreDefaults).click()
        assert [f.value() for f in dialog.fields] == [600, 1000, 1800]
        dialog.buttons.button(QDialogButtonBox.Cancel).click()
        assert window._amplitude_limits == AmplitudeRange(400, 800, 1600)
        settings.sync()
    finally:
        window.close()
    reopened = MainWindow(settings=QSettings(path, QSettings.IniFormat))
    qtbot.addWidget(reopened)
    try:
        assert reopened._amplitude_limits == AmplitudeRange(400, 800, 1600)
        dialog = AmplitudeRangeDialog(reopened._amplitude_limits, reopened)
        dialog.range_applied.connect(reopened._apply_amplitude_range)
        dialog.buttons.button(QDialogButtonBox.RestoreDefaults).click()
        dialog.buttons.button(QDialogButtonBox.Apply).click()
        assert reopened._amplitude_limits == AmplitudeRange()
        assert AmplitudeRange.load(reopened._settings) == AmplitudeRange()
    finally:
        reopened.close()


@pytest.mark.parametrize("bad", ["invalid", "nan", "inf", -1, 1800])
def test_invalid_saved_amplitude_range_falls_back(tmp_path, bad):
    from PySide6.QtCore import QSettings
    from pcbt_app.ui.amplitude_range import AmplitudeRange
    settings = QSettings(str(tmp_path / "bad.ini"), QSettings.IniFormat)
    settings.setValue("encoder/amplitude/yellow_start", bad)
    assert AmplitudeRange.load(settings) == AmplitudeRange()


def test_amplitude_range_arrow_buttons_are_clickable(qtbot):
    from PySide6.QtWidgets import QStyle, QStyleOptionSpinBox
    from pcbt_app.ui.amplitude_range import AmplitudeRange, AmplitudeRangeDialog
    window = MainWindow()
    qtbot.addWidget(window)
    dialog = AmplitudeRangeDialog(AmplitudeRange(), window)
    qtbot.addWidget(dialog)
    try:
        dialog.show()
        qtbot.waitExposed(dialog)
        for field in dialog.fields:
            option = QStyleOptionSpinBox()
            field.initStyleOption(option)
            initial = field.value()
            for control, expected in (
                (QStyle.SC_SpinBoxUp, initial + field.singleStep()),
                (QStyle.SC_SpinBoxDown, initial),
            ):
                rect = field.style().subControlRect(QStyle.CC_SpinBox, option, control, field)
                assert not field.lineEdit().geometry().contains(rect.center())
                qtbot.mouseClick(field, Qt.LeftButton, pos=rect.center())
                assert field.value() == expected
    finally:
        dialog.close()
        window.close()


def test_amplitude_legend_shows_both_red_limits():
    from pcbt_app.ui.amplitude_range import AmplitudeRange
    legend = AmplitudeRange(400, 800, 1600).legend()
    assert '#C43D32">●</span> &lt;400' in legend
    assert '#C43D32">●</span> &gt;1600' in legend
