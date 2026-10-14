"""Порт src/lib/pdfStructure.test.ts."""

import re

from md_blinder.convert.pdf_structure import (
    PdfPageLayout,
    PdfTextItem,
    items_to_markdown,
    strip_repeated_lines_and_page_numbers,
)

BODY = 10


def item(**kw):
    base = dict(str="text", x=0, y=0, w=20, fontSize=BODY, fontName="F1", hasEOL=False)
    base.update(kw)
    return PdfTextItem(**base)


def test_body_size_and_heading_levels():
    pages = [[
        item(str="Title Sixteen", y=300, w=100, fontSize=BODY * 1.6, hasEOL=True),
        item(str="Heading Thirteen", y=260, w=100, fontSize=BODY * 1.3, hasEOL=True),
        item(str="Heading Twelve", y=220, w=100, fontSize=BODY * 1.2, hasEOL=True),
        item(str="This is the body paragraph text of the sample contract document repeated for length.",
             y=180, w=400, hasEOL=True),
    ]]
    md = items_to_markdown(pages)
    assert "# Title Sixteen" in md
    assert "## Heading Thirteen" in md
    assert "### Heading Twelve" in md


def test_line_assembly():
    pages = [[
        item(str="Section", x=0, y=300, w=50),
        item(str="One", x=100, y=300, w=30, hasEOL=True),
        item(str="Company Alpha agreement text goes here for padding purposes only today.",
             y=260, w=300, hasEOL=True),
    ]]
    blocks = items_to_markdown(pages).split("\n\n")
    assert blocks[0] == "Section One"
    assert "Company Alpha agreement" in blocks[1]


def test_all_caps_heading():
    pages = [[
        item(str="GENERAL PROVISIONS", y=300, w=120, hasEOL=True),
        item(str="This paragraph explains general provisions of the sample contract for testing purposes.",
             y=260, w=400, hasEOL=True),
    ]]
    assert "## GENERAL PROVISIONS" in items_to_markdown(pages)


def test_numbered_clause_kept():
    pages = [[
        item(str="2.1 The parties agree to the terms outlined in this sample section below.", y=300, w=400, hasEOL=True),
        item(str="This body paragraph follows the clause and is padded with extra sample words.", y=260, w=400, hasEOL=True),
    ]]
    md = items_to_markdown(pages)
    assert "2.1 The parties agree to the terms outlined in this sample section below." in md
    assert "1. 2.1" not in md


def test_bullet_and_lettered():
    md = items_to_markdown([[item(str="• Foo bar sample bullet item for the synthetic contract test.", y=300, w=400, hasEOL=True)]])
    assert "- Foo bar sample bullet item for the synthetic contract test." in md
    md = items_to_markdown([[item(str="(a) Foo bar sample lettered sub-clause for the synthetic contract test.", y=300, w=400, hasEOL=True)]])
    assert "(a) Foo bar sample lettered sub-clause for the synthetic contract test." in md


def test_paragraph_merging():
    pages = [[
        item(str="First line of a sample paragraph about Company Alpha and its obligations.", y=300, w=400, hasEOL=True),
        item(str="continues here with more sample padding words for the test paragraph text.", y=288, w=400, hasEOL=True),
        item(str="A separate paragraph far below the first one in the sample document text.", y=200, w=400, hasEOL=True),
    ]]
    blocks = items_to_markdown(pages).split("\n\n")
    assert blocks == [
        "First line of a sample paragraph about Company Alpha and its obligations. "
        "continues here with more sample padding words for the test paragraph text.",
        "A separate paragraph far below the first one in the sample document text.",
    ]


def test_adjacent_columns_not_merged():
    pages = [[
        item(str="Left column body cell text for the sample contract padding here.", x=0, y=300, w=200, hasEOL=True),
        item(str="Right column body cell text for the sample contract padding here.", x=300, y=300, w=200, hasEOL=True),
    ]]
    blocks = items_to_markdown(pages).split("\n\n")
    assert blocks == [
        "Left column body cell text for the sample contract padding here.",
        "Right column body cell text for the sample contract padding here.",
    ]


def test_two_columns_not_interleaved():
    pages = [[
        item(str="Company", x=50, y=200, w=45),
        item(str="Alpha", x=100, y=200, w=35, hasEOL=True),
        item(str="PhoneMarker", x=300, y=200, w=70),
        item(str="One", x=375, y=200, w=25, hasEOL=True),
        item(str="Party", x=50, y=185, w=45),
        item(str="Beta", x=100, y=185, w=35, hasEOL=True),
        item(str="EmailMarker", x=300, y=185, w=70),
        item(str="Two", x=375, y=185, w=25, hasEOL=True),
    ]]
    md = items_to_markdown(pages)
    blocks = md.split("\n\n")
    assert "Company Alpha" in blocks
    assert "PhoneMarker One" in blocks
    for b in blocks:
        assert not ("Company" in b and "PhoneMarker" in b)
        assert not ("Party" in b and "EmailMarker" in b)
    assert not re.search(r"Company[^\n]*PhoneMarker[^\n]*Alpha", md)


def test_header_and_page_number_suppression():
    def page(n):
        return [
            item(str="Sample Contract Header", y=300, w=200, hasEOL=True),
            item(str=f"Body paragraph number {n} for the synthetic sample contract padding text goes here.",
                 y=260, w=400, hasEOL=True),
            item(str=str(n), y=20, w=10, hasEOL=True),
        ]

    layouts = [PdfPageLayout(bottom=0, height=320, pageNumber=n, totalPages=3) for n in (1, 2, 3)]
    md = items_to_markdown([page(1), page(2), page(3)], layouts)
    assert "Sample Contract Header" not in md
    for n in (1, 2, 3):
        assert f"Body paragraph number {n}" in md
    assert not any(re.fullmatch(r"\d+", b) for b in md.split("\n\n"))


def test_empty():
    assert items_to_markdown([]) == ""
    assert items_to_markdown([[]]) == ""


def test_ocr_strip_preserves_numbers():
    lines = ["Bank account:", "40702810900000012345", "Payment amount:", "50000", "Page 3 describes payment.", "End of contract."]
    assert strip_repeated_lines_and_page_numbers([lines]) == [lines]
    numbered = ["Intro", "1", "Page 2", "Closing"]
    assert strip_repeated_lines_and_page_numbers([numbered]) == [numbered]


def test_numeric_body_line_kept():
    page = [item(str=s, y=600 - i * 30, hasEOL=True)
            for i, s in enumerate(["Payment amount:", "1", "50000", "40702810900000012345", "Closing."])]
    md = items_to_markdown([page], [PdfPageLayout(0, 800, 1, 1)])
    assert "1" in md.split("\n\n")
    assert "50000" in md and "40702810900000012345" in md


def test_edge_amount_kept():
    page = [item(str="Amount payable", y=100, hasEOL=True), item(str="5000", y=20)]
    assert "5000" in items_to_markdown([page], [PdfPageLayout(0, 800, 1, 1)])


def test_ocr_strip_repeated_headers_and_labels():
    pages = [
        ["Sample Contract Header", "Body paragraph number 1 for the synthetic sample text.", "12"],
        ["Sample Contract Header", "Body paragraph number 2 for the synthetic sample text.", "Page 3"],
        ["Sample Contract Header", "Body paragraph number 3 for the synthetic sample text.", "14"],
    ]
    assert strip_repeated_lines_and_page_numbers(pages) == [
        ["Body paragraph number 1 for the synthetic sample text.", "12"],
        ["Body paragraph number 2 for the synthetic sample text."],
        ["Body paragraph number 3 for the synthetic sample text.", "14"],
    ]


def test_ocr_strip_fewer_than_three_pages():
    pages = [
        ["Sample Contract Header", "Body paragraph one for the synthetic sample text.", "1"],
        ["Sample Contract Header", "Body paragraph two for the synthetic sample text.", "Page 2"],
    ]
    result = strip_repeated_lines_and_page_numbers(pages)
    assert result[0] == pages[0]
    assert result[1] == ["Sample Contract Header", "Body paragraph two for the synthetic sample text."]


def test_ocr_strip_does_not_mutate():
    pages = [["Header", "Body one."], ["Header", "Body two."], ["Header", "Body three."]]
    snapshot = [list(p) for p in pages]
    strip_repeated_lines_and_page_numbers(pages)
    assert pages == snapshot
