import zipfile

from md_blinder.convert.docx import docx_to_markdown
from md_blinder.convert.docx_numbering import format_number, with_numbering_as_text

from fixtures import make_docx, numbering_level, numbering_num, para, table

CLAUSES = (
    '<w:abstractNum w:abstractNumId="0">'
    + numbering_level(0, "%1.")
    + numbering_level(1, "%1.%2.")
    + numbering_level(2, "(%3)", "lowerLetter")
    + "</w:abstractNum>"
)


def test_numbers_continue_across_table_rows(tmp_path):
    rows = [
        [para("Services", (1, 0)) + para("The Consultant shall perform the Services.", (1, 1)), ""],
        [
            para("Warranties", (1, 0))
            + para("The Consultant warrants that:", (1, 1))
            + para("it has the right to enter into this Agreement;", (1, 2))
            + para("the Services do not infringe any rights.", (1, 2))
            + para("The Client warrants that it has full capacity.", (1, 1)),
            "",
        ],
    ]
    md = docx_to_markdown(make_docx(tmp_path / "t.docx", table(rows, raw=True), CLAUSES + numbering_num(1, 0)))
    assert "| 1. Services<br>1.1. The Consultant shall perform the Services. |" in md
    assert (
        "| 2. Warranties<br>2.1. The Consultant warrants that:<br>(a) it has the right to enter into this Agreement;"
        "<br>(b) the Services do not infringe any rights.<br>2.2. The Client warrants that it has full capacity. |"
    ) in md


def test_numbers_continue_after_plain_paragraph_and_restart_on_override(tmp_path):
    body = (
        para("Term", (1, 0))
        + para("Payment", (1, 0))
        + para("Текст без номера.")
        + para("Liability", (1, 0))
        + para("Приложение: первый пункт", (2, 0))
        + para("Приложение: второй пункт", (2, 0))
    )
    restart = '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride>'
    md = docx_to_markdown(make_docx(tmp_path / "t.docx", body, CLAUSES + numbering_num(1, 0) + numbering_num(2, 0, restart)))
    lines = [line for line in md.splitlines() if line]
    assert lines == [
        "1. Term",
        "2. Payment",
        "Текст без номера.",
        "3. Liability",
        "1. Приложение: первый пункт",
        "2. Приложение: второй пункт",
    ]


def test_numbering_from_paragraph_style_with_legal_and_letter_formats(tmp_path):
    numbering = (
        '<w:abstractNum w:abstractNumId="0">'
        + numbering_level(0, "Article %1", "upperRoman")
        + numbering_level(1, "%1.%2", extra="<w:isLgl/>")
        + numbering_level(2, "%3)", "russianLower")
        + "</w:abstractNum>"
        + numbering_num(5, 0)
    )
    styles = (
        '<w:style w:type="paragraph" w:styleId="Clause1"><w:name w:val="heading 1"/>'
        '<w:pPr><w:numPr><w:numId w:val="5"/></w:numPr></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Clause2"><w:basedOn w:val="Clause1"/>'
        '<w:pPr><w:numPr><w:ilvl w:val="1"/></w:numPr></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Clause2Body"><w:basedOn w:val="Clause2"/></w:style>'
    )
    body = (
        para("Definitions", style="Clause1")
        + para("Термины.", style="Clause2Body")
        + para("первый", (5, 2))
        + para("второй", (5, 2))
        + para("Term", style="Clause1")
        + para("Срок.", style="Clause2Body")
        + para("Без номера.", (0, 0), style="Clause2Body")
    )
    md = docx_to_markdown(make_docx(tmp_path / "t.docx", body, numbering, styles))
    lines = [line for line in md.splitlines() if line]
    assert lines == [
        "# Article I Definitions",
        "1.1 Термины.",
        "а) первый",
        "б) второй",
        "# Article II Term",
        "2.1 Срок.",
        "Без номера.",
    ]


def test_bullets_stay_a_markdown_list(tmp_path):
    numbering = (
        '<w:abstractNum w:abstractNumId="0">'
        + numbering_level(0, "%1.")
        + numbering_level(1, "•", "bullet")
        + numbering_level(2, "o", "bullet")
        + "</w:abstractNum>"
        + numbering_num(1, 0)
    )
    body = (
        para("Obligations", (1, 0))
        + para("first", (1, 1))
        + para("nested", (1, 2))
        + para("second", (1, 1))
        + para("Fees", (1, 0))
    )
    md = docx_to_markdown(make_docx(tmp_path / "t.docx", body, numbering))
    lines = [line.rstrip() for line in md.splitlines() if line.strip()]
    assert lines == ["1. Obligations", "* first", "  * nested", "* second", "2. Fees"]


def test_format_number():
    assert format_number(4, "lowerRoman") == "iv"
    assert format_number(14, "upperRoman") == "XIV"
    assert format_number(2, "lowerLetter") == "b"
    assert format_number(28, "upperLetter") == "BB"
    assert format_number(10, "russianLower") == "к"
    assert format_number(3, "decimalZero") == "03"
    assert format_number(7, "decimal") == "7"


def test_broken_numbering_keeps_the_document(tmp_path):
    path = make_docx(tmp_path / "t.docx", para("Пункт", (1, 0)), CLAUSES + numbering_num(1, 0))
    with zipfile.ZipFile(path) as zf:
        parts = {name: zf.read(name) for name in zf.namelist()}
    parts["word/numbering.xml"] = b"<w:numbering"
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in parts.items():
            zf.writestr(name, payload)
    data = path.read_bytes()
    assert with_numbering_as_text(data) == data
