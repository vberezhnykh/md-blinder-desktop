"""Склейка разорванных NER адресов. Порт src/lib/anonymize/stitchAddress.ts."""

from __future__ import annotations

import re
from typing import Optional

from .types import RawMatch

# GLiNER (small) рвёт длинные составные адреса на куски и уверенно отдаёт лишь
# отдельные фрагменты ("ROOM C05" из целого адреса). Склеиваем соседние
# ADDRESS-спаны в один, когда между ними только "клей" (запятые, слэши, цифры,
# пробелы) в пределах одного абзаца и без границы предложения.
# Не-ADDRESS матчи проходят насквозь без изменений.

# Пробельные символы JS \s (отличаются от Python \s: ﻿ только в JS,
# \x1c-\x1f и \x85 только в Python).
_WS_CHARS = r"\t\n\v\f\r    -     　﻿"

_PARAGRAPH_RE = re.compile(r"\n[ \t]*\n")
# Граница предложения: точка/!/? затем пробел и НЕ-цифра (JS \d — только ASCII).
_SENTENCE_RE = re.compile(rf"""[.!?]["')\]]?[{_WS_CHARS}]+[^0-9{_WS_CHARS}]""")
# Стоп-слова, которые чаще всего разделяют ДВЕ РАЗНЫЕ сущности в перечислении
# ("1 Main St to 2 Oak Ave", "from ... between ... and ..."), а не куски одного
# адреса. re.A: \b и регистронезависимость — только ASCII, как в JS без флага u.
_STOP_WORDS_RE = re.compile(r"\b(?:to|and|or|from|between|versus|vs)\b", re.I | re.A)


def _max_score(a: Optional[float], b: Optional[float]) -> Optional[float]:
    # None побеждает (означает "уверенность не ограничена сверху" — например
    # структурный матч), иначе берём максимум.
    if a is None or b is None:
        return None
    return max(a, b)


def stitch_address_runs(matches: list[RawMatch], text: str) -> list[RawMatch]:
    others = [m for m in matches if m.type != "ADDRESS"]
    addr = sorted((m for m in matches if m.type == "ADDRESS"), key=lambda m: m.start)

    def bridgeable(prev_end: int, next_start: int) -> bool:
        if next_start <= prev_end:
            return True  # перекрытие / вплотную
        gap = text[prev_end:next_start]
        if _PARAGRAPH_RE.search(gap):
            return False  # граница абзаца
        # Первый символ следующего фрагмента лежит за срезом gap, поэтому пробуем
        # gap + этот символ. Точку ПЕРЕД цифрой ("No. 1912") за границу не считаем.
        probe = gap + text[next_start:next_start + 1]
        if _SENTENCE_RE.search(probe):
            return False
        if _STOP_WORDS_RE.search(gap):
            return False  # разделитель перечисления ("... to ...")
        return len(gap) <= 45  # адреса длинные; зазор увеличен с 30

    merged: list[RawMatch] = []
    run: Optional[tuple[int, int, Optional[float]]] = None  # (start, end, score)

    def flush() -> None:
        if run is not None:
            start, end, score = run
            merged.append(RawMatch(type="ADDRESS", value=text[start:end], start=start, end=end, score=score))

    for m in addr:
        if run is not None and bridgeable(run[1], m.start):
            run = (run[0], max(run[1], m.end), _max_score(run[2], m.score))
        else:
            flush()
            run = (m.start, m.end, m.score)
    flush()

    return [*others, *merged]
