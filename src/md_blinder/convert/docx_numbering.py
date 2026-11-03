"""Номера автонумерации Word как текст абзаца.

В DOCX номер пункта («5.», «5.1.», «(a)») не хранится в тексте: Word считает его
при показе по numbering.xml. mammoth превращает такие абзацы в списки <ol>, и в
Markdown номер становится «1.», «2.» внутри каждого списка: «5.1.» превращается
в «1.», нумерация начинается заново после каждой таблицы, абзаца без номера и в
каждой ячейке таблицы. Ссылки вида «см. п. 7.1» перестают совпадать с пунктами.

Здесь номера считаются так же, как в Word (счетчики общего описания списка,
сброс вложенных уровней, начальные значения, формат «%1.%2.», римские цифры и
буквы), и вписываются в начало абзаца обычным текстом, а нумерация абзаца
снимается. Маркированные списки (bullet) не трогаются: mammoth выводит их как
списки Markdown.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Optional
from xml.etree import ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_W = "{%s}" % W
_DOCUMENT = "word/document.xml"
_NUMBERING = "word/numbering.xml"
_MAX_LEVEL = 9


def _w(name: str) -> str:
    return _W + name


def _val(el: Optional[ET.Element], name: str) -> Optional[str]:
    if el is None:
        return None
    child = el.find(_w(name))
    return None if child is None else child.get(_w("val"))


def _int(raw: Optional[str], default: Optional[int]) -> Optional[int]:
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


@dataclass
class _Level:
    fmt: str = "decimal"
    text: str = ""
    start: int = 1
    restart: Optional[int] = None
    legal: bool = False
    suffix: str = " "
    style: Optional[str] = None


def _read_level(el: ET.Element) -> _Level:
    suff = _val(el, "suff")
    return _Level(
        fmt=_val(el, "numFmt") or "decimal",
        text=_val(el, "lvlText") or "",
        start=_int(_val(el, "start"), 1) or 0,
        restart=_int(_val(el, "lvlRestart"), None),
        legal=el.find(_w("isLgl")) is not None and _val(el, "isLgl") not in ("0", "false"),
        suffix="" if suff == "nothing" else " ",
        style=_val(el, "pStyle"),
    )


@dataclass
class _Abstract:
    levels: dict[int, _Level] = field(default_factory=dict)
    style_link: Optional[str] = None


@dataclass
class _Num:
    abstract: str
    starts: dict[int, int] = field(default_factory=dict)
    levels: dict[int, _Level] = field(default_factory=dict)


class _Numbering:
    def __init__(self, numbering_xml: Optional[bytes], styles_xml: Optional[bytes]) -> None:
        self.abstracts: dict[str, _Abstract] = {}
        self.nums: dict[str, _Num] = {}
        # Стиль абзаца → (numId, ilvl) из его свойств, с учетом basedOn.
        self.style_num: dict[str, tuple[Optional[str], Optional[int]]] = {}
        self.style_parent: dict[str, str] = {}
        self.default_style: Optional[str] = None
        # Стиль абзаца → уровень списка, к которому он привязан в numbering.xml.
        self.level_by_style: dict[str, tuple[str, int]] = {}
        if numbering_xml:
            self._read_numbering(ET.fromstring(numbering_xml))
        if styles_xml:
            self._read_styles(ET.fromstring(styles_xml))

    def _read_numbering(self, root: ET.Element) -> None:
        for el in root.findall(_w("abstractNum")):
            abstract = _Abstract(style_link=_val(el, "numStyleLink"))
            for lvl in el.findall(_w("lvl")):
                index = _int(lvl.get(_w("ilvl")), 0) or 0
                abstract.levels[index] = _read_level(lvl)
            self.abstracts[el.get(_w("abstractNumId")) or ""] = abstract
        for el in root.findall(_w("num")):
            num = _Num(abstract=_val(el, "abstractNumId") or "")
            for override in el.findall(_w("lvlOverride")):
                index = _int(override.get(_w("ilvl")), 0) or 0
                start = _int(_val(override, "startOverride"), None)
                if start is not None:
                    num.starts[index] = start
                lvl = override.find(_w("lvl"))
                if lvl is not None:
                    num.levels[index] = _read_level(lvl)
            self.nums[el.get(_w("numId")) or ""] = num

    def _read_styles(self, root: ET.Element) -> None:
        for el in root.findall(_w("style")):
            style_id = el.get(_w("styleId"))
            if not style_id:
                continue
            if el.get(_w("type")) == "paragraph" and el.get(_w("default")) in ("1", "true"):
                self.default_style = style_id
            parent = _val(el, "basedOn")
            if parent:
                self.style_parent[style_id] = parent
            num_pr = el.find(f"{_w('pPr')}/{_w('numPr')}")
            if num_pr is not None:
                self.style_num[style_id] = (_val(num_pr, "numId"), _int(_val(num_pr, "ilvl"), None))
        for abstract_id, abstract in self.abstracts.items():
            for index, level in abstract.levels.items():
                if level.style:
                    self.level_by_style.setdefault(level.style, (abstract_id, index))

    def abstract_id(self, num_id: str) -> Optional[str]:
        """Описание списка, на которое ссылается numId, с переходом по numStyleLink."""
        seen: set[str] = set()
        while num_id in self.nums and num_id not in seen:
            seen.add(num_id)
            abstract_id = self.nums[num_id].abstract
            abstract = self.abstracts.get(abstract_id)
            if abstract is None:
                return None
            if not abstract.style_link:
                return abstract_id
            linked = self.style_num.get(abstract.style_link, (None, None))[0]
            if linked is None:
                return abstract_id if abstract.levels else None
            num_id = linked
        return None

    def style_numbering(self, style_id: Optional[str]) -> tuple[Optional[str], Optional[int]]:
        # numId и ilvl наследуются по basedOn независимо: стиль может задать только уровень.
        num_id: Optional[str] = None
        level: Optional[int] = None
        seen: set[str] = set()
        while style_id and style_id not in seen and (num_id is None or level is None):
            seen.add(style_id)
            own_num, own_level = self.style_num.get(style_id, (None, None))
            num_id = own_num if num_id is None else num_id
            level = own_level if level is None else level
            style_id = self.style_parent.get(style_id)
        return num_id, level

    def bullet_depth(self, num_id: str, index: int) -> int:
        """Сколько маркированных уровней списка над этим уровнем."""
        abstract_id = self.abstract_id(num_id)
        if abstract_id is None:
            return 0
        levels = (self.level(num_id, abstract_id, i) for i in range(index))
        return sum(1 for level in levels if level is not None and level.fmt == "bullet")

    def level(self, num_id: str, abstract_id: str, index: int) -> Optional[_Level]:
        num = self.nums.get(num_id)
        if num is not None and index in num.levels:
            return num.levels[index]
        return self.abstracts[abstract_id].levels.get(index)


_ROMAN = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
          (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
_LATIN = "abcdefghijklmnopqrstuvwxyz"
# Буквенная нумерация Word для русского: без ё, й, ъ, ы, ь.
_RUSSIAN = "абвгдежзиклмнопрстуфхцчшщэюя"


def _roman(value: int) -> str:
    out = []
    for amount, letters in _ROMAN:
        while value >= amount:
            out.append(letters)
            value -= amount
    return "".join(out)


def _letters(value: int, alphabet: str) -> str:
    # Word: a…z, затем aa…zz, aaa… (буква повторяется, а не «ab»).
    if value <= 0:
        return str(value)
    letter = alphabet[(value - 1) % len(alphabet)]
    return letter * ((value - 1) // len(alphabet) + 1)


def format_number(value: int, fmt: str) -> str:
    if fmt in ("lowerRoman", "upperRoman") and value > 0:
        text = _roman(value)
        return text.upper() if fmt == "upperRoman" else text
    if fmt in ("lowerLetter", "upperLetter"):
        text = _letters(value, _LATIN)
        return text.upper() if fmt == "upperLetter" else text
    if fmt in ("russianLower", "russianUpper"):
        text = _letters(value, _RUSSIAN)
        return text.upper() if fmt == "russianUpper" else text
    if fmt == "decimalZero" and 0 <= value < 10:
        return f"0{value}"
    return str(value)


_PLACEHOLDER_RE = re.compile(r"%([1-9])")


class _Counter:
    """Счетчики одного описания списка: Word ведет их общими для всех numId с этим описанием."""

    def __init__(self) -> None:
        self.values: dict[int, int] = {}


class NumberingLabels:
    """Номера абзацев по порядку документа."""

    def __init__(self, numbering: _Numbering) -> None:
        self.numbering = numbering
        self.counters: dict[str, _Counter] = {}
        self.used_nums: set[str] = set()

    def _start(self, num_id: str, abstract_id: str, index: int) -> int:
        num = self.numbering.nums.get(num_id)
        if num is not None and index in num.starts:
            return num.starts[index]
        level = self.numbering.level(num_id, abstract_id, index)
        return level.start if level else 1

    def next(self, num_id: str, index: int) -> Optional[tuple[_Level, str]]:
        """Продвигает счетчик уровня и возвращает уровень и его номер; None, если списка нет."""
        abstract_id = self.numbering.abstract_id(num_id)
        if abstract_id is None:
            return None
        level = self.numbering.level(num_id, abstract_id, index)
        if level is None:
            return None
        counter = self.counters.setdefault(abstract_id, _Counter())
        num = self.numbering.nums.get(num_id)
        if num_id not in self.used_nums:
            self.used_nums.add(num_id)
            # numId с переопределением начала начинает список заново.
            if num is not None and num.starts:
                counter.values.clear()
        values = counter.values
        values[index] = values[index] + 1 if index in values else self._start(num_id, abstract_id, index)
        for deeper in range(index + 1, _MAX_LEVEL):
            deeper_level = self.numbering.level(num_id, abstract_id, deeper)
            restart = deeper_level.restart if deeper_level else None
            # lvlRestart — номер уровня (с 1), после которого этот уровень начинается заново; 0 — никогда.
            if restart is None or (restart != 0 and index <= restart - 1):
                values.pop(deeper, None)

        def piece(match: re.Match) -> str:
            ref = int(match.group(1)) - 1
            ref_level = self.numbering.level(num_id, abstract_id, ref)
            value = values.get(ref)
            if value is None:
                value = self._start(num_id, abstract_id, ref)
            # isLgl: все номера в тексте уровня арабскими цифрами («1.2» вместо «I.2»).
            fmt = "decimal" if level.legal or ref_level is None else ref_level.fmt
            return format_number(value, fmt)

        return level, _PLACEHOLDER_RE.sub(piece, level.text)


def _paragraphs(body: ET.Element):
    """Абзацы в порядке документа, кроме вариантов mc:Choice: mammoth читает mc:Fallback."""
    stack = [body]
    while stack:
        el = stack.pop()
        if el.tag == "{%s}Choice" % MC:
            continue
        if el.tag == _w("p"):
            yield el
        stack.extend(reversed(list(el)))


def _paragraph_numbering(p: ET.Element, numbering: _Numbering) -> Optional[tuple[str, int]]:
    ppr = p.find(_w("pPr"))
    style = _val(ppr, "pStyle") or numbering.default_style
    style_num, style_level = numbering.style_numbering(style)
    num_pr = ppr.find(_w("numPr")) if ppr is not None else None
    num_id = _val(num_pr, "numId")
    level = _int(_val(num_pr, "ilvl"), None)
    if num_id is None:
        num_id = style_num
        if num_id is None and style in numbering.level_by_style:
            # Стиль привязан к уровню списка только из numbering.xml.
            abstract_id, index = numbering.level_by_style[style]
            num_id = next((nid for nid, n in numbering.nums.items() if n.abstract == abstract_id), None)
            level = index if level is None else level
    if num_id is None or num_id == "0":
        return None
    if level is None:
        level = style_level
    if level is None and style in numbering.level_by_style:
        level = numbering.level_by_style[style][1]
    return num_id, max(0, min(level or 0, _MAX_LEVEL - 1))


def _set_numbering(p: ET.Element, num_id: str, level: int) -> ET.Element:
    """Ставит абзацу numPr; numId 0 снимает нумерацию, в том числе унаследованную от стиля."""
    ppr = p.find(_w("pPr"))
    if ppr is None:
        ppr = ET.Element(_w("pPr"))
        p.insert(0, ppr)
    for old in ppr.findall(_w("numPr")):
        ppr.remove(old)
    # ilvl нужен mammoth и при numId 0: без него он ищет уровень списка по стилю абзаца.
    num_pr = ET.Element(_w("numPr"))
    ET.SubElement(num_pr, _w("ilvl")).set(_w("val"), str(level))
    ET.SubElement(num_pr, _w("numId")).set(_w("val"), num_id)
    style = ppr.find(_w("pStyle"))
    ppr.insert(1 if style is not None and list(ppr).index(style) == 0 else 0, num_pr)
    return ppr


def _write_label(p: ET.Element, label: str) -> None:
    ppr = _set_numbering(p, "0", 0)
    if not label:
        return
    run = ET.Element(_w("r"))
    text = ET.SubElement(run, _w("t"))
    text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text.text = label
    p.insert(list(p).index(ppr) + 1, run)


def _next_id(ids, floor: int = 1000) -> str:
    numeric = [int(i) for i in ids if i.isdigit()]
    return str(max(numeric + [floor]) + 1)


def _bullet_list(numbering_xml: bytes, abstract_id: str, num_id: str) -> bytes:
    """numbering.xml с маркированным списком на все уровни для перенесенных маркеров."""
    root = ET.fromstring(numbering_xml)
    abstract = ET.Element(_w("abstractNum"), {_w("abstractNumId"): abstract_id})
    for index in range(_MAX_LEVEL):
        lvl = ET.SubElement(abstract, _w("lvl"), {_w("ilvl"): str(index)})
        ET.SubElement(lvl, _w("numFmt"), {_w("val"): "bullet"})
        ET.SubElement(lvl, _w("lvlText"), {_w("val"): "•"})
    nums = root.findall(_w("num"))
    root.insert(list(root).index(nums[0]) if nums else len(root), abstract)
    num = ET.SubElement(root, _w("num"), {_w("numId"): num_id})
    ET.SubElement(num, _w("abstractNumId"), {_w("val"): abstract_id})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def number_paragraphs(
    document_xml: bytes, numbering_xml: bytes, styles_xml: Optional[bytes]
) -> tuple[bytes, bytes]:
    """Возвращает document.xml и numbering.xml, где номера нумерованных абзацев вписаны в текст.

    Маркированные абзацы остаются списком, но переходят в отдельный маркированный
    список с уровнем, равным числу маркированных уровней над ними: иначе маркер
    под пунктом «5.1.» стал бы в Markdown вложенным списком без родителя («* * текст»).
    Если менять нечего, возвращаются те же объекты bytes.
    """
    numbering = _Numbering(numbering_xml, styles_xml)
    if not numbering.nums:
        return document_xml, numbering_xml
    root = ET.fromstring(document_xml)
    labels = NumberingLabels(numbering)
    bullet_num = _next_id(numbering.nums)
    bullets = False
    changed = False
    for p in _paragraphs(root):
        found = _paragraph_numbering(p, numbering)
        if found is None:
            continue
        result = labels.next(*found)
        if result is None:
            continue
        level, label = result
        if level.fmt == "bullet":
            _set_numbering(p, bullet_num, numbering.bullet_depth(*found))
            bullets = True
        else:
            _write_label(p, label + level.suffix if label.strip() else "")
        changed = True
    if not changed:
        return document_xml, numbering_xml
    if bullets:
        numbering_xml = _bullet_list(numbering_xml, _next_id(numbering.abstracts), bullet_num)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), numbering_xml


def with_numbering_as_text(data: bytes) -> bytes:
    """Копия DOCX, где номера автонумерации стали текстом. При ошибке разбора — исходный файл."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as src:
            names = set(src.namelist())
            if _DOCUMENT not in names or _NUMBERING not in names:
                return data
            document = src.read(_DOCUMENT)
            styles = src.read("word/styles.xml") if "word/styles.xml" in names else None
            new_document, new_numbering = number_paragraphs(document, src.read(_NUMBERING), styles)
            if new_document is document:
                return data
            replaced = {_DOCUMENT: new_document, _NUMBERING: new_numbering}
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
                for info in src.infolist():
                    dst.writestr(info, replaced.get(info.filename) or src.read(info.filename))
            return out.getvalue()
    except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError):
        return data
