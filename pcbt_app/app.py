"""GUI entry point (``pcbt-gui`` / ``python -m pcbt_app.app``)."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtWidgets import QApplication

from pcbt_app import APP_ORGANIZATION, APP_TITLE, __version__
from pcbt_app.ui.main_window import MainWindow

# QSettings key under which adapter selections and amplitude limits persist.
# Kept version-independent so upgrades retain the operator's choices.
SETTINGS_APPLICATION = "VD_PCBTestSuite"
LOG_FILE_NAME = "pcbt_app.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 5


def configure_logging() -> Path | None:
    """Send diagnostics to a rotating file; the frozen GUI has no console.

    Returns the log file path, or ``None`` if the log directory is unwritable
    (logging then falls back to stderr only).
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    if sys.stderr is not None:  # None in the windowed (console=False) exe
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)

    log_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / LOG_FILE_NAME, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
        )
    except OSError:
        logging.getLogger(__name__).warning("Cannot write log files to %s", log_dir, exc_info=True)
        return None
    handler.setFormatter(formatter)
    root.addHandler(handler)
    return log_dir / LOG_FILE_NAME


def main() -> int:
    """Create the Qt application, configure logging and run the main window until it closes."""
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_ORGANIZATION)
    app.setApplicationName(APP_TITLE)
    app.setApplicationDisplayName(APP_TITLE)
    app.setApplicationVersion(__version__)
    log_path = configure_logging()
    logging.getLogger(__name__).info("Starting %s %s (log: %s)", APP_TITLE, __version__, log_path)
    window = MainWindow(settings=QSettings(APP_ORGANIZATION, SETTINGS_APPLICATION))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
