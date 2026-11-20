"""Порт src/lib/pdfPageClassify.ts: какие страницы PDF отправлять в OCR."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence, Union

import regex


@dataclass
class PdfPageSignals:
    nonWsCount: int
    bodyNonWsCount: int
    textAreaRatio: float
    imageAreaRatio: float
    # Доля слов, непохожих на слова: «ne1poB», «eneKTPOHHOtO», «.Qorosopy».
    garbledRatio: float = 0.0
    wordCount: int = 0


PageKind = Literal["text", "ocr"]


_WORD_RE = regex.compile(r"[\p{L}\d'’\".,:(){}\\]+")
_NORMAL_PART_RE = regex.compile(r"\p{Lu}?\p{Ll}+|\p{Lu}+s?|(?:\p{Lu}\.)+\p{Lu}?|\d+")
_EDGE_CHARS = ".,;:()'’\"{}\\"
# Порог доли испорченных слов. На корпусе у нормальных страниц не больше 0,05
# («N.D.A.M», «BioNutrients»), у слоя чужого OCR, прочитавшего кириллицу
# латиницей, 0,4 даже при половине английского текста на странице.
GARBLED_RATIO = 0.15
GARBLED_MIN_WORDS = 30


def garbled_words(text: str) -> tuple[int, int]:
    """(слов, испорченных слов) текстового слоя.

    Сканы бывают с готовым невидимым слоем чужого OCR. Если тот прочитал
    кириллицу латиницей, вместо «Іван Петров» в слое «IBaH ne1poB»:
    регистр скачет внутри слова, внутри букв стоят цифры и знаки. Такой слой
    нельзя брать, страницу надо распознать заново.
    """
    words = garbled = 0
    for match in _WORD_RE.finditer(text):
        core = match.group().strip(_EDGE_CHARS)
        if sum(ch.isalpha() for ch in core) < 3:
            continue
        words += 1
        parts = [p for p in regex.split(r"['’]", core) if p]
        if not all(_NORMAL_PART_RE.fullmatch(p) for p in parts):
            garbled += 1
    return words, garbled


def classify_pdf_pages(pages: Sequence[Union[int, PdfPageSignals]], threshold: int = 8) -> list[PageKind]:
    # Текстовый колонтитул не делает читаемым скан под ним: большая площадь
    # растра при редком тексте в теле страницы отправляет страницу в OCR.
    result: list[PageKind] = []
    for page in pages:
        if isinstance(page, int):
            result.append("ocr" if page < threshold else "text")
        elif page.nonWsCount < threshold:
            result.append("ocr")
        elif page.wordCount >= GARBLED_MIN_WORDS and page.garbledRatio >= GARBLED_RATIO:
            result.append("ocr")
        elif page.imageAreaRatio >= 0.35 and (page.bodyNonWsCount < 80 or page.textAreaRatio < 0.01):
            result.append("ocr")
        else:
            result.append("text")
    return result
