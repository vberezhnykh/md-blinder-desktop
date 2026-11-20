"""DOCX → Markdown через mammoth (порт src/lib/docxToMarkdown.ts)."""

from __future__ import annotations

import io
from pathlib import Path

import mammoth

from .docx_numbering import with_numbering_as_text
from .html_to_md import html_to_markdown


def docx_to_markdown(path: str | Path) -> str:
    data = Path(path).read_bytes()
    # Номера пунктов из автонумерации Word вписываются в текст до mammoth: сам он
    # выводит их списками, и в Markdown «5.1.» становится «1.».
    # По умолчанию mammoth встраивает картинки как data URI, как и JS-версия;
    # при обезличивании они заменяются отметкой об удалении.
    html = mammoth.convert_to_html(io.BytesIO(with_numbering_as_text(data))).value
    return html_to_markdown(html)
