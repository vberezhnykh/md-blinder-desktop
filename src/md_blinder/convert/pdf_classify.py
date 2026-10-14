"""Порт src/lib/pdfPageClassify.ts: какие страницы PDF отправлять в OCR."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence, Union


@dataclass
class PdfPageSignals:
    nonWsCount: int
    bodyNonWsCount: int
    textAreaRatio: float
    imageAreaRatio: float


PageKind = Literal["text", "ocr"]


def classify_pdf_pages(pages: Sequence[Union[int, PdfPageSignals]], threshold: int = 8) -> list[PageKind]:
    # Текстовый колонтитул не делает читаемым скан под ним: большая площадь
    # растра при редком тексте в теле страницы отправляет страницу в OCR.
    result: list[PageKind] = []
    for page in pages:
        if isinstance(page, int):
            result.append("ocr" if page < threshold else "text")
        elif page.nonWsCount < threshold:
            result.append("ocr")
        elif page.imageAreaRatio >= 0.35 and (page.bodyNonWsCount < 80 or page.textAreaRatio < 0.01):
            result.append("ocr")
        else:
            result.append("text")
    return result
