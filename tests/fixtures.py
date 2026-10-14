"""Синтетические DOCX и PDF для тестов. Никаких реальных данных."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pymupdf

LONG_TEXT = (
    "Synthetic document text with enough body characters to prove that its text layer "
    "contains useful searchable content."
)


def make_pdf(path: Path, pages: list[dict]) -> Path:
    """pages: [{"lines": [(x, y_top_down, text, size)], "image": bool}]"""
    doc = pymupdf.open()
    for spec in pages:
        page = doc.new_page(width=600, height=800)
        if spec.get("image"):
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), 0)
            pix.set_rect(pix.irect, (200, 200, 200))
            page.insert_image(page.rect, pixmap=pix)
        for x, y, text, size in spec.get("lines", []):
            page.insert_text((x, y), text, fontsize=size, fontname="helv")
    doc.save(path)
    doc.close()
    return path


def make_docx(path: Path, body_xml: str) -> Path:
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body_xml}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document)
    return path


def para(text: str) -> str:
    return f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def table(rows: list[list[str]]) -> str:
    cells = "".join(
        "<w:tr>" + "".join(f"<w:tc>{para(c)}</w:tc>" for c in row) + "</w:tr>" for row in rows
    )
    return f"<w:tbl>{cells}</w:tbl>"
