"""Порт src/lib/htmlToMarkdown.test.ts."""

from bs4 import BeautifulSoup

from md_blinder.convert.html_to_md import html_to_markdown, preprocess_table_html


def test_mammoth_table_header():
    md = html_to_markdown("""
      <table>
        <tr><td><p>Name</p></td><td><p>Age</p></td></tr>
        <tr><td><p>Alice</p></td><td><p>30</p></td></tr>
      </table>""")
    assert "| Name | Age |" in md
    assert "| --- | --- |" in md
    assert "| Alice | 30 |" in md


def test_multi_paragraph_cell():
    md = html_to_markdown("<table><tr><td><p>Header</p></td></tr><tr><td><p>A</p><p>B</p></td></tr></table>")
    assert "A<br>B" in md


def test_cell_content_order():
    md = html_to_markdown("""<p>Literal {{BR}} outside</p><table><tr><td>Details</td></tr><tr><td>
      <p>Obligations: {{BR}}</p><ul><li>Pay 1000 EUR</li><li>Contact Alice Smith</li></ul>
      <p>After list<br>Second line</p><img src="data:image/png;base64,c3ludGhldGlj" alt="Signature">
    </td></tr></table>""")
    assert "Literal {{BR}} outside" in md
    assert "Obligations: {{BR}}" in md
    assert "Pay 1000 EUR" in md
    assert "Contact Alice Smith" in md
    assert "After list<br>Second line" in md
    assert "![Signature](data:image/png;base64,c3ludGhldGlj)" in md
    line = next(l for l in md.split("\n") if "Obligations:" in l)
    assert line.index("Obligations:") < line.index("Pay 1000 EUR")
    assert line.index("Contact Alice Smith") < line.index("After list")
    assert "![Signature]" in line


def test_pipe_escaped():
    assert "A \\| B" in html_to_markdown("<table><tr><td><p>A | B</p></td></tr></table>")


def test_existing_thead():
    md = html_to_markdown("""<table><thead><tr><th>Name</th><th>Age</th></tr></thead>
      <tbody><tr><td>Bob</td><td>25</td></tr></tbody></table>""")
    assert "| Name | Age |" in md
    assert "| --- | --- |" in md
    assert "| Bob | 25 |" in md


def test_header_colspan_expanded():
    md = html_to_markdown("""<table><tr><td colspan="2"><p>Wide Header</p></td></tr>
      <tr><td><p>A</p></td><td><p>B</p></td></tr></table>""")
    assert "<table" not in md
    assert "| Wide Header |  |" in md
    assert "| --- | --- |" in md
    assert "| A | B |" in md


def test_rowspan_keeps_columns_aligned():
    # Вывод mammoth для таблицы с вертикальным и горизонтальным объединением.
    html = (
        '<table><tr><td><p>r0c0</p></td><td colspan="2"><p>r0c1</p><p>r0c2</p></td></tr>'
        '<tr><td rowspan="2"><p>r1c0</p><p>r2c0</p></td><td><p>r1c1</p></td><td><p>r1c2</p></td></tr>'
        "<tr><td><p>r2c1</p></td><td><p>r2c2</p></td></tr></table>"
    )
    assert html_to_markdown(html).splitlines() == [
        "| r0c0 | r0c1<br>r0c2 |  |",
        "| --- | --- | --- |",
        "| r1c0<br>r2c0 | r1c1 | r1c2 |",
        "|  | r2c1 | r2c2 |",
    ]
    assert "colspan" not in preprocess_table_html(html)
    assert "rowspan" not in preprocess_table_html(html)


def test_rowspan_and_colspan_block_in_middle():
    html = (
        "<table><tr><th>A</th><th>B</th><th>C</th></tr>"
        '<tr><td>a1</td><td rowspan="2" colspan="2">merged</td></tr>'
        "<tr><td>a2</td></tr>"
        "<tr><td>a3</td><td>b3</td><td>c3</td></tr></table>"
    )
    assert html_to_markdown(html).splitlines() == [
        "| A | B | C |",
        "| --- | --- | --- |",
        "| a1 | merged |  |",
        "| a2 |  |  |",
        "| a3 | b3 | c3 |",
    ]


def test_short_rows_padded_and_rowspan_clamped():
    html = '<table><tr><td>H1</td><td>H2</td><td>H3</td></tr><tr><td rowspan="9">x</td><td>y</td></tr></table>'
    assert html_to_markdown(html).splitlines() == [
        "| H1 | H2 | H3 |",
        "| --- | --- | --- |",
        "| x | y |  |",
    ]


def test_nested_table_spans_expanded_separately():
    html = (
        "<table><tr><td>Outer</td><td>Second</td></tr>"
        '<tr><td><table><tr><td colspan="2">inner</td></tr><tr><td>i1</td><td>i2</td></tr></table></td>'
        "<td>o2</td></tr></table>"
    )
    soup = BeautifulSoup(preprocess_table_html(html), "html.parser")
    outer = soup.find("table")
    inner = outer.find("table")
    assert len(inner.find("tr").find_all(["td", "th"], recursive=False)) == 2
    outer_rows = [tr for tr in outer.find_all("tr") if tr.find_parent("table") is outer]
    assert [len(tr.find_all(["td", "th"], recursive=False)) for tr in outer_rows] == [2, 2]
