"""Планирование NER-батчей (порт src/workers/nerBatching.ts).

Чистая логика: дедуп идентичных фрагментов и сортировка по длине перед нарезкой
на батчи. Модель не нужна — тестируется отдельно.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Sequence

from ..anonymize.types import EntityType, RawMatch
from .chunk_text import Chunk
from .gliner_span import WORD_RE

# Замер на настоящей модели (scripts/bench_ner.py, CPU, 4 ядра): батч 2 быстрее
# батча 8 на 9–22% на прозе и не медленнее на таблицах, найденное не меняется.
# Батч 1 на CPU еще быстрее, но с fp16 сдвигает оценку отдельных спанов.
NER_BATCH_SIZE = 2


class NerItem(Protocol):
    label: str
    start: int
    end: int
    score: float


def count_words(text: str) -> int:
    """Считает «слова» тем же регулярным выражением, что словоделитель модели, —
    сортировка отражает реальную нагрузку (~O(слов²)), а не длину в символах."""
    return sum(1 for _ in WORD_RE.finditer(text))


@dataclass
class PlanResult:
    batches: list[list[str]]
    occurrences: dict[str, list[Chunk]]


def plan_batches(chunks: Sequence[Chunk], batch_size: int) -> PlanResult:
    """Группирует фрагменты по ТОЧНОМУ тексту (без trim/нормализации — это другой
    вход модели): повторяющиеся фрагменты договора проходят инференс один раз.
    Уникальные тексты сортируются по числу слов, затем по длине строки (сортировка
    стабильна — результат детерминирован), чтобы в батче было меньше паддинга."""
    occurrences: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        occurrences.setdefault(chunk.text, []).append(chunk)
    unique_texts = sorted(occurrences, key=lambda text: (count_words(text), len(text)))
    batches = [unique_texts[i : i + batch_size] for i in range(0, len(unique_texts), batch_size)]
    return PlanResult(batches, occurrences)


def expand_batch_result(
    batch_texts: Sequence[str],
    batch_result: Sequence[Sequence[NerItem]],
    occurrences: dict[str, list[Chunk]],
    map_label: Callable[[str], Optional[EntityType]],
) -> list[RawMatch]:
    """Разворачивает результат по уникальному тексту на ВСЕ его вхождения: смещения
    считаются от base каждого вхождения, а не представителя."""
    matches: list[RawMatch] = []
    for j, text in enumerate(batch_texts):
        items = batch_result[j] if j < len(batch_result) else []
        for occ in occurrences.get(text, []):
            for item in items:
                entity_type = map_label(item.label)
                if not entity_type:
                    continue
                matches.append(RawMatch(
                    type=entity_type,
                    value=occ.text[item.start : item.end],
                    start=occ.base + item.start,
                    end=occ.base + item.end,
                    score=item.score,
                ))
    return matches
