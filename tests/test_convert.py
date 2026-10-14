from md_blinder.convert.docx import docx_to_markdown
from md_blinder.convert.file_type import detect_file_kind
from md_blinder.convert.pdf import analyze_pdf, assemble_markdown

from fixtures import LONG_TEXT, make_docx, make_pdf, para, table


def test_detect_file_kind(tmp_path):
    pdf = make_pdf(tmp_path / "a.bin", [{"lines": [(20, 100, "x", 12)]}])
    assert detect_file_kind(pdf) == "pdf"
    docx = make_docx(tmp_path / "b.docx", para("x"))
    assert detect_file_kind(docx) == "docx"
    legacy = tmp_path / "c.doc"
    legacy.write_bytes(b"\xd0\xcf\x11\xe0")
    assert detect_file_kind(legacy) == "legacy-doc"
    other = tmp_path / "d.txt"
    other.write_text("hi")
    assert detect_file_kind(other) == "unknown"


def test_docx_paragraphs_and_table(tmp_path):
    path = make_docx(tmp_path / "t.docx", para("Договор поставки") + table([["Имя", "Роль"], ["Company Alpha", "Seller"]]))
    md = docx_to_markdown(path)
    assert "Договор поставки" in md
    assert "| Имя | Роль |" in md
    assert "| --- | --- |" in md
    assert "| Company Alpha | Seller |" in md


def test_text_pdf(tmp_path):
    path = make_pdf(tmp_path / "t.pdf", [{"lines": [(20, 400, LONG_TEXT[:80], 10), (20, 412, LONG_TEXT[80:], 10)]}])
    analysis = analyze_pdf(path)
    assert analysis.kinds == ["text"]
    md = assemble_markdown(analysis, {})
    assert md == LONG_TEXT


def test_mixed_pdf_order_and_ocr_pages(tmp_path):
    pages = []
    for name in ("Alpha.", "Beta.", "Gamma."):
        pages.append({"lines": [(20, 400, f"{name} {LONG_TEXT[:70]}", 10)]})
        pages.append({"image": True})
    path = make_pdf(tmp_path / "m.pdf", pages)
    analysis = analyze_pdf(path)
    assert analysis.kinds == ["text", "ocr"] * 3
    assert analysis.ocr_page_numbers == [2, 4, 6]
    md = assemble_markdown(analysis, {n: f"Recognized {n}" for n in (2, 4, 6)})
    order = [md.index(s) for s in ("Alpha.", "Recognized 2", "Beta.", "Recognized 4", "Gamma.", "Recognized 6")]
    assert order == sorted(order)


def test_scan_with_text_header_goes_to_ocr(tmp_path):
    path = make_pdf(tmp_path / "s.pdf", [{"image": True, "lines": [(20, 30, "Synthetic header", 12)]}])
    assert analyze_pdf(path).kinds == ["ocr"]


def test_force_ocr_skips_text_layer(tmp_path):
    path = make_pdf(tmp_path / "f.pdf", [{"lines": [(20, 400, LONG_TEXT, 8)]}] * 2)
    analysis = analyze_pdf(path, force_ocr=True)
    assert analysis.kinds == ["ocr", "ocr"]
    assert analysis.pages == [[], []]


def test_two_column_signature_block(tmp_path):
    lines = [
        (50, 600, "Company Alpha", 10), (300, 600, "PhoneMarker One", 10),
        (50, 615, "Party Beta", 10), (300, 615, "EmailMarker Two", 10),
    ]
    path = make_pdf(tmp_path / "c.pdf", [{"lines": lines + [(20, 300, LONG_TEXT, 8)]}])
    md = assemble_markdown(analyze_pdf(path), {})
    for block in md.split("\n\n"):
        assert not ("Company" in block and "PhoneMarker" in block)
        assert not ("Party" in block and "EmailMarker" in block)
