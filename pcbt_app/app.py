from __future__ import annotations

import sys
from PySide6.QtCore import QSettings

from PySide6.QtWidgets import QApplication

from pcbt_app.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("VD_PCBTestSuite_V1.2")
    app.setApplicationDisplayName("VD_PCBTestSuite_V1.2")
    window = MainWindow(settings=QSettings("VinD", "VD_PCBTestSuite"))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
