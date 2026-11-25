"""Обратная подстановка в документе Word: метки заменяются настоящими значениями.

Обезличенный .docx уходит в нейросеть или юристу и возвращается поправленным, с
теми же метками [PERSON_1]. Метки заменяются прямо в файле, остальное остается
как было: оформление, таблицы, нумерация, колонтитулы, сноски, примечания и
исправления в режиме рецензирования. Значение берет оформление начала метки: если
метка разбита на фрагменты с разным оформлением, значение встает в первый.

Метки ищутся в основном тексте, сносках, колонтитулах, примечаниях, надписях,
кодах полей, диаграммах и SmartArt. Метка в удаленном исправлением тексте тоже
заменяется, чтобы при отклонении правки вернулось настоящее значение.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from .redacted_docx import (
    A,
    C,
    W,
    RedactedDocxError,
    _apply_spans,
    _main_part,
    _Package,
    _Stream,
    _w,
)
from .restore import RestoreMap, find_labels, restore_text, unknown_labels, value_at

# Части документа с текстом в разметке WordprocessingML (по корневому элементу).
_TEXT_ROOTS = {_w(name) for name in ("document", "hdr", "ftr", "footnotes", "endnotes", "comments", "glossaryDocument")}
_CONTAINERS = {"p", "tc", "tr", "tbl", "txbxContent", "footnote", "endnote", "comment", "hdr", "ftr"}
# Значения в таблице замен — срезы Markdown: выделение и экранирование убираются.
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_MD_MARK_RE = re.compile(r"\\([!-/:-@\[-`{-~])|\*+")


class RestoreDocxError(RedactedDocxError):
    """Ошибка с готовым текстом для пользователя."""


@dataclass
class RestoreDocxReport:
    replaced: int = 0
    labels: set[str] = field(default_factory=set)
    unknown: list[str] = field(default_factory=list)  # метки, которых нет в таблице замен


def plain_value(value: str) -> str:
    """Значение из Markdown в виде обычного текста документа."""
    text = _MD_MARK_RE.sub(lambda m: m.group(1) or "", _BR_RE.sub(" ", value))
    return " ".join(text.split())


def _plain_map(mapping: RestoreMap) -> dict[str, list[str]]:
    result = {}
    for label, value in mapping.items():
        forms: list[str] = []
        for form in [value] if isinstance(value, str) else value:
            plain = plain_value(form)
            if plain and plain not in forms:
                forms.append(plain)
        if forms:
            result[label] = forms
    return result


def _collect(root: etree._Element, original: bool) -> _Stream:
    """Текст части подряд. Видимый текст — как после принятия исправлений; исходный
    (original) — как до них: удаленный текст есть, вставленного нет."""
    stream = _Stream()

    def walk(el: etree._Element, inserted: bool) -> None:
        tag = el.tag
        if not isinstance(tag, str):
            return
        if tag.startswith(f"{{{W}}}"):
            name = tag[len(W) + 2:]
            if name == "t":
                if not (original and inserted):
                    stream.add(el, "t", el.text or "")
                return
            if name == "delText":
                if original:
                    stream.add(el, "t", el.text or "")
                return
            if name in ("tab", "ptab"):
                stream.add(el, "x", "\t")
                return
            if name in ("br", "cr"):
                stream.add(el, "x", "\n")
                return
            if name == "noBreakHyphen":
                stream.add(el, "x", "-")
                return
            if name.endswith("Pr") or name in ("instrText", "delInstrText", "sym", "softHyphen"):
                return
            if name in ("ins", "moveTo"):
                for child in el:
                    walk(child, True)
                return
            if name in _CONTAINERS:
                stream.add(None, "sep", "\n")
                for child in el:
                    walk(child, inserted)
                stream.add(None, "sep", "\n")
                return
        for child in el:
            walk(child, inserted)

    walk(root, False)
    return stream


def _spans(stream: _Stream, mapping: RestoreMap, only_deleted: bool) -> list[tuple[int, int, str]]:
    text = stream.text
    result = []
    for a, b, key in find_labels(text, mapping):
        if "\n" in text[a:b]:
            continue  # метка не переходит через границу абзаца или ячейки
        if only_deleted:
            touched = [u for u in stream.units if u.kind == "t" and u.start < b and a < u.end]
            if not touched or any(u.node.tag != _w("delText") for u in touched):
                continue
        result.append((a, b, key))
    return result


def _restore_part(root: etree._Element, mapping: RestoreMap, report: RestoreDocxReport) -> None:
    # Сначала видимый текст, затем метки, целиком лежащие в удаленном тексте.
    for only_deleted in (False, True):
        stream = _collect(root, original=only_deleted)
        spans = _spans(stream, mapping, only_deleted)
        text = stream.text
        done = _apply_spans(stream, [(a, b, value_at(text, a, b, mapping[key])) for a, b, key in spans])
        placed = set(done)
        for a, b, key in spans:
            if (a, b) in placed:
                report.replaced += 1
                report.labels.add(key)
    for el in root.iter(_w("instrText"), _w("delInstrText")):
        if el.text:
            el.text = _restore_plain(el.text, mapping, report)
    for el in root.iter(_w("fldSimple")):
        instr = el.get(_w("instr"))
        if instr:
            el.set(_w("instr"), _restore_plain(instr, mapping, report))


def _restore_plain(text: str, mapping: RestoreMap, report: RestoreDocxReport) -> str:
    result = restore_text(text, mapping)
    report.replaced += result.replaced
    report.labels |= result.labels
    return result.text


def _visible_text(pkg: _Package, names: list[str]) -> str:
    lines = []
    for name in names:
        root = pkg.xml(name)
        if root is None:
            continue
        for paragraph in root.iter(_w("p"), f"{{{A}}}p"):
            lines.append("".join(t.text or "" for t in paragraph.iter(_w("t"), f"{{{A}}}t")))
    return "\n".join(lines)


def restore_docx_bytes(data: bytes, mapping: RestoreMap) -> tuple[bytes, RestoreDocxReport]:
    pkg = _Package(data)
    if pkg.xml(_main_part(pkg)) is None:
        raise RestoreDocxError("Файл не похож на документ Word: основной текст не читается.")
    values = _plain_map(mapping)
    report = RestoreDocxReport()
    touched: list[str] = []
    for name in list(pkg.raw):
        if not name.endswith(".xml"):
            continue
        if re.match(r"word/(charts|diagrams)/[^/]+\.xml$", name):
            root = pkg.xml(name)
            if root is None:
                continue
            for el in root.iter(f"{{{A}}}t", f"{{{C}}}v"):
                if el.text:
                    el.text = _restore_plain(el.text, values, report)
            touched.append(name)
            continue
        if not name.startswith("word/"):
            continue
        root = pkg.xml(name)
        if root is None or root.tag not in _TEXT_ROOTS:
            continue
        _restore_part(root, values, report)
        touched.append(name)
    report.unknown = unknown_labels(_visible_text(pkg, touched))
    return pkg.save(), report


def restore_docx(source: str | Path, target: str | Path, mapping: RestoreMap) -> RestoreDocxReport:
    try:
        data = Path(source).read_bytes()
    except OSError as exc:
        raise RestoreDocxError(f"Не удалось прочитать файл: {exc}") from exc
    result, report = restore_docx_bytes(data, mapping)
    try:
        Path(target).write_bytes(result)
    except OSError as exc:
        raise RestoreDocxError(f"Не удалось сохранить файл: {exc}") from exc
    return report


_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def restored_file_name(name: str, mapping: RestoreMap) -> str:
    """Имя файла с настоящими данными: метки в имени заменяются значениями, отметка
    «(обезличенный)» убирается."""
    path = Path(name)
    values = _plain_map(mapping)
    stem = restore_text(path.stem, values).text
    stem = re.sub(r"\s*\(обезличенн\w*\)", "", stem, flags=re.I)
    stem = " ".join(_BAD_NAME_CHARS.sub(" ", stem).split()).strip(" .") or "документ"
    return f"{stem} (с настоящими данными){path.suffix or '.docx'}"

