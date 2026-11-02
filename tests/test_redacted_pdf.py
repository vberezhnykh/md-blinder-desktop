"""Обезличенный PDF: поиск значений на страницах, слова скана, сборка файла."""

import os
from pathlib import Path

import numpy as np
import pymupdf
import pytest

from md_blinder.anonymize.redacted_pdf import (
    PageLine,
    RedactedPdfError,
    Word,
    _split_words,
    text_frame,
    export_redacted_pdf,
    find_redactions,
    fold,
    normalize_value,
    replacement_values,
)
from md_blinder.anonymize.types import Entity, Position
from md_blinder.components import ocr_models
from md_blinder.convert.ocr import ocr_pdf_pages
from md_blinder.convert.pdf import analyze_pdf, assemble_markdown

FONT = Path(__file__).resolve().parents[1] / "src" / "md_blinder" / "ui" / "assets" / "fonts" / "IBMPlexSans-Regular.ttf"
MODELS = Path(os.environ.get("MD_BLINDER_OCR_MODELS") or ocr_models.ocr_models_dir())
needs_models = pytest.mark.skipif(not ocr_models.is_ocr_present(MODELS), reason="нет моделей OCR")


def line(text: str, y: float = 0, x: float = 0, step: float = 60) -> PageLine:
    words = [Word(w, (x + i * step, y, x + i * step + 50, y + 20)) for i, w in enumerate(text.split())]
    return PageLine(words, (x, y, x + len(words) * step, y + 20))


def entities_for(md: str, values: dict[str, str]) -> list[Entity]:
    result = []
    for value, placeholder in values.items():
        positions = []
        start = md.find(value)
        while start != -1:
            positions.append(Position(start, start + len(value)))
            start = md.find(value, start + 1)
        assert positions, value
        result.append(Entity(id=placeholder, type="PERSON", value=value, placeholder=placeholder, positions=positions))
    return result


# ---------------------------------------------------------------- сравнение текста

def test_fold_keeps_length_and_merges_lookalikes():
    assert fold("ООО «Альфа»") == fold("000 \"Aльфa\"")
    assert len(fold("İstanbul")) == len("İstanbul")
    assert normalize_value(r"Иван\_Петров  ") == fold("Иван_Петров")


def test_value_across_lines_columns_and_hyphenation():
    pages = [[
        line("Директор Иван", y=0),
        line("Петров подписал", y=30),
        line("Компания Ромаш-", y=100),
        line("ка", y=130),
    ]]
    redactions, unmatched = find_redactions(pages, [("Иван Петров", "[PERSON_1]"), ("Ромашка", "[COMPANY_1]")])
    assert unmatched == []
    got = {(r.line, r.first, r.last, r.label, r.continued) for r in redactions[0]}
    # Каждая часть переноса подписана; в текстовом слое метка одна.
    assert got == {
        (0, 1, 1, "[PERSON_1]", False), (1, 0, 0, "[PERSON_1]", True),
        (2, 1, 1, "[COMPANY_1]", False), (3, 0, 0, "[COMPANY_1]", True),
    }


def test_value_in_column_below_not_in_reading_order():
    # Indra прочла сначала строку правой колонки, затем продолжение левой.
    pages = [[
        line("Fernando Jorge Da", y=0, x=0),
        line("Phone: 123", y=0, x=600),
        line("Silva Santiago", y=30, x=0),
    ]]
    redactions, unmatched = find_redactions(pages, [("Fernando Jorge Da Silva Santiago", "[PERSON_1]")])
    assert unmatched == []
    assert {(r.line, r.first, r.last) for r in redactions[0]} == {(0, 0, 2), (2, 0, 1)}


def test_value_does_not_match_inside_longer_word_or_number():
    pages = [[line("Annual report 41234"), line("Ann signed 123")]]
    redactions, unmatched = find_redactions(pages, [("Ann", "[PERSON_1]"), ("123", "[ACCOUNT_1]")])
    assert unmatched == []
    assert {(r.line, r.first) for r in redactions[0]} == {(1, 0), (1, 2)}


def test_partial_word_keeps_label_in_text_and_whole_word_under_plate():
    pages = [[line("ИНН7736321479, КПП")]]
    redactions, _ = find_redactions(pages, [("7736321479", "[REG_NUMBER_1]")])
    (r,) = redactions[0]
    assert (r.first, r.last, r.prefix, r.suffix) == (0, 0, "ИНН", ",")


def test_longer_value_wins_and_unmatched_is_reported():
    pages = [[line("Иван Петров и Петров")]]
    values = replacement_values(
        "Иван Петров и Петров",
        [
            Entity("1", "PERSON", "Петров", "[PERSON_2]", [Position(14, 20)]),
            Entity("2", "PERSON", "Иван Петров", "[PERSON_1]", [Position(0, 11)]),
            Entity("3", "PERSON", "Сидоров", "[PERSON_3]", [Position(0, 4)], anonymize=False),
        ],
    )
    assert [v for v, _ in values] == ["Иван Петров", "Петров"]
    redactions, unmatched = find_redactions(pages, values + [("Сидоров", "[PERSON_3]")])
    assert {(r.first, r.last, r.label) for r in redactions[0]} == {(0, 1, "[PERSON_1]"), (3, 3, "[PERSON_2]")}
    assert unmatched == [("[PERSON_3]", "Сидоров")]


# ---------------------------------------------------------------- слова скана

def _line_image(words: list[str], size: int = 28, space: float = 1.0) -> tuple[np.ndarray, list[pymupdf.Rect]]:
    doc = pymupdf.open()
    page = doc.new_page(width=800, height=60)
    page.insert_font(fontname="plex", fontfile=str(FONT))
    x = 20.0
    font = pymupdf.Font(fontfile=str(FONT))
    rects = []
    for word in words:
        page.insert_text((x, 40), word, fontsize=size, fontname="plex")
        width = font.text_length(word, fontsize=size)
        rects.append(pymupdf.Rect(x, 15, x + width, 45) * (200 / 72))
        x += width + font.text_length(" ", fontsize=size) * space
    pix = page.get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, : pix.width]
    return np.ascontiguousarray(gray), rects


def test_split_words_finds_each_word_on_the_scan():
    words = ["Договор", "между", "Иван", "Петров", "и", "ООО", "«Альфа»"]
    gray, rects = _line_image(words)
    ink = np.argwhere(gray < 128)
    box = (float(ink[:, 1].min()) - 4, float(ink[:, 0].min()) - 4, float(ink[:, 1].max()) + 4, float(ink[:, 0].max()) + 4)
    found = _split_words(gray, box, " ".join(words))
    assert [w.text for w in found] == words
    for word, rect in zip(found, rects):
        x0, _, x1, _ = word.box
        # Плашка закрывает все слово и не доходит до середины соседнего.
        assert x0 <= rect.x0 + 1 and x1 >= rect.x1 - 1, word.text
    for k, word in enumerate(found[:-1]):
        assert word.box[2] < (rects[k + 1].x0 + rects[k + 1].x1) / 2


def test_split_words_on_justified_capitals():
    # Строка с выравниванием по ширине: пробелы втрое шире обычного, слова заглавными.
    words = ["188975,", "Singapore,", "and", "GLOBAL", "ROUTE", "LIMITED", "(the", "Provider"]
    gray, rects = _line_image(words, size=20, space=3.0)
    ink = np.argwhere(gray < 128)
    box = (float(ink[:, 1].min()) - 4, float(ink[:, 0].min()) - 4, float(ink[:, 1].max()) + 4, float(ink[:, 0].max()) + 4)
    found = _split_words(gray, box, " ".join(words))
    for word, rect in zip(found, rects):
        assert word.box[0] <= rect.x0 + 1 and word.box[2] >= rect.x1 - 1, word.text


def test_plates_stay_within_their_line(tmp_path):
    """Плашка не заходит на соседние строки, даже если рамки букв шрифта выше интервала."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_font(fontname="plex", fontfile=str(FONT))
    text = " ".join(["registered office at #02-27, 135 MIDDLE ROAD, BYLANDS BUILDING, 188975, Singapore"] * 6)
    page.insert_textbox(pymupdf.Rect(56, 80, 540, 400), text, fontsize=11, fontname="plex", align=3, lineheight=1.15)
    frame = text_frame(page)
    assert len(frame.lines) >= 4
    for i, line in enumerate(frame.lines):
        for other in frame.lines[i + 1:]:
            for word in line.words:
                for near in other.words:
                    overlap_x = min(word.box[2], near.core[2]) - max(word.box[0], near.core[0])
                    overlap_y = min(word.box[3], near.core[3]) - max(word.box[1], near.core[1])
                    assert overlap_x <= 0 or overlap_y <= 0.5, (word.text, near.text)
                    overlap_y = min(near.box[3], word.core[3]) - max(near.box[1], word.core[1])
                    assert overlap_x <= 0 or overlap_y <= 0.5, (near.text, word.text)


# ---------------------------------------------------------------- сборка файла

def text_pdf(path: Path, lines: list[str]) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    page.insert_font(fontname="plex", fontfile=str(FONT))
    for i, text in enumerate(lines):
        page.insert_text((50, 100 + 30 * i), text, fontsize=14, fontname="plex")
    doc.set_metadata({"author": "Иван Петров", "title": "Договор с Иваном Петровым"})
    doc.save(path)
    return path


def convert(path: Path) -> str:
    analysis = analyze_pdf(path)
    recognized = ocr_pdf_pages(path, analysis.ocr_page_numbers, MODELS) if analysis.ocr_page_numbers else {}
    return assemble_markdown(analysis, recognized)


def test_text_pdf_export_hides_values_and_keeps_text_layer(tmp_path):
    source = text_pdf(tmp_path / "src.pdf", [
        "Договор между ООО «Альфа» и Иван Петров.",
        "Телефон +7 916 123-45-67, подписал Иван Петров.",
    ])
    md = convert(source)
    entities = entities_for(md, {"Иван Петров": "[PERSON_1]", "+7 916 123-45-67": "[PHONE_1]"})
    target = tmp_path / "out.pdf"
    report = export_redacted_pdf(source, target, md, entities, None)
    assert report.pages == 1 and report.unmatched == [] and report.redactions == 3
    with pymupdf.open(target) as out:
        text = out[0].get_text()
        assert "Петров" not in text and "916" not in text
        assert text.count("[PERSON_1]") == 2 and "[PHONE_1]" in text
        assert "Договор между ООО «Альфа» и [PERSON_1]." in text
        assert not out.metadata.get("author") and not out.metadata.get("title")
        # Страница — одна картинка, весь текст невидимый.
        assert len(out[0].get_images()) == 1
        for trace in out[0].get_texttrace():
            assert trace["type"] == 3
    assert b"\xd0\x9f\xd0\xb5\xd1\x82\xd1\x80\xd0\xbe\xd0\xb2" not in target.read_bytes()  # «Петров» в UTF-8


def test_lines_missing_from_markdown_are_hidden(tmp_path):
    source = text_pdf(tmp_path / "src.pdf", ["Первая строка договора", "Секретная пометка Сидорова", "Последняя строка"])
    md = "Первая строка договора\n\nПоследняя строка"
    target = tmp_path / "out.pdf"
    report = export_redacted_pdf(source, target, md, [], None)
    assert report.hidden_lines == 1
    with pymupdf.open(target) as out:
        text = out[0].get_text()
    assert "Сидорова" not in text and "Последняя строка" in text


def test_foreign_pdf_is_refused(tmp_path):
    source = text_pdf(tmp_path / "src.pdf", ["Совсем другой документ", "про погоду и море"])
    with pytest.raises(RedactedPdfError):
        export_redacted_pdf(source, tmp_path / "out.pdf", "Договор поставки между сторонами", [], None)
    assert not (tmp_path / "out.pdf").exists()


@needs_models
@pytest.mark.ocr
def test_scan_export_hides_values_on_the_image(tmp_path):
    src = pymupdf.open()
    page = src.new_page(width=600, height=800)
    page.insert_font(fontname="plex", fontfile=str(FONT))
    lines = [
        "Договор поставки № 15 от 1 марта 2026 года",
        "Покупатель: Иван Петров, паспорт 4510 123456",
        "Поставщик: ООО «Ромашка», ИНН 7736321479",
        "Подписи сторон: Иван Петров",
    ]
    for i, text in enumerate(lines):
        page.insert_text((40, 100 + 40 * i), text, fontsize=14, fontname="plex")
    scan = pymupdf.open()
    scan_page = scan.new_page(width=600, height=800)
    scan_page.insert_image(scan_page.rect, pixmap=page.get_pixmap(dpi=150))
    source = tmp_path / "scan.pdf"
    scan.save(source)

    md = convert(source)
    entities = entities_for(md, {"Иван Петров": "[PERSON_1]", "7736321479": "[REG_NUMBER_1]", "4510 123456": "[ID_DOC_1]"})
    target = tmp_path / "out.pdf"
    report = export_redacted_pdf(source, target, md, entities, MODELS)
    assert report.unmatched == []
    with pymupdf.open(target) as out:
        layer = out[0].get_text()
    assert "Петров" not in layer and "7736321479" not in layer and "[PERSON_1]" in layer
    # Картинку читает OCR заново: значений на ней нет, остальной текст на месте.
    seen = ocr_pdf_pages(target, [1], MODELS)[1]
    assert "Петров" not in seen and "7736321479" not in seen and "123456" not in seen
    assert "Договор поставки" in seen and "Ромашка" in seen


JUSTIFIED = (
    "This BULK SMS SERVICE AGREEMENT (the \"Agreement\") is made and entered into on September 30, 2025 (the "
    "\"Effective Date\") by and between SKYLINE DIGITAL PTE. LTD. (the \"Client\"), a legal entity registered under "
    "the laws of Singapore, with the registration number: 202522193W, having its registered office at #02-27, "
    "135 MIDDLE ROAD, BYLANDS BUILDING, 188975, Singapore, and GLOBAL ROUTE LIMITED (the \"Service Provider\"), a "
    "legal entity duly incorporated and operating under the laws of Hong Kong, registration number 3312456 with its "
    "registered office at Unit 5, 12/F, Harbour Centre, 25 Harbour Road, Wan Chai, Hong Kong."
)


@needs_models
@pytest.mark.ocr
def test_justified_scan_hides_whole_values(tmp_path):
    """Скан текста с выравниванием по ширине: пробелы разной ширины, слова заглавными.
    Раньше граница слова съезжала, и «LIMITED» выглядывал из-под плашки как «ITED»."""
    src = pymupdf.open()
    page = src.new_page(width=595, height=842)
    page.insert_textbox(pymupdf.Rect(56, 80, 540, 400), JUSTIFIED, fontsize=11, fontname="helv", align=3, lineheight=1.15)
    scan = pymupdf.open()
    scan_page = scan.new_page(width=595, height=842)
    scan_page.insert_image(scan_page.rect, pixmap=page.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY))
    source = tmp_path / "scan.pdf"
    scan.save(source)

    md = convert(source)
    values = {
        "SKYLINE DIGITAL PTE. LTD.": "[COMPANY_1]",
        "GLOBAL ROUTE LIMITED": "[COMPANY_2]",
        "3312456": "[REG_NUMBER_1]",
        "Unit 5, 12/F, Harbour Centre, 25 Harbour Road, Wan Chai, Hong Kong": "[ADDRESS_1]",
    }
    import re

    entities = []
    for value, placeholder in values.items():
        pattern = re.compile(r"\s+".join(re.escape(t) for t in value.split()))
        positions = [Position(m.start(), m.end()) for m in pattern.finditer(md)]
        assert positions, value
        entities.append(Entity(placeholder, "OTHER", value, placeholder, positions))
    target = tmp_path / "out.pdf"
    assert export_redacted_pdf(source, target, md, entities, MODELS).unmatched == []
    seen = ocr_pdf_pages(target, [1], MODELS)[1]
    for word in ("SKYLINE", "DIGITAL", "GLOBAL", "ROUTE", "LIMITED", "ITED", "3312456", "Unit", "Harbour", "Chai"):
        assert word not in seen, word
    assert "Singapore" in seen and "Service Provider" in seen
