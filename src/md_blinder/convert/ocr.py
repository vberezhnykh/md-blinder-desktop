"""OCR страниц PDF движком Indra OCR (datalab-to/indra-ocr) на ONNX Runtime.

Модели скачиваются по запросу (components/ocr_models.py). Страница рендерится
MuPDF в 200 DPI в оттенках серого, как это делает сама Indra в режиме ocr, и
распознается на CPU: модели маленькие, видеокарта им не дает выигрыша.

Indra возвращает Markdown: абзацы целиком, заголовки, списки, таблицы в HTML.
Таблицы Indra на сканах ненадежны: слова расходятся по ячейкам, а текст в две
колонки (двуязычные договоры) превращается в таблицу с перемешанными строками или
в один абзац из строк обеих колонок. Поэтому такие блоки собираются заново по
строкам распознавания и их рамкам (table_from_lines, prose_columns). Колонтитулы
Indra оставляет (furniture=True), а повторяющиеся строки и номера страниц
убираются, как в остальном тексте PDF: разметка колонтитулов моделью может
выбросить строку с данными. Латинские двойники в кириллических словах
исправляет script_mix.
"""

from __future__ import annotations

import math
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np
import pymupdf

from ..cancel import CancelToken, check
from .pdf_structure import strip_repeated_lines_and_page_numbers
from .pdf_tables import table_to_markdown
from .script_mix import fix_mixed_scripts

OCR_DPI = 200

_PICTURE_RE = re.compile(r"^!\[\]\(picture-\d+\)$")
_TABLE_END = "</table>"


@dataclass
class OcrProgress:
    page: int  # порядковый номер распознаваемой страницы, с 1
    total: int


_engine_lock = threading.Lock()
_engine: Optional[tuple[Path, object]] = None


def _threads() -> int:
    return max(1, min(8, os.cpu_count() or 4))


def load_engine(model_dir: str | Path):
    """Indra загружается один раз (около секунды) и переиспользуется следующими документами."""
    global _engine
    model_dir = Path(model_dir)
    with _engine_lock:
        if _engine is None or _engine[0] != model_dir:
            from indra_ocr import OrtOCR

            _engine = (model_dir, OrtOCR(model_dir, threads=_threads(), furniture=True))
        return _engine[1]


def render_gray(page: pymupdf.Page, dpi: int = OCR_DPI) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)
    return np.ascontiguousarray(gray[:, : pix.width])


def _table_or_line(lines: list[str]) -> list[str]:
    """Части Markdown страницы: таблица — один элемент, уже в Markdown."""
    items: list[str] = []
    table: list[str] = []
    for line in lines:
        stripped = line.strip()
        if table or stripped.startswith("<table"):
            table.append(line)
            if _TABLE_END in stripped:
                items.append(table_to_markdown("\n".join(table)))
                table = []
            continue
        if stripped and not _PICTURE_RE.match(stripped):
            items.append(stripped)
    if table:
        items.append(table_to_markdown("\n".join(table) + _TABLE_END))
    return items


Piece = tuple[list[float], str]  # рамка строки Indra (x0, y0, x1, y1) и ее текст


def _group_rows(pieces: Sequence[Piece]) -> list[tuple[float, float, list[Piece]]]:
    """Строки таблицы по вертикали: (верх, низ, куски слева направо)."""
    rows: list[list] = []
    for box, text in sorted(pieces, key=lambda item: ((item[0][1] + item[0][3]) / 2, item[0][0])):
        center = (box[1] + box[3]) / 2
        if rows and rows[-1][0] <= center <= rows[-1][1]:
            rows[-1][2].append((box, text))
            rows[-1][1] = max(rows[-1][1], box[3])
        else:
            rows.append([box[1], box[3], [(box, text)]])
    return [(top, bottom, sorted(parts, key=lambda item: item[0][0])) for top, bottom, parts in rows]


def _join(pieces: Sequence[Piece]) -> str:
    return " ".join(" ".join(text for _, text in pieces).split())


def _rows(pieces: Sequence[Piece]) -> list[tuple[float, float, str]]:
    """Строки текста в порядке чтения Indra: соседние куски, перекрывающиеся по вертикали,
    — одна строка. Порядок модели надежнее сортировки по y на сканах с наклоном."""
    rows: list[list] = []
    for box, text in pieces:
        if rows:
            top, bottom = rows[-1][0], rows[-1][1]
            overlap = min(bottom, box[3]) - max(top, box[1])
            if overlap > 0.5 * min(bottom - top, box[3] - box[1]):
                rows[-1][1] = max(bottom, box[3])
                rows[-1][2].append((box, text))
                continue
        rows.append([box[1], box[3], [(box, text)]])
    return [(top, bottom, _join(parts)) for top, bottom, parts in rows]


_CLAUSE_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s")


def _paragraphs(rows: list[tuple[float, float, str]]) -> list[str]:
    """Строки колонки в абзацы: новый абзац после интервала больше обычного или с номера пункта."""
    rows = [row for row in rows if row[2]]
    if not rows:
        return []
    pitches = sorted(b[0] - a[0] for a, b in zip(rows, rows[1:]))
    pitch = pitches[len(pitches) // 2] if pitches else 0
    paragraphs: list[str] = [rows[0][2]]
    for prev, row in zip(rows, rows[1:]):
        if (pitch and row[0] - prev[0] > pitch * 1.6) or (
            _CLAUSE_RE.match(row[2]) and paragraphs[-1].endswith((".", ";", ":", "»", '"', "”"))
        ):
            paragraphs.append(row[2])
        elif paragraphs[-1].endswith("-"):
            paragraphs[-1] += row[2]
        else:
            paragraphs[-1] += " " + row[2]
    return paragraphs


def _inside(bbox: Sequence[float], lines: Sequence[Piece]) -> list[Piece]:
    x0, y0, x1, y1 = bbox
    return [(box, text) for box, text in lines
            if x0 - 2 <= (box[0] + box[2]) / 2 <= x1 + 2 and y0 - 2 <= (box[1] + box[3]) / 2 <= y1 + 2]


def _columns(pieces: Sequence[Piece], width: float) -> list[list[float]]:
    """Колонки блока: промежутки по x, которые не пересекает ни одна строка.

    Строки шире 60% блока (заголовок над таблицей) не учитываются, если их немного."""
    heights = sorted(box[3] - box[1] for box, _ in pieces)
    # Просвет между колонками у двуязычных договоров бывает уже межбуквенного интервала
    # строки; ложных колонок от этого нет: в одной колонке строки перекрывают друг друга.
    min_gap = max(6.0, heights[len(heights) // 2] * 0.3)
    wide = [box for box, _ in pieces if box[2] - box[0] > 0.6 * width]
    narrow = [box for box, _ in pieces if box[2] - box[0] <= 0.6 * width]
    boxes = narrow if wide and narrow and len(wide) <= 0.2 * len(pieces) else [box for box, _ in pieces]
    columns: list[list[float]] = []
    for left, right in sorted((box[0], box[2]) for box in boxes):
        if columns and left - columns[-1][1] < min_gap:
            columns[-1][1] = max(columns[-1][1], right)
        else:
            columns.append([left, right])
    return columns


def _column_of(box: Sequence[float], columns: list[list[float]]) -> Optional[int]:
    """Номер колонки, в которой лежит строка; None — строка пересекает просвет."""
    for index, (left, right) in enumerate(columns):
        if left - 1 <= box[0] and box[2] <= right + 1:
            return index
    return None


# Колонка «прозы»: в среднем не меньше стольких символов в строке. В таблицах с
# данными хотя бы одна колонка короткая (номера, суммы, количества).
PROSE_MIN_CHARS = 20


def prose_columns(bbox: Sequence[float], lines: Sequence[Piece]) -> Optional[list[str]]:
    """Абзацы блока, набранного в 2–3 колонки текста, по колонкам слева направо; иначе None."""
    pieces = _inside(bbox, lines)
    if len(pieces) < 6:
        return None
    columns = _columns(pieces, bbox[2] - bbox[0])
    if not 2 <= len(columns) <= 3:
        return None
    paragraphs: list[str] = []
    for index in range(len(columns)):
        rows = _rows([piece for piece in pieces if _column_of(piece[0], columns) == index])
        if len(rows) < 3 or sum(len(r[2]) for r in rows) / len(rows) < PROSE_MIN_CHARS:
            return None
        paragraphs.extend(_paragraphs(rows))
    return paragraphs


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def table_from_lines(bbox: Sequence[float], lines: Sequence[Piece]) -> Optional[list[str]]:
    """Блок, который Indra разметила таблицей, заново по строкам распознавания.

    Indra делит строки на ячейки по небольшим просветам, и на сканах с выключкой по
    ширине или отступами пунктов слова расходятся по ячейкам («| Heim | Brecht |»).
    Здесь ячейки — колонки по просветам, которые не пересекает ни одна строка блока.
    Одна колонка — это текст, а не таблица: он выводится абзацами. Колонки прозы
    читаются по очереди (prose_columns), остальное — таблица Markdown."""
    pieces = _inside(bbox, lines)
    if not pieces:
        return None
    columns = _columns(pieces, bbox[2] - bbox[0])
    if len(columns) < 2:
        return _paragraphs(_rows(pieces))
    prose = prose_columns(bbox, lines)
    if prose is not None:
        return prose
    table: list[list[str]] = []
    for _, _, parts in _group_rows(pieces):
        cells: list[list[Piece]] = [[] for _ in columns]
        for piece in parts:
            index = _column_of(piece[0], columns)
            cells[0 if index is None else index].append(piece)
        row = [_cell(_join(cell)) for cell in cells]
        if any(row):
            table.append(row)
    if not table:
        return None
    lines_md = ["| " + " | ".join(table[0]) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    lines_md += ["| " + " | ".join(row) + " |" for row in table[1:]]
    return ["\n".join(lines_md)]


def _straighten(box: Sequence[float], tilt: float) -> list[float]:
    """Рамка в координатах выпрямленной страницы: Indra отдает рамки на странице как
    она есть, и на скане с наклоном строки соседних колонок расходятся по высоте."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    dx, dy = cy * tilt, -cx * tilt
    return [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]


def page_items(page) -> list[str]:
    """Строки страницы Indra для поиска колонтитулов; таблица — один элемент."""
    markdown = page.markdown
    if markdown is None:
        markdown = "\n".join(line.text for line in page.lines)
    parts = [part for part in markdown.split("\n\n") if part.strip()]
    tilt = math.tan(math.radians(page.skew or 0.0))
    lines = [(_straighten(line.box, tilt), line.text) for line in page.lines]
    items: list[str] = []
    # Части Markdown и блоки разметки идут в одном порядке; если число не совпало,
    # блоки не пересобираются.
    blocks = page.blocks if len(page.blocks) == len(parts) else [None] * len(parts)
    for part, block in zip(parts, blocks):
        rebuilt = None
        if block and block["label"] == "Table":
            rebuilt = table_from_lines(_straighten(block["bbox"], tilt), lines)
        elif block and block["label"] != "Picture":
            rebuilt = prose_columns(_straighten(block["bbox"], tilt), lines)
        items.extend(rebuilt if rebuilt is not None else _table_or_line(part.split("\n")))
    return [fix_mixed_scripts(item) for item in items if item]


def markdown_items(markdown: str) -> list[str]:
    return [fix_mixed_scripts(item) for item in _table_or_line(markdown.split("\n")) if item]


def ocr_pdf_pages(
    path: str | Path,
    page_numbers: Sequence[int],
    model_dir: str | Path,
    on_progress: Optional[Callable[[OcrProgress], None]] = None,
    cancel: Optional[CancelToken] = None,
) -> dict[int, str]:
    numbers = sorted(set(page_numbers))
    if not numbers:
        return {}
    engine = load_engine(model_dir)
    pages_items: list[list[str]] = []
    with pymupdf.open(path) as doc:
        for idx, number in enumerate(numbers):
            check(cancel)
            if on_progress:
                on_progress(OcrProgress(idx + 1, len(numbers)))
            result = engine(render_gray(doc[number - 1]))
            pages_items.append(page_items(result))
    check(cancel)
    cleaned = strip_repeated_lines_and_page_numbers(pages_items)
    return {number: "\n\n".join(items) for number, items in zip(numbers, cleaned)}
