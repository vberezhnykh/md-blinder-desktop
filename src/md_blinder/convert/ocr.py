"""OCR страниц PDF встроенным в MuPDF движком Tesseract (замена tesseract.js).

Нужны только языковые файлы *.traineddata (см. components/tessdata.py), отдельно
ставить Tesseract не требуется. Страница рендерится в 144 DPI, как в браузерной
версии (scale 2). Первым идет rus: Tesseract считает первый язык основным; ukr
нужен для і/ї/є/ґ, которых нет в rus.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

import pymupdf

from ..cancel import CancelToken, check
from .pdf_structure import strip_repeated_lines_and_page_numbers

OCR_LANGS = ("rus", "ukr", "eng")
OCR_DPI = 144


@dataclass
class OcrProgress:
    page: int  # порядковый номер распознаваемой страницы, с 1
    total: int


def ocr_pdf_pages(
    path: str | Path,
    page_numbers: Sequence[int],
    tessdata_dir: str | Path,
    on_progress: Optional[Callable[[OcrProgress], None]] = None,
    cancel: Optional[CancelToken] = None,
) -> dict[int, str]:
    numbers = sorted(set(page_numbers))
    if not numbers:
        return {}
    language = "+".join(OCR_LANGS)
    texts: list[str] = []
    with pymupdf.open(path) as doc:
        for idx, number in enumerate(numbers):
            check(cancel)
            if on_progress:
                on_progress(OcrProgress(idx + 1, len(numbers)))
            page = doc[number - 1]
            textpage = page.get_textpage_ocr(language=language, dpi=OCR_DPI, full=True, tessdata=str(tessdata_dir))
            texts.append(page.get_text(textpage=textpage).strip())
    check(cancel)
    pages_lines = [[line.strip() for line in text.split("\n") if line.strip()] for text in texts]
    cleaned = strip_repeated_lines_and_page_numbers(pages_lines)
    return {number: "\n".join(lines) for number, lines in zip(numbers, cleaned)}
