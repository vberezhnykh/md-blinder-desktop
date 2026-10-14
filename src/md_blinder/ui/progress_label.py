"""Тексты индикатора обработки (порт src/lib/progressLabel.ts).

Пока модель разворачивается, процентов нет, поэтому каждый этап назван, а рядом
тикает время: иначе долгий шаг выглядит как зависание.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

Phase = Literal[
    "pdf", "ocr", "ocr-download", "regex", "ner-download", "ner-init", "ner-warmup", "ner-detect", "related"
]


@dataclass
class UiProgress:
    phase: Phase
    page: Optional[int] = None
    total: Optional[int] = None
    pct: Optional[int] = None
    chunk: Optional[int] = None
    chunks: Optional[int] = None
    batch: Optional[int] = None
    batches: Optional[int] = None


def format_elapsed(sec: float) -> str:
    s = max(0, int(sec))
    return f"{s // 60}:{s % 60:02d}"


def _base_label(p: UiProgress) -> str:
    if p.phase == "pdf":
        return f"Извлечение текста из PDF… (страница {p.page}/{p.total})"
    if p.phase == "ocr":
        return f"Распознавание скана (OCR)… страница {p.page}/{p.total}"
    if p.phase == "ocr-download":
        return f"Скачивание языковых файлов OCR… {p.pct or 0}%"
    if p.phase == "regex":
        return "Поиск телефонов, email и реквизитов…"
    if p.phase == "ner-download":
        return f"Скачивание модели распознавания… {p.pct or 0}% (~580 МБ, только при первом запуске)"
    if p.phase == "ner-init":
        return "Подготовка модели распознавания…"
    if p.phase == "ner-warmup":
        return "Пробный запуск модели…"
    if p.phase == "ner-detect":
        chunks = p.chunks or 0
        done = p.chunk or 0
        batch_part = f", партия {p.batch}/{p.batches}" if p.batches and p.batches > 1 else ""
        return f"Поиск чувствительных данных… фрагмент {min(done + 1, chunks)}/{chunks}{batch_part}"
    if p.phase == "related":
        return "Поиск похожих упоминаний…"
    return "Обработка файла…"


def status_label(p: Optional[UiProgress], elapsed_sec: float = 0) -> str:
    base = _base_label(p) if p else "Обработка файла…"
    return f"{base} · {format_elapsed(elapsed_sec)}" if elapsed_sec > 0 else base


def status_hint(p: Optional[UiProgress]) -> Optional[str]:
    if p is None:
        return None
    if p.phase == "ocr":
        return "OCR медленный — распознавание может занять несколько минут."
    if p.phase == "ner-download":
        return "Модель скачивается один раз и сохраняется на этом компьютере."
    if p.phase in ("ner-init", "ner-warmup"):
        return "Модель разворачивается на CPU, обычно 10–40 секунд. Процентов на этом шаге нет, это нормально."
    if p.phase == "ner-detect":
        return "Расчет идет на CPU и занимает секунды на каждый фрагмент."
    return None
