"""HTML (вывод mammoth) → Markdown. Порт src/lib/htmlToMarkdown.ts на markdownify."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

_CELL_BREAK_RE = re.compile(r"\r?\n\s*\n?")


def preprocess_table_html(html: str) -> str:
    """Делает первую строку таблицы без <thead> заголовком из <th>.

    mammoth не выдает <thead>; без него строка заголовка таблицы получается пустой.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
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
        colspan = 1
        raw = el.attrs.get("colspan")
        if raw and str(raw).isdigit():
            colspan = max(1, min(1000, int(raw)))
        return " " + value + " |" * colspan

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
