"""Поиск сущностей моделью GLiNER (порт src/workers/ner.worker.ts и
src/lib/anonymize/ner.ts).

В браузере модель жила в Web Worker'е; здесь вызывающий держит один GlinerModel
и запускает detect_entities_ner в фоновом потоке. Отмена — CancelToken,
проверяется между батчами (один session.run не прерывается).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

from ..anonymize.config import NER_GRAY_ZONE_LOW
from ..anonymize.strip_formatting import remap_matches, strip_inline_formatting
from ..anonymize.types import EntityType, RawMatch
from ..cancel import CancelToken, check
from ..components import model_asset
from .batching import NER_BATCH_SIZE, count_words, expand_batch_result, plan_batches
from .calibration import CALIBRATION_LABELS, CALIBRATION_TEXT, CALIBRATION_THRESHOLD, CalibrationError, assert_calibration
from .chunk_text import chunk_text
from .runtime import GRAPH_OPTIMIZATION_FALLBACKS, GlinerModel, NerRuntimeError, OptimizationConfig

log = logging.getLogger(__name__)

NER_LABELS: tuple[str, ...] = ("person", "company", "address", "registration number")

# Фазы в порядке прохождения: у каждой свой маркер, чтобы после «100%» загрузки
# интерфейс не молчал десятки секунд, пока создается сессия и идет прогрев.
NerStage = Literal["download", "init", "warmup", "detect"]
EP_NAME = "cpu"


@dataclass(frozen=True)
class NerProgress:
    """Одна форма прогресса на все фазы (порт nerProgress.ts); поля чужих фаз пусты."""

    stage: NerStage
    pct: Optional[int] = None  # download: 0..100
    cached: Optional[bool] = None  # download: модель уже лежала на диске
    ep: Optional[str] = None  # init/warmup/detect: на чем считаем
    chunk: Optional[int] = None  # detect: сколько фрагментов посчитано
    chunks: Optional[int] = None  # detect: всего фрагментов
    batch: Optional[int] = None  # detect: номер текущей партии
    batches: Optional[int] = None  # detect: всего партий
    batch_chunks: Optional[int] = None  # detect: сколько фрагментов закроет текущая партия
    elapsed_ms: Optional[int] = None  # мс с начала обработки запроса


ProgressCallback = Callable[[NerProgress], None]


class NerModelError(RuntimeError):
    """Модель не загрузилась или не прошла калибровку ни на одном варианте сессии."""


def map_label(label: str) -> Optional[EntityType]:
    return {
        "person": "PERSON",
        "company": "COMPANY",
        "address": "ADDRESS",
        "registration number": "REG_NUMBER",
    }.get(label.lower())


def _fmt_ms(ms: float) -> str:
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{round(ms)}ms"


def _emit(on_progress: Optional[ProgressCallback], progress: NerProgress) -> None:
    if on_progress:
        on_progress(progress)


def load_model(
    model_dir: Path | str,
    on_progress: Optional[ProgressCallback] = None,
    cancel: Optional[CancelToken] = None,
    configs: tuple[OptimizationConfig, ...] = GRAPH_OPTIMIZATION_FALLBACKS,
) -> GlinerModel:
    """Создает сессию и прогоняет калибровку RU/UA/EN. Не прошедший вариант
    освобождается, пробуется следующий; если не прошел ни один — NerModelError."""
    errors: list[str] = []
    for config in configs:
        check(cancel)
        # Создание сессии — самая «тихая» фаза: ORT разбирает и оптимизирует граф на
        # 580 МБ, это секунды без единого события.
        _emit(on_progress, NerProgress(stage="init", ep=EP_NAME))
        t1 = time.perf_counter()
        try:
            model = GlinerModel(model_dir, optimization=config)
        except NerRuntimeError as error:
            log.warning("[ner] сессия (%s) не создана: %s", config, error)
            errors.append(str(error))
            continue
        log.info("[ner] сессия ONNX (%s) создана за %s", config, _fmt_ms((time.perf_counter() - t1) * 1000))

        _emit(on_progress, NerProgress(stage="warmup", ep=EP_NAME))
        t2 = time.perf_counter()
        try:
            check(cancel)
            calibration = model.infer([CALIBRATION_TEXT], CALIBRATION_LABELS, CALIBRATION_THRESHOLD)[0]
            assert_calibration(calibration)
        except (CalibrationError, NerRuntimeError, ValueError) as error:
            model.release()
            log.warning("[ner] вариант %s не прошел калибровку: %s", config, error)
            errors.append(f"{config}: {error}")
            continue
        except BaseException:
            model.release()
            raise
        log.info("[ner] прогрев (%s) за %s", config, _fmt_ms((time.perf_counter() - t2) * 1000))
        return model
    raise NerModelError("Модель NER не прошла проверку качества RU/UA/EN: " + "; ".join(errors))


def ensure_model(
    on_progress: Optional[ProgressCallback] = None,
    cancel: Optional[CancelToken] = None,
    directory: Optional[Path] = None,
) -> GlinerModel:
    """Докачивает недостающие файлы модели (фаза download) и загружает ее."""
    directory = directory or model_asset.model_dir()
    if model_asset.is_model_present(directory):
        _emit(on_progress, NerProgress(stage="download", pct=100, cached=True))
    else:
        last_pct = [-1]

        def report(received: int, total: int) -> None:
            pct = min(99, round(received / total * 100)) if total else 0
            if pct != last_pct[0]:
                last_pct[0] = pct
                _emit(on_progress, NerProgress(stage="download", pct=pct, cached=False))

        t0 = time.perf_counter()
        model_asset.download_model(report, cancel, directory)
        log.info("[ner] модель скачана за %s", _fmt_ms((time.perf_counter() - t0) * 1000))
        _emit(on_progress, NerProgress(stage="download", pct=100, cached=False))
    return load_model(directory, on_progress, cancel)


def detect_in_plain_text(
    text: str,
    model: GlinerModel,
    labels: tuple[str, ...] | list[str] = NER_LABELS,
    threshold: float = NER_GRAY_ZONE_LOW,
    on_progress: Optional[ProgressCallback] = None,
    cancel: Optional[CancelToken] = None,
) -> list[RawMatch]:
    """Тело обработчика ner.worker.ts: фрагменты → батчи → инференс → матчи."""
    req_start = time.perf_counter()
    chunks = chunk_text(text)
    matches: list[RawMatch] = []
    # Фрагменты идут в session.run небольшими партиями (NER_BATCH_SIZE).
    # Одинаковые фрагменты (типовые фразы) считаются один раз, уникальные
    # сортируются по числу слов, чтобы короткие не добивались паддингом до
    # длинного. Смещения — от base каждого вхождения, дедуп позиции не сливает.
    plan = plan_batches(chunks, NER_BATCH_SIZE)

    def elapsed_ms() -> int:
        return round((time.perf_counter() - req_start) * 1000)

    processed = 0
    for b, batch_texts in enumerate(plan.batches):
        check(cancel)
        batch_chunks = sum(len(plan.occurrences.get(t, [])) for t in batch_texts)
        batch_words = sum(count_words(t) for t in batch_texts)
        # Прогресс отдается ДО инференса, иначе на коротком документе (1 батч)
        # индикатор стоит на нуле все время счета и выглядит как зависание.
        progress = dict(stage="detect", ep=EP_NAME, chunks=len(chunks), batch=b + 1,
                        batches=len(plan.batches), batch_chunks=batch_chunks)
        _emit(on_progress, NerProgress(chunk=min(processed, len(chunks)), elapsed_ms=elapsed_ms(), **progress))

        b_start = time.perf_counter()
        result = model.infer(batch_texts, list(labels), threshold)
        batch_ms = (time.perf_counter() - b_start) * 1000
        matches.extend(expand_batch_result(batch_texts, result, plan.occurrences, map_label))
        processed += batch_chunks
        log.info(
            "[ner] батч %d/%d: %d уник. текстов (%d чанк(ов), %d слов) за %s — %d мс/слово",
            b + 1, len(plan.batches), len(batch_texts), batch_chunks, batch_words, _fmt_ms(batch_ms),
            round(batch_ms / max(batch_words, 1)),
        )
        _emit(on_progress, NerProgress(chunk=min(processed, len(chunks)), elapsed_ms=elapsed_ms(), **progress))

    log.info(
        "[ner] итого chunks=%d (уник. %d, батчей %d) за %s",
        len(chunks), len(plan.occurrences), len(plan.batches), _fmt_ms(elapsed_ms()),
    )
    return matches


def detect_entities_ner(
    md: str,
    model: GlinerModel,
    on_progress: Optional[ProgressCallback] = None,
    cancel: Optional[CancelToken] = None,
) -> list[RawMatch]:
    """NER по Markdown: снимает инлайн-разметку и картинки, ищет сущности в чистом
    тексте и переводит смещения обратно в исходник."""
    check(cancel)
    stripped = strip_inline_formatting(md)
    if not stripped.plain.strip():
        return []
    matches = detect_in_plain_text(stripped.plain, model, NER_LABELS, NER_GRAY_ZONE_LOW, on_progress, cancel)
    check(cancel)
    return remap_matches(matches, stripped.plain, stripped.map, stripped.excluded)
