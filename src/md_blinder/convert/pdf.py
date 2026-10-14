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
from .pdf_tables import find_page_tables, table_owner

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
    signals = PdfPageSignals(0, 0, 0.0, 0.0)
    data = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES)
    # Фрагменты в порядке текстового слоя: (span, последний ли в строке).
    spans: list[tuple[dict, bool]] = []
    for block in data["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            line_spans = [s for s in line["spans"] if s["text"]]
            spans.extend((span, index == len(line_spans) - 1) for index, span in enumerate(line_spans))

    for span, _ in spans:
        count = len("".join(span["text"].split()))
        relative_y = (height - span["origin"][1]) / height if height else 0
        signals.nonWsCount += count
        if 0.12 < relative_y < 0.88:
            signals.bodyNonWsCount += count
        if count:
            x0, _, x1, _ = span["bbox"]
            signals.textAreaRatio += abs((x1 - x0) * float(span["size"])) / max(1.0, area)

    tables = find_page_tables(page, [(s["text"], pymupdf.Rect(s["bbox"])) for s, _ in spans]) if spans else []
    emitted: set[int] = set()
    items: list[PdfTextItem] = []
    for span, eol in spans:
        owner = table_owner(pymupdf.Rect(span["bbox"]), tables)
        if owner is not None:
            # Таблица встает в поток там, где встретился ее первый фрагмент.
            if owner not in emitted:
                emitted.add(owner)
                table = tables[owner]
                items.append(PdfTextItem(
                    str="", x=table.rect.x0, y=height - table.rect.y0, w=table.rect.width, fontSize=0,
                    hasEOL=True, table_html=table.html, table_cols=table.cols,
                ))
            continue
        x0, _, x1, _ = span["bbox"]
        items.append(PdfTextItem(
            # pdf.js: y — базовая линия снизу страницы, больше y = выше.
            str=span["text"], x=x0, y=height - span["origin"][1], w=x1 - x0, fontSize=float(span["size"]),
            fontName=span.get("font", ""), hasEOL=eol,
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
