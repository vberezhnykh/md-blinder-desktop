"""HTML (вывод mammoth) → Markdown. Порт src/lib/htmlToMarkdown.ts на markdownify."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag
from markdownify import MarkdownConverter

_CELL_BREAK_RE = re.compile(r"\r?\n\s*\n?")
# Ограничение на colspan/rowspan: защита от испорченного документа с огромным
# значением, которое иначе раздует таблицу.
_MAX_SPAN = 1000


def _span(cell: Tag, attr: str, fallback: int) -> int:
    raw = str(cell.get(attr) or "").strip()
    if not raw.isdigit():
        return 1
    value = int(raw)
    # rowspan="0" по стандарту тянется до конца таблицы.
    return fallback if value == 0 else min(value, _MAX_SPAN)


def _own_rows(table: Tag) -> list[Tag]:
    return [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]


def _own_cells(row: Tag) -> list[Tag]:
    return row.find_all(["td", "th"], recursive=False)


def _expand_spans(soup: BeautifulSoup, table: Tag) -> None:
    """Раскладывает объединенные ячейки в полную сетку.

    В таблице Markdown нет объединенных ячеек. Значение остается в первой ячейке
    объединения, покрытые ею места заполняются пустыми ячейками. Без этого строка
    под ячейкой с rowspan теряет столбец, и ее значения сдвигаются влево.
    Короткие строки дополняются до ширины таблицы.
    """
    rows = _own_rows(table)
    # occupied[r] — столбцы строки r, занятые ячейками с rowspan из строк выше.
    occupied: list[set[int]] = [set() for _ in rows]
    for r, row in enumerate(rows):
        col = 0

        def fill(before: Tag | None, tag: str) -> None:
            nonlocal col
            while col in occupied[r]:
                empty = soup.new_tag(tag)
                if before is None:
                    row.append(empty)
                else:
                    before.insert_before(empty)
                col += 1

        for cell in _own_cells(row):
            fill(cell, cell.name)
            colspan = _span(cell, "colspan", 1)
            rowspan = min(_span(cell, "rowspan", len(rows) - r), len(rows) - r)
            del cell["colspan"], cell["rowspan"]
            for dr in range(1, rowspan):
                occupied[r + dr].update(range(col, col + colspan))
            for _ in range(1, colspan):
                cell.insert_after(soup.new_tag(cell.name))
            col += colspan
        cells = _own_cells(row)
        fill(None, cells[-1].name if cells else "td")
    width = max((len(_own_cells(row)) for row in rows), default=0)
    for row in rows:
        cells = _own_cells(row)
        tag = cells[-1].name if cells else "td"
        for _ in range(len(cells), width):
            row.append(soup.new_tag(tag))


def preprocess_table_html(html: str) -> str:
    """Раскладывает объединенные ячейки и делает первую строку без <thead> заголовком из <th>.

    mammoth не выдает <thead>; без него строка заголовка таблицы получается пустой.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        _expand_spans(soup, table)
        if table.find("thead"):
            continue
        first_row = table.find("tr")
        if first_row is None:
            continue
        for cell in first_row.find_all("td", recursive=False):
            cell.name = "th"
        thead = soup.new_tag("thead")
        first_row.extract()
        thead.append(first_row)
        table.insert(0, thead)
    return str(soup)


class _Converter(MarkdownConverter):
    """Ячейки таблицы конвертируются как блоки, затем сплющиваются в одну строку.

    markdownify по умолчанию сворачивает содержимое ячейки в inline-текст и теряет
    абзацы, списки и картинки. Здесь, как в Turndown, ячейка конвертируется целиком,
    а границы блоков заменяются на <br>.
    """

    def _cell(self, el, parent_tags) -> str:
        name = el.name
        el.name = "div"
        try:
            content = self.process_tag(el, parent_tags={"_cell"})
        finally:
            el.name = name
        value = _CELL_BREAK_RE.sub("<br>", content.strip()).replace("|", "\\|")
        return " " + value + " |"

    def convert_td(self, el, text, parent_tags):
        return self._cell(el, parent_tags)

    def convert_th(self, el, text, parent_tags):
        return self._cell(el, parent_tags)

    def convert_br(self, el, text, parent_tags):
        if "_cell" in parent_tags:
            return "<br>"
        return super().convert_br(el, text, parent_tags)


def html_to_markdown(html: str) -> str:
    converter = _Converter(heading_style="ATX", bullets="*", code_language="", escape_misc=False)
    md = converter.convert(preprocess_table_html(html))
    return re.sub(r"\n{3,}", "\n\n", md).strip()
