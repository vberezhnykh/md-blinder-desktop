"""Иконка программы для окон и панели задач.

Рисунки лежат в assets/icon и собираются скриптом scripts/make_icon.py; app.ico оттуда
же PyInstaller вшивает в MD Blinder.exe, поэтому ярлыки и пункт меню проводника
показывают ту же иконку.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon

ICON_DIR = Path(__file__).parent / "assets" / "icon"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
APP_USER_MODEL_ID = "md-blinder.desktop"


def app_icon() -> QIcon:
    icon = QIcon()
    for size in SIZES:
        icon.addFile(str(ICON_DIR / f"app-{size}.png"))
    return icon


def set_taskbar_identity() -> None:
    """При запуске из Python (run.bat) Windows группирует окно с python.exe и берет его иконку.

    Собственный идентификатор отделяет программу от Python. Собранному exe он не нужен:
    там иконка вшита в exe, а идентификатор отделил бы окно от ярлыка, закрепленного
    на панели задач.
    """
    if sys.platform != "win32" or getattr(sys, "frozen", False):
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def refresh_shell_icons() -> None:
    """После обновления просит проводник перечитать иконки: иначе ярлыки до перезагрузки показывают старую."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        shcne_assocchanged, shcnf_idlist = 0x08000000, 0
        ctypes.windll.shell32.SHChangeNotify(shcne_assocchanged, shcnf_idlist, None, None)
    except (AttributeError, OSError):
        pass
