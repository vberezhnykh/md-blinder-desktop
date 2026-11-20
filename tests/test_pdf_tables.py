"""Таблицы в текстовом слое PDF: поиск, место в тексте, продолжение на следующей странице."""

import pymupdf

from md_blinder.convert import pdf_tables
from md_blinder.convert.pdf import analyze_pdf, assemble_markdown
from md_blinder.convert.pdf_tables import join_tables

from fixtures import draw_table

BEFORE = "Synthetic contract text before the table with enough characters."
AFTER = "Synthetic contract text after the table with enough characters."


def convert(doc, path):
    doc.save(path)
    doc.close()
    return assemble_markdown(analyze_pdf(path), {})


def test_layout_model_is_available():
    assert pdf_tables.layout_available()


def test_bordered_table_stays_in_place(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 80), BEFORE, fontsize=10)
    draw_table(page, 50, 100, [60, 200, 100], [["No", "Item", "Sum"], ["1", "Pump", "150 000"], ["2", "Setup", "40 000"]])
    page.insert_text((50, 220), AFTER, fontsize=10)
    md = convert(doc, tmp_path / "t.pdf")
    assert md.split("\n\n") == [
        BEFORE,
        "| No | Item | Sum |\n| --- | --- | --- |\n| 1 | Pump | 150 000 |\n| 2 | Setup | 40 000 |",
        AFTER,
    ]


def test_page_with_only_a_table(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    draw_table(page, 50, 100, [150, 150], [["Name", "Value"], ["Supplier", "Alpha LLC"], ["Buyer", "Beta LLC"]])
    md = convert(doc, tmp_path / "o.pdf")
    assert md == "| Name | Value |\n| --- | --- |\n| Supplier | Alpha LLC |\n| Buyer | Beta LLC |"


def test_vertically_merged_cell_keeps_columns(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 80), BEFORE, fontsize=10)
    rows = [["Stage", "Work", "Sum"], ["One", "Design", "100"], ["", "Review", "20"], ["Two", "Supply", "300"]]
    draw_table(page, 50, 100, [60, 200, 100], rows, merged_below={(1, 0)})
    md = convert(doc, tmp_path / "m.pdf")
    assert "| One | Design | 100 |\n|  | Review | 20 |\n| Two | Supply | 300 |" in md


def test_table_continued_on_next_page_is_joined_without_repeated_header(tmp_path):
    doc = pymupdf.open()
    header = ["No", "Item", "Sum"]
    first = doc.new_page(width=600, height=800)
    first.insert_text((50, 80), BEFORE, fontsize=10)
    draw_table(first, 50, 600, [60, 200, 100], [header] + [[str(i), f"Item {i}", f"{i}00"] for i in range(1, 6)])
    second = doc.new_page(width=600, height=800)
    draw_table(second, 50, 60, [60, 200, 100], [header] + [[str(i), f"Item {i}", f"{i}00"] for i in range(6, 9)])
    second.insert_text((50, 200), AFTER, fontsize=10)
    md = convert(doc, tmp_path / "c.pdf")
    blocks = md.split("\n\n")
    assert blocks[0] == BEFORE and blocks[-1] == AFTER
    table = blocks[1].splitlines()
    assert table[:2] == ["| No | Item | Sum |", "| --- | --- | --- |"]
    assert table[2:] == [f"| {i} | Item {i} | {i}00 |" for i in range(1, 9)]


def test_table_losing_text_falls_back_to_plain_text(tmp_path, monkeypatch):
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    draw_table(page, 50, 100, [100, 100], [["Alpha", "Beta"], ["Gamma", "Delta"]])
    path = tmp_path / "f.pdf"
    doc.save(path)
    doc.close()

    class Lossy:
        bbox = (50, 100, 250, 148)
        col_count = 2

        def extract(self):
            return [["Alpha", "Beta"], ["Gamma", None]]

        def to_html(self):
            return "<table><tr><th>Alpha</th><th>Beta</th></tr><tr><td>Gamma</td><td></td></tr></table>"

    class Found:
        tables = [Lossy()]

    monkeypatch.setattr(pymupdf.Page, "find_tables", lambda self, **kwargs: Found())
    md = assemble_markdown(analyze_pdf(path), {})
    assert "|" not in md
    assert "Delta" in md


def test_wrapped_cell_lines_are_joined():
    join = pdf_tables._join_wrapped
    assert join(["Иванов Петр", "Сергеевич"]) == "Иванов Петр Сергеевич"
    assert join(["централь-", "ный"]) == "центральный"
    assert join(["НЦ-", "200"]) == "НЦ-200"
    assert join(["Москва -", "столица"]) == "Москва - столица"


def test_join_tables_appends_rows_and_drops_repeated_header():
    first = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
    second = "<table><tr><th>A</th><th>B</th></tr><tr><th>3</th><td>4</td></tr></table>"
    joined = join_tables(first, second)
    assert joined.count("<tr>") == 3
    assert "<td>3</td>" in joined
