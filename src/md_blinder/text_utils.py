"""Чистая логика для интерфейса: контекст кандидата, привязка к границе слова,
русские формы множественного числа (порт src/lib/candidateContext.ts, wordSnap.ts,
plural.ts)."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

# Пробельные символы JS (\s и trim): в Python \s и str.strip шире или уже.
JS_WHITESPACE = "\t\n\v\f\r                  　﻿"
_JS_WS_RUN_RE = re.compile("[" + re.escape(JS_WHITESPACE) + "]+")


@dataclass(frozen=True)
class CandidateContext:
    before: str
    match: str
    after: str
    # Обрезан ли текст слева или справа: тогда в начале или конце ставится «…».
    cut_before: bool
    cut_after: bool


def _squash(s: str) -> str:
    """Пробелы и переводы строк сводятся к одному пробелу: в карточке проверки
    фрагмент показывается одной строкой."""
    return _JS_WS_RUN_RE.sub(" ", s)


def candidate_context(text: str, start: int, end: int, radius: int = 60) -> CandidateContext:
    """Фрагмент текста вокруг вхождения кандидата: около radius символов с каждой
    стороны, обрезанный по пробелу, чтобы не рвать слова."""
    from_ = max(0, start - radius)
    if from_ > 0:
        space = text.find(" ", from_)
        if space != -1 and space < start:
            from_ = space + 1
    to = min(len(text), end + radius)
    if to < len(text):
        space = text.rfind(" ", 0, to + 1)
        if space > end:
            to = space
    return CandidateContext(
        before=_squash(text[from_:start]).lstrip(JS_WHITESPACE),
        match=text[start:end],
        after=_squash(text[end:to]).rstrip(JS_WHITESPACE),
        cut_before=from_ > 0,
        cut_after=to < len(text),
    )


def snap_to_word_boundary(text: str, pos: int) -> int:
    """Привязывает смещение к ближайшей границе СЛОВА: месту, где пробел встречается
    с непробелом (плюс оба конца документа). При равенстве — меньший индекс."""
    length = len(text)
    clamped = max(0, min(pos, length))

    boundaries = [0, length]
    for i in range(1, length):
        if (text[i - 1] in JS_WHITESPACE) != (text[i] in JS_WHITESPACE):
            boundaries.append(i)
    boundaries.sort()

    best = boundaries[0]
    best_dist = abs(best - clamped)
    for b in boundaries:
        dist = abs(b - clamped)
        # При равном расстоянии остается уже найденная (меньшая) граница.
        if dist < best_dist:
            best = b
            best_dist = dist
    return best


def _ru_plural_category(n: float) -> str:
    """CLDR-правила для ru (как Intl.PluralRules('ru-RU').select)."""
    if isinstance(n, float):
        if not math.isfinite(n) or not n.is_integer():
            return "other"  # дробные (v != 0), NaN, бесконечность
        n = int(n)
    i = abs(n)
    mod10 = i % 10
    mod100 = i % 100
    if mod10 == 1 and mod100 != 11:
        return "one"
    if 2 <= mod10 <= 4 and not 12 <= mod100 <= 14:
        return "few"
    return "many"


def _js_number(n: float) -> str:
    """Число в строке так, как его печатает JS для целых и обычных дробных."""
    if isinstance(n, float):
        if math.isnan(n):
            return "NaN"
        if math.isinf(n):
            return "Infinity" if n > 0 else "-Infinity"
        if n.is_integer():
            return str(int(n))
    return str(n)


def plural(n: float, forms: Sequence[str]) -> str:
    """forms: (одна, две, пять) — «находка», «находки», «находок»."""
    rule = _ru_plural_category(n)
    form = forms[0] if rule == "one" else forms[1] if rule == "few" else forms[2]
    return f"{_js_number(n)} {form}"
