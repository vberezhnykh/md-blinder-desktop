"""Текст для NER без разметки (порт src/lib/anonymize/stripFormatting.ts).

GLiNER не узнает сущности, если к словам приклеены маркеры *, _, `, ~. Картинки
и data URI исключаются: модель не читает пиксели, а base64 раздувает число
фрагментов в десятки раз. map[i] — индекс i-го символа plain в исходнике.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .image_safety import find_image_exclusion_ranges
from .types import Position, RawMatch

_DATA_URI_RE = re.compile(r"data:[\w.+-]+/[\w.+-]+;base64,[A-Za-z0-9+/=]+", re.A)


@dataclass
class StrippedText:
    plain: str
    map: list[int]
    excluded: list[Position]


def strip_inline_formatting(md: str) -> StrippedText:
    skip = list(find_image_exclusion_ranges(md))
    skip.extend(Position(m.start(), m.end()) for m in _DATA_URI_RE.finditer(md))
    skip.sort(key=lambda r: (r.start, -r.end))
    si = 0
    plain: list[str] = []
    mapping: list[int] = []
    i = 0
    n = len(md)
    while i < n:
        while si < len(skip) and i >= skip[si].end:
            si += 1
        if si < len(skip) and i >= skip[si].start:
            i = skip[si].end
            continue
        ch = md[i]
        if ch not in "*_`~":
            plain.append(ch)
            mapping.append(i)
        i += 1
    return StrippedText("".join(plain), mapping, skip)


def remap_matches(
    matches: list[RawMatch], plain: str, mapping: list[int], excluded: list[Position] | None = None
) -> list[RawMatch]:
    """Переводит смещения из plain в исходник; value берется из plain."""
    excluded = excluded or []
    out: list[RawMatch] = []
    for m in matches:
        if not isinstance(m.start, int) or not isinstance(m.end, int):
            continue
        if m.start < 0 or m.end <= m.start or m.start >= len(mapping):
            continue
        end_idx = min(m.end, len(mapping)) - 1
        start = mapping[m.start]
        end = mapping[end_idx] + 1
        # Спан модели не должен поглотить удаленную картинку и текст вокруг нее.
        if any(start < r.end and r.start < end for r in excluded):
            continue
        out.append(RawMatch(type=m.type, value=plain[m.start:m.end], start=start, end=end, score=m.score))
    return out
