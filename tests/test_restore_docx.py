"""Обратная подстановка в Word: метки меняются на значения, оформление и правки остаются."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from lxml import etree

from md_blinder.anonymize.redacted_docx import RedactedDocxError, export_redacted_docx
from md_blinder.anonymize.restore_docx import plain_value, restore_docx, restored_file_name
from md_blinder.convert.docx import docx_to_markdown

from test_redacted_docx import HEAD, NS, REL, VALUES, W_NS, entities_for, make_rich_docx, para, part, run

W = f"{{{W_NS}}}"
MAPPING = {"[COMPANY_1]": "ООО «Ро**машка»**", "[PERSON_1]": "Иван Петров", "[EMAIL_1]": "ivan\\_petrov@example.com"}


def texts(root: etree._Element) -> list[str]:
    return ["".join(t.text or "" for t in p.iter(f"{W}t")) for p in root.iter(f"{W}p")]


def make_edited_docx(path: Path, body: str, comment: str = "") -> Path:
    """Документ, который вернула нейросеть: метки, правки в режиме рецензирования, примечание."""
    parts = {
        "[Content_Types].xml": (
            f'{HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            f'{HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{REL}officeDocument" Target="word/document.xml"/></Relationships>'
        ),
        "word/document.xml": f"{HEAD}<w:document {NS}><w:body>{body}</w:body></w:document>",
        "word/_rels/document.xml.rels": (
            f'{HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId12" Type="{REL}comments" Target="comments.xml"/></Relationships>'
        ),
        "word/comments.xml": (
            f'{HEAD}<w:comments {NS}><w:comment w:id="0" w:author="AI" w:initials="AI">'
            f"<w:p>{run(comment)}</w:p></w:comment></w:comments>"
        ),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data.encode("utf-8"))
    return path


def test_round_trip_returns_real_values_with_formatting(tmp_path):
    source = make_rich_docx(tmp_path / "contract.docx")
    md = docx_to_markdown(source)
    entities = entities_for(md, VALUES)
    redacted = tmp_path / "redacted.docx"
    export_redacted_docx(source, redacted, md, entities)
    mapping = {e.placeholder: e.value for e in entities}
    mapping["[COMPANY_1]"] = "ООО «Ро**машка»**"  # срез Markdown с выделением

    report = restore_docx(redacted, tmp_path / "back.docx", mapping)
    data = (tmp_path / "back.docx").read_bytes()
    body = part(data, "word/document.xml")
    first = body.find(f"{W}body/{W}p")
    runs = [(r.find(f"{W}t").text or "", r.find(f"{W}rPr/{W}b") is not None) for r in first.iter(f"{W}r")]
    # Значение встает в первый фрагмент метки и берет его оформление.
    assert runs[0] == ("Поставщик: ООО «Ромашка»", False)
    assert "".join(t for t, _ in runs) == "Поставщик: ООО «Ромашка», директор Иван Петров."
    assert "Почта: ivan@example.com, телефон +7 916 765-43-21." in texts(body)
    assert len(body.findall(f".//{W}numPr")) == 2 and len(body.findall(f".//{W}tbl")) == 1
    assert texts(part(data, "word/header1.xml")) == ["Иван Петров — конфиденциально"]
    assert "Подписал Иван Петров лично." in texts(part(data, "word/footnotes.xml"))
    assert report.unknown == []
    assert report.labels == {"[COMPANY_1]", "[PERSON_1]", "[EMAIL_1]", "[PHONE_1]"}


def test_edited_document_keeps_revisions_and_comments(tmp_path):
    body = "".join([
        # Метка разбита на фрагменты, нейросеть добавила свою правку и примечание.
        para(run("Исполнитель: [PER"), run("SON_1]", bold=True), run(", почта [ email_1 ].")),
        para(
            run("Сторона: "),
            '<w:del w:id="1" w:author="AI"><w:r><w:delText>[PERSON_1]</w:delText></w:r></w:del>'
            '<w:ins w:id="2" w:author="AI">' + run("[COMPANY_1]") + "</w:ins>",
            '<w:commentRangeStart w:id="0"/>' + run(" подписывает") + '<w:commentRangeEnd w:id="0"/>'
            '<w:r><w:commentReference w:id="0"/></w:r>',
        ),
        para(run("Новая метка [PERSON_9] и ссылка [1]. Через абзац [PERSON_")),
        para(run("1] не меняется.")),
    ])
    edited = make_edited_docx(tmp_path / "edited.docx", body, comment="Проверьте, что [PERSON_1] директор")
    report = restore_docx(edited, tmp_path / "back.docx", MAPPING)
    data = (tmp_path / "back.docx").read_bytes()
    root = part(data, "word/document.xml")
    lines = texts(root)
    assert lines[0] == "Исполнитель: Иван Петров, почта ivan_petrov@example.com."
    assert lines[1] == "Сторона: ООО «Ромашка» подписывает"  # видимый текст, удаление в delText
    assert [t.text for t in root.iter(f"{W}delText")] == ["Иван Петров"]
    assert root.find(f".//{W}ins") is not None and root.find(f".//{W}commentReference") is not None
    assert texts(part(data, "word/comments.xml")) == ["Проверьте, что Иван Петров директор"]
    assert lines[2:] == ["Новая метка [PERSON_9] и ссылка [1]. Через абзац [PERSON_", "1] не меняется."]
    assert report.replaced == 5
    assert report.unknown == ["[PERSON_9]"]


def test_bilingual_label_follows_paragraph_language(tmp_path):
    body = para(run("Исполнитель: [PERSON_2]")) + para(run("Contractor: [PERSON_2]"))
    edited = make_edited_docx(tmp_path / "edited.docx", body)
    restore_docx(edited, tmp_path / "back.docx", {"[PERSON_2]": ["John Smith", "Джон Смит"]})
    lines = texts(part((tmp_path / "back.docx").read_bytes(), "word/document.xml"))
    assert lines == ["Исполнитель: Джон Смит", "Contractor: John Smith"]


def test_not_a_word_file(tmp_path):
    path = tmp_path / "answer.docx"
    path.write_text("не документ", encoding="utf-8")
    with pytest.raises(RedactedDocxError):
        restore_docx(path, tmp_path / "back.docx", MAPPING)


def test_plain_value_drops_markdown():
    assert plain_value("ООО «Ро**машка»**") == "ООО «Ромашка»"
    assert plain_value("ivan\\_petrov@example.com") == "ivan_petrov@example.com"
    assert plain_value("ул. Мира, 5<br>кв. 2") == "ул. Мира, 5 кв. 2"


def test_restored_file_name():
    mapping = {"[PERSON_1]": "Иван Петров", "[COMPANY_1]": 'ООО "Альфа"'}
    assert restored_file_name("Договор [PERSON_1] (обезличенный).docx", mapping) == (
        "Договор Иван Петров (с настоящими данными).docx"
    )
    assert restored_file_name("[COMPANY_1] правки.docx", mapping) == "ООО Альфа правки (с настоящими данными).docx"
