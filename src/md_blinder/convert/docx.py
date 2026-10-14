"""DOCX → Markdown через mammoth (порт src/lib/docxToMarkdown.ts)."""

from __future__ import annotations

from pathlib import Path

import mammoth

from .html_to_md import html_to_markdown


def docx_to_markdown(path: str | Path) -> str:
    with open(path, "rb") as fh:
        # По умолчанию mammoth встраивает картинки как data URI, как и JS-версия;
        # при обезличивании они заменяются отметкой об удалении.
        html = mammoth.convert_to_html(fh).value
    return html_to_markdown(html)
