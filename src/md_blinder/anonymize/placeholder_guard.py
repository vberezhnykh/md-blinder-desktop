"""Защита от коллизии плейсхолдеров (порт src/lib/anonymize/placeholderGuard.ts).

Если исходный документ уже содержит строки вида [PERSON_1], они неотличимы от
плейсхолдеров в выдаче. Такие строки обратимо экранируются символом нулевой
ширины после «[».
"""

from __future__ import annotations

import re

from .types import ALL_ENTITY_TYPES, Position

ZW = "​"
_PLACEHOLDER_RE = re.compile(r"\[(?:" + "|".join(ALL_ENTITY_TYPES) + r")_[0-9]+\]")


def find_placeholder_ranges(text: str) -> list[Position]:
    return [Position(m.start(), m.end()) for m in _PLACEHOLDER_RE.finditer(text)]


def has_placeholder_collision(text: str) -> bool:
    return _PLACEHOLDER_RE.search(text) is not None


def escape_placeholder_literal(literal: str) -> str:
    return "[" + ZW + literal[1:]


def unescape_placeholders(text: str) -> str:
    return text.replace("[" + ZW, "[")
