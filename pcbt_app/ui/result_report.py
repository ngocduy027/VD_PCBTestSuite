"""Result cards: per-test PASS/FAIL panels and the metric rows they display."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pcbt_app.protocol.fct_can import fct_state_label
from pcbt_app.protocol.mit_firmware import status_label

RESULT_CARD_SPECS: dict[str, dict[str, Any]] = {
    "openloop": {
        "title": "Open-loop",
        "main": (("fct_state", "State"), ("fct_pass_count", "Pass"), ("fct_fail_count", "Fail")),
        "details": (
            ("returned_driver_id", "Driver ID"),
            ("duration", "Duration"),
            ("theta_step", "Theta step"),
            ("elapsed", "Elapsed"),
            ("reason", "Reason"),
        ),
    },
    "can_stress": {
        "title": "CAN Stress",
        "main": (
            ("tx_attempt_count", "Requests sent"),
            ("fct_pass_count", "Firmware valid"),
            ("fct_fail_count", "Firmware failed"),
        ),
        "details": (
            ("returned_driver_id", "Driver ID"),
            ("actual_response_count", "App responses"),
            ("success_rate", "Success rate"),
            ("receive_rate", "Response rate"),
            ("interval", "Interval"),
            ("elapsed", "Elapsed"),
            ("reason", "Reason"),
        ),
    },
    "rs485_stress": {
        "title": "RS485",
        "main": (
            ("received_message_count", "PC valid"),
            ("fct_pass_count", "Firmware sent"),
            ("fct_fail_count", "Firmware failed"),
        ),
        "details": (
            ("returned_driver_id", "Driver ID"),
            ("fct_state", "Firmware state"),
            ("duration", "Requested duration"),
            ("period", "Period"),
            ("requested_frequency", "Requested rate"),
            ("malformed_count", "Malformed data"),
            ("sequence_error_count", "Sequence errors"),
            ("first_sequence", "First sequence"),
            ("last_sequence", "Last sequence"),
            ("received_byte_count", "Received bytes"),
            ("elapsed", "Elapsed"),
            ("reason", "Reason"),
        ),
    },
    "gongwang": {
        "title": "Encoder Reading",
        "main": (("fct_state", "State"), ("fct_pass_count", "Valid"), ("fct_fail_count", "Errors")),
        "details": (
            ("returned_driver_id", "Driver ID"),
            ("period", "Period"),
            ("duration", "Duration"),
            ("success_rate", "Success rate"),
            ("elapsed", "Elapsed"),
            ("reason", "Reason"),
        ),
    },
}


CARD_STATUS_STYLES = {
    "not_run": ("NOT RUN", "#E5E7EB", "#4B5563"),
    "running": ("RUNNING", "#D18A1D", "#FFFFFF"),
    "pass": ("PASS", "#36A853", "#FFFFFF"),
    "fail": ("FAIL", "#E03616", "#FFFFFF"),
}


def value_label(text: str) -> QLabel:
    """Selectable, word-wrapped label used for result values."""
    label = QLabel(text)
    label.setObjectName("ValueLabel")
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setWordWrap(True)
    label.setMinimumHeight(18)
    label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    return label


def _set_card_status(card: dict, state: str) -> None:
    text, background, color = CARD_STATUS_STYLES[state]
    status = card["status"]
    status.setText(text)
    status.setStyleSheet(
        f"background: {background}; color: {color}; border-radius: 4px; "
        "padding: 2px 9px; font-weight: 800;"
    )


def make_result_card(test_key: str) -> dict:
    """Build the result card widget for ``test_key`` from ``RESULT_CARD_SPECS``; returns a dict of its widgets."""
    spec = RESULT_CARD_SPECS[test_key]
    card = QFrame()
    card.setObjectName("ResultCard")
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    card.setMinimumWidth(0)
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(8, 7, 8, 7)
    card_layout.setSpacing(5)

    header = QHBoxLayout()
    title = QLabel(spec["title"])
    title.setObjectName("ResultCardTitle")
    status = QLabel()
    status.setObjectName("ResultCardStatus")
    status.setAlignment(Qt.AlignCenter)
    header.addWidget(title)
    header.addStretch(1)
    header.addWidget(status)
    card_layout.addLayout(header)

    fields: dict[str, QLabel] = {}
    metrics = QGridLayout()
    metrics.setContentsMargins(0, 0, 0, 0)
    metrics.setHorizontalSpacing(6)
    for column, (key, label_text) in enumerate(spec["main"]):
        cell = QWidget()
        cell.setMinimumWidth(0)
        cell.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        cell_layout = QVBoxLayout(cell)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        cell_layout.setSpacing(0)
        label = QLabel(label_text)
        label.setObjectName("ResultMetricLabel")
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        value = value_label("-")
        value.setObjectName("ResultMetricValue")
        value.setAlignment(Qt.AlignCenter)
        value.setMinimumWidth(0)
        fields[key] = value
        cell_layout.addWidget(label)
        cell_layout.addWidget(value)
        metrics.addWidget(cell, 0, column)
        metrics.setColumnStretch(column, 1)
    card_layout.addLayout(metrics)

    failure = QLabel()
    failure.setObjectName("ResultFailure")
    failure.setWordWrap(True)
    failure.setVisible(False)
    card_layout.addWidget(failure)

    details_button = QToolButton()
    details_button.setObjectName("DetailsButton")
    details_button.setText("Details")
    details_button.setCheckable(True)
    details_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    details_button.setArrowType(Qt.RightArrow)
    card_layout.addWidget(details_button)

    details_widget = QWidget()
    details = QGridLayout(details_widget)
    details.setContentsMargins(8, 0, 0, 0)
    details.setHorizontalSpacing(8)
    details.setVerticalSpacing(1)
    details.setColumnStretch(1, 1)
    for row, (key, label_text) in enumerate(spec["details"]):
        label = QLabel(label_text)
        label.setObjectName("ResultDetailLabel")
        value = value_label("-")
        fields[key] = value
        details.addWidget(label, row, 0)
        details.addWidget(value, row, 1)
    details_widget.setVisible(False)
    card_layout.addWidget(details_widget)

    def toggle_details(visible: bool) -> None:
        details_widget.setVisible(visible)
        details_button.setArrowType(Qt.DownArrow if visible else Qt.RightArrow)

    details_button.toggled.connect(toggle_details)
    result = {
        "widget": card,
        "status": status,
        "fields": fields,
        "failure": failure,
        "details_button": details_button,
        "details_widget": details_widget,
    }
    _set_card_status(result, "not_run")
    return result


def set_result_card_running(card: dict) -> None:
    """Show RUNNING and clear previous values."""
    _set_card_status(card, "running")
    for field in card["fields"].values():
        field.setText("-")
    card["failure"].clear()
    card["failure"].setVisible(False)
    card["details_button"].setChecked(False)


def reset_result_card(card: dict) -> None:
    """Return the card to its idle state."""
    _set_card_status(card, "not_run")
    for field in card["fields"].values():
        field.setText("-")
    card["failure"].clear()
    card["failure"].setVisible(False)
    card["details_button"].setChecked(False)


def set_result_card_error(card: dict, message: str) -> None:
    """Show an exception message on the card."""
    _set_card_status(card, "fail")
    card["failure"].setText(message)
    card["failure"].setVisible(True)
    if "reason" in card["fields"]:
        card["fields"]["reason"].setText(message)


def _optional_int(value: int | None) -> str:
    return "-" if value is None else str(value)


def set_result_card(
    card: dict,
    verdict,
    run_inputs: Mapping[str, Any],
    format_driver_id: Callable[[int | None], str],
) -> None:
    """Fill the card from a verdict plus the inputs the test was started with."""
    _set_card_status(card, "pass" if verdict.passed else "fail")
    fct_state = (
        fct_state_label(verdict.fct_state)
        if verdict.fct_state is not None
        else status_label(verdict.firmware_status)
    )
    values = {
        "fct_state": fct_state,
        "fct_pass_count": _optional_int(verdict.fct_pass_count),
        "fct_fail_count": _optional_int(verdict.fct_fail_count),
        "tx_attempt_count": _optional_int(verdict.tx_attempt_count),
        "received_message_count": _optional_int(verdict.received_message_count),
        "actual_response_count": _optional_int(verdict.actual_response_count),
        "expected_message_count": _optional_int(verdict.expected_message_count),
        "missing_message_count": _optional_int(verdict.missing_message_count),
        "success_rate": "-" if verdict.success_rate_percent is None else f"{verdict.success_rate_percent:.1f}%",
        "receive_rate": "-" if verdict.receive_rate_hz is None else f"{verdict.receive_rate_hz:.1f} msg/s",
        "received_byte_count": _optional_int(verdict.received_byte_count),
        "bytes_per_message": _optional_int(verdict.bytes_per_message),
        "requested_frequency": "-" if verdict.requested_frequency_hz is None else f"{verdict.requested_frequency_hz:.1f} frame/s",
        "malformed_count": _optional_int(verdict.malformed_count),
        "sequence_error_count": _optional_int(verdict.sequence_error_count),
        "first_sequence": "-" if verdict.first_sequence is None else f"0x{verdict.first_sequence:06X}",
        "last_sequence": "-" if verdict.last_sequence is None else f"0x{verdict.last_sequence:06X}",
        "returned_driver_id": format_driver_id(verdict.returned_driver_id),
        "duration": f"{run_inputs['duration_ms']} ms" if "duration_ms" in run_inputs else "-",
        "theta_step": f"{float(run_inputs['theta_step']):.5f} rad/tick" if "theta_step" in run_inputs else "-",
        "interval": f"{run_inputs['interval_ms']} ms" if "interval_ms" in run_inputs else "-",
        "period": f"{run_inputs['period_ticks']} ticks" if "period_ticks" in run_inputs else "-",
        "elapsed": "-" if verdict.elapsed_ms is None else f"{verdict.elapsed_ms} ms",
        "reason": verdict.reason,
    }
    for key, field in card["fields"].items():
        field.setText(str(values.get(key, "-")))
    card["failure"].setText(verdict.reason)
    card["failure"].setVisible(not verdict.passed)


GONGWANG_ALMC_FAULT_BITS: tuple[tuple[int, str], ...] = (
    (4, "Cảnh báo khe hở phía động cơ - khe hở quá xa hoặc quá gần"),
    (5, "Cảnh báo khe hở phía hộp giảm tốc - khe hở quá xa hoặc quá gần"),
)


def gongwang_fault_log_lines(fault_code: int | None) -> tuple[str, ...]:
    """Console lines (Vietnamese) explaining each set bit of the encoder ALMC fault code."""
    if fault_code is None:
        return ()
    lines = [f"Mã lỗi ALMC bộ mã hóa: {fault_code} (0b{fault_code:08b})"]
    if fault_code == 0:
        lines.append("Bộ mã hóa không có lỗi")
        return tuple(lines)
    known_mask = 0
    for bit, description in GONGWANG_ALMC_FAULT_BITS:
        mask = 1 << bit
        known_mask |= mask
        if fault_code & mask:
            lines.append(f"Lỗi bộ mã hóa bit {bit} (0x{mask:02X}): {description}")
    reserved_bits = fault_code & ~known_mask
    if reserved_bits:
        lines.append(f"ALMC bộ mã hóa có bit dự phòng/không xác định: 0x{reserved_bits:X}")
    return tuple(lines)
