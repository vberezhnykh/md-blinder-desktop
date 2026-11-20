"""XLSX → Markdown через openpyxl: каждый лист становится разделом с таблицей.

Таблица собирается как HTML и проходит через тот же html_to_markdown, что и
DOCX, поэтому ячейки, переносы строк и экранирование выглядят одинаково.
"""

from __future__ import annotations

import datetime as dt
import html
import warnings
from pathlib import Path

import openpyxl

from .html_to_md import html_to_markdown

HIDDEN_SUFFIX = " (скрытый лист)"


def _format_number(value: float, number_format: str) -> str:
    if "%" in number_format:
        return _format_number(value * 100, "") + "%"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e16:
        return str(int(value))
    if isinstance(value, float):
        # 15 значащих цифр, как показывает Excel: 0.1 + 0.2 дает 0.3, а не 0.30000000000000004.
        return f"{value:.15g}"
    return str(value)


def cell_text(value: object, number_format: str = "General") -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "ИСТИНА" if value else "ЛОЖЬ"
    if isinstance(value, dt.datetime):
        if value.time() == dt.time(0, 0):
            return value.strftime("%d.%m.%Y")
        return value.strftime("%d.%m.%Y %H:%M" if value.second == 0 else "%d.%m.%Y %H:%M:%S")
    if isinstance(value, dt.date):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, dt.time):
        return value.strftime("%H:%M" if value.second == 0 else "%H:%M:%S")
    if isinstance(value, (int, float)):
        return _format_number(value, number_format or "")
    return str(value).strip()


def _sheet_grid(ws) -> list[list[str]]:
    """Непустые строки листа; пустые строки и столбцы выбрасываются."""
    # Размеры листа в файле бывают неверными или отсутствуют: читаем все строки, что есть.
    ws.reset_dimensions()
    cells: dict[tuple[int, int], str] = {}
    for r, row in enumerate(ws.iter_rows()):
        for c, cell in enumerate(row):
            text = cell_text(cell.value, getattr(cell, "number_format", "General"))
            if text:
                cells[(r, c)] = text
    rows = sorted({r for r, _ in cells})
    cols = sorted({c for _, c in cells})
    return [[cells.get((r, c), "") for c in cols] for r in rows]


def _cell_html(text: str) -> str:
    return html.escape(text).replace("\r\n", "\n").replace("\n", "<br>")


def xlsx_to_markdown(path: str | Path) -> str:
    """Пустая строка, если во всех листах нет заполненных ячеек.

    Берутся сохраненные значения формул (то, что видно в Excel). Скрытые листы
    тоже выгружаются, с пометкой в заголовке: в них часто лежат те же данные.
    """
    parts: list[str] = []
    with warnings.catch_warnings():
        # openpyxl предупреждает о неподдерживаемых расширениях (проверка данных и т. п.).
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                grid = _sheet_grid(ws)
                if not grid:
                    continue
                title = ws.title + (HIDDEN_SUFFIX if ws.sheet_state != "visible" else "")
                rows = "".join("<tr>" + "".join(f"<td>{_cell_html(v)}</td>" for v in row) + "</tr>" for row in grid)
                parts.append(f"<h2>{html.escape(title)}</h2><table>{rows}</table>")
        finally:
            wb.close()
    return html_to_markdown("".join(parts)) if parts else ""
