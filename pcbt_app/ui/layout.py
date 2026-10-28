"""Builds every widget of the main window and attaches them as ``MainWindow`` attributes."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pcbt_app.config import (
    DEFAULT_BACKEND,
    DEFAULT_BITRATE,
    DEFAULT_SERIAL_BAUD,
    WAVESHARE_BACKEND,
    WAVESHARE_MODEL_B_BACKEND,
)
from pcbt_app.protocol.fct_can import (
    FCT_MAX_DURATION_MS,
    FCT_MAX_PERIOD_TICKS,
    FCT_OPENLOOP_MAX_THETA_STEP_RAD,
    FCT_OPENLOOP_MIN_THETA_STEP_RAD,
)
from pcbt_app.protocol.mit_firmware import (
    CAN_STRESS_DEFAULT_INTERVAL_MS,
    CAN_STRESS_DEFAULT_TOTAL_MESSAGES,
    GONGWANG_DEFAULT_DURATION_MS,
    OPEN_LOOP_DEFAULT_DURATION_MS,
    OPEN_LOOP_DEFAULT_THETA_STEP,
    RS485_STRESS_DEFAULT_BAUD,
    RS485_STRESS_DEFAULT_DURATION_MS,
    RS485_STRESS_DEFAULT_PERIOD_TICKS,
)
from pcbt_app.ui.presets import (
    BITRATE_PRESETS,
    CAN_STRESS_APP_TIMEOUT_DEFAULT_MS,
    DETECT_ID_DEFAULT_TIMEOUT_MS,
    GONGWANG_APP_TIMEOUT_DEFAULT_MS,
    MAX_TEST_TIMEOUT_MS,
    OPENLOOP_APP_TIMEOUT_DEFAULT_MS,
    RS485_BAUD_PRESETS,
    RS485_STRESS_DEFAULT_TIMEOUT_MS,
    SERIAL_BAUD_PRESETS,
)
from pcbt_app.ui.result_report import RESULT_CARD_SPECS, make_result_card


def build_main_window_ui(self) -> None:
    """Create all widgets and layouts and store them as attributes on ``self``.

    Called once from ``MainWindow.__init__``. Widget attribute names here are the
    contract used by ``main_window``, the panel mixins and ``tests/test_gui.py``;
    rename them in all places together.
    """
    root = QWidget(self)
    root.setObjectName("AppRoot")
    self.setCentralWidget(root)

    page = QGridLayout(root)
    page.setContentsMargins(10, 8, 10, 10)
    page.setHorizontalSpacing(8)
    page.setVerticalSpacing(8)
    page.setColumnStretch(0, 0)
    page.setColumnStretch(1, 1)
    page.setRowStretch(0, 1)

    self.com_group = self._group("CAN Port")
    com_layout = QGridLayout(self.com_group)
    com_layout.setHorizontalSpacing(8)
    com_layout.setVerticalSpacing(6)

    self.backend = QComboBox()
    self.backend.addItems([WAVESHARE_BACKEND, WAVESHARE_MODEL_B_BACKEND, "pcan", "vector", "ixxat", "slcan"])
    self.backend.setCurrentText(DEFAULT_BACKEND)
    self.backend.setVisible(False)
    self.adapter_model = QComboBox()
    self.adapter_model.addItem("Model A (USB-CAN-A)", WAVESHARE_BACKEND)
    self.adapter_model.addItem("Model B (USB-CAN-FD-B)", WAVESHARE_MODEL_B_BACKEND)
    self.adapter_model.setCurrentIndex(self.adapter_model.findData(DEFAULT_BACKEND))
    self.adapter_model.currentIndexChanged.connect(
        lambda: self.backend.setCurrentText(self.adapter_model.currentData())
    )

    self.channel = QComboBox()
    self.channel.setEditable(True)
    self.serial_baud = QComboBox()
    for label, value in SERIAL_BAUD_PRESETS:
        self.serial_baud.addItem(label, value)
    self._set_serial_baud_value(DEFAULT_SERIAL_BAUD)

    self.refresh_ports_button = QPushButton("Refresh COM")
    self.com_toggle_button = QPushButton("OPEN")
    self.com_toggle_button.setObjectName("OpenButton")
    self.connect_button = self.com_toggle_button
    self.disconnect_button = self.com_toggle_button

    com_layout.addWidget(QLabel("Adapter"), 0, 0)
    com_layout.addWidget(self.adapter_model, 0, 1, 1, 2)
    com_layout.addWidget(QLabel("Port"), 1, 0)
    com_layout.addWidget(self.channel, 1, 1, 1, 2)
    com_layout.addWidget(QLabel("Baudrate"), 2, 0)
    com_layout.addWidget(self.serial_baud, 2, 1, 1, 2)
    com_layout.addWidget(self.refresh_ports_button, 3, 0, 1, 2)
    com_layout.addWidget(self.com_toggle_button, 3, 2)
    self.model_b_device_label = QLabel("CAN channel")
    self.model_b_channel = QComboBox()
    self.model_b_channel.addItems(["CAN1", "CAN2"])
    self.model_b_channel.setCurrentIndex(1)
    com_layout.addWidget(self.model_b_device_label, 4, 0)
    com_layout.addWidget(self.model_b_channel, 4, 1, 1, 2)
    self.channel.currentIndexChanged.connect(self._model_b_selection_changed)
    self.model_b_channel.currentIndexChanged.connect(self._model_b_selection_changed)

    self.rs485_group = self._group("RS485 Port")
    rs485_layout = QGridLayout(self.rs485_group)
    rs485_layout.setHorizontalSpacing(8)
    rs485_layout.setVerticalSpacing(6)
    self.rs485_channel = QComboBox()
    self.rs485_channel.setEditable(True)
    self.rs485_baud = QComboBox()
    for label, value in RS485_BAUD_PRESETS:
        self.rs485_baud.addItem(label, value)
    self._set_rs485_baud_value(RS485_STRESS_DEFAULT_BAUD)
    self.refresh_rs485_ports_button = QPushButton("Refresh RS485 COM")
    self.rs485_toggle_button = QPushButton("OPEN")
    self.rs485_toggle_button.setObjectName("OpenButton")
    rs485_layout.addWidget(QLabel("Port"), 0, 0)
    rs485_layout.addWidget(self.rs485_channel, 0, 1, 1, 2)
    rs485_layout.addWidget(QLabel("Baudrate"), 1, 0)
    rs485_layout.addWidget(self.rs485_baud, 1, 1, 1, 2)
    rs485_layout.addWidget(self.refresh_rs485_ports_button, 2, 0, 1, 2)
    rs485_layout.addWidget(self.rs485_toggle_button, 2, 2)

    self.test_group = self._group("Test")
    test_layout = QVBoxLayout(self.test_group)
    test_layout.setContentsMargins(8, 12, 8, 8)
    test_layout.setSpacing(6)
    self.detect_id_button = QPushButton("Detect ID")
    self.detect_id_button.setObjectName("MotorButton")
    self.can_test_button = self.detect_id_button
    self.driver_id_display = self._read_only_value("")
    self.openloop_button = QPushButton("Start Open-loop Test")
    self.openloop_button.setObjectName("MotorButton")
    self.can_stress_button = QPushButton("Start CAN Stress Test")
    self.can_stress_button.setObjectName("MotorButton")
    self.rs485_stress_button = QPushButton("RS485 Stress Test")
    self.rs485_stress_button.setObjectName("MotorButton")
    self.gongwang_button = QPushButton("Start GongWang Encoder Test")
    self.gongwang_button.setObjectName("MotorButton")
    self.openloop_theta_step = QLineEdit(f"{OPEN_LOOP_DEFAULT_THETA_STEP:.5f}")
    self.openloop_theta_step.setValidator(
        QDoubleValidator(
            FCT_OPENLOOP_MIN_THETA_STEP_RAD,
            FCT_OPENLOOP_MAX_THETA_STEP_RAD,
            5,
            self,
        )
    )
    self.openloop_duration = QLineEdit(str(OPEN_LOOP_DEFAULT_DURATION_MS))
    self.openloop_duration.setValidator(QIntValidator(1, FCT_MAX_DURATION_MS, self))
    self.openloop_app_timeout = QLineEdit(str(OPENLOOP_APP_TIMEOUT_DEFAULT_MS))
    self.openloop_app_timeout.setValidator(QIntValidator(1, MAX_TEST_TIMEOUT_MS, self))
    self.detect_id_timeout = QLineEdit(str(DETECT_ID_DEFAULT_TIMEOUT_MS))
    self.detect_id_timeout.setValidator(QIntValidator(1, MAX_TEST_TIMEOUT_MS, self))
    self.can_stress_interval = QLineEdit(str(CAN_STRESS_DEFAULT_INTERVAL_MS))
    self.can_stress_interval.setValidator(QIntValidator(1, 1000, self))
    self.can_stress_duration = QLineEdit(str(CAN_STRESS_DEFAULT_TOTAL_MESSAGES))
    self.can_stress_duration.setValidator(QIntValidator(1, 1_000_000, self))
    self.can_stress_app_timeout = QLineEdit(str(CAN_STRESS_APP_TIMEOUT_DEFAULT_MS))
    self.can_stress_app_timeout.setValidator(QIntValidator(1, MAX_TEST_TIMEOUT_MS, self))
    self.can_stress_received_display = self._read_only_value("0")
    self.rs485_stress_interval = QLineEdit(str(RS485_STRESS_DEFAULT_PERIOD_TICKS))
    self.rs485_stress_interval.setValidator(QIntValidator(1, FCT_MAX_PERIOD_TICKS, self))
    self.rs485_stress_total = QLineEdit(str(RS485_STRESS_DEFAULT_DURATION_MS))
    self.rs485_stress_total.setValidator(QIntValidator(1, FCT_MAX_DURATION_MS, self))
    self.rs485_stress_timeout = QLineEdit(str(RS485_STRESS_DEFAULT_TIMEOUT_MS))
    self.rs485_stress_timeout.setValidator(QIntValidator(1, MAX_TEST_TIMEOUT_MS, self))
    self.rs485_stress_received_display = self._read_only_value("0")
    self.gongwang_duration = QLineEdit(str(GONGWANG_DEFAULT_DURATION_MS))
    self.gongwang_duration.setValidator(QIntValidator(1, FCT_MAX_DURATION_MS, self))
    self.gongwang_period = QLineEdit("1")
    self.gongwang_period.setValidator(QIntValidator(1, FCT_MAX_PERIOD_TICKS, self))
    self.gongwang_app_timeout = QLineEdit(str(GONGWANG_APP_TIMEOUT_DEFAULT_MS))
    self.gongwang_app_timeout.setValidator(QIntValidator(1, MAX_TEST_TIMEOUT_MS, self))

    self.overall_status = QLabel("IDLE")
    self.overall_status.setObjectName("ResultBanner")
    self.overall_status.setAlignment(Qt.AlignCenter)
    self.status_group = self._group("Test Status")
    self.status_group.setFixedWidth(200)
    status_group_layout = QVBoxLayout(self.status_group)
    status_group_layout.setContentsMargins(8, 12, 8, 8)
    self.overall_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    status_group_layout.addWidget(self.overall_status)
    def make_test_card(button: QPushButton, fields: list[tuple[str, QLineEdit]]) -> QFrame:
        card = QFrame()
        card.setObjectName("TestCard")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        card_layout = QGridLayout(card)
        card_layout.setContentsMargins(4, 4, 4, 4)
        card_layout.setHorizontalSpacing(5)
        card_layout.setVerticalSpacing(2)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        button.setFixedHeight(24)
        column_count = max(1, len(fields))
        card_layout.addWidget(button, 0, 0, 1, column_count)
        for index, (label, field) in enumerate(fields):
            field_cell = QWidget()
            field_cell_layout = QVBoxLayout(field_cell)
            field_cell_layout.setContentsMargins(0, 0, 0, 0)
            field_cell_layout.setSpacing(1)
            field_cell_layout.addWidget(QLabel(label))
            field.setFixedHeight(24)
            field_cell_layout.addWidget(field)
            card_layout.addWidget(field_cell, 1, index)
            card_layout.setColumnStretch(index, 1)
        return card

    test_specs = {
        "detect_id": (
            self.detect_id_button,
            [("Timeout ms", self.detect_id_timeout), ("Driver ID", self.driver_id_display)],
            self._run_detect_id,
        ),
        "openloop": (
            self.openloop_button,
            [
                ("App timeout ms", self.openloop_app_timeout),
                ("Theta step (rad/tick)", self.openloop_theta_step),
                ("Duration ms", self.openloop_duration),
            ],
            self._run_openloop_test,
        ),
        "can_stress": (
            self.can_stress_button,
            [
                ("App timeout ms", self.can_stress_app_timeout),
                ("Interval ms", self.can_stress_interval),
                ("Total messages", self.can_stress_duration),
                ("App responses", self.can_stress_received_display),
            ],
            self._run_can_stress_test,
        ),
        "rs485_stress": (
            self.rs485_stress_button,
            [
                ("Timeout ms", self.rs485_stress_timeout),
                ("Period (20 kHz ticks)", self.rs485_stress_interval),
                ("Duration ms", self.rs485_stress_total),
                ("App received", self.rs485_stress_received_display),
            ],
            self._run_rs485_stress_test,
        ),
        "gongwang": (
            self.gongwang_button,
            [
                ("App timeout ms", self.gongwang_app_timeout),
                ("Period (20 kHz ticks)", self.gongwang_period),
                ("Duration ms", self.gongwang_duration),
            ],
            self._run_gongwang_test,
        ),
    }
    self._test_button_copies = {key: [spec[0]] for key, spec in test_specs.items()}
    self._test_field_copies = {
        key: {source: [source] for _, source in spec[1]} for key, spec in test_specs.items()
    }

    def mirrored_field(source: QLineEdit) -> QLineEdit:
        clone = QLineEdit(source.text())
        clone.setReadOnly(source.isReadOnly())
        clone.setObjectName(source.objectName())
        if source.validator() is not None:
            clone.setValidator(source.validator())
        source.textChanged.connect(clone.setText)
        clone.textChanged.connect(source.setText)
        return clone

    def mirrored_test_button(test_key: str) -> QPushButton:
        source_button, _, handler = test_specs[test_key]
        button = QPushButton(source_button.text())
        button.setObjectName(source_button.objectName())
        button.clicked.connect(handler)
        self._test_button_copies[test_key].append(button)
        return button

    def mirrored_test_field(
        test_key: str, source: QLineEdit, *, default: int | None = None
    ) -> QLineEdit:
        clone = mirrored_field(source) if default is None else QLineEdit(str(default))
        if default is not None:
            clone.setValidator(source.validator())
        self._test_field_copies[test_key][source].append(clone)
        return clone

    def fixed_quick_field(test_key: str, source: QLineEdit) -> QLineEdit:
        clone = QLineEdit(source.text())
        clone.setReadOnly(True)
        clone.setObjectName("ReadOnlyField")
        if source.isReadOnly():
            source.textChanged.connect(clone.setText)
        self._test_field_copies[test_key][source].append(clone)
        return clone

    def make_controls_page(keys: tuple[str, ...], *, canonical: bool = False):
        scroll = QScrollArea()
        scroll.setObjectName("TestControlsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        widget = QWidget()
        widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(5)
        for key in keys:
            source_button, source_fields, handler = test_specs[key]
            if canonical:
                button = source_button
                fields = source_fields
            else:
                button = QPushButton(source_button.text())
                button.setObjectName(source_button.objectName())
                button.clicked.connect(
                    lambda _checked=False, test_key=key: self._run_default_test(test_key)
                )
                self._test_button_copies[key].append(button)
                fields = []
                for label, source in source_fields:
                    clone = fixed_quick_field(key, source)
                    fields.append((label, clone))
            layout.addWidget(make_test_card(button, fields))
        layout.addStretch(1)
        scroll.setWidget(widget)
        return scroll, widget, layout

    c1_scroll, self.c1_controls_widget, self.c1_controls_layout = make_controls_page(
        ("detect_id", "openloop", "can_stress", "rs485_stress")
    )
    c2_scroll, self.c2_controls_widget, self.c2_controls_layout = make_controls_page(
        ("detect_id", "openloop", "can_stress", "gongwang")
    )
    controls_scroll, controls_widget, controls_layout = make_controls_page(
        ("detect_id", "openloop", "can_stress", "rs485_stress", "gongwang"),
        canonical=True,
    )
    self.test_controls_scroll = controls_scroll
    self.test_controls_widget = controls_widget
    self.test_controls_layout = controls_layout
    self.test_tabs = QTabWidget()
    self.test_tabs.setObjectName("TestTabs")
    self.test_tabs.addTab(c1_scroll, "C1")
    self.test_tabs.addTab(c2_scroll, "C2")
    self.test_tabs.addTab(controls_scroll, "Advanced")
    self.test_tabs.setCurrentIndex(0)

    results_scroll = QScrollArea()
    results_scroll.setObjectName("ResultsScroll")
    results_scroll.setWidgetResizable(True)
    results_scroll.setFrameShape(QFrame.NoFrame)
    results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    results_widget = QWidget()
    results_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    results_layout = QVBoxLayout(results_widget)
    results_layout.setContentsMargins(4, 4, 4, 4)
    results_layout.setSpacing(6)
    results_layout.setAlignment(Qt.AlignTop)
    self.result_cards = {key: make_result_card(key) for key in RESULT_CARD_SPECS}
    for card in self.result_cards.values():
        results_layout.addWidget(card["widget"])
    results_layout.addStretch(1)

    result_keys_by_tab = {
        0: {"openloop", "can_stress", "rs485_stress"},
        1: {"openloop", "can_stress", "gongwang"},
        2: set(RESULT_CARD_SPECS),
    }

    def show_results_for_tab(index: int) -> None:
        visible_keys = result_keys_by_tab[index]
        for key, card in self.result_cards.items():
            card["widget"].setVisible(key in visible_keys)

    self.test_tabs.currentChanged.connect(show_results_for_tab)
    self.test_tabs.currentChanged.connect(self._clear_completed_result_cards)
    show_results_for_tab(self.test_tabs.currentIndex())
    results_scroll.setWidget(results_widget)
    self.results_scroll = results_scroll
    self.results_widget = results_widget
    self.results_layout = results_layout

    test_body_layout = QGridLayout()
    test_body_layout.setContentsMargins(0, 0, 0, 0)
    test_body_layout.setHorizontalSpacing(12)
    test_body_layout.setColumnStretch(0, 4)
    test_body_layout.setColumnStretch(1, 5)
    test_body_layout.addWidget(self.test_tabs, 0, 0)
    test_body_layout.addWidget(results_scroll, 0, 1)

    fct_page = QWidget()
    fct_page.setLayout(test_body_layout)

    gw_live_content = QWidget()
    gw_live_content.setMinimumHeight(550)
    gw_live_page_layout = QVBoxLayout(gw_live_content)
    gw_live_page_layout.setContentsMargins(8, 8, 8, 8)
    gw_live_page_layout.setSpacing(8)

    gw_controls = QFrame()
    gw_controls.setObjectName("TestCard")
    gw_controls_layout = QGridLayout(gw_controls)
    gw_controls_layout.setContentsMargins(8, 6, 8, 6)
    gw_controls_layout.setHorizontalSpacing(8)
    self.gw_live_frequency = QSpinBox()
    self.gw_live_frequency.setRange(1, 50)
    self.gw_live_frequency.setValue(50)
    self.gw_live_driver_id = self._read_only_value("")
    self.gw_live_state = self._read_only_value("Stopped")
    self.gw_live_sample_count = self._read_only_value("-")
    self.gw_live_button = QPushButton("Start")
    self.gw_live_button.setObjectName("MotorButton")
    self.gw_live_read_500_button = QPushButton("Read 500 Samples")
    self.gw_live_read_500_button.setObjectName("MotorButton")
    self.gw_live_clear_button = QPushButton("Clear")
    gw_controls_layout.addWidget(QLabel("Polling frequency (Hz)"), 0, 0)
    gw_controls_layout.addWidget(self.gw_live_frequency, 1, 0)
    gw_controls_layout.addWidget(QLabel("Detected driver ID"), 0, 1)
    gw_controls_layout.addWidget(self.gw_live_driver_id, 1, 1)
    gw_controls_layout.addWidget(QLabel("Reader state"), 0, 2)
    gw_controls_layout.addWidget(self.gw_live_state, 1, 2)
    gw_controls_layout.addWidget(QLabel("Complete samples"), 0, 3)
    gw_controls_layout.addWidget(self.gw_live_sample_count, 1, 3)
    gw_controls_layout.addWidget(self.gw_live_button, 0, 4, 2, 1)
    gw_controls_layout.addWidget(self.gw_live_read_500_button, 0, 5, 2, 1)
    gw_controls_layout.addWidget(self.gw_live_clear_button, 0, 6, 2, 1)
    gw_controls_layout.setColumnStretch(0, 1)
    gw_controls_layout.setColumnStretch(1, 1)
    gw_controls_layout.setColumnStretch(2, 1)
    gw_controls_layout.setColumnStretch(3, 1)

    gw_live_test_controls = QFrame()
    gw_live_test_controls.setObjectName("TestCard")
    gw_live_test_layout = QGridLayout(gw_live_test_controls)
    gw_live_test_layout.setContentsMargins(8, 6, 8, 6)
    gw_live_test_layout.setHorizontalSpacing(8)
    self.gw_live_detect_id_button = mirrored_test_button("detect_id")
    self.gw_live_openloop_button = mirrored_test_button("openloop")
    self.gw_live_openloop_button.clicked.disconnect()
    self.gw_live_openloop_button.clicked.connect(
        lambda: self._run_openloop_test(use_live_fields=True)
    )
    self.gw_live_openloop_app_timeout = mirrored_test_field(
        "openloop", self.openloop_app_timeout, default=30_000
    )
    self.gw_live_openloop_theta_step = mirrored_test_field(
        "openloop", self.openloop_theta_step
    )
    self.gw_live_openloop_duration = mirrored_test_field(
        "openloop", self.openloop_duration, default=20_000
    )
    for column, (label, field) in enumerate(
        (
            ("App timeout ms", self.gw_live_openloop_app_timeout),
            ("Theta step (rad/tick)", self.gw_live_openloop_theta_step),
            ("Duration ms", self.gw_live_openloop_duration),
        ),
        start=0,
    ):
        gw_live_test_layout.addWidget(QLabel(label), 1, column)
        gw_live_test_layout.addWidget(field, 2, column)
        gw_live_test_layout.setColumnStretch(column, 1)
    gw_live_test_layout.addWidget(self.gw_live_detect_id_button, 0, 0)
    gw_live_test_layout.addWidget(self.gw_live_openloop_button, 0, 2)

    def make_gw_ring_card(
        title: str,
    ) -> tuple[QGroupBox, dict[str, QLineEdit], QPushButton]:
        card = self._group(title)
        card_layout = QGridLayout(card)
        card_layout.setContentsMargins(8, 12, 8, 8)
        card_layout.setHorizontalSpacing(6)
        card_layout.setVerticalSpacing(4)
        specs = (
            ("sin_raw", "Sin raw", 0, 0),
            ("cos_raw", "Cos raw", 0, 2),
            ("sin_mv", "Sin (mV)", 1, 0),
            ("cos_mv", "Cos (mV)", 1, 2),
            ("count", "Count", 2, 2),
            ("angle_deg", "Angle (deg)", 3, 0),
            ("resolution", "Resolution", 3, 2),
        )
        fields: dict[str, QLineEdit] = {}
        for key, label, row, column in specs:
            field = self._read_only_value("-")
            field.setFixedHeight(24)
            fields[key] = field
            card_layout.addWidget(QLabel(label), row, column)
            card_layout.addWidget(field, row, column + 1)

        amplitude_widget = QWidget()
        amplitude_widget.setMinimumHeight(28)
        amplitude_layout = QGridLayout(amplitude_widget)
        amplitude_layout.setContentsMargins(0, 0, 0, 0)
        amplitude_layout.setHorizontalSpacing(3)
        for column, (key, initial_text) in enumerate(
            (("amplitude_mv", "-"), ("amplitude_min_mv", "Min: -"), ("amplitude_max_mv", "Max: -"))
        ):
            field = self._read_only_value(initial_text)
            field.setFixedHeight(24)
            field.setMinimumWidth(0)
            fields[key] = field
            amplitude_layout.addWidget(field, 0, column)
            amplitude_layout.setColumnStretch(column, 1)
        copy_button = QPushButton("⧉")
        copy_button.setToolTip("Copy Avg, Min and Max as tab-separated values")
        copy_button.setAccessibleName("Copy amplitude Avg, Min and Max")
        copy_button.setStyleSheet(
            "min-width: 20px; max-width: 20px; min-height: 20px; "
            "max-height: 20px; padding: 0; border-radius: 3px; font-size: 11pt;"
        )
        amplitude_layout.addWidget(copy_button, 0, 3)
        card_layout.addWidget(QLabel("Amplitude (mV)"), 2, 0)
        card_layout.addWidget(amplitude_widget, 2, 1)
        card_layout.setColumnStretch(1, 1)
        card_layout.setColumnStretch(3, 1)
        return card, fields, copy_button

    motor_card, motor_fields, motor_copy_button = make_gw_ring_card("Motor / Input Ring")
    output_card, output_fields, output_copy_button = make_gw_ring_card(
        "Output / Retarder Ring"
    )
    self.gw_live_fields = {"motor": motor_fields, "output": output_fields}
    self.gw_live_copy_buttons = {
        "motor": motor_copy_button,
        "output": output_copy_button,
    }
    motor_copy_button.clicked.connect(lambda: self._copy_gw_live_amplitudes("motor"))
    output_copy_button.clicked.connect(lambda: self._copy_gw_live_amplitudes("output"))
    gw_cards = QGridLayout()
    gw_cards.setContentsMargins(0, 0, 0, 0)
    gw_cards.setHorizontalSpacing(8)
    gw_cards.addWidget(motor_card, 0, 0)
    gw_cards.addWidget(output_card, 1, 0)
    gw_cards.setColumnStretch(0, 1)
    gw_live_page_layout.addWidget(gw_controls)
    gw_live_page_layout.addWidget(gw_live_test_controls)
    gw_live_page_layout.addLayout(gw_cards)
    gw_live_page_layout.addStretch(1)

    gw_live_page = QScrollArea()
    gw_live_page.setWidgetResizable(True)
    gw_live_page.setFrameShape(QFrame.NoFrame)
    gw_live_page.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    gw_live_page.setWidget(gw_live_content)

    calibration_content = QWidget()
    calibration_content.setMinimumHeight(430)
    calibration_layout = QVBoxLayout(calibration_content)
    calibration_layout.setContentsMargins(8, 8, 8, 8)
    calibration_layout.setSpacing(8)

    calibration_controls = QFrame()
    calibration_controls.setObjectName("TestCard")
    calibration_controls_layout = QGridLayout(calibration_controls)
    calibration_controls_layout.setContentsMargins(8, 6, 8, 6)
    calibration_controls_layout.setHorizontalSpacing(8)
    self.gw_calibration_driver_id = self._read_only_value("")
    self.gw_calibration_state = self._read_only_value("IDLE")
    self.gw_calibration_step = self._read_only_value("-")
    self.gw_calibration_encoder_status = self._read_only_value("-")
    for field in (
        self.gw_calibration_driver_id,
        self.gw_calibration_state,
        self.gw_calibration_step,
        self.gw_calibration_encoder_status,
    ):
        field.setFixedWidth(90)
    self.gw_calibration_button = QPushButton("Start Calibration")
    self.gw_calibration_button.setObjectName("MotorButton")
    self.gw_calibration_button.setFixedWidth(110)
    self.gw_calibration_read_button = QPushButton("Read Result")
    self.gw_calibration_read_button.setObjectName("MotorButton")
    self.gw_calibration_read_button.setFixedWidth(90)
    self.gw_calibration_clear_button = QPushButton("Clear")
    self.gw_calibration_clear_button.setFixedWidth(52)
    self.gw_calibration_detect_id_button = mirrored_test_button("detect_id")
    for column, (label, field) in enumerate(
        (
            ("Driver ID", self.gw_calibration_driver_id),
            ("State", self.gw_calibration_state),
            ("STEP", self.gw_calibration_step),
            ("Encoder status", self.gw_calibration_encoder_status),
        )
    ):
        label_widget = QLabel(label)
        label_widget.setFixedWidth(90)
        calibration_controls_layout.addWidget(label_widget, 0, column)
        calibration_controls_layout.addWidget(field, 1, column)
        calibration_controls_layout.setColumnStretch(column, 1)
    calibration_controls_layout.addWidget(self.gw_calibration_detect_id_button, 2, 0)
    calibration_controls_layout.addWidget(self.gw_calibration_button, 2, 1)
    calibration_controls_layout.addWidget(self.gw_calibration_read_button, 2, 2)
    calibration_controls_layout.addWidget(self.gw_calibration_clear_button, 2, 3)

    self.gw_calibration_message = QLabel("Ready to start calibration.")
    self.gw_calibration_message.setObjectName("CalibrationMessage")
    self.gw_calibration_message.setWordWrap(True)

    def make_calibration_result_card(title: str) -> tuple[QGroupBox, dict[str, QLineEdit]]:
        card = self._group(title)
        card_layout = QGridLayout(card)
        card_layout.setContentsMargins(8, 12, 8, 5)
        card_layout.setHorizontalSpacing(6)
        card_layout.setVerticalSpacing(2)
        fields: dict[str, QLineEdit] = {}

        specs = (
            ("master_sin_amplitude_v", "Master Sin amplitude", "V"),
            ("master_sin_center_v", "Master Sin center", "V"),
            ("master_cos_amplitude_v", "Master Cos amplitude", "V"),
            ("master_cos_center_v", "Master Cos center", "V"),
            ("master_sin_cos_percent", "Master Sin/Cos", "%"),
            ("slave_sin_amplitude_v", "Slave Sin amplitude", "V"),
            ("slave_sin_center_v", "Slave Sin center", "V"),
            ("slave_cos_amplitude_v", "Slave Cos amplitude", "V"),
            ("slave_cos_center_v", "Slave Cos center", "V"),
            ("slave_sin_cos_percent", "Slave Sin/Cos", "%"),
            ("relative_deviation_deg", "Relative deviation", "°"),
            ("status", "Result status", ""),
        )
        for row, (key, label, unit) in enumerate(specs):
            field = self._read_only_value("-")
            field.setFixedHeight(24)
            field.setFixedWidth(76)
            fields[key] = field
            label_widget = QLabel(label)
            label_widget.setFixedWidth(140)
            card_layout.addWidget(label_widget, row, 0)
            card_layout.addWidget(field, row, 1)
            if unit:
                card_layout.addWidget(QLabel(unit), row, 2)
        card_layout.setColumnStretch(1, 1)
        return card, fields

    calibration_motor_card, calibration_motor_fields = make_calibration_result_card(
        "Motor-side Results"
    )
    calibration_output_card, calibration_output_fields = make_calibration_result_card(
        "Output-side Results"
    )
    self.gw_calibration_fields = {
        "motor": calibration_motor_fields,
        "output": calibration_output_fields,
    }
    calibration_results_layout = QGridLayout()
    calibration_results_layout.setContentsMargins(0, 0, 0, 0)
    calibration_results_layout.setHorizontalSpacing(8)
    calibration_results_layout.addWidget(calibration_motor_card, 0, 0)
    calibration_results_layout.addWidget(calibration_output_card, 0, 1)
    calibration_results_layout.setColumnStretch(0, 1)
    calibration_results_layout.setColumnStretch(1, 1)
    calibration_layout.addWidget(calibration_controls)
    calibration_layout.addWidget(self.gw_calibration_message)
    calibration_layout.addLayout(calibration_results_layout)
    calibration_layout.addStretch(1)

    calibration_page = QScrollArea()
    calibration_page.setWidgetResizable(True)
    calibration_page.setFrameShape(QFrame.NoFrame)
    calibration_page.setWidget(calibration_content)

    self.gw_tabs = QTabWidget()
    self.gw_tabs.setObjectName("GwTabs")
    self.gw_tabs.addTab(calibration_page, "Calibration")
    self.gw_tabs.addTab(gw_live_page, "Live Amplitude")
    self.gw_tabs.setCurrentIndex(0)
    self.gw_tabs.currentChanged.connect(self._clear_completed_result_cards)

    range_header = QWidget()
    range_header_layout = QHBoxLayout(range_header)
    range_header_layout.setContentsMargins(6, 0, 4, 0)
    range_header_layout.setSpacing(8)
    self.amplitude_range_legend = QLabel(self._amplitude_limits.legend())
    self.amplitude_range_legend.setStyleSheet("font-size: 8pt; color: #4B5563;")
    self.amplitude_range_legend.setToolTip(
        "Amplitude limits in mV, shared by motor/output and both tabs.\n"
        "Yellow: lower limit included, upper limit excluded.\n"
        "Green: both limits included. Red: outside these ranges."
    )
    range_header_layout.addWidget(self.amplitude_range_legend)
    self.amplitude_range_button = QPushButton("Amplitude color range…")
    self.amplitude_range_button.setToolTip("Edit amplitude color ranges")
    self.amplitude_range_button.setStyleSheet(
        "QPushButton { min-height: 0; padding: 3px 8px; font-size: 8pt; }"
    )
    self.amplitude_range_button.clicked.connect(self._show_amplitude_range_dialog)
    range_header_layout.addWidget(self.amplitude_range_button)
    self.gw_tabs.setCornerWidget(range_header, Qt.TopRightCorner)

    self.workspace_tabs = QTabWidget()
    self.workspace_tabs.setObjectName("WorkspaceTabs")
    self.workspace_tabs.addTab(fct_page, "FCT PCB")
    self.workspace_tabs.addTab(self.gw_tabs, "GW Encoder")
    self.workspace_tabs.setCurrentIndex(0)
    self.workspace_tabs.currentChanged.connect(self._clear_completed_result_cards)
    test_layout.addWidget(self.workspace_tabs, 1)

    self.console_group = self._group("Console")
    console_layout = QGridLayout(self.console_group)
    self.console_box = QPlainTextEdit()
    self.console_box.setReadOnly(True)
    self.console_box.setObjectName("Console")
    self.log_box = self.console_box
    self.clear_console_button = QPushButton("Clear")
    console_layout.addWidget(self.console_box, 0, 0, 1, 2)
    console_layout.addWidget(self.clear_console_button, 1, 1)

    left_column = QVBoxLayout()
    left_column.setSpacing(8)
    left_column.setContentsMargins(0, 0, 0, 0)
    left_column.addWidget(self.com_group)
    left_column.addWidget(self.rs485_group)
    left_column.addWidget(self.console_group, 1)
    self.left_column_layout = left_column
    left_container = QWidget()
    self.left_container = left_container
    left_container.setObjectName("LeftColumn")
    left_container.setLayout(left_column)
    left_container.setFixedWidth(340)
    page.addWidget(left_container, 0, 0)

    right_column = QVBoxLayout()
    right_column.setSpacing(8)
    right_column.setContentsMargins(0, 0, 0, 0)
    self.right_column_layout = right_column
    right_container = QWidget()
    self.right_container = right_container
    right_container.setObjectName("RightColumn")
    right_container.setLayout(right_column)
    page.addWidget(right_container, 0, 1)

    self.can_group = self._group("CAN Configure")
    can_layout = QGridLayout(self.can_group)
    can_layout.setContentsMargins(8, 12, 8, 8)
    can_layout.setHorizontalSpacing(8)
    can_layout.setVerticalSpacing(4)
    self.data_send = self._read_only_value("")
    self.frame_type = self._read_only_value("Extended Frame")
    self.bitrate = QComboBox()
    for label, value in BITRATE_PRESETS:
        self.bitrate.addItem(label, value)
    self._set_bitrate_value(DEFAULT_BITRATE)
    self.filter_id = self._read_only_value("00000000")
    self.mask_id = self._read_only_value("00000000")
    self.set_and_start_button = QPushButton("Set and Start")
    self.set_and_start_button.setObjectName("SetAndStartButton")
    self.set_and_start_button.setMinimumWidth(112)

    self.config_summary_label = QLabel("Data Send")
    can_layout.addWidget(self.config_summary_label, 0, 0)
    can_layout.addWidget(self.data_send, 0, 1, 1, 3)
    can_layout.addWidget(self.set_and_start_button, 0, 4, 3, 1)
    can_layout.addWidget(QLabel("Type"), 1, 0)
    can_layout.addWidget(self.frame_type, 1, 1)
    can_layout.addWidget(QLabel("Baudrate"), 1, 2)
    can_layout.addWidget(self.bitrate, 1, 3)
    self.filter_start_label = QLabel("Filter ID")
    can_layout.addWidget(self.filter_start_label, 2, 0)
    can_layout.addWidget(self.filter_id, 2, 1)
    self.filter_end_label = QLabel("Mask ID")
    can_layout.addWidget(self.filter_end_label, 2, 2)
    can_layout.addWidget(self.mask_id, 2, 3)
    header_widget = QWidget()
    header_layout = QGridLayout(header_widget)
    header_layout.setContentsMargins(0, 0, 0, 0)
    header_layout.setHorizontalSpacing(8)
    header_layout.setColumnStretch(0, 1)
    header_layout.addWidget(self.can_group, 0, 0)
    header_layout.addWidget(self.status_group, 0, 1)
    self.right_header_widget = header_widget
    self.right_header_layout = header_layout
    right_column.addWidget(header_widget)
    right_column.addWidget(self.test_group, 1)

    self.refresh_ports_button.clicked.connect(self._refresh_ports)
    self.com_toggle_button.clicked.connect(self._toggle_com)
    self.refresh_rs485_ports_button.clicked.connect(self._refresh_rs485_ports)
    self.rs485_toggle_button.clicked.connect(self._toggle_rs485_com)
    self.detect_id_button.clicked.connect(self._run_detect_id)
    self.openloop_button.clicked.connect(self._run_openloop_test)
    self.can_stress_button.clicked.connect(self._run_can_stress_test)
    self.rs485_stress_button.clicked.connect(self._run_rs485_stress_test)
    self.gongwang_button.clicked.connect(self._run_gongwang_test)
    self.gw_live_button.clicked.connect(self._toggle_gw_live)
    self.gw_live_read_500_button.clicked.connect(self._read_500_gw_live_samples)
    self.gw_live_clear_button.clicked.connect(self._clear_gw_live_data)
    self.gw_calibration_button.clicked.connect(self._start_gw_calibration)
    self.gw_calibration_read_button.clicked.connect(self._read_gw_calibration_result)
    self.gw_calibration_clear_button.clicked.connect(self._clear_gw_calibration_data)
    self.clear_console_button.clicked.connect(self.console_box.clear)
    self.set_and_start_button.clicked.connect(self._set_and_start_can)
    self.bitrate.currentIndexChanged.connect(self._update_config_packet)
    self.backend.currentTextChanged.connect(self._backend_changed)
    self._backend_changed(self.backend.currentText())
    self._refresh_rs485_ports()

