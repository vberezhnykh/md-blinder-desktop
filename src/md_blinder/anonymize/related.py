"""Поиск «родственных» вхождений подтвержденных сущностей
(порт src/lib/anonymize/relatedOccurrences.ts).

B3: недетектированные формы того же ПД (фамилия без имени, инициалы и фамилия,
косвенные падежи для кириллицы, название компании без ОПФ или аббревиатура,
переформатированные email и телефон). Все найденное уходит в «серую зону» (B1)
как кандидат со ссылкой related_to на исходную сущность; причина «Похоже на
[PLACEHOLDER]» вычисляется при показе (candidate_reason), чтобы метка не
устаревала после перенумерации. Ничего не анонимизируется автоматически.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import regex

from .apply import next_placeholder_number
from .image_safety import mask_images_for_detection
from .normalize import normalize_value
from .types import Entity, EntityType, Position

LEGAL_FORMS = frozenset({
    "ltd", "limited", "llc", "llp", "inc", "incorporated", "plc", "corp",
    "corporation", "co", "company", "gmbh", "kft", "ag", "sa", "bv", "nv", "oy",
    "ab", "as", "spa", "srl", "pllc", "pc",
    "ооо", "оао", "зао", "пао", "ао", "нко", "нао", "пк",
    "тов", "пат", "прат", "пп", "фоп", "кп", "дп",
})

# Класс пробелов JS \s (в Python \s шире: включает \x1c-\x1f и \x85, но не ﻿).
_WS = r"[\t\n\v\f\r    -     　﻿]"

_QUOTE_RE = regex.compile("[«»\"“”„'‘’`]")
_CYR_RE = regex.compile("[Ѐ-ӿ]")
_SPLIT_RE = regex.compile(r"(?:" + _WS + r"|[,./\\])+")
_EDGE_RE = regex.compile(r"^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+\Z")
_INITIAL_RE = regex.compile(r"\p{L}\.?")
_ADJ_SURNAME_RE = regex.compile(r"(?:ский|цкий|ой|ый|ий)\Z")
_ADJ_ENDING_RE = regex.compile(r"(?:ий|ый|ой)\Z")
_TRAILING_DOT_RE = regex.compile(r"\.\Z")
_NOT_PHONE_CHAR_RE = regex.compile(r"[^0-9+]")

_FLAGS_IU = regex.IGNORECASE | regex.V0


def _escape(s: str) -> str:
    return regex.escape(s)


def _bounded_word(inner: str) -> str:
    """Граница «слова» с поддержкой кириллицы (JS \\b — только ASCII)."""
    return rf"(?<![\p{{L}}\p{{N}}])(?:{inner})(?![\p{{L}}\p{{N}}])"


@dataclass
class _Form:
    pattern: regex.Pattern
    min_len: int


def _word_form(word: str, min_len: int = 4) -> _Form:
    return _Form(regex.compile(_bounded_word(_escape(word)), _FLAGS_IU), min_len)


def _split_words(value: str) -> list[str]:
    words = _SPLIT_RE.split(_QUOTE_RE.sub(" ", value))
    return [w for w in (_EDGE_RE.sub("", w) for w in words) if w]


def _is_initial_token(w: str) -> bool:
    return _INITIAL_RE.fullmatch(w) is not None


def _cyrillic_declensions(surname: str) -> list[str]:
    """Простая таблица суффиксов косвенных падежей кириллической фамилии (без
    морфоанализа). Над-генерация допустима: все уходит в кандидаты на проверку."""
    forms: dict[str, None] = {}
    if _ADJ_SURNAME_RE.search(surname):
        stem = _ADJ_ENDING_RE.sub("", surname, count=1)
        for suf in ("ого", "ому", "ым", "ом", "ая", "ой", "ую"):
            forms[stem + suf] = None
    else:
        for suf in ("а", "у", "ом", "ым", "е", "ой", "ых", "ову", "ове"):
            forms[surname + suf] = None
    forms.pop(surname, None)
    return [f for f in forms if len(f) >= 4]


def _initials_forms(words: list[str]) -> list[_Form]:
    names = [w for w in words if not _is_initial_token(w) and len(w) >= 2]
    if len(names) < 2:
        return []
    forms: list[_Form] = []

    def push(initial_words: list[str], surname: str, initials_first: bool) -> None:
        if not initial_words:
            return
        inits = rf"{_WS}*".join(rf"{_escape(w[0])}\.?" for w in initial_words)
        sur = _escape(surname)
        inner = rf"{inits}{_WS}*{sur}" if initials_first else rf"{sur}{_WS}*{inits}"
        forms.append(_Form(regex.compile(_bounded_word(inner), _FLAGS_IU), len(surname)))

    # первое слово как фамилия (рус. порядок «Фамилия Имя Отчество»)
    push(names[1:], names[0], True)
    push(names[1:], names[0], False)
    # последнее слово как фамилия (зап. порядок «Имя … Фамилия»)
    push(names[:-1], names[-1], True)
    push(names[:-1], names[-1], False)
    return forms


def _person_forms(value: str) -> list[_Form]:
    words = _split_words(value)
    names = [w for w in words if not _is_initial_token(w)]
    forms: list[_Form] = []
    # Порядок важен: сначала длинные формы (инициалы и фамилия), затем склонения,
    # затем одиночные слова, чтобы «И.И. Иванов» занял спан раньше, чем голое
    # «Иванов» перехватит подстроку внутри него.
    forms.extend(_initials_forms(words))
    for w in names:
        if _CYR_RE.search(w):
            forms.extend(_word_form(d) for d in _cyrillic_declensions(w))
    for w in names:
        if len(w) >= 4:
            forms.append(_word_form(w))
    return forms


def company_core_words(value: str) -> list[str]:
    """Слова названия компании без ОПФ и кавычек: «ООО «Гамма Плюс»» → ['Гамма', 'Плюс']."""
    return [w for w in _split_words(value) if _TRAILING_DOT_RE.sub("", w.lower(), count=1) not in LEGAL_FORMS]


def _company_forms(value: str) -> list[_Form]:
    core = company_core_words(value)
    forms: list[_Form] = []
    # Полная «ядровая» фраза (без ОПФ и кавычек) первой, чтобы занять спан.
    if len(core) >= 2:
        phrase = " ".join(core)
        if phrase.lower() != value.lower():
            inner = rf"{_WS}+".join(_escape(w) for w in core)
            forms.append(_Form(regex.compile(_bounded_word(inner), _FLAGS_IU), 4))
        acronym = "".join(w[0] for w in core)
        if len(acronym) >= 2:
            forms.append(_Form(regex.compile(_bounded_word(_escape(acronym)), _FLAGS_IU), 2))
    if core and len(core[0]) >= 4:
        forms.append(_word_form(core[0]))
    return forms


def _email_forms(value: str) -> list[_Form]:
    # TS: escapeRegExp(value), затем «@» → \s*@\s* и «\.» → \s*\.\s*. Здесь то же
    # строится посимвольно (см. отчет: TS ломается на «\» перед точкой).
    gap = rf"{_WS}*"
    parts: list[str] = []
    for ch in value:
        if ch == "@":
            parts.append(rf"{gap}@{gap}")
        elif ch == ".":
            parts.append(rf"{gap}\.{gap}")
        else:
            parts.append(_escape(ch))
    return [_Form(regex.compile("".join(parts), _FLAGS_IU), 4)]


def _phone_forms(value: str) -> list[_Form]:
    core = _NOT_PHONE_CHAR_RE.sub("", value)
    if core.startswith("+"):
        core = core[1:]
    if len(core) < 7:
        return []
    sep = r"(?:" + _WS + r"|[\-().])*"
    body = sep.join(_escape(c) for c in core)
    # Флаги TS: только g (без i и u); \d в JS — ASCII.
    return [_Form(regex.compile(rf"(?<![0-9])\+?{sep}{body}(?![0-9])", regex.V0), 7)]


def _forms_for(entity: Entity) -> list[_Form]:
    exact = [_word_form(entity.value, 2)]
    if entity.type == "PERSON":
        return exact + _person_forms(entity.value)
    if entity.type == "COMPANY":
        return exact + _company_forms(entity.value)
    if entity.type == "EMAIL":
        return exact + _email_forms(entity.value)
    if entity.type == "PHONE":
        return exact + _phone_forms(entity.value)
    return exact


@dataclass
class _Group:
    type: EntityType
    related_to: str
    first_value: str
    first_start: int
    positions: list[Position] = field(default_factory=list)


def find_related_occurrences(source_text: str, entities: list[Entity]) -> list[Entity]:
    """Новые сущности-кандидаты (candidate=True, anonymize=False, related_to=id источника)."""
    sources = [e for e in entities if not e.candidate and not e.dismissed and e.anonymize]
    searchable_text = mask_images_for_detection(source_text)
    claimed: list[tuple[int, int]] = [(p.start, p.end) for e in entities for p in e.positions]
    working: list[Entity] = list(entities)
    result: list[Entity] = []
    groups: dict[str, _Group] = {}

    for src in sources:
        for form in _forms_for(src):
            # Как цикл exec с lastIndex в JS: после пустого совпадения сдвиг на 1.
            pos = 0
            while pos <= len(searchable_text):
                m = form.pattern.search(searchable_text, pos)
                if m is None:
                    break
                text = m.group(0)
                if not text:
                    pos = m.start() + 1
                    continue
                start = m.start()
                end = m.end()
                pos = end
                if len(text) < form.min_len:
                    continue
                if any(start < c_end and c_start < end for c_start, c_end in claimed):
                    continue
                key = f"{src.type} {normalize_value(src.type, text)}"
                g = groups.get(key)
                if g is None:
                    g = _Group(src.type, src.id, text, start)
                    groups[key] = g
                g.positions.append(Position(start, end))
                g.first_start = min(g.first_start, start)
                claimed.append((start, end))

    for g in sorted(groups.values(), key=lambda g: g.first_start):
        placeholder = f"[{g.type}_{next_placeholder_number(working, g.type)}]"
        entity = Entity(
            id=str(uuid.uuid4()),
            type=g.type,
            value=g.first_value,
            placeholder=placeholder,
            positions=sorted(g.positions, key=lambda p: p.start),
            anonymize=False,
            manual=False,
            candidate=True,
            related_to=g.related_to,
        )
        working.append(entity)
        result.append(entity)
    return result
