"""Папки с документами: сбор файлов из вложенных папок и выгрузка пакета
в папку с той же структурой."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable

SUPPORTED_SUFFIXES = (".docx", ".xlsx", ".pdf")
LEGACY_SUFFIXES = (".doc", ".xls")

# Символы, которые Windows не допускает в именах файлов и папок.
_INVALID_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_RESERVED_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


@dataclass
class CollectedFiles:
    """Файлы для обработки и их имена в истории.

    Имя файла из папки — путь от самой папки, включая ее имя, через «/»:
    «Клиент/2024/договор.docx». По нему пакет выгружается в папку с той же
    структурой. Файл, выбранный отдельно, сохраняет просто свое имя."""

    paths: list[Path] = field(default_factory=list)
    names: dict[Path, str] = field(default_factory=dict)
    folders: list[Path] = field(default_factory=list)
    legacy: int = 0  # пропущенные .doc и .xls из папок

    def name(self, path: Path) -> str:
        return self.names.get(path, path.name)


def _skipped(name: str) -> bool:
    # Скрытые файлы и папки, а также временные файлы Office («~$договор.docx»).
    return name.startswith((".", "~$"))


def _sort_key(rel: PurePosixPath) -> tuple:
    # Файлы папки идут перед ее подпапками, внутри уровня — по алфавиту без учета регистра.
    return tuple((i < len(rel.parts) - 1, part.casefold()) for i, part in enumerate(rel.parts))


def _walk(folder: Path) -> tuple[list[PurePosixPath], int]:
    found: list[PurePosixPath] = []
    legacy = 0
    for root, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = [d for d in dirs if not _skipped(d)]
        base = PurePosixPath(*Path(root).relative_to(folder).parts)
        for name in files:
            if _skipped(name):
                continue
            suffix = Path(name).suffix.lower()
            if suffix in SUPPORTED_SUFFIXES:
                found.append(base / name)
            elif suffix in LEGACY_SUFFIXES:
                legacy += 1
    found.sort(key=_sort_key)
    return found, legacy


def collect_files(paths: Iterable[Path]) -> CollectedFiles:
    """Раскрывает папки в файлы .docx, .xlsx и .pdf на любой глубине вложенности.
    Файлы, выбранные напрямую, проходят как есть, даже с другим расширением:
    их тип и ошибку определит обработка. Повторы убираются."""
    result = CollectedFiles()
    seen: set[Path] = set()

    def add(path: Path, name: str) -> None:
        key = Path(os.path.normcase(os.path.abspath(path)))
        if key in seen:
            return
        seen.add(key)
        result.paths.append(path)
        result.names[path] = name

    for path in paths:
        path = Path(path)
        if path.is_dir():
            result.folders.append(path)
            found, legacy = _walk(path)
            result.legacy += legacy
            top = path.resolve().name or path.anchor.strip("\\/:") or "folder"
            for rel in found:
                add(path.joinpath(*rel.parts), str(PurePosixPath(top) / rel))
        else:
            add(path, path.name)
    return result


def _clean_part(part: str) -> str:
    part = _INVALID_CHARS.sub("_", part).strip().rstrip(".")
    if not part or part in (".", ".."):
        return ""
    if part.split(".")[0].casefold() in _RESERVED_NAMES:
        part = f"_{part}"
    return part


def export_relative_path(name: str) -> PurePosixPath:
    """Относительный путь .md для документа по его имени в истории. Имя может
    прийти из импорта истории, поэтому путь не выходит за пределы выбранной папки:
    «..», корень диска и запрещенные в Windows символы убираются."""
    parts = [p for p in (_clean_part(x) for x in re.split(r"[\\/]+", name)) if p]
    if not parts:
        parts = ["document"]
    *dirs, file_name = parts
    stem = Path(file_name).stem.strip().rstrip(".") if Path(file_name).suffix else file_name
    return PurePosixPath(*dirs, f"{stem or 'document'}.md")


def export_plan(names: list[str]) -> list[PurePosixPath]:
    """Пути .md для документов пакета в том же порядке. Одинаковые пути (например,
    «акт.docx» и «акт.pdf» в одной папке) получают номер: «акт (2).md»."""
    used: set[str] = set()
    plan: list[PurePosixPath] = []
    for name in names:
        rel = export_relative_path(name)
        candidate, n = rel, 1
        while str(candidate).casefold() in used:
            n += 1
            candidate = rel.with_name(f"{rel.stem} ({n}){rel.suffix}")
        used.add(str(candidate).casefold())
        plan.append(candidate)
    return plan
