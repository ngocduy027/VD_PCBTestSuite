"""Operator-configurable amplitude colour thresholds and their settings dialog."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
)


@dataclass(frozen=True)
class AmplitudeRange:
    """Colour thresholds (mV) for encoder amplitude fields.

    Below ``yellow_start`` or above ``green_end`` is red, ``yellow_start`` up to
    ``green_start`` is yellow, and ``green_start..green_end`` is green. Persisted in
    QSettings under ``encoder/amplitude/*``.
    """

    yellow_start: float = 600.0
    green_start: float = 1000.0
    green_end: float = 1800.0

    def __post_init__(self):
        values = (self.yellow_start, self.green_start, self.green_end)
        if not all(isfinite(v) for v in values) or not (
            0 <= self.yellow_start < self.green_start < self.green_end <= 1_000_000
        ):
            raise ValueError("Use 0 ≤ yellow start < green start < green end ≤ 1,000,000 mV.")

    @classmethod
    def load(cls, settings):
        """Read limits from QSettings, falling back to defaults on missing or invalid values."""
        if settings is None:
            return cls()
        try:
            return cls(*(float(settings.value(f"encoder/amplitude/{key}", default))
                         for key, default in vars(cls()).items()))
        except (ValueError, TypeError, OverflowError):
            return cls()

    def save(self, settings):
        """Write limits to QSettings (no-op without settings)."""
        if settings is not None:
            for key, value in vars(self).items():
                settings.setValue(f"encoder/amplitude/{key}", value)

    def legend(self):
        """Rich-text colour legend shown under the amplitude fields."""
        low, start, end = self.yellow_start, self.green_start, self.green_end
        return (f'<span style="color:#C43D32">●</span> &lt;{low:g}'
                f' &nbsp; <span style="color:#A16207">●</span> {low:g}–&lt;{start:g}'
                f' &nbsp; <span style="color:#16803C">●</span> {start:g}–{end:g}'
                f' &nbsp; <span style="color:#C43D32">●</span> &gt;{end:g} mV')


class AmplitudeRangeDialog(QDialog):
    """Dialog to edit the thresholds; emits ``range_applied`` with a validated :class:`AmplitudeRange`."""

    range_applied = Signal(object)

    def __init__(self, limits: AmplitudeRange, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Amplitude color range")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        subtitle = QLabel("Amplitude limits · mV")
        subtitle.setStyleSheet("font-weight: 600; color: #374151;")
        layout.addWidget(subtitle)
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(4)
        self.fields = []
        for column, (label, value) in enumerate(zip(
            ("Yellow from", "Green from", "Green to"), vars(limits).values(), strict=True,
        )):
            field = QDoubleSpinBox()
            field.setDecimals(2)
            field.setRange(0, 1_000_000)
            field.setValue(value)
            field.setFixedWidth(122)
            field.setMinimumHeight(28)
            caption = QLabel(label)
            caption.setStyleSheet("color: #854D0E;" if column == 0 else "color: #166534;")
            form.addWidget(caption, 0, column)
            form.addWidget(field, 1, column)
            self.fields.append(field)
        layout.addLayout(form)
        note = QLabel("Red below Yellow from or above Green to.\nApplies to both encoders and both tabs.")
        note.setStyleSheet("font-size: 8pt; color: #6B7280;")
        layout.addWidget(note)
        self.error = QLabel()
        self.error.setStyleSheet("color: #8A1C13;")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Apply | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults
        )
        self.buttons.button(QDialogButtonBox.RestoreDefaults).setText("Defaults")
        self.buttons.button(QDialogButtonBox.Apply).setDefault(True)
        self.buttons.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        self.buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._restore)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _restore(self):
        for field, value in zip(self.fields, vars(AmplitudeRange()).values(), strict=True):
            field.setValue(value)
        self.error.clear()
        self.error.hide()

    def _apply(self):
        try:
            limits = AmplitudeRange(*(field.value() for field in self.fields))
        except ValueError as error:
            self.error.setText(str(error))
            self.error.show()
            return
        self.range_applied.emit(limits)
        self.accept()
