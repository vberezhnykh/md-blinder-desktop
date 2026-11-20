"""Каталоги данных приложения.

Windows: %LOCALAPPDATA%\\md-blinder; иначе ~/.local/share/md-blinder (или
$XDG_DATA_HOME/md-blinder). Переменная MD_BLINDER_DATA_DIR переопределяет корень —
для тестов и переносной установки. Функции только вычисляют пути; каталоги
создает тот, кто пишет в них.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "md-blinder"
DATA_DIR_ENV = "MD_BLINDER_DATA_DIR"


def data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return root / APP_NAME


def models_dir() -> Path:
    return data_dir() / "models"


def history_db_path() -> Path:
    return data_dir() / "history.sqlite"
