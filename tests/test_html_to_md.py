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


def test_colspan_kept():
    html = """<table><tr><td colspan="2"><p>Wide Header</p></td></tr>
      <tr><td><p>A</p></td><td><p>B</p></td></tr></table>"""
    pre = preprocess_table_html(html)
    th = BeautifulSoup(pre, "html.parser").select_one("thead th")
    assert th is not None and th.get("colspan") == "2"
    assert th.get_text().strip() == "Wide Header"
    md = html_to_markdown(html)
    assert "Wide Header" in md
    assert "<table" not in md
