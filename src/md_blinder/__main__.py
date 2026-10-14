"""Точка входа: python -m md_blinder."""

from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow
    from .ui.theme import STYLESHEET

    app = QApplication(sys.argv)
    app.setApplicationName("MD Blinder")
    app.setOrganizationName("md-blinder")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
