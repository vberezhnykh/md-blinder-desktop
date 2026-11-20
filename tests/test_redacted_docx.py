"""Обезличенный DOCX: замены на своих местах, оформление и чистка служебных частей."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from md_blinder.anonymize.redacted_docx import (
    RedactedDocxError,
    accept_revisions,
    docx_export_note,
    export_redacted_docx,
)
from md_blinder.anonymize.types import Entity, Position
from md_blinder.convert.docx import docx_to_markdown

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
NS = (
    f'xmlns:w="{W_NS}" xmlns:r="{R_NS}" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" mc:Ignorable="w14"'
)
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
# Картинка-«фото»: байты, которых не должно остаться в результате.
PHOTO = b"\x89PNG\r\n\x1a\nSECRET-PHOTO-BYTES"


def run(text: str, bold: bool = False) -> str:
    props = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:r>{props}<w:t xml:space="preserve">{text}</w:t></w:r>'


def para(*runs: str, numbered: bool = False) -> str:
    props = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>' if numbered else ""
    return f"<w:p>{props}{''.join(runs)}</w:p>"


BODY = (
    # Название компании и имя разбиты на фрагменты с разным оформлением.
    para(run("Поставщик: ООО «Ро"), run("машка»", bold=True), run(", директор Иван"), run(" Петров.")),
    para(
        run("Почта: "),
        '<w:hyperlink r:id="rId20">' + run("ivan@example.com") + "</w:hyperlink>",
        run(", телефон +7 916 765-43-21."),
        numbered=True,
    ),
    para(
        run("Ответственный: "),
        '<w:del w:id="1" w:author="Reviewer Person" w:date="2024-01-01T00:00:00Z">'
        '<w:r><w:delText>Секретов Секрет</w:delText></w:r></w:del>'
        '<w:ins w:id="2" w:author="Reviewer Person" w:date="2024-01-01T00:00:00Z">' + run("Иван Петров") + "</w:ins>",
        '<w:commentRangeStart w:id="0"/>',
        run(" (по доверенности)"),
        '<w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r>',
        run(", см. сноску"),
        '<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:footnoteReference w:id="1"/></w:r>',
        numbered=True,
    ),
    para(
        "<w:r><w:drawing><wp:inline><wp:extent cx=\"914400\" cy=\"914400\"/>"
        '<wp:docPr id="1" name="Picture 1" descr="Фото Петрова"/>'
        "<a:graphic><a:graphicData uri=\"http://schemas.openxmlformats.org/drawingml/2006/picture\">"
        '<pic:pic><pic:blipFill><a:blip r:embed="rId21"/></pic:blipFill></pic:pic>'
        "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    ),
    "<w:tbl><w:tr><w:tc><w:p>" + run("Подпись") + "</w:p></w:tc><w:tc><w:p>" + run("Иван Петров")
    + "</w:p></w:tc></w:tr></w:tbl>",
    para(run("Справочно: Ромашка — товарный знак.")),
    '<w:sectPr><w:headerReference w:type="default" r:id="rId22"/></w:sectPr>',
)

NUMBERING = (
    f'{HEAD}<w:numbering {NS}><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/>'
    '<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum>'
    '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
)


def make_rich_docx(path: Path, body: str = "".join(BODY)) -> Path:
    parts = {
        "[Content_Types].xml": (
            f'{HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="jpeg" ContentType="image/jpeg"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/comments.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>'
            '<Override PartName="/docProps/core.xml" '
            'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
            '<Override PartName="/docProps/custom.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            f'{HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{REL}officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/'
            'metadata/core-properties" Target="docProps/core.xml"/>'
            f'<Relationship Id="rId3" Type="{REL}extended-properties" Target="docProps/app.xml"/>'
            f'<Relationship Id="rId4" Type="{REL}custom-properties" Target="docProps/custom.xml"/>'
            "</Relationships>"
        ),
        "word/document.xml": f"{HEAD}<w:document {NS}><w:body>{body}</w:body></w:document>",
        "word/_rels/document.xml.rels": (
            f'{HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId10" Type="{REL}numbering" Target="numbering.xml"/>'
            f'<Relationship Id="rId11" Type="{REL}footnotes" Target="footnotes.xml"/>'
            f'<Relationship Id="rId12" Type="{REL}comments" Target="comments.xml"/>'
            f'<Relationship Id="rId13" Type="{REL}settings" Target="settings.xml"/>'
            f'<Relationship Id="rId20" Type="{REL}hyperlink" Target="mailto:ivan@example.com" '
            'TargetMode="External"/>'
            f'<Relationship Id="rId21" Type="{REL}image" Target="media/image1.jpeg"/>'
            f'<Relationship Id="rId22" Type="{REL}header" Target="header1.xml"/>'
            "</Relationships>"
        ),
        "word/numbering.xml": NUMBERING,
        "word/footnotes.xml": (
            f'{HEAD}<w:footnotes {NS}><w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r>'
            '</w:p></w:footnote><w:footnote w:id="1"><w:p>' + run("Подписал Иван Петров лично.")
            + "</w:p></w:footnote></w:footnotes>"
        ),
        "word/comments.xml": (
            f'{HEAD}<w:comments {NS}><w:comment w:id="0" w:author="Commenter Person" w:initials="CP">'
            "<w:p>" + run("Уточнить у Петрова") + "</w:p></w:comment></w:comments>"
        ),
        "word/header1.xml": f"{HEAD}<w:hdr {NS}><w:p>" + run("Иван Петров — конфиденциально") + "</w:p></w:hdr>",
        "word/settings.xml": (
            f'{HEAD}<w:settings {NS}><w:attachedTemplate r:id="rId1"/>'
            '<w:docVars><w:docVar w:name="client" w:val="Petrov"/></w:docVars></w:settings>'
        ),
        "word/_rels/settings.xml.rels": (
            f'{HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{REL}attachedTemplate" '
            'Target="file:///C:/Users/ipetrov/Templates/contract.dotx" TargetMode="External"/></Relationships>'
        ),
        "word/media/image1.jpeg": PHOTO,
        "docProps/core.xml": (
            f'{HEAD}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
            'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<dc:title>Договор с Петровым</dc:title><dc:creator>Author Person</dc:creator>"
            "<cp:lastModifiedBy>Editor Person</cp:lastModifiedBy>"
            '<dcterms:created xsi:type="dcterms:W3CDTF">2024-01-01T00:00:00Z</dcterms:created>'
            "</cp:coreProperties>"
        ),
        "docProps/app.xml": (
            f'{HEAD}<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
            "<Company>Company Secret</Company><Pages>1</Pages></Properties>"
        ),
        "docProps/custom.xml": (
            f'{HEAD}<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties">'
            "<property name=\"Client\">Custom Secret</property></Properties>"
        ),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def entities_for(md: str, values: dict[str, tuple[str, str]], skip: tuple[str, ...] = ()) -> list[Entity]:
    """values: значение в Markdown → (тип, метка); вхождения ищутся в Markdown."""
    result = []
    for value, (kind, label) in values.items():
        positions = [Position(m.start(), m.end()) for m in re.finditer(re.escape(value), md)]
        plain = value.replace("**", "")
        result.append(Entity(label, kind, plain, label, positions, anonymize=value not in skip))
    return result


VALUES = {
    # Жирная только часть названия: в Markdown выделение посреди слова.
    "ООО «Ро**машка»**": ("COMPANY", "[COMPANY_1]"),
    "Иван Петров": ("PERSON", "[PERSON_1]"),
    "ivan@example.com": ("EMAIL", "[EMAIL_1]"),
    "+7 916 765-43-21": ("PHONE", "[PHONE_1]"),
}


def all_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return "\n".join(archive.read(n).decode("utf-8", errors="replace") for n in archive.namelist())


def part(data: bytes, name: str) -> etree._Element:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return etree.fromstring(archive.read(name))


@pytest.fixture
def exported(tmp_path):
    source = make_rich_docx(tmp_path / "contract.docx")
    md = docx_to_markdown(source)
    entities = entities_for(md, VALUES)
    assert all(e.positions for e in entities)
    target = tmp_path / "out.docx"
    report = export_redacted_docx(source, target, md, entities)
    return source, target, md, report


def test_values_replaced_in_text_and_service_parts(exported):
    _, target, _, report = exported
    data = target.read_bytes()
    text = all_text(data)
    for secret in (
        "ООО «Ро", "машка»", "Петров", "ivan@example.com", "765-43-21", "Секретов", "Reviewer Person", "Commenter Person",
        "Author Person", "Editor Person", "Company Secret", "Custom Secret", "ipetrov", "SECRET-PHOTO-BYTES",
    ):
        assert secret not in text, secret
    assert report.unmatched == []
    assert report.revisions == 2 and report.comments == 1 and report.images == 1


def test_markdown_of_result_matches_anonymized_markdown(exported):
    from md_blinder.anonymize.apply import build_anonymized_text

    _, target, md, _ = exported
    entities = entities_for(md, VALUES)
    words = re.compile(r"[^\W_]+")

    def plain(text: str) -> list[str]:
        # Адреса ссылок не сравниваются: ссылка mailto: с адресом в результате стала текстом.
        return words.findall(re.sub(r"\]\([^)]*\)", "]", re.sub(r"\\(.)", r"\1", text)))

    # Картинка в результате — серый квадрат без подписи, в Markdown — отметка об удалении.
    expected = plain(build_anonymized_text(md, entities).replace("[Изображение удалено при обезличивании]", ""))
    got = plain(docx_to_markdown(target))
    assert got == expected


def test_label_takes_formatting_of_first_fragment(exported):
    _, target, _, _ = exported
    body = part(target.read_bytes(), "word/document.xml")
    w = f"{{{W_NS}}}"
    first = body.find(f"{w}body/{w}p")
    texts = [(r.find(f"{w}t").text or "", r.find(f"{w}rPr/{w}b") is not None) for r in first.iter(f"{w}r")]
    # «ООО «Ро» + «машка»» (жирное): метка в первом фрагменте, второй пуст.
    assert texts[0] == ("Поставщик: [COMPANY_1]", False)
    assert texts[1] == ("", True)
    assert "".join(t for t, _ in texts) == "Поставщик: [COMPANY_1], директор [PERSON_1]."
    # Нумерация, таблица и колонтитул на месте, примечаний нет.
    assert len(body.findall(f".//{w}numPr")) == 2
    assert len(body.findall(f".//{w}tbl")) == 1
    assert body.find(f".//{w}commentReference") is None
    assert body.find(f".//{w}headerReference") is not None


def test_header_footnote_and_link(exported):
    _, target, _, _ = exported
    data = target.read_bytes()
    w = f"{{{W_NS}}}"
    header = "".join(t.text or "" for t in part(data, "word/header1.xml").iter(f"{w}t"))
    assert header == "[PERSON_1] — конфиденциально"
    notes = "".join(t.text or "" for t in part(data, "word/footnotes.xml").iter(f"{w}t"))
    assert notes == "Подписал [PERSON_1] лично."
    body = part(data, "word/document.xml")
    # Ссылка mailto: с адресом больше не ссылка, текст ее заменен.
    assert body.find(f".//{w}hyperlink") is None
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        assert "word/comments.xml" not in names and "docProps/custom.xml" not in names
        assert "word/media/image1.jpeg" not in names
        rels = archive.read("word/_rels/document.xml.rels").decode()
        assert "comments.xml" not in rels and "mailto" not in rels and "media/md-blinder-removed.png" in rels
        # Каждая XML-часть читается.
        for name in names:
            if name.endswith((".xml", ".rels")):
                etree.fromstring(archive.read(name))
    root = part(data, "word/document.xml")
    assert root.nsmap.get("w14") == "http://schemas.microsoft.com/office/word/2010/wordml"


def test_dismissed_value_stays_where_markdown_keeps_it(tmp_path):
    source = make_rich_docx(tmp_path / "contract.docx")
    md = docx_to_markdown(source)
    entities = entities_for(md, VALUES, skip=("+7 916 765-43-21",))
    target = tmp_path / "out.docx"
    export_redacted_docx(source, target, md, entities)
    assert "+7 916 765-43-21" in docx_to_markdown(target)


def test_unmarked_occurrence_in_text_is_kept(tmp_path):
    """Вхождение, которое в Markdown осталось без замены, в документе тоже остается:
    решает проверка пользователя, а не поиск по значению."""
    source = make_rich_docx(tmp_path / "contract.docx")
    md = docx_to_markdown(source)
    entities = entities_for(md, VALUES)
    for e in entities:
        if e.type == "PERSON":
            e.positions = e.positions[:1]
    target = tmp_path / "out.docx"
    report = export_redacted_docx(source, target, md, entities)
    result = docx_to_markdown(target).replace("\\_", "_")
    assert result.count("[PERSON_1]") == 1
    assert result.count("Иван Петров") == md.count("Иван Петров") - 1
    assert report.unmatched == []


def test_foreign_file_is_rejected(tmp_path):
    source = make_rich_docx(tmp_path / "contract.docx")
    other = "Совсем другой текст о поставке оборудования, без общих слов с договором. " * 20
    with pytest.raises(RedactedDocxError):
        export_redacted_docx(source, tmp_path / "out.docx", other, [])


def test_not_a_docx(tmp_path):
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(RedactedDocxError):
        export_redacted_docx(bad, tmp_path / "out.docx", "text", [])


def test_deleted_paragraph_mark_merges_paragraphs():
    root = etree.fromstring(
        f'<w:body xmlns:w="{W_NS}"><w:p><w:pPr><w:rPr><w:del w:id="1" w:author="X"/></w:rPr></w:pPr>'
        f'{run("Первый ")}</w:p><w:p><w:pPr><w:jc w:val="center"/></w:pPr>{run("второй")}</w:p></w:body>'
    )
    assert accept_revisions(root) == 1
    paragraphs = root.findall(f"{{{W_NS}}}p")
    assert len(paragraphs) == 1
    assert "".join(t.text for t in paragraphs[0].iter(f"{{{W_NS}}}t")) == "Первый второй"
    assert paragraphs[0].find(f"{{{W_NS}}}pPr/{{{W_NS}}}jc") is not None


def test_text_box_choice_gets_redacted_fallback(tmp_path):
    wps = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
    box = "<w:txbxContent><w:p>" + run("Иван Петров") + "</w:p></w:txbxContent>"
    body = (
        para(run("Текст договора с подписью, где стоит имя.")),
        para(
            f'<w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><wps:wsp xmlns:wps="{wps}">'
            f"<wps:txbx>{box}</wps:txbx></wps:wsp></w:drawing></mc:Choice>"
            '<mc:Fallback><w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml"><v:textbox>'
            f"{box}</v:textbox></v:shape></w:pict></mc:Fallback></mc:AlternateContent></w:r>"
        ),
    )
    source = make_rich_docx(tmp_path / "box.docx", "".join(body))
    md = docx_to_markdown(source)
    entities = entities_for(md, {"Иван Петров": ("PERSON", "[PERSON_1]")})
    target = tmp_path / "out.docx"
    export_redacted_docx(source, target, md, entities)
    text = all_text(target.read_bytes())
    assert "Петров" not in text
    assert text.count("[PERSON_1]") >= 3  # Choice, Fallback и колонтитул


def test_note_lists_extra_changes(exported):
    *_, report = exported
    assert docx_export_note(report) == (
        "Исправления приняты, примечания удалены, картинки заменены серыми прямоугольниками."
    )
