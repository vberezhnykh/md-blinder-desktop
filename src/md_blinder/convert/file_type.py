"""Порт src/lib/fileType.ts: определение типа файла по сигнатуре и имени.

XLSX есть только в настольной версии."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

FileKind = Literal["docx", "xlsx", "pdf", "legacy-doc", "legacy-xls", "unknown"]


def detect_file_kind(path: str | Path) -> FileKind:
    path = Path(path)
    name = path.name.lower()
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(b"%PDF-") or name.endswith(".pdf"):
        return "pdf"
    if head.startswith(b"PK\x03\x04") and name.endswith(".docx"):
        return "docx"
    if head.startswith(b"PK\x03\x04") and name.endswith(".xlsx"):
        return "xlsx"
    if name.endswith(".doc"):
        return "legacy-doc"
    if name.endswith(".xls"):
        return "legacy-xls"
    if name.endswith(".docx"):
        return "docx"
    if name.endswith(".xlsx"):
        # Не ZIP: обычно книга, защищенная паролем. Ошибку объяснит обработка.
        return "xlsx"
    return "unknown"
