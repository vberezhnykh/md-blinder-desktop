"""Таблица замен в CSV (порт src/lib/anonymize/csv.ts и экспорта из App.tsx)."""

from __future__ import annotations

import re

from .apply import entity_current_value
from .types import Entity

_FORMULA_START_RE = re.compile(r"[=+\-@\t\r]")
_NEEDS_QUOTES_RE = re.compile(r"[\"\n\r,]")
_SURROGATE_RE = re.compile("[\ud800-\udfff]")

CSV_HEADER = ("Плейсхолдер", "Тип", "Оригинальное значение", "Вхождений")


def _csv_escape(field: str) -> str:
    f = field
    # Защита от формул: Excel исполняет ячейки, начинающиеся с = + - @ TAB CR.
    if _FORMULA_START_RE.match(f):
        f = "'" + f
    if _NEEDS_QUOTES_RE.search(f):
        return '"' + f.replace('"', '""') + '"'
    return f


def entities_to_csv(entities: list[Entity], source_text: str) -> str:
    """CSV активных замен: плейсхолдер, тип, текущее исходное значение, число вхождений."""
    rows: list[list[str]] = [list(CSV_HEADER)]
    for e in entities:
        if e.anonymize:
            rows.append([e.placeholder, e.type, entity_current_value(source_text, e), str(len(e.positions))])
    body = "\r\n".join(",".join(_csv_escape(c) for c in row) for row in rows)
    # Excel (в том числе с русской локалью) делит столбцы правильно только с явной подсказкой.
    return "sep=,\r\n" + body


def csv_bytes(source_text: str, entities: list[Entity]) -> bytes:
    """Содержимое файла *-replacements.csv: UTF-8 с BOM, как Blob в App.tsx.

    Одиночные суррогаты Blob заменяет на U+FFFD; здесь так же, чтобы кодирование
    не падало.
    """
    text = "﻿" + entities_to_csv(entities, source_text)
    return _SURROGATE_RE.sub("�", text).encode("utf-8")
