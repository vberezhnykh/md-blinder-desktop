"""Обратная подстановка: метки в чужом тексте заменяются настоящими значениями.

Обезличенный договор уходит в нейросеть или внешнему юристу, а ответ приходит с
метками [PERSON_1], [COMPANY_2]. Таблица замен уже есть в истории, поэтому ответ
можно вернуть к настоящим именам прямо на компьютере.

За одной меткой бывает несколько написаний: в двуязычном договоре одно имя или
адрес латиницей и кириллицей получают одну метку. Тогда на место метки встает
написание той же азбуки, что текст вокруг нее (абзац или ячейка таблицы); если
подходящих несколько или азбука не ясна, берется первое вхождение в документе.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, Union

import regex

from .apply import entity_current_value
from .types import ALL_ENTITY_TYPES, Entity

# Метка типа в ответе. Нейросеть пишет Markdown и экранирует скобки и подчеркивание
# («\[PERSON\_1\]»), иногда ставит пробелы внутри скобок или меняет регистр.
_TYPES = "|".join(ALL_ENTITY_TYPES)
_TYPED_LABEL_RE = regex.compile(
    rf"\\?\[\s*({_TYPES})\\?_(\d+)\s*\\?\]", regex.IGNORECASE | regex.V0,
)


@dataclass
class RestoreResult:
    text: str
    replaced: int = 0
    labels: set[str] = field(default_factory=set)  # метки, которые нашлись и были заменены
    unknown: list[str] = field(default_factory=list)  # метки из ответа, которых нет в таблице замен


# Метка → написания значения, первое — основное. Одно значение можно дать строкой.
RestoreMap = dict[str, Union[str, list[str]]]


def restore_map(docs: Iterable[tuple[str, list[Entity]]]) -> dict[str, list[str]]:
    """Метка → написания по документам (текст, сущности). Первым идет написание
    первого вхождения из первого документа: в пакете он важнее остальных."""
    mapping: dict[str, list[str]] = {}
    for source_text, entities in docs:
        for e in entities:
            if e.candidate or e.dismissed or not e.anonymize or not e.positions:
                continue
            forms = mapping.setdefault(e.placeholder, [])
            rest = sorted(e.positions[1:], key=lambda p: p.start)
            values = [entity_current_value(source_text, e), *(source_text[p.start:p.end] for p in rest)]
            for value in values:
                value = " ".join(value.split())
                if value and value not in forms:
                    forms.append(value)
    return {label: forms for label, forms in mapping.items() if forms}


def _forms(value: Union[str, list[str]]) -> list[str]:
    return [value] if isinstance(value, str) else value


def _script(text: str) -> str | None:
    cyrillic = sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")
    latin = sum(1 for ch in text if ch.isalpha() and ch < "\u0250")
    if cyrillic == latin:
        return None
    return "cyrillic" if cyrillic > latin else "latin"


def _context(text: str, start: int, end: int) -> str:
    """Абзац или ячейка таблицы вокруг метки, без самих меток."""
    lo = max(text.rfind("\n", 0, start), text.rfind("|", 0, start)) + 1
    ends = [i for i in (text.find("\n", end), text.find("|", end)) if i >= 0]
    hi = min(ends) if ends else len(text)
    return _TYPED_LABEL_RE.sub(" ", text[lo:hi])


def value_at(text: str, start: int, end: int, value: Union[str, list[str]]) -> str:
    """Написание для метки на месте [start, end): той же азбуки, что текст вокруг."""
    forms = _forms(value)
    if len(forms) == 1:
        return forms[0]
    script = _script(_context(text, start, end))
    if script is None:
        return forms[0]
    return next((form for form in forms if _script(form) == script), forms[0])


def _key(type_: str, number: str) -> str:
    return f"[{type_.upper()}_{int(number)}]"


def find_labels(text: str, mapping: RestoreMap) -> Iterator[tuple[int, int, str]]:
    """Метки из mapping в тексте: (начало, конец, метка как в таблице замен). Метки
    типов узнаются и в экранированном Markdown, свои метки («[Поставщик]») — только
    как написаны."""
    custom = sorted((label for label in mapping if is_custom_label_text(label)), key=len, reverse=True)
    sources = [_TYPED_LABEL_RE.pattern, *(_custom_pattern(label) for label in custom)]
    combined = regex.compile("|".join(f"(?:{src})" for src in sources), regex.IGNORECASE | regex.V0)
    for m in combined.finditer(text):
        if m.group(1) is not None:
            key = _key(m.group(1), m.group(2))
        else:
            key = next(label for label in custom if _custom_matches(label, m.group(0)))
        if key in mapping:
            yield m.start(), m.end(), key


def unknown_labels(text: str) -> list[str]:
    """Метки типов, оставшиеся в тексте, без повторов, в порядке появления."""
    seen: list[str] = []
    for m in _TYPED_LABEL_RE.finditer(text):
        label = _key(m.group(1), m.group(2))
        if label not in seen:
            seen.append(label)
    return seen


def restore_text(text: str, mapping: RestoreMap) -> RestoreResult:
    """Заменяет метки из mapping значениями."""
    result = RestoreResult(text)
    pieces: list[str] = []
    pos = 0
    for start, end, key in find_labels(text, mapping):
        pieces.append(text[pos:start])
        pieces.append(value_at(text, start, end, mapping[key]))
        pos = end
        result.replaced += 1
        result.labels.add(key)
    pieces.append(text[pos:])
    result.text = "".join(pieces)
    result.unknown = unknown_labels(result.text)
    return result


def is_custom_label_text(label: str) -> bool:
    """Своя метка вроде «[Поставщик]», а не «[COMPANY_1]»."""
    return _TYPED_LABEL_RE.fullmatch(label) is None


def _custom_pattern(label: str) -> str:
    inner = label[1:-1] if label.startswith("[") and label.endswith("]") else label
    return rf"\\?\[\s*{regex.escape(inner)}\s*\\?\]"


def _custom_matches(label: str, found: str) -> bool:
    return regex.fullmatch(_custom_pattern(label), found, regex.IGNORECASE | regex.V0) is not None
