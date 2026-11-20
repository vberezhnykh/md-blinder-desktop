"""Обезличенный DOCX: те же замены, что в Markdown, прямо в документе Word.

Форматирование остается как в исходнике: стили, таблицы, автонумерация,
колонтитулы, сноски, поля страницы. Меняется только текст находок: метка
занимает место значения и берет оформление его первого фрагмента. В Word текст
абзаца разбит на фрагменты (w:r/w:t) по правкам и оформлению, и значение часто
лежит в нескольких фрагментах: метка встает в первый, из остальных значение
вырезается.

Где заменять, решает Markdown, который проверял пользователь. Markdown собран
mammoth из того же файла, поэтому слова основного текста, сносок и концевых
сносок идут в нем в том же порядке. Слова Markdown и документа сопоставляются
выравниванием (difflib), и каждое вхождение замены переносится на свое место в
документе. Вхождение, оставленное в Markdown без замены, остается и в документе.

Текст, которого в Markdown нет (колонтитулы, надписи, коды полей, подписи к
картинкам, места, где выравнивание не сошлось), проверяется по значениям замен:
каждое найденное значение заменяется своей меткой.

Что еще меняется, потому что содержит персональные данные или не попало в
проверку:
- исправления принимаются (как в Markdown): удаленный текст и авторы правок
  уходят из файла;
- примечания удаляются вместе с авторами;
- картинки заменяются серыми прямоугольниками того же размера, как в Markdown
  они заменяются отметкой об удалении; встроенные объекты (OLE) остаются
  картинкой;
- свойства документа (автор, кем изменен, организация, название, шаблон),
  миниатюра, пользовательские свойства и данные customXml удаляются;
- ссылки, адрес которых содержит заменяемое значение, перестают быть ссылками.
"""

from __future__ import annotations

import copy
import difflib
import io
import posixpath
import re
import zipfile
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Sequence
from urllib.parse import unquote

from lxml import etree

from ..cancel import CancelToken, check
from .image_safety import find_image_ranges
from .redacted_pdf import fold, normalize_value
from .types import Entity

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
V = "urn:schemas-microsoft-com:vml"
VO = "urn:schemas-microsoft-com:office:office"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC = "http://purl.org/dc/elements/1.1/"
EP = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
VT = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"

REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def _w(name: str) -> str:
    return f"{{{W}}}{name}"


# Доля слов Markdown, которые должны найтись в документе, иначе файл считается чужим.
MIN_MATCHED_SHARE = 0.6
# Серый квадрат 8×8 вместо картинок: Word растягивает его до размера рамки.
BLANK_IMAGE_NAME = "md-blinder-removed.png"

_TOKEN_RE = re.compile(r"[^\W_]+")
# Слово для выравнивания. Выделение Markdown посреди слова («Ро**машка**», когда
# жирная только часть слова) и экранирование («file\_name») слово не разрывают.
_ALIGN_TOKEN_RE = re.compile(r"[^\W_]+(?:(?:\\?[*_])+[^\W_]+)*")
_MARKS_RE = re.compile(r"[\\*_]")
_MD_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")
_BR_RE = re.compile(r"<br\s*/?>", re.I)

# Отметки правок, которые при принятии исправлений просто убираются.
_CHANGE_MARKS = {
    "rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "tblGridChange", "tcPrChange", "trPrChange",
    "tblPrExChange", "numberingChange", "cellIns", "cellDel", "cellMerge",
    "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd",
    "customXmlInsRangeStart", "customXmlInsRangeEnd", "customXmlDelRangeStart", "customXmlDelRangeEnd",
    "customXmlMoveFromRangeStart", "customXmlMoveFromRangeEnd", "customXmlMoveToRangeStart",
    "customXmlMoveToRangeEnd", "delText", "delInstrText",
}
# Свойства документа, в которых бывают имена и названия.
_CORE_DROP = {
    f"{{{DC}}}creator", f"{{{CP}}}lastModifiedBy", f"{{{DC}}}title", f"{{{DC}}}subject", f"{{{CP}}}keywords",
    f"{{{DC}}}description", f"{{{CP}}}category", f"{{{CP}}}contentStatus", f"{{{DC}}}identifier",
    f"{{{CP}}}version",
}
# HLinks — адреса всех ссылок документа, в том числе mailto: с почтой.
_APP_DROP = {f"{{{EP}}}{name}" for name in ("Company", "Manager", "HyperlinkBase", "Template", "HLinks")}


class RedactedDocxError(Exception):
    """Ошибка с готовым текстом для пользователя."""


@dataclass
class RedactedDocxReport:
    replaced: int = 0  # вхождений Markdown, перенесенных в документ
    outside: int = 0  # замен в тексте, которого нет в Markdown (колонтитулы, надписи…)
    images: int = 0
    comments: int = 0
    revisions: int = 0
    # Метки, значения которых остались в документе чаще, чем в обезличенном Markdown.
    unmatched: list[tuple[str, str]] = field(default_factory=list)


def docx_export_note(report: RedactedDocxReport) -> str:
    """Что в документе изменилось помимо замен, одной фразой для уведомления."""
    parts = []
    if report.revisions:
        parts.append("исправления приняты")
    if report.comments:
        parts.append("примечания удалены")
    if report.images:
        parts.append("картинки заменены серыми прямоугольниками")
    if not parts:
        return ""
    text = ", ".join(parts)
    return text[0].upper() + text[1:] + "."


# ---------------------------------------------------------------- пакет OPC


def _rels_name(part: str) -> str:
    folder, name = posixpath.split(part)
    return posixpath.join(folder, "_rels", f"{name}.rels")


def _resolve(part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(part), target))


def _relative(part: str, target: str) -> str:
    return posixpath.relpath(target, posixpath.dirname(part) or ".")


class _Package:
    def __init__(self, data: bytes) -> None:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
            self.order = [info.filename for info in archive.infolist() if not info.is_dir()]
            self.raw = {name: archive.read(name) for name in self.order}
        except (zipfile.BadZipFile, OSError, KeyError) as exc:
            raise RedactedDocxError("Файл не открывается как документ Word (.docx).") from exc
        self.trees: dict[str, etree._Element] = {}

    def has(self, name: str) -> bool:
        return name in self.raw

    def xml(self, name: str) -> Optional[etree._Element]:
        if name not in self.trees:
            if name not in self.raw:
                return None
            try:
                self.trees[name] = etree.fromstring(self.raw[name], etree.XMLParser(resolve_entities=False, huge_tree=True))
            except etree.XMLSyntaxError:
                return None
        return self.trees[name]

    def remove(self, name: str) -> None:
        self.raw.pop(name, None)
        self.trees.pop(name, None)
        self.raw.pop(_rels_name(name), None)
        self.trees.pop(_rels_name(name), None)
        types = self.xml("[Content_Types].xml")
        if types is not None:
            for override in types.findall(f"{{{CT}}}Override"):
                if override.get("PartName", "").lstrip("/") == name:
                    types.remove(override)

    def put(self, name: str, data: bytes) -> None:
        if name not in self.raw:
            self.order.append(name)
        self.raw[name] = data
        self.trees.pop(name, None)

    def rels(self, part: str) -> list[etree._Element]:
        root = self.xml(_rels_name(part))
        return [] if root is None else list(root.findall(f"{{{PR}}}Relationship"))

    def drop_rel(self, part: str, rel: etree._Element) -> None:
        rel.getparent().remove(rel)

    def related(self, part: str, kind: str) -> list[str]:
        """Внутренние части, на которые ссылается part, по окончанию типа связи."""
        return [
            _resolve(part, rel.get("Target", "")) for rel in self.rels(part)
            if rel.get("Type", "").endswith("/" + kind) and rel.get("TargetMode") != "External"
        ]

    def all_rels_files(self) -> list[str]:
        return [name for name in list(self.raw) if name.endswith(".rels")]

    def owner_of(self, rels_file: str) -> str:
        folder, name = posixpath.split(rels_file)
        return posixpath.join(posixpath.dirname(folder), name[: -len(".rels")])

    def save(self) -> bytes:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in self.order:
                if name not in self.raw:
                    continue
                if name in self.trees:
                    data = etree.tostring(self.trees[name], xml_declaration=True, encoding="UTF-8", standalone=True)
                else:
                    data = self.raw[name]
                archive.writestr(name, data)
        return out.getvalue()


# ---------------------------------------------------------------- исправления и примечания


def _unwrap(el: etree._Element) -> None:
    parent = el.getparent()
    index = parent.index(el)
    for offset, child in enumerate(list(el)):
        parent.insert(index + offset, child)
    parent.remove(el)


def _drop(el: etree._Element) -> None:
    parent = el.getparent()
    if parent is not None:
        parent.remove(el)


def accept_revisions(root: etree._Element) -> int:
    """Принимает исправления, как их показывает Markdown: вставки остаются, удаления
    и перемещенный оттуда текст исчезают, отметки правок с авторами убираются."""
    count = 0
    for el in list(root.iter(_w("del"), _w("moveFrom"))):
        if el.getparent() is None:
            continue
        count += 1
        parent = el.getparent()
        if parent.tag == _w("rPr") and parent.getparent() is not None and parent.getparent().tag == _w("pPr"):
            # Удален знак абзаца: абзац сливается со следующим.
            paragraph = parent.getparent().getparent()
            _drop(el)
            following = paragraph.getnext() if paragraph is not None else None
            if paragraph is not None and following is not None and following.tag == _w("p"):
                head = following.find(_w("pPr"))
                at = 0 if head is None else following.index(head) + 1
                for offset, child in enumerate([c for c in paragraph if c.tag != _w("pPr")]):
                    following.insert(at + offset, child)
                _drop(paragraph)
        elif parent.tag == _w("trPr"):
            row = parent.getparent()
            if row is not None:
                _drop(row)
        elif parent.tag == _w("rPr"):
            _drop(el)  # отметка удаления у знака, который уже убран вместе с текстом
        else:
            _drop(el)
    for el in list(root.iter(_w("ins"), _w("moveTo"))):
        if el.getparent() is None:
            continue
        count += 1
        if el.getparent().tag in (_w("rPr"), _w("trPr")):
            _drop(el)
        else:
            _unwrap(el)
    for el in list(root.iter(*(_w(name) for name in _CHANGE_MARKS))):
        if el.getparent() is not None:
            if el.tag.endswith("Change"):
                count += 1
            _drop(el)
    return count


def remove_comment_marks(root: etree._Element) -> int:
    count = 0
    for el in list(root.iter(_w("commentRangeStart"), _w("commentRangeEnd"), _w("commentReference"),
                             _w("permStart"), _w("permEnd"))):
        parent = el.getparent()
        if el.tag == _w("commentReference"):
            count += 1
        _drop(el)
        if parent is not None and parent.tag == _w("r") and all(c.tag == _w("rPr") for c in parent):
            _drop(parent)
    return count


# ---------------------------------------------------------------- текст частей


@dataclass
class _Unit:
    node: Optional[etree._Element]
    kind: str  # "t" — текст w:t, "x" — табуляция или разрыв строки, "sep" — граница абзаца
    start: int
    text: str

    @property
    def end(self) -> int:
        return self.start + len(self.text)


class _Stream:
    """Текст части документа подряд, с привязкой каждого знака к узлу XML."""

    def __init__(self) -> None:
        self.units: list[_Unit] = []
        self.parts: list[str] = []
        self.length = 0
        self.choices: list[etree._Element] = []
        self.hyperlinks: list[tuple[int, int, etree._Element]] = []

    def add(self, node: Optional[etree._Element], kind: str, text: str) -> None:
        if kind == "sep" and (not self.units or self.units[-1].kind == "sep"):
            return
        self.units.append(_Unit(node, kind, self.length, text))
        self.parts.append(text)
        self.length += len(text)

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def walk(self, el: etree._Element, mammoth_order: bool = True) -> None:
        """Обходит элемент как mammoth: из mc:AlternateContent берет mc:Fallback.
        Текст mc:Choice запоминается отдельно (choices)."""
        tag = el.tag
        if not isinstance(tag, str):
            return
        if tag == f"{{{MC}}}AlternateContent" and mammoth_order:
            fallback = el.find(f"{{{MC}}}Fallback")
            for choice in el.findall(f"{{{MC}}}Choice"):
                self.choices.append(choice)
            if fallback is not None:
                self.walk(fallback, mammoth_order)
            return
        if tag.startswith(f"{{{W}}}"):
            name = tag[len(W) + 2:]
            if name == "t":
                self.add(el, "t", el.text or "")
                return
            if name in ("tab", "ptab"):
                self.add(el, "x", "\t")
                return
            if name in ("br", "cr"):
                self.add(el, "x", "\n")
                return
            if name == "noBreakHyphen":
                self.add(el, "x", "-")
                return
            if name.endswith("Pr") or name in ("instrText", "delText", "delInstrText", "sym", "softHyphen"):
                return
            if name == "hyperlink":
                start = self.length
                for child in el:
                    self.walk(child, mammoth_order)
                self.hyperlinks.append((start, self.length, el))
                return
            if name in ("p", "tc", "tr", "tbl", "txbxContent", "footnote", "endnote", "hdr", "ftr"):
                self.add(None, "sep", "\n")
                for child in el:
                    self.walk(child, mammoth_order)
                self.add(None, "sep", "\n")
                return
        for child in el:
            self.walk(child, mammoth_order)


def _set_text(node: etree._Element, text: str) -> None:
    node.text = text
    if text != text.strip() or "  " in text:
        node.set(XML_SPACE, "preserve")


def _apply_spans(stream: _Stream, spans: Sequence[tuple[int, int, str]]) -> list[tuple[int, int]]:
    """Заменяет участки потока метками. Участки не пересекаются. Возвращает
    участки, где метка действительно встала (для ссылок)."""
    starts = [u.start for u in stream.units]
    done: list[tuple[int, int]] = []
    for a, b, label in sorted(spans, key=lambda s: -s[0]):
        first = max(0, bisect_right(starts, a) - 1)
        placed = False
        touched: list[_Unit] = []
        for unit in stream.units[first:]:
            if unit.start >= b:
                break
            if unit.end <= a and unit.text:
                continue
            touched.append(unit)
        for unit in touched:
            if unit.kind == "t":
                lo, hi = max(a, unit.start) - unit.start, min(b, unit.end) - unit.start
                new = unit.text[:lo] + ("" if placed else label) + unit.text[hi:]
                placed = True
                unit.text = new
                _set_text(unit.node, new)
            elif unit.kind == "x" and a <= unit.start and unit.end <= b:
                _drop(unit.node)
                unit.text = ""
        if not placed:
            continue
        done.append((a, b))
    return done


# ---------------------------------------------------------------- значения для поиска вне Markdown


def _unescape(md: str) -> str:
    return _MD_ESCAPE_RE.sub(r"\1", _BR_RE.sub(" ", md))


@dataclass
class _Values:
    pattern: Optional[re.Pattern]
    labels: dict[str, str]  # нормализованное значение → метка

    @staticmethod
    def build(source_text: str, entities: Sequence[Entity]) -> "_Values":
        labels: dict[str, str] = {}
        for e in entities:
            if not e.anonymize:
                continue
            forms = [e.value, *e.batch_aliases, *(source_text[p.start:p.end] for p in e.positions)]
            for form in forms:
                key = normalize_value(_BR_RE.sub(" ", form))
                if len(key) >= 2 and _TOKEN_RE.search(key) and key not in labels:
                    labels[key] = e.placeholder
        if not labels:
            return _Values(None, {})
        pieces = []
        for key in sorted(labels, key=len, reverse=True):
            body = r"\s+".join(re.escape(part) for part in key.split(" "))
            head = r"(?<![^\W_])" if _TOKEN_RE.match(key[0]) else ""
            tail = r"(?![^\W_])" if _TOKEN_RE.match(key[-1]) else ""
            pieces.append(head + body + tail)
        return _Values(re.compile("|".join(pieces)), labels)

    def find(self, text: str) -> Iterator[tuple[int, int, str]]:
        if self.pattern is None:
            return
        for m in self.pattern.finditer(fold(text)):
            key = " ".join(m.group(0).split())
            label = self.labels.get(key)
            if label is not None:
                yield m.start(), m.end(), label

    def count(self, text: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for _, _, label in self.find(text):
            result[label] = result.get(label, 0) + 1
        return result

    def replace(self, text: str) -> tuple[str, int]:
        hits = list(self.find(text))
        for a, b, label in reversed(hits):
            text = text[:a] + label + text[b:]
        return text, len(hits)


def _sweep(stream: _Stream, values: _Values, allowed: Callable[[int, int], bool] = lambda a, b: True,
           ) -> list[tuple[int, int, str]]:
    text = stream.text
    return [(a, b, label) for a, b, label in values.find(text) if allowed(a, b)]


# ---------------------------------------------------------------- выравнивание Markdown и документа


@dataclass
class _Alignment:
    md_tokens: list[tuple[int, int]]
    doc_tokens: list[tuple[int, int]]
    md_to_doc: dict[int, int]
    blocks: list[tuple[int, int]]  # участки документа, совпавшие с Markdown, в знаках

    @property
    def share(self) -> float:
        return len(self.md_to_doc) / max(1, len(self.md_tokens))


def _tokens(text: str) -> tuple[list[str], list[tuple[int, int]]]:
    words, spans = [], []
    for m in _ALIGN_TOKEN_RE.finditer(text):
        words.append(_MARKS_RE.sub("", m.group(0)).casefold())
        spans.append((m.start(), m.end()))
    return words, spans


def align(md: str, doc_text: str) -> _Alignment:
    masked = md
    for image in find_image_ranges(md):
        masked = masked[:image.start] + " " * (image.end - image.start) + masked[image.end:]
    md_words, md_spans = _tokens(masked)
    doc_words, doc_spans = _tokens(doc_text)
    matcher = difflib.SequenceMatcher(None, md_words, doc_words, autojunk=False)
    md_to_doc: dict[int, int] = {}
    blocks: list[tuple[int, int]] = []
    for i, j, size in matcher.get_matching_blocks():
        if size == 0:
            continue
        for k in range(size):
            md_to_doc[i + k] = j + k
        blocks.append((doc_spans[j][0], doc_spans[j + size - 1][1]))
    return _Alignment(md_spans, doc_spans, md_to_doc, blocks)


def _flexible(text: str) -> Optional[re.Pattern]:
    """Текст вхождения с любыми пробелами между знаками: в Markdown слова ячейки
    бывают склеены («2nddoor») или разделены иначе, чем в документе."""
    chars = [ch for ch in normalize_value(text) if not ch.isspace()]
    if not chars:
        return None
    return re.compile(r"\s*".join(re.escape(ch) for ch in chars))


def _extend(doc: str, a: int, b: int, prefix: str, suffix: str) -> tuple[int, int]:
    """Добавляет к участку знаки вокруг слов («+», «(», кавычки), если они есть и в
    Markdown, и в документе."""
    i = len(prefix) - 1
    while i >= 0 and a > 0 and fold(doc[a - 1]) == fold(prefix[i]) and not prefix[i].isspace():
        a -= 1
        i -= 1
    i = 0
    while i < len(suffix) and b < len(doc) and fold(doc[b]) == fold(suffix[i]) and not suffix[i].isspace():
        b += 1
        i += 1
    return a, b


def map_occurrence(md: str, doc: str, al: _Alignment, s: int, e: int) -> Optional[tuple[int, int]]:
    """Участок документа для вхождения [s, e) Markdown или None."""
    starts = [sp[0] for sp in al.md_tokens]
    lo = bisect_left([sp[1] for sp in al.md_tokens], s + 1)
    hi = bisect_left(starts, e)
    inside = list(range(lo, hi))
    if inside:
        mapped = [al.md_to_doc.get(i) for i in inside]
        if all(m is not None for m in mapped) and all(y > x for x, y in zip(mapped, mapped[1:])) \
                and mapped[-1] - mapped[0] <= len(inside) - 1 + 2:
            first, last = al.md_tokens[inside[0]], al.md_tokens[inside[-1]]
            a = al.doc_tokens[mapped[0]][0] + max(0, s - first[0])
            b = al.doc_tokens[mapped[-1]][1] - max(0, last[1] - e)
            prefix = _unescape(md[s:first[0]]) if s < first[0] else ""
            suffix = _unescape(md[last[1]:e]) if last[1] < e else ""
            return _extend(doc, a, b, prefix, suffix)
    # Слова вхождения не сопоставились: ищем его текст между ближайшими
    # сопоставленными словами до и после.
    before = next((al.md_to_doc[i] for i in range(lo - 1, -1, -1) if i in al.md_to_doc), None)
    after = next((al.md_to_doc[i] for i in range(hi, len(al.md_tokens)) if i in al.md_to_doc), None)
    window_start = al.doc_tokens[before][1] if before is not None else 0
    window_end = al.doc_tokens[after][0] if after is not None else len(doc)
    pattern = _flexible(_unescape(md[s:e]))
    if pattern is None or window_end <= window_start:
        return None
    m = pattern.search(fold(doc), window_start, window_end)
    return (m.start(), m.end()) if m else None


def _merge(spans: Iterable[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    merged: list[list] = []
    for a, b, label in sorted(spans, key=lambda s: (s[0], -s[1])):
        if merged and a < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b, label])
    return [(a, b, label) for a, b, label in merged]


def _overlaps(blocks: Sequence[tuple[int, int]], a: int, b: int) -> bool:
    i = bisect_right(blocks, (a, float("inf"))) - 1
    for block in blocks[max(0, i): i + 2]:
        if block[0] < b and a < block[1]:
            return True
    return False


# ---------------------------------------------------------------- картинки, ссылки, свойства


def _blank_png() -> bytes:
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    size = 8
    raw = b"".join(b"\x00" + bytes([0xD9, 0xD9, 0xD9]) * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _blank_images(pkg: _Package) -> int:
    """Каждая связь с картинкой (в тексте, колонтитулах, диаграммах, внешняя)
    ведет на серый квадрат; прежние файлы картинок удаляются."""
    target = f"word/media/{BLANK_IMAGE_NAME}"
    old: set[str] = set()
    count = 0
    for rels_file in pkg.all_rels_files():
        owner = pkg.owner_of(rels_file)
        for rel in pkg.rels(owner):
            if not rel.get("Type", "").endswith("/image"):
                continue
            count += 1
            if rel.get("TargetMode") == "External":
                del rel.attrib["TargetMode"]
            else:
                old.add(_resolve(owner, rel.get("Target", "")))
            rel.set("Target", _relative(owner, target))
    if not count:
        return 0
    for name in old:
        pkg.remove(name)
    pkg.put(target, _blank_png())
    types = pkg.xml("[Content_Types].xml")
    if types is not None and not any(
        d.get("Extension", "").lower() == "png" for d in types.findall(f"{{{CT}}}Default")
    ):
        etree.SubElement(types, f"{{{CT}}}Default", Extension="png", ContentType="image/png")
    return count


def _drop_rel_targets(pkg: _Package, part: str, kinds: Sequence[str]) -> set[str]:
    """Удаляет связи части с типами kinds (и сами части); возвращает их r:id."""
    ids: set[str] = set()
    for rel in pkg.rels(part):
        kind = rel.get("Type", "").rsplit("/", 1)[-1]
        if kind in kinds:
            ids.add(rel.get("Id", ""))
            if rel.get("TargetMode") != "External":
                pkg.remove(_resolve(part, rel.get("Target", "")))
            pkg.drop_rel(part, rel)
    return ids


def _clean_properties(pkg: _Package) -> None:
    core = pkg.xml("docProps/core.xml")
    if core is not None:
        for el in list(core):
            if el.tag in _CORE_DROP:
                core.remove(el)
    app = pkg.xml("docProps/app.xml")
    if app is not None:
        for el in list(app):
            if el.tag in _APP_DROP:
                app.remove(el)
        for el in app.iter(f"{{{VT}}}lpstr"):
            if el.getparent() is not None and el.getparent().getparent() is not None \
                    and el.getparent().getparent().tag == f"{{{EP}}}TitlesOfParts":
                el.text = ""
    # Миниатюра первой страницы и пользовательские свойства — из корня пакета.
    _drop_rel_targets(pkg, "", ("thumbnail", "custom-properties"))
    for name in [n for n in pkg.raw if n.startswith("docProps/thumbnail")]:
        pkg.remove(name)


def _clean_settings(pkg: _Package, main: str) -> None:
    for settings in pkg.related(main, "settings"):
        root = pkg.xml(settings)
        if root is None:
            continue
        for name in ("attachedTemplate", "docVars", "mailMerge"):
            for el in list(root.iter(_w(name))):
                _drop(el)
        for rel in pkg.rels(settings):
            if rel.get("Type", "").rsplit("/", 1)[-1] in ("attachedTemplate", "mailMergeSource",
                                                          "mailMergeHeaderSource", "recipientData"):
                pkg.drop_rel(settings, rel)


def _strip_attribute_text(root: etree._Element) -> None:
    """Описания картинок и фигур, где бывают имена: их нет в Markdown."""
    for el in root.iter(f"{{{WP}}}docPr", f"{{{A}}}docPr"):
        for attr in ("descr", "title"):
            if attr in el.attrib:
                el.set(attr, "")
    for el in root.iter(f"{{{V}}}shape", f"{{{V}}}imagedata", f"{{{V}}}rect", f"{{{V}}}group"):
        for attr in ("alt", f"{{{VO}}}title"):
            if attr in el.attrib:
                el.set(attr, "")


def _redact_fields(root: etree._Element, values: _Values) -> int:
    count = 0
    for el in root.iter(_w("instrText")):
        if el.text:
            el.text, hits = values.replace(el.text)
            count += hits
    for el in root.iter(_w("fldSimple")):
        instr = el.get(_w("instr"))
        if instr:
            new, hits = values.replace(instr)
            el.set(_w("instr"), new)
            count += hits
    return count


def _unlink_hyperlinks(pkg: _Package, part: str, root: etree._Element, values: _Values,
                       replaced_links: Iterable[etree._Element] = ()) -> None:
    """Ссылки на адреса с заменяемыми значениями (mailto:, сайт) превращаются в текст."""
    bad: set[str] = set()
    for rel in pkg.rels(part):
        if rel.get("Type", "").endswith("/hyperlink") and rel.get("TargetMode") == "External":
            target = rel.get("Target", "")
            if values.count(target) or values.count(unquote(target)):
                bad.add(rel.get("Id", ""))
    links = set(replaced_links)
    for link in list(root.iter(_w("hyperlink"))):
        if link in links or link.get(f"{{{R}}}id") in bad:
            rid = link.get(f"{{{R}}}id")
            if rid:
                bad.add(rid)
            _unwrap(link)
    for click in list(root.iter(f"{{{A}}}hlinkClick", f"{{{A}}}hlinkHover")):
        if click.get(f"{{{R}}}id") in bad:
            _drop(click)
    for rel in pkg.rels(part):
        if rel.get("Id") in bad and rel.get("Type", "").endswith("/hyperlink"):
            pkg.drop_rel(part, rel)
    # Поле HYPERLINK со значением в адресе: код поля уже заменен меткой.


def _drop_embeddings(pkg: _Package, part: str, root: etree._Element) -> None:
    """Встроенные объекты (таблица Excel, другой документ) остаются картинкой без данных."""
    ids = _drop_rel_targets(pkg, part, ("oleObject", "package", "aFChunk", "control"))
    if not ids:
        return
    for el in list(root.iter(f"{{{VO}}}OLEObject", _w("altChunk"), _w("control"), f"{{{C}}}externalData")):
        if el.get(f"{{{R}}}id") in ids:
            _drop(el)


def _text_of(pkg: _Package) -> str:
    """Весь видимый текст пакета по абзацам: для итоговой проверки остатков."""
    lines: list[str] = []
    for name in pkg.raw:
        if not name.endswith(".xml") and not name.endswith(".rels"):
            continue
        root = pkg.xml(name)
        if root is None:
            continue
        if name.endswith(".rels"):
            lines.extend(rel.get("Target", "") for rel in root)
            continue
        for paragraph in root.iter(_w("p"), f"{{{A}}}p"):
            lines.append("".join(t.text or "" for t in paragraph.iter(_w("t"), f"{{{A}}}t")))
        for el in root.iter(_w("instrText"), f"{{{C}}}v"):
            lines.append(el.text or "")
        if name.startswith("docProps/"):
            lines.extend(el.text or "" for el in root.iter() if el.text)
    return "\n".join(lines)


# ---------------------------------------------------------------- сборка


def _main_part(pkg: _Package) -> str:
    for rel in pkg.rels(""):
        if rel.get("Type", "").endswith("/officeDocument"):
            return _resolve("", rel.get("Target", ""))
    raise RedactedDocxError("Файл не похож на документ Word: в нем нет основного текста.")


def redact_docx_bytes(
    data: bytes, source_text: str, entities: Sequence[Entity], cancel: Optional[CancelToken] = None,
) -> tuple[bytes, RedactedDocxReport]:
    pkg = _Package(data)
    main = _main_part(pkg)
    body = pkg.xml(main)
    if body is None:
        raise RedactedDocxError("Файл не похож на документ Word: основной текст не читается.")
    report = RedactedDocxReport()
    values = _Values.build(source_text, entities)

    notes = [p for kind in ("footnotes", "endnotes") for p in pkg.related(main, kind)]
    margins = [p for kind in ("header", "footer") for p in pkg.related(main, kind)]
    glossary = pkg.related(main, "glossaryDocument")
    wml_parts = [main, *notes, *margins, *glossary]

    # Исправления принимаются, примечания удаляются: так выглядит и Markdown.
    for part in wml_parts:
        root = pkg.xml(part)
        if root is None:
            continue
        report.revisions += accept_revisions(root)
        report.comments += remove_comment_marks(root)
        for el in list(root.iter(_w("dataBinding"), "{http://schemas.microsoft.com/office/word/2012/wordml}dataBinding")):
            _drop(el)
    for kind in ("comments", "commentsExtended", "commentsIds", "commentsExtensible", "people", "customXml"):
        _drop_rel_targets(pkg, main, (kind,))
    for name in [n for n in pkg.raw if n.startswith("customXml/")]:
        pkg.remove(name)
    check(cancel)

    # Основной текст и сноски: замены из Markdown на свои места.
    stream = _Stream()
    for part in [main, *notes]:
        root = pkg.xml(part)
        if root is not None:
            stream.walk(root)
    doc_text = stream.text
    al = align(source_text, doc_text)
    if al.md_tokens and al.share < MIN_MATCHED_SHARE:
        raise RedactedDocxError(
            "Этот файл Word не совпадает с текстом документа: выберите исходный файл, из которого он был получен."
        )
    check(cancel)
    spans: list[tuple[int, int, str]] = []
    images = find_image_ranges(source_text)
    for e in entities:
        if not e.anonymize:
            continue
        for p in e.positions:
            if any(img.start <= p.start and p.end <= img.end for img in images):
                continue
            found = map_occurrence(source_text, doc_text, al, p.start, p.end)
            if found is not None and found[1] > found[0]:
                spans.append((found[0], found[1], e.placeholder))
                report.replaced += 1
    blocks = sorted(al.blocks)
    mapped = _merge(spans)
    taken = [(a, b) for a, b, _ in mapped]
    outside = [
        s for s in _sweep(stream, values, lambda a, b: not _overlaps(blocks, a, b) and not _overlaps(taken, a, b))
    ]
    report.outside += len(outside)
    done = _apply_spans(stream, _merge([*mapped, *outside]))
    replaced_links = [link for start, end, link in stream.hyperlinks if _overlaps(sorted(done), start, end)]

    # Надписи: mammoth читает mc:Fallback, Word показывает mc:Choice. Текст надписи
    # из Fallback (уже обезличенный) переносится в Choice; остальное — по значениям.
    for choice in stream.choices:
        fallback = choice.getparent().find(f"{{{MC}}}Fallback") if choice.getparent() is not None else None
        boxes = list(choice.iter(_w("txbxContent")))
        sources = list(fallback.iter(_w("txbxContent"))) if fallback is not None else []
        if boxes and len(boxes) == len(sources):
            for box, src in zip(boxes, sources):
                for child in list(box):
                    box.remove(child)
                for child in src:
                    box.append(copy.deepcopy(child))
        extra = _Stream()
        extra.walk(choice, mammoth_order=False)
        hits = _sweep(extra, values)
        report.outside += len(hits)
        _apply_spans(extra, _merge(hits))
    check(cancel)

    # Колонтитулы и прочий текст, которого нет в Markdown: по значениям.
    for part in [*margins, *glossary]:
        root = pkg.xml(part)
        if root is None:
            continue
        extra = _Stream()
        extra.walk(root, mammoth_order=False)
        hits = _sweep(extra, values)
        report.outside += len(hits)
        _apply_spans(extra, _merge(hits))
    for part in wml_parts:
        root = pkg.xml(part)
        if root is None:
            continue
        report.outside += _redact_fields(root, values)
        _strip_attribute_text(root)
        _unlink_hyperlinks(pkg, part, root, values, replaced_links if part == main else ())
        _drop_embeddings(pkg, part, root)
    # Диаграммы и SmartArt: подписи и значения.
    for name in list(pkg.raw):
        if re.match(r"word/(charts|diagrams)/[^/]+\.xml$", name):
            root = pkg.xml(name)
            if root is None:
                continue
            for el in root.iter(f"{{{A}}}t", f"{{{C}}}v"):
                if el.text:
                    el.text, hits = values.replace(el.text)
                    report.outside += hits
            _strip_attribute_text(root)
            _drop_embeddings(pkg, name, root)
    check(cancel)

    report.images = _blank_images(pkg)
    _clean_properties(pkg)
    _clean_settings(pkg, main)

    # Итоговая проверка: значение не должно встречаться в файле чаще, чем в Markdown.
    from .apply import build_anonymized_text

    allowed = values.count(build_anonymized_text(source_text, list(entities)))
    left = values.count(_text_of(pkg))
    by_label = {label: key for key, label in values.labels.items()}
    report.unmatched = sorted(
        (label, by_label.get(label, "")) for label, n in left.items() if n > allowed.get(label, 0)
    )
    return pkg.save(), report


def export_redacted_docx(
    source: str | Path, target: str | Path, source_text: str, entities: Sequence[Entity],
    cancel: Optional[CancelToken] = None,
) -> RedactedDocxReport:
    try:
        data = Path(source).read_bytes()
    except OSError as exc:
        raise RedactedDocxError(f"Не удалось прочитать исходный файл: {exc}") from exc
    result, report = redact_docx_bytes(data, source_text, entities, cancel)
    check(cancel)
    Path(target).write_bytes(result)
    return report
