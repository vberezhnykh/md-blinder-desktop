"""Порт src/lib/fileType.ts: определение типа файла по сигнатуре и имени."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

FileKind = Literal["docx", "pdf", "legacy-doc", "unknown"]


def detect_file_kind(path: str | Path) -> FileKind:
    path = Path(path)
    name = path.name.lower()
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(b"%PDF-") or name.endswith(".pdf"):
        return "pdf"
    if head.startswith(b"PK\x03\x04") and name.endswith(".docx"):
        return "docx"
    if name.endswith(".doc"):
        return "legacy-doc"
    if name.endswith(".docx"):
        return "docx"
    return "unknown"
