from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from egorus_monitor.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("EgoRUS Predictive Monitor")
    app.setOrganizationName("EgoRUS")

    window = MainWindow()
    window.resize(1440, 900)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
