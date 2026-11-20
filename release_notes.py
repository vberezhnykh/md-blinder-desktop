"""Описание релиза: раздел версии из CHANGELOG.md и строка об установке.

Запуск: python release_notes.py <версия> <файл>. Вызывается из workflow «Desktop».
"""

import re
import sys
from pathlib import Path

INSTALL = (
    "Настольная версия MD Blinder {version} для Windows. Обновление ставится из программы; "
    "первая установка — распаковать архив в свою папку и запустить MD Blinder.exe."
)


def release_notes(version: str, changelog: str) -> str:
    match = re.search(rf"^## {re.escape(version)}\b[^\n]*\n(.*?)(?=^## |\Z)", changelog, re.M | re.S)
    changes = match.group(1).strip() if match else ""
    install = INSTALL.format(version=version)
    return f"{changes}\n\n{install}\n" if changes else f"{install}\n"


if __name__ == "__main__":
    version, out = sys.argv[1], sys.argv[2]
    path = Path(__file__).with_name("CHANGELOG.md")
    changelog = path.read_text(encoding="utf-8") if path.exists() else ""
    Path(out).write_text(release_notes(version, changelog), encoding="utf-8")
