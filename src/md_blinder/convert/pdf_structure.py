"""Реконструкция Markdown из текстового слоя PDF.

Порт src/lib/pdfStructure.ts. Модуль чистый: на вход нормализованные
текстовые элементы (PdfTextItem) в системе координат pdf.js (y растет вверх),
на выход строка Markdown. В отличие от веб-версии, элемент может быть
готовой таблицей (HTML), найденной pdf_tables.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, TypeVar

from .pdf_tables import join_tables, table_to_markdown


@dataclass
class PdfTextItem:
    str: str
    x: float  # левый край
    y: float  # baseline; больше y = выше на странице
    w: float  # ширина элемента
    fontSize: float
    fontName: str = ""
    hasEOL: bool = False
    # Таблица, найденная на странице (только десктоп): HTML вместо текста, str пустая.
    table_html: Optional[str] = None
    table_cols: int = 0
    # Нижний край таблицы (в тех же координатах, что y): по нему видно, что следующая
    # таблица идет вплотную и продолжает эту.
    table_bottom: float = 0.0


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
    # Для таблицы text — ее HTML.
    is_table: bool = False
    table_cols: int = 0
    x1: float = 0.0
    yBottom: float = 0.0


PAGE_NUMBER_RE = re.compile(r"^[\s\-–—]*(\d{1,4})[\s\-–—]*$")
PAGE_WORD_RE = re.compile(r"^(?:page|страница|сторінка)\s+\d{1,4}(?:\s+(?:of|из|з)\s+\d{1,4})?$", re.I)
NUMBERED_CLAUSE_RE = re.compile(r"^\d+(?:\.\d+)*\.?\)?\s+\S")
# Число в начале строки посреди предложения («…within\n30 days»), а не номер пункта.
NUMBER_IN_SENTENCE_RE = re.compile(r"^\d+\s+[a-zа-яёіїєґ]")
SENTENCE_END_RE = re.compile(r"[.:;!?»\"”)]$")
LOWER_START_RE = re.compile(r"[a-zа-яёіїєґ]")
MARKER_ONLY_RE = re.compile(r"^(?:\d+(?:\.\d+)*\.?|\(?(?:[a-z]|[ivx]+|\d+)\))$", re.I)
BULLET_RE = re.compile(r"^[•▪◦‣·]\s+")
DASH_BULLET_RE = re.compile(r"^[-–—]\s+[^\s\-–—_]")
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
        if item.table_html is not None:
            raw_lines.append([item])
            prev = item
            continue
        if not item.str:
            continue
        break_line = (
            not raw_lines
            or (prev is not None and (prev.hasEOL or prev.table_html is not None))
            or (prev is not None and prev.y - item.y > 0.6 * max(body_size, item.fontSize))
        )
        if break_line:
            raw_lines.append([item])
        else:
            raw_lines[-1].append(item)
        prev = item

    lines: list[_Line] = []
    for line_items in raw_lines:
        head = line_items[0]
        if head.table_html is not None:
            lines.append(_Line(
                head.table_html, body_size, head.x, head.y, is_table=True, table_cols=head.table_cols,
                x1=head.x + head.w, yBottom=head.table_bottom,
            ))
            continue
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
    return _attach_markers(lines, body_size)


def _attach_markers(lines: list[_Line], body_size: float) -> list[_Line]:
    """Номер пункта, стоящий отдельно на поле слева («1.2», «(b)»), соединяется с текстом пункта.

    В таких документах номер и текст — разные строки текстового слоя на одной высоте.
    Без соединения номер оказывался в конце предыдущего абзаца или отдельным абзацем."""
    result: list[_Line] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        following = lines[index + 1] if index + 1 < len(lines) else None
        if (
            following is not None and not line.is_table and not following.is_table
            and MARKER_ONLY_RE.match(line.text)
            and abs(line.yTop - following.yTop) <= 0.3 * body_size
            and following.x0 > line.x0
        ):
            result.append(_Line(
                f"{line.text} {following.text}", max(line.fontSize, following.fontSize), line.x0,
                max(line.yTop, following.yTop),
            ))
            index += 2
            continue
        result.append(line)
        index += 1
    return result


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


def _line_pitch(pages_lines: list[list[_Line]], body_size: float) -> float:
    """Обычный шаг строк документа: медиана расстояний между соседними строками текста.

    В документах с полуторным и двойным интервалом шаг больше 1,8 кегля, и без
    этой поправки каждая строка становилась отдельным абзацем."""
    gaps: list[float] = []
    for lines in pages_lines:
        for prev, line in zip(lines, lines[1:]):
            if prev.is_table or line.is_table or abs(prev.x0 - line.x0) > 2 * body_size:
                continue
            gap = prev.yTop - line.yTop
            # Двойной интервал — около 2,3 кегля; больше — уже отбивка между абзацами.
            if 0.2 * body_size < gap <= 2.6 * body_size:
                gaps.append(gap)
    # По нескольким строкам шаг строк от отбивки абзацев не отличить.
    if len(gaps) < 8:
        return body_size
    gaps.sort()
    return gaps[len(gaps) // 2]


def _starts_item(text: str) -> bool:
    return bool(
        NUMBERED_CLAUSE_RE.match(text) or BULLET_RE.match(text) or DASH_BULLET_RE.match(text)
        or LETTERED_RE.match(text) or ROMAN_RE.match(text)
    )


def _tables_adjoin(prev: _Line, line: _Line, body_size: float) -> bool:
    """Таблица идет вплотную под предыдущей, с теми же столбцами и краями.

    Поиск таблиц делит одну таблицу на части по строкам-подзаголовкам или пустым
    строкам; без склейки каждая часть выводилась отдельной таблицей с новой шапкой."""
    tolerance = max(body_size, 3.0)
    return (
        prev.table_cols == line.table_cols
        and abs(prev.x0 - line.x0) <= tolerance
        and abs(prev.x1 - line.x1) <= tolerance
        and -tolerance <= prev.yBottom - line.yTop <= 2 * max(body_size, 6.0)
    )


def _lines_to_blocks(pages_lines: list[list[_Line]], body_size: float) -> list[str]:
    blocks: list[str] = []
    buffer: list[str] = []
    buffer_last: Optional[_Line] = None
    buffer_is_item = False
    # Абзац дошел до конца страницы: продолжение ищется в начале следующей.
    page_break = False
    # Таблицы копятся как HTML (номер блока -> HTML и число столбцов), чтобы
    # продолжение со следующей страницы можно было дописать в конец.
    tables: dict[int, tuple[str, int]] = {}
    last_table: Optional[_Line] = None
    pitch = _line_pitch(pages_lines, body_size)
    max_gap = max(1.8 * body_size, 1.35 * pitch)

    def flush() -> None:
        nonlocal buffer, buffer_last, buffer_is_item, page_break
        if buffer:
            blocks.append(" ".join(buffer))
        buffer = []
        buffer_last = None
        buffer_is_item = False
        page_break = False

    def start(text: str, line: _Line, is_item: bool) -> None:
        nonlocal buffer_last, buffer_is_item
        flush()
        buffer.append(text)
        buffer_last = line
        buffer_is_item = is_item

    def same_column(line: _Line) -> bool:
        assert buffer_last is not None
        shift = line.x0 - buffer_last.x0
        if len(buffer) == 1 and buffer_is_item:
            # Перенос пункта выравнивается по тексту после номера или по полю слева.
            return -4 * body_size <= shift <= 6 * body_size
        if len(buffer) == 1:
            # Красная строка: продолжение левее первой строки абзаца.
            return -4 * body_size <= shift <= 2 * body_size
        return abs(shift) <= 2 * body_size

    def continues(line: _Line) -> bool:
        if not buffer or buffer_last is None:
            return False
        prev_text = buffer[-1]
        lower_tail = bool(LOWER_START_RE.match(line.text)) and not SENTENCE_END_RE.search(prev_text)
        if page_break:
            # Через границу страницы геометрия не сравнима: склеивается только
            # предложение, оборванное на полуслове.
            return lower_tail
        v_gap = buffer_last.yTop - line.yTop
        if v_gap <= 0.2 * body_size or not same_column(line):
            return False
        if v_gap <= max_gap:
            return True
        # Строка со строчной буквы после незаконченной фразы продолжает ее и при
        # увеличенном интервале.
        return lower_tail and v_gap <= 2.5 * max(pitch, body_size)

    for lines in pages_lines:
        # Таблица, которой закончилась предыдущая страница: продолжение на этой
        # странице, если она с нее начинается и столбцов столько же.
        carry = len(blocks) - 1 if blocks and not buffer and len(blocks) - 1 in tables else None
        for position, line in enumerate(lines):
            text = line.text
            if line.is_table:
                flush()
                previous_index = len(blocks) - 1 if blocks and len(blocks) - 1 in tables else None
                previous = tables.get(previous_index) if previous_index is not None else None
                joined = previous is not None and (
                    (position == 0 and previous_index == carry and previous[1] == line.table_cols)
                    or (position > 0 and last_table is not None and _tables_adjoin(last_table, line, body_size))
                )
                if joined:
                    tables[previous_index] = (join_tables(previous[0], text), previous[1])
                else:
                    tables[len(blocks)] = (text, line.table_cols)
                    blocks.append("")
                last_table = line
                continue
            last_table = None
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
            if _starts_item(text) and not (NUMBER_IN_SENTENCE_RE.match(text) and continues(line)):
                if BULLET_RE.match(text):
                    text = "- " + BULLET_RE.sub("", text, count=1)
                elif DASH_BULLET_RE.match(text):
                    text = "- " + re.sub(r"^[-–—]\s+", "", text, count=1)
                # Пункт начинает новый абзац, а его перенесенные строки дописываются к нему.
                start(text, line, is_item=True)
                continue

            if continues(line):
                prev_text = buffer[-1]
                if re.search(r"[a-zа-яё][-‐]$", prev_text) and re.match(r"[a-zа-яё]", text):
                    buffer[-1] = prev_text[:-1] + text
                else:
                    buffer.append(text)
                buffer_last = line
                page_break = False
            else:
                start(text, line, is_item=False)
        if buffer:
            page_break = True
        last_table = None
    flush()
    for index, (html, _) in tables.items():
        blocks[index] = table_to_markdown(html)
    return blocks


def items_to_markdown(
    pages: Sequence[Sequence[PdfTextItem]], layouts: Optional[Sequence[PdfPageLayout]] = None
) -> str:
    body_size = detect_body_size(pages)
    # Без текста вне таблиц размер шрифта не определить, но таблицы терять нельзя.
    if body_size == 0 and not any(item.table_html is not None for items in pages for item in items):
        return ""
    pages_lines = [_build_lines(items, body_size) for items in pages]
    _strip_headers_footers(pages_lines, layouts)
    blocks = _lines_to_blocks(pages_lines, body_size)
    return re.sub(r"\n{3,}", "\n\n", "\n\n".join(blocks)).strip()
