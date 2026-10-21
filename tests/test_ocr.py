"""OCR через Indra. Запускается, если модели OCR скачаны в каталог данных (MD_BLINDER_DATA_DIR)
или лежат в MD_BLINDER_OCR_MODELS."""

import os
from pathlib import Path

import pymupdf
import pytest

from md_blinder.cancel import Cancelled, CancelToken
from md_blinder.components import ocr_models
from md_blinder.convert.ocr import markdown_items, ocr_pdf_pages, prose_columns
from md_blinder.convert.pdf import analyze_pdf, assemble_markdown

MODELS = Path(os.environ.get("MD_BLINDER_OCR_MODELS") or ocr_models.ocr_models_dir())
FONT = Path(__file__).resolve().parents[1] / "src" / "md_blinder" / "ui" / "assets" / "fonts" / "IBMPlexSans-Regular.ttf"

needs_models = pytest.mark.skipif(not ocr_models.is_ocr_present(MODELS), reason="нет моделей OCR")
pytestmark = pytest.mark.ocr


def scanned_pdf(path: Path, pages: list[str | list[tuple[float, str]]], dpi: int = 150) -> Path:
    """Страница — текст от y=100 или список (y, текст)."""
    out = pymupdf.open()
    for blocks in pages:
        src = pymupdf.open()
        page = src.new_page(width=600, height=800)
        page.insert_font(fontname="plex", fontfile=str(FONT))
        for y, text in [(100, blocks)] if isinstance(blocks, str) else blocks:
            page.insert_text((40, y), text, fontsize=14, fontname="plex")
        pix = page.get_pixmap(dpi=dpi)
        scan = out.new_page(width=600, height=800)
        scan.insert_image(scan.rect, pixmap=pix)
    out.save(path)
    return path


@needs_models
def test_ocr_mixed_languages(tmp_path):
    path = scanned_pdf(tmp_path / "s.pdf", [
        "Олена Коваленко працює у ТОВ «Весна»\nИван Петров, ИНН 7736321479, КПП 773601001\n"
        "John Smith signed\n50000"
    ])
    analysis = analyze_pdf(path)
    assert analysis.kinds == ["ocr"]
    text = ocr_pdf_pages(path, analysis.ocr_page_numbers, MODELS)[1]
    assert "Олена Коваленко" in text
    assert "Иван Петров" in text
    assert "ИНН 7736321479" in text
    assert "КПП 773601001" in text
    assert "John Smith" in text
    assert "50000" in text
    md = assemble_markdown(analysis, {1: text})
    assert md == text


@needs_models
def test_ocr_strips_repeated_headers(tmp_path):
    bodies = ["Первый раздел договора", "Второй раздел договора", "Третий раздел договора"]
    pages = [[(50, "Синтетический колонтитул"), (300, b), (500, f"Окончание {b.split()[0].lower()}")] for b in bodies]
    path = scanned_pdf(tmp_path / "h.pdf", pages)
    result = ocr_pdf_pages(path, [1, 2, 3], MODELS)
    assert all("колонтитул" not in t for t in result.values())
    assert "Второй раздел" in result[2]


@needs_models
def test_ocr_reads_table(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    page.insert_font(fontname="plex", fontfile=str(FONT))
    rows = [("Item", "Amount"), ("Cleaning", "3,750.00"), ("Windows", "960.00")]
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            rect = pymupdf.Rect(60 + c * 200, 100 + r * 32, 260 + c * 200, 132 + r * 32)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((rect.x0 + 8, rect.y1 - 10), cell, fontsize=13, fontname="plex")
    pix = page.get_pixmap(dpi=200)
    scan = pymupdf.open()
    scan.new_page(width=600, height=800).insert_image(pymupdf.Rect(0, 0, 600, 800), pixmap=pix)
    scan.save(tmp_path / "t.pdf")
    text = ocr_pdf_pages(tmp_path / "t.pdf", [1], MODELS)[1]
    assert "| Cleaning | 3,750.00 |" in text
    assert "<table" not in text


@needs_models
def test_ocr_cancel(tmp_path):
    path = scanned_pdf(tmp_path / "c.pdf", ["Текст"])
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        ocr_pdf_pages(path, [1], MODELS, cancel=token)


def test_page_items_tables_pictures_and_twins():
    markdown = (
        "## Договор\n\nИHH 7736321479, ОГPH 1027700132195\n\n![](picture-0)\n\n"
        "<table>\n<tr><th>A</th><th>B</th></tr>\n<tr><td>1</td><td>2</td></tr>\n</table>"
    )
    assert markdown_items(markdown) == [
        "## Договор", "ИНН 7736321479, ОГРН 1027700132195", "| A | B |\n| --- | --- |\n| 1 | 2 |",
    ]


def _two_column_lines(right_text: str) -> list[tuple[list[float], str]]:
    lines = []
    for row in range(4):
        y = 100 + row * 30
        # Выключка по ширине режет строку в разных местах.
        lines.append(([50, y, 150 + row * 30, y + 20], f"Левая колонка строка {row}"))
        lines.append(([160 + row * 30, y, 290, y + 20], "продолжение"))
        lines.append(([310, y, 560, y + 20], right_text.format(row=row)))
    return lines


def test_prose_columns_reads_columns_in_turn():
    lines = _two_column_lines("Right column line {row} of the text")
    paragraphs = prose_columns([40, 90, 570, 240], lines)
    assert paragraphs == [
        " ".join(f"Левая колонка строка {r} продолжение" for r in range(4)),
        " ".join(f"Right column line {r} of the text" for r in range(4)),
    ]


def test_prose_columns_keeps_data_tables():
    # Короткая колонка с суммами: это таблица, а не текст в две колонки.
    assert prose_columns([40, 90, 570, 240], _two_column_lines("{row}50.00")) is None
    # Один столбец текста: строки перекрываются, колонок нет.
    single = [([50, 100 + r * 30, 560, 120 + r * 30], "Обычная строка абзаца договора") for r in range(6)]
    assert prose_columns([40, 90, 570, 300], single) is None
