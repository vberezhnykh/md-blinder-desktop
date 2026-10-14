"""Реконструкция Markdown из текстового слоя PDF.

Порт src/lib/pdfStructure.ts. Модуль чистый: на вход нормализованные
текстовые элементы (PdfTextItem) в системе координат pdf.js (y растет вверх),
на выход строка Markdown.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, TypeVar


@dataclass
class PdfTextItem:
    str: str
    x: float  # левый край
    y: float  # baseline; больше y = выше на странице
    w: float  # ширина элемента
    fontSize: float
    fontName: str = ""
    hasEOL: bool = False


@dataclass
class PdfPageLayout:
    bottom: float
    height: float
    pageNumber: int
    totalPages: int


@dataclass
class _Line:
    text: str
    fontSize: float
    x0: float
    yTop: float


PAGE_NUMBER_RE = re.compile(r"^[\s\-–—]*(\d{1,4})[\s\-–—]*$")
PAGE_WORD_RE = re.compile(r"^(?:page|страница|сторінка)\s+\d{1,4}(?:\s+(?:of|из|з)\s+\d{1,4})?$", re.I)
NUMBERED_CLAUSE_RE = re.compile(r"^\d+(?:\.\d+)*\.?\)?\s+\S")
BULLET_RE = re.compile(r"^[•▪◦‣·]\s+")
DASH_BULLET_RE = re.compile(r"^[-–—]\s+\S")
LETTERED_RE = re.compile(r"^\(?[a-z]\)\s+", re.I)
ROMAN_RE = re.compile(r"^\(?[ivxlcdm]+\)\s+", re.I)


def _js_round(value: float) -> float:
    # Math.round округляет .5 вверх, round() в Python — к четному.
    return math.floor(value + 0.5)


def detect_body_size(pages: Sequence[Sequence[PdfTextItem]]) -> float:
    totals: dict[float, int] = {}
    for page in pages:
        for item in page:
            if not item.str.strip():
                continue
            rounded = _js_round(item.fontSize * 2) / 2
            totals[rounded] = totals.get(rounded, 0) + len(item.str)
    body_size = 0.0
    best = -1
    for size, length in totals.items():
        if length > best:
            best = length
            body_size = size
    return body_size


def _build_lines(items: Sequence[PdfTextItem], body_size: float) -> list[_Line]:
    # Порядок элементов сохраняется: пересортировка перемешивает двухколоночные
    # блоки (подписи сторон) построчно.
    raw_lines: list[list[PdfTextItem]] = []
    prev: Optional[PdfTextItem] = None
    for item in items:
        if not item.str:
            continue
        break_line = (
            not raw_lines
            or (prev is not None and prev.hasEOL)
            or (prev is not None and prev.y - item.y > 0.6 * max(body_size, item.fontSize))
        )
        if break_line:
            raw_lines.append([item])
        else:
            raw_lines[-1].append(item)
        prev = item

    lines: list[_Line] = []
    for line_items in raw_lines:
        text = ""
        max_font = 0.0
        x0 = math.inf
        y_top = -math.inf
        for i, item in enumerate(line_items):
            max_font = max(max_font, item.fontSize)
            x0 = min(x0, item.x)
            y_top = max(y_top, item.y)
            if i == 0:
                text = item.str
                continue
            p = line_items[i - 1]
            gap = item.x - (p.x + p.w)
            threshold = 0.25 * max(body_size, p.fontSize)
            already_has_space = bool(re.search(r"\s$", text)) or bool(re.match(r"\s", item.str))
            if gap > threshold and not already_has_space:
                text += " "
            text += item.str
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        lines.append(_Line(text, max_font, x0, y_top))
    return lines


def _normalize_for_repeat(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"^[\d\s]+", "", text)
    text = re.sub(r"[\d\s]+$", "", text)
    return text.strip()


T = TypeVar("T")


def _suppress_repeated_first_last(pages_lines: list[list[T]], get_text: Callable[[T], str]) -> None:
    threshold = math.ceil(len(pages_lines) * 0.5)

    def count(pick: Callable[[list[T]], Optional[T]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for lines in pages_lines:
            line = pick(lines)
            if line is None:
                continue
            norm = _normalize_for_repeat(get_text(line))
            if not norm:
                continue
            counts[norm] = counts.get(norm, 0) + 1
        return counts

    first_counts = count(lambda lines: lines[0] if lines else None)
    last_counts = count(lambda lines: lines[-1] if lines else None)

    for lines in pages_lines:
        if not lines:
            continue
        first_norm = _normalize_for_repeat(get_text(lines[0]))
        drop_first = bool(first_norm) and first_counts.get(first_norm, 0) >= threshold
        last_norm = _normalize_for_repeat(get_text(lines[-1]))
        drop_last = len(lines) > 1 and bool(last_norm) and last_counts.get(last_norm, 0) >= threshold
        if drop_last:
            lines.pop()
        if drop_first:
            lines.pop(0)


def _strip_explicit_page_labels(pages_lines: list[list[T]], get_text: Callable[[T], str]) -> None:
    # Без геометрии безопасно убрать только явную подпись страницы на границе:
    # голое число может быть суммой или номером счета.
    for lines in pages_lines:
        if len(lines) > 1 and PAGE_WORD_RE.match(get_text(lines[-1])):
            lines.pop()
        if len(lines) > 1 and PAGE_WORD_RE.match(get_text(lines[0])):
            lines.pop(0)


def _strip_headers_footers(pages_lines: list[list[_Line]], layouts: Optional[Sequence[PdfPageLayout]]) -> None:
    for page_index, lines in enumerate(pages_lines):
        layout = layouts[page_index] if layouts and page_index < len(layouts) else None
        if layout is None:
            continue
        for index in (len(lines) - 1, 0):
            if index < 0 or index >= len(lines) or len(lines) <= 1:
                continue
            line = lines[index]
            match = PAGE_NUMBER_RE.match(line.text)
            relative_y = (line.yTop - layout.bottom) / layout.height
            if (
                match
                and (relative_y < 0.08 or relative_y > 0.92)
                and int(match.group(1)) == layout.pageNumber
                and int(match.group(1)) <= layout.totalPages
            ):
                lines.pop(index)
    if len(pages_lines) >= 3:
        _suppress_repeated_first_last(pages_lines, lambda line: line.text)
    _strip_explicit_page_labels(pages_lines, lambda line: line.text)


def strip_repeated_lines_and_page_numbers(pages: Sequence[Sequence[str]]) -> list[list[str]]:
    """Вариант для построчного текста OCR, где нет координат. Числовые строки сохраняются."""
    working = [list(lines) for lines in pages]
    if len(working) >= 3:
        _suppress_repeated_first_last(working, lambda t: t)
    _strip_explicit_page_labels(working, lambda t: t)
    return working


def _is_all_caps_short(text: str) -> bool:
    if len(text) > 60:
        return False
    return bool(re.search(r"[A-ZА-ЯЁ]", text)) and not re.search(r"[a-zа-яё]", text)


def _lines_to_blocks(pages_lines: list[list[_Line]], body_size: float) -> list[str]:
    blocks: list[str] = []
    buffer: list[str] = []
    buffer_last: Optional[_Line] = None

    def flush() -> None:
        nonlocal buffer, buffer_last
        if buffer:
            blocks.append(" ".join(buffer))
            buffer = []
            buffer_last = None

    for lines in pages_lines:
        for line in lines:
            text = line.text
            is_big_heading = line.fontSize >= body_size * 1.15
            if is_big_heading or _is_all_caps_short(text):
                flush()
                level = "###"
                if line.fontSize >= body_size * 1.6:
                    level = "#"
                elif line.fontSize >= body_size * 1.3:
                    level = "##"
                elif not is_big_heading:
                    level = "##"
                blocks.append(f"{level} {text}")
                continue
            if NUMBERED_CLAUSE_RE.match(text):
                flush()
                blocks.append(text)
                continue
            if BULLET_RE.match(text):
                flush()
                blocks.append("- " + BULLET_RE.sub("", text, count=1))
                continue
            if DASH_BULLET_RE.match(text):
                flush()
                blocks.append("- " + re.sub(r"^[-–—]\s+", "", text, count=1))
                continue
            if LETTERED_RE.match(text) or ROMAN_RE.match(text):
                flush()
                blocks.append(text)
                continue

            # Строка продолжает абзац, только если идет ниже предыдущей в пределах
            # межстрочного интервала и в той же колонке.
            v_gap = buffer_last.yTop - line.yTop if buffer_last else math.inf
            same_column = abs(buffer_last.x0 - line.x0) <= 2 * body_size if buffer_last else False
            can_continue = (
                bool(buffer)
                and buffer_last is not None
                and v_gap > 0.2 * body_size
                and v_gap <= 1.8 * body_size
                and same_column
            )
            if can_continue:
                prev_text = buffer[-1]
                if re.search(r"[a-zа-яё][-‐]$", prev_text) and re.match(r"[a-zа-яё]", text):
                    buffer[-1] = prev_text[:-1] + text
                else:
                    buffer.append(text)
            else:
                flush()
                buffer.append(text)
            buffer_last = line
        # Абзацы не переносятся через границу страницы.
        flush()
    flush()
    return blocks


def items_to_markdown(
    pages: Sequence[Sequence[PdfTextItem]], layouts: Optional[Sequence[PdfPageLayout]] = None
) -> str:
    body_size = detect_body_size(pages)
    if body_size == 0:
        return ""
    pages_lines = [_build_lines(items, body_size) for items in pages]
    _strip_headers_footers(pages_lines, layouts)
    blocks = _lines_to_blocks(pages_lines, body_size)
    return re.sub(r"\n{3,}", "\n\n", "\n\n".join(blocks)).strip()
