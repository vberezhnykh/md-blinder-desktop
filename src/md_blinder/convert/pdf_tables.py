"""Таблицы в текстовом слое PDF.

Таблицы ищет PyMuPDF (find_tables). Пакет pymupdf_layout добавляет к поиску по
линиям модель разметки страницы: она находит таблицы без рамок и объединенные
ячейки. Таблица выдается как HTML и переводится в Markdown тем же конвертером,
что таблицы DOCX, поэтому объединения раскладываются одинаково.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

import pymupdf
from bs4 import BeautifulSoup

from .html_to_md import html_to_markdown

# Какая доля символов в области таблицы может не попасть в ее ячейки. Больше —
# и таблица отбрасывается: текст страницы собирается по-старому, без потерь.
MAX_MISSING_CHARS = 0.02

_layout_state: bool | None = None


def layout_available() -> bool:
    """Подключает модель разметки при первом вызове. Импорт загружает модели ONNX, около секунды."""
    global _layout_state
    if _layout_state is None:
        try:
            import pymupdf.layout  # noqa: F401  регистрирует модель для page.get_layout()

            _layout_state = True
        except ImportError:
            _layout_state = False
    return _layout_state


@dataclass
class PdfTable:
    rect: pymupdf.Rect
    html: str
    cols: int


def _chars(texts: Sequence[str]) -> Counter[str]:
    return Counter(ch for text in texts for ch in text if not ch.isspace())


def find_page_tables(page: pymupdf.Page, spans: Sequence[tuple[str, pymupdf.Rect]]) -> list[PdfTable]:
    """Таблицы страницы. spans — текст и рамка каждого фрагмента текстового слоя."""
    layout_available()
    try:
        found = page.find_tables(refine=True).tables
    except Exception:  # noqa: BLE001 — сбой поиска не должен ломать разбор страницы
        return []
    tables: list[PdfTable] = []
    for table in found:
        rect = pymupdf.Rect(table.bbox)
        inside = [text for text, bbox in spans if _center_in(bbox, rect)]
        expected = _chars(inside)
        if not expected:
            continue
        cells = [cell for row in table.extract() for cell in row if cell]
        missing = expected - _chars(cells)
        if sum(missing.values()) > MAX_MISSING_CHARS * sum(expected.values()):
            continue
        tables.append(PdfTable(rect=rect, html=_unwrap_cells(table.to_html()), cols=table.col_count))
    return tables


_SOFT_HYPHEN_RE = re.compile(r"[a-zа-яё][-‐]$")
_LOWER_START_RE = re.compile(r"[a-zа-яё]")


def _join_wrapped(lines: Sequence[str]) -> str:
    """Склеивает строки, на которые ячейку разбила ширина столбца.

    В PDF перенос строки в ячейке почти всегда визуальный. Если оставить его
    как <br>, имя или адрес, перенесенные в ячейке, распадутся на части, и NER
    их не узнает. Перенос по слогам склеивается без дефиса, как в абзацах.
    """
    text = ""
    for line in (line.strip() for line in lines):
        if not line:
            continue
        if not text:
            text = line
        elif _SOFT_HYPHEN_RE.search(text) and _LOWER_START_RE.match(line):
            text = text[:-1] + line
        elif text.endswith(("-", "‐")) and not text[-2:-1].isspace():
            text += line
        else:
            text += " " + line
    return text


def _unwrap_cells(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for cell in soup.find_all(["td", "th"]):
        if cell.find("table") is None:
            cell.string = _join_wrapped(cell.get_text("\n").split("\n"))
    return str(soup)


def _center_in(bbox: pymupdf.Rect, rect: pymupdf.Rect) -> bool:
    return rect.contains(pymupdf.Point((bbox.x0 + bbox.x1) / 2, (bbox.y0 + bbox.y1) / 2))


def table_owner(bbox: pymupdf.Rect, tables: Sequence[PdfTable]) -> int | None:
    """Номер таблицы, которой принадлежит фрагмент текста, или None."""
    for index, table in enumerate(tables):
        if _center_in(bbox, table.rect):
            return index
    return None


def _row_texts(row) -> list[str]:
    return [" ".join(cell.get_text(" ").split()) for cell in row.find_all(["td", "th"], recursive=False)]


def join_tables(first_html: str, second_html: str) -> str:
    """Продолжение таблицы со следующей страницы дописывается строками в конец первой.

    Если продолжение начинается с повтора шапки, повтор отбрасывается. Строки
    продолжения становятся обычными: поиск на новой странице считает первую
    строку шапкой.
    """
    soup = BeautifulSoup(first_html, "html.parser")
    target = soup.find("table")
    second = BeautifulSoup(second_html, "html.parser").find("table")
    if target is None or second is None:
        return first_html + second_html
    first_rows = [tr for tr in target.find_all("tr") if tr.find_parent("table") is target]
    rows = [tr for tr in second.find_all("tr") if tr.find_parent("table") is second]
    if rows and first_rows and _row_texts(rows[0]) == _row_texts(first_rows[0]):
        rows = rows[1:]
    parent = first_rows[-1].parent if first_rows else target
    for row in rows:
        for cell in row.find_all("th", recursive=False):
            cell.name = "td"
        parent.append(row.extract())
    return str(soup)


def table_to_markdown(html: str) -> str:
    return html_to_markdown(html).strip()
