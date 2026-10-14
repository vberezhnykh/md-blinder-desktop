"""PDF → Markdown через PyMuPDF (порт src/lib/pdfToMarkdown.ts).

Разбор идет в два шага. analyze_pdf читает текстовый слой и решает, какие страницы
нужно распознавать через OCR. assemble_markdown собирает итоговый текст, когда
результаты OCR готовы. Между шагами интерфейс может предложить скачать языковые
файлы OCR.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import pymupdf

from ..cancel import CancelToken, check
from .pdf_classify import PageKind, PdfPageSignals, classify_pdf_pages
from .pdf_structure import PdfPageLayout, PdfTextItem, items_to_markdown

PdfProgress = Callable[[int, int], None]


@dataclass
class PdfAnalysis:
    pages: list[list[PdfTextItem]]
    layouts: list[Optional[PdfPageLayout]]
    kinds: list[PageKind]
    signals: list[Optional[PdfPageSignals]] = field(default_factory=list)

    @property
    def ocr_page_numbers(self) -> list[int]:
        return [i + 1 for i, kind in enumerate(self.kinds) if kind == "ocr"]


def _image_area_ratio(page: pymupdf.Page, area: float) -> float:
    image_area = 0.0
    page_rect = page.rect
    for info in page.get_image_info():
        rect = pymupdf.Rect(info["bbox"]) & page_rect
        if not rect.is_empty:
            image_area += abs(rect.width * rect.height)
    return min(1.0, image_area / max(1.0, area))


def _page_items(page: pymupdf.Page) -> tuple[list[PdfTextItem], PdfPageLayout, PdfPageSignals]:
    rect = page.rect
    height = rect.height
    area = height * rect.width
    items: list[PdfTextItem] = []
    signals = PdfPageSignals(0, 0, 0.0, 0.0)
    data = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES)
    for block in data["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"]]
            for index, span in enumerate(spans):
                text = span["text"]
                x0, _, x1, _ = span["bbox"]
                # pdf.js: y — базовая линия снизу страницы, больше y = выше.
                y = height - span["origin"][1]
                font_size = float(span["size"])
                width = x1 - x0
                count = len("".join(text.split()))
                relative_y = y / height if height else 0
                signals.nonWsCount += count
                if 0.12 < relative_y < 0.88:
                    signals.bodyNonWsCount += count
                if count:
                    signals.textAreaRatio += abs(width * font_size) / max(1.0, area)
                items.append(PdfTextItem(
                    str=text, x=x0, y=y, w=width, fontSize=font_size,
                    fontName=span.get("font", ""), hasEOL=index == len(spans) - 1,
                ))
    layout = PdfPageLayout(bottom=0, height=height, pageNumber=page.number + 1, totalPages=page.parent.page_count)
    # Плотному текстовому слою дорогая проверка картинок не нужна.
    if signals.nonWsCount >= 8 and (signals.bodyNonWsCount < 80 or signals.textAreaRatio < 0.01):
        signals.imageAreaRatio = _image_area_ratio(page, area)
    return items, layout, signals


def analyze_pdf(
    path: str | Path,
    force_ocr: bool = False,
    on_progress: Optional[PdfProgress] = None,
    cancel: Optional[CancelToken] = None,
) -> PdfAnalysis:
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("PDF защищен паролем")
        total = doc.page_count
        pages: list[list[PdfTextItem]] = []
        layouts: list[Optional[PdfPageLayout]] = []
        signals: list[Optional[PdfPageSignals]] = []
        for index in range(total):
            check(cancel)
            if force_ocr:
                pages.append([])
                layouts.append(None)
                signals.append(None)
                continue
            items, layout, page_signals = _page_items(doc[index])
            pages.append(items)
            layouts.append(layout)
            signals.append(page_signals)
            if on_progress:
                on_progress(index + 1, total)
    kinds: list[PageKind] = ["ocr"] * total if force_ocr else classify_pdf_pages([s for s in signals if s])
    return PdfAnalysis(pages=pages, layouts=layouts, kinds=kinds, signals=signals)


def assemble_markdown(analysis: PdfAnalysis, recognized: dict[int, str]) -> str:
    """Собирает текст: серии текстовых страниц размечаются вместе, OCR-страницы встают по порядку."""
    outputs: list[str] = []
    kinds = analysis.kinds
    idx = 0
    while idx < len(kinds):
        if kinds[idx] == "ocr":
            outputs.append(recognized.get(idx + 1, ""))
            idx += 1
            continue
        start = idx
        while idx < len(kinds) and kinds[idx] == "text":
            idx += 1
        layouts = [layout for layout in analysis.layouts[start:idx] if layout is not None]
        outputs.append(items_to_markdown(analysis.pages[start:idx], layouts))
    return "\n\n".join(outputs)
