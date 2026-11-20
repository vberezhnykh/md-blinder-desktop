"""Обратная подстановка: метки в чужом тексте заменяются настоящими значениями.

Обезличенный договор уходит в нейросеть или внешнему юристу, а ответ приходит с
метками [PERSON_1], [COMPANY_2]. Таблица замен уже есть в истории, поэтому ответ
можно вернуть к настоящим именам прямо на компьютере.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

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


def restore_map(docs: Iterable[tuple[str, list[Entity]]]) -> dict[str, str]:
    """Метка → значение по документам (текст, сущности). Первый документ важнее:
    если в пакете одна метка стоит за разными написаниями, берется написание из него."""
    mapping: dict[str, str] = {}
    for source_text, entities in docs:
        for e in entities:
            if e.candidate or e.dismissed or not e.anonymize or not e.positions:
                continue
            mapping.setdefault(e.placeholder, " ".join(entity_current_value(source_text, e).split()))
    return mapping


def _key(type_: str, number: str) -> str:
    return f"[{type_.upper()}_{int(number)}]"


def restore_text(text: str, mapping: dict[str, str]) -> RestoreResult:
    """Заменяет метки из mapping значениями. Метки типов узнаются и в экранированном
    Markdown, свои метки («[Поставщик]») — только как написаны."""
    result = RestoreResult(text)
    custom = sorted((label for label in mapping if is_custom_label_text(label)), key=len, reverse=True)
    pieces: list[str] = []
    pos = 0
    sources = [_TYPED_LABEL_RE.pattern, *(_custom_pattern(label) for label in custom)]
    combined = regex.compile("|".join(f"(?:{src})" for src in sources), regex.IGNORECASE | regex.V0)
    for m in combined.finditer(text):
        if m.group(1) is not None:
            key = _key(m.group(1), m.group(2))
        else:
            key = next(label for label in custom if _custom_matches(label, m.group(0)))
        value = mapping.get(key)
        if value is None:
            continue
        pieces.append(text[pos:m.start()])
        pieces.append(value)
        pos = m.end()
        result.replaced += 1
        result.labels.add(key)
    pieces.append(text[pos:])
    result.text = "".join(pieces)
    seen: set[str] = set()
    for m in _TYPED_LABEL_RE.finditer(result.text):
        label = _key(m.group(1), m.group(2))
        if label not in seen:
            seen.add(label)
            result.unknown.append(label)
    return result


def is_custom_label_text(label: str) -> bool:
    """Своя метка вроде «[Поставщик]», а не «[COMPANY_1]»."""
    return _TYPED_LABEL_RE.fullmatch(label) is None


def _custom_pattern(label: str) -> str:
    inner = label[1:-1] if label.startswith("[") and label.endswith("]") else label
    return rf"\\?\[\s*{regex.escape(inner)}\s*\\?\]"


def _custom_matches(label: str, found: str) -> bool:
    return regex.fullmatch(_custom_pattern(label), found, regex.IGNORECASE | regex.V0) is not None
