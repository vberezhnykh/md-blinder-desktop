"""OCR через MuPDF. Запускается, если языковые файлы лежат в MD_BLINDER_TESSDATA."""

import os
from pathlib import Path

import pymupdf
import pytest

from md_blinder.cancel import Cancelled, CancelToken
from md_blinder.convert.ocr import ocr_pdf_pages
from md_blinder.convert.pdf import analyze_pdf, assemble_markdown

TESSDATA = os.environ.get("MD_BLINDER_TESSDATA", "")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

pytestmark = [
    pytest.mark.tessdata,
    pytest.mark.skipif(
        not TESSDATA or not all((Path(TESSDATA) / f"{l}.traineddata").exists() for l in ("rus", "ukr", "eng")),
        reason="нет языковых файлов OCR",
    ),
    pytest.mark.skipif(not os.path.exists(FONT), reason="нет шрифта с кириллицей"),
]


def scanned_pdf(path: Path, pages: list[str]) -> Path:
    out = pymupdf.open()
    for text in pages:
        src = pymupdf.open()
        page = src.new_page(width=600, height=800)
        page.insert_font(fontname="dv", fontfile=FONT)
        page.insert_text((40, 100), text, fontsize=16, fontname="dv")
        pix = page.get_pixmap(dpi=144)
        scan = out.new_page(width=600, height=800)
        scan.insert_image(scan.rect, pixmap=pix)
    out.save(path)
    return path


def test_ocr_mixed_languages(tmp_path):
    path = scanned_pdf(tmp_path / "s.pdf", ["Олена Коваленко працює у ТОВ «Весна»\nИван Петров\nJohn Smith signed\n50000"])
    analysis = analyze_pdf(path)
    assert analysis.kinds == ["ocr"]
    text = ocr_pdf_pages(path, analysis.ocr_page_numbers, TESSDATA)[1]
    assert "Олена Коваленко" in text
    assert "Иван Петров" in text
    assert "John Smith" in text
    assert "50000" in text
    md = assemble_markdown(analysis, {1: text})
    assert md == text


def test_ocr_strips_repeated_headers(tmp_path):
    bodies = ["Первый раздел договора", "Второй раздел договора", "Третий раздел договора"]
    pages = [f"Синтетический колонтитул\n{b}\nОкончание {b.split()[0].lower()}" for b in bodies]
    path = scanned_pdf(tmp_path / "h.pdf", pages)
    result = ocr_pdf_pages(path, [1, 2, 3], TESSDATA)
    assert all("колонтитул" not in t for t in result.values())
    assert "Второй раздел" in result[2]


def test_ocr_cancel(tmp_path):
    path = scanned_pdf(tmp_path / "c.pdf", ["Текст"])
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        ocr_pdf_pages(path, [1], TESSDATA, cancel=token)
