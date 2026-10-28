"""Qt stylesheets and colour tables for the main window."""

from __future__ import annotations

from pcbt_app.ui.amplitude_range import AmplitudeRange

DEFAULT_AMPLITUDE_RANGE = AmplitudeRange()

BANNER_STYLES: dict[str, tuple[str, str, str]] = {
    "idle": ("IDLE", "#D7DEE2", "#2F3E46"),
    "running": ("RUNNING", "#D18A1D", "#FFFFFF"),
    "pass": ("PASS", "#36A853", "#FFFFFF"),
    "fail": ("FAIL", "#E03616", "#FFFFFF"),
}

AMPLITUDE_COLORS = {
    "neutral": ("#F3F4F6", "#4B5563"),
    "red": ("#FDE8E5", "#8A1C13"),
    "yellow": ("#FFF4CC", "#6B4E00"),
    "green": ("#DCFCE7", "#166534"),
}


def amplitude_value_stylesheet(
    value_mv: float | None, limits: AmplitudeRange = DEFAULT_AMPLITUDE_RANGE
) -> str:
    """Stylesheet colouring an amplitude field by ``limits``; neutral when the value is ``None``."""
    if value_mv is None:
        background, color = AMPLITUDE_COLORS["neutral"]
    elif value_mv < limits.yellow_start or value_mv > limits.green_end:
        background, color = AMPLITUDE_COLORS["red"]
    elif value_mv < limits.green_start:
        background, color = AMPLITUDE_COLORS["yellow"]
    else:
        background, color = AMPLITUDE_COLORS["green"]
    return f"background: {background}; color: {color}; border: 1px solid #D4D4D4;"


def main_window_stylesheet() -> str:
    """Application-wide Qt stylesheet."""
    return """
            QWidget#AppRoot {
                background: #ECEFF1;
                color: #111827;
                font-family: "Segoe UI";
                font-size: 9.5pt;
            }
            QGroupBox#ClassicGroup {
                background: #F7F7F7;
                border: 1px solid #5E5E5E;
                border-radius: 5px;
                margin-top: 10px;
                padding: 6px 6px 6px 6px;
                font-size: 10.5pt;
            }
            QGroupBox#ClassicGroup::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 0 4px;
                color: #111827;
                background: #ECEFF1;
            }
            QLabel {
                color: #111827;
            }
            QComboBox, QLineEdit {
                min-height: 24px;
                border: 1px solid #D4D4D4;
                border-radius: 5px;
                padding: 1px 6px;
                background: #FFFFFF;
            }
            QLineEdit#ReadOnlyField, QLabel#ValueLabel, QPlainTextEdit#Console {
                font-family: Consolas, "Cascadia Mono", monospace;
            }
            QLineEdit#ReadOnlyField {
                color: #4B5563;
                background: #F3F4F6;
            }
            QPushButton {
                min-height: 24px;
                border: 1px solid #9CA3AF;
                border-radius: 5px;
                background: #DCDCDC;
                padding: 2px 8px;
                color: #111827;
            }
            QPushButton:hover:enabled {
                background: #D0D0D0;
            }
            QPushButton:disabled {
                color: #7A7A7A;
                background: #ECECEC;
            }
            QPushButton#OpenButton {
                background: #49B653;
                color: #FFFFFF;
                font-weight: 700;
            }
            QPushButton#OpenButton:hover:enabled {
                background: #3EA147;
            }
            QPushButton#CloseButton {
                background: #F3E5E3;
                color: #7D2B22;
            }
            QPushButton#SetAndStartButton {
                min-height: 52px;
                font-size: 11pt;
                color: #5F6368;
            }
            QPushButton#MotorButton {
                min-height: 24px;
            }
            QLabel#ResultBanner {
                min-height: 44px;
                border-radius: 5px;
                font-size: 18pt;
                font-weight: 800;
                letter-spacing: 0px;
            }
            QLabel#ReportHeader {
                color: #111827;
                font-size: 9pt;
                font-weight: 800;
            }
            QLabel#WorkspaceLabel {
                color: #6B7280;
                font-size: 8.5pt;
                font-weight: 600;
            }
            QLabel#ValueLabel {
                min-height: 18px;
                font-size: 8.5pt;
            }
            QPlainTextEdit#Console {
                background: #FFFFFF;
                color: #111827;
                border: 1px solid #9CA3AF;
                border-radius: 3px;
                font-size: 9pt;
            }
            QFrame#TestCard {
                background: #F7F7F7;
                border: 1px solid #D4D4D4;
                border-radius: 4px;
            }
            QFrame#ResultCard {
                background: #FFFFFF;
                border: 1px solid #C7CDD1;
                border-radius: 6px;
            }
            QLabel#ResultCardTitle {
                font-size: 10.5pt;
                font-weight: 800;
                color: #1F2937;
            }
            QLabel#ResultCardStatus {
                min-width: 64px;
                min-height: 20px;
            }
            QLabel#ResultMetricLabel, QLabel#ResultDetailLabel {
                color: #6B7280;
                font-size: 8pt;
                font-weight: 600;
            }
            QLabel#ResultMetricValue {
                min-height: 22px;
                color: #111827;
                font-family: Consolas, "Cascadia Mono", monospace;
                font-size: 10pt;
                font-weight: 700;
            }
            QLabel#ResultFailure {
                padding: 4px 6px;
                border-radius: 4px;
                background: #FDE8E5;
                color: #A1261D;
                font-size: 8.5pt;
            }
            QLabel#CalibrationMessage {
                min-height: 24px;
                padding: 4px 7px;
                border-radius: 4px;
                background: #EEF2F4;
                color: #374151;
            }
            QTabWidget#TestTabs::pane, QTabWidget#WorkspaceTabs::pane, QTabWidget#GwTabs::pane {
                border: 1px solid #C7CDD1;
                border-radius: 4px;
                background: #FFFFFF;
                top: -1px;
            }
            QTabWidget#TestTabs QTabBar::tab, QTabWidget#WorkspaceTabs QTabBar::tab, QTabWidget#GwTabs QTabBar::tab {
                min-width: 58px;
                min-height: 26px;
                padding: 2px 8px;
                border: 1px solid #C7CDD1;
                border-bottom: none;
                background: #E5E7EB;
                color: #374151;
            }
            QTabWidget#TestTabs QTabBar::tab:selected, QTabWidget#WorkspaceTabs QTabBar::tab:selected, QTabWidget#GwTabs QTabBar::tab:selected {
                background: #FFFFFF;
                color: #111827;
                font-weight: 700;
            }
            QToolButton#DetailsButton {
                min-height: 24px;
                margin-top: 4px;
                border: none;
                color: #374151;
                font-weight: 700;
                text-align: left;
            }
            QToolButton#DetailsButton:hover {
                color: #111827;
                background: #E5E7EB;
                border-radius: 4px;
            }
            QScrollArea#TestControlsScroll, QScrollArea#ResultsScroll {
                background: transparent;
                border: none;
            }
            """


def banner_stylesheet(background: str, color: str) -> str:
    """Stylesheet for the overall PASS/FAIL/RUNNING banner."""
    return (
        f"background: {background}; color: {color}; min-height: 44px; border-radius: 5px; "
        "font-size: 18pt; font-weight: 800; letter-spacing: 0px;"
    )
