"""Пункт «Анонимизировать в MD Blinder» в контекстном меню проводника.

Пункт прописывается в реестре текущего пользователя
(HKCU\\Software\\Classes\\SystemFileAssociations\\<расширение>\\shell), поэтому
права администратора не нужны, а ассоциации файлов с Word, Excel и
просмотрщиком PDF не меняются. Команда запускает программу с путем к файлу.
Тот же пункт есть у папок (HKCU\\Software\\Classes\\Directory\\shell): папка
со всеми вложенными папками обрабатывается пакетом.
В Windows 11 пункт стоит в разделе «Показать дополнительные параметры»
(Shift+F10): в сокращенное меню попадают только программы, установленные
пакетом MSIX.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence

EXTENSIONS = (".docx", ".xlsx", ".pdf")
VERB = "MDBlinder"
LABEL = "Анонимизировать в MD Blinder"


def available() -> bool:
    """Регистрировать имеет смысл только собранную программу под Windows: у нее постоянный путь к exe."""
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


FOLDER_KEY = rf"Software\Classes\Directory\shell\{VERB}"


def _key(ext: str) -> str:
    return rf"Software\Classes\SystemFileAssociations\{ext}\shell\{VERB}"


def _keys() -> list[str]:
    return [*(_key(ext) for ext in EXTENSIONS), FOLDER_KEY]


def command_line(exe: Path) -> str:
    return f'"{exe}" "%1"'


def register(exe: Path, reg=None) -> None:
    """Создает или обновляет пункт: после переноса папки программы путь в команде меняется."""
    reg = reg or _winreg()
    for base in _keys():
        with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, base, 0, reg.KEY_WRITE) as key:
            reg.SetValueEx(key, None, 0, reg.REG_SZ, LABEL)
            reg.SetValueEx(key, "Icon", 0, reg.REG_SZ, f'"{exe}",0')
            # При выборе нескольких файлов пункт не показывается: командная
            # строка передает один путь. Несколько файлов — это папка или перетаскивание.
            reg.SetValueEx(key, "MultiSelectModel", 0, reg.REG_SZ, "Single")
        with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, base + r"\command", 0, reg.KEY_WRITE) as key:
            reg.SetValueEx(key, None, 0, reg.REG_SZ, command_line(exe))


def unregister(reg=None) -> None:
    reg = reg or _winreg()
    for base in _keys():
        for sub in (base + r"\command", base):
            try:
                reg.DeleteKey(reg.HKEY_CURRENT_USER, sub)
            except FileNotFoundError:
                pass


def sync(enabled: bool) -> Optional[str]:
    """Приводит реестр к настройке. Возвращает текст ошибки или None."""
    if not available():
        return None
    try:
        if enabled:
            register(Path(sys.executable))
        else:
            unregister()
    except OSError as exc:
        return f"Не удалось изменить пункт в меню проводника: {exc}"
    return None


def file_argument(argv: Sequence[str]) -> Optional[Path]:
    """Путь из командной строки: первый аргумент, который не ключ и указывает на файл или папку."""
    for arg in argv[1:]:
        if arg.startswith("--"):
            continue
        path = Path(arg)
        if path.is_file() or path.is_dir():
            return path
    return None


def _winreg():
    import winreg

    return winreg
