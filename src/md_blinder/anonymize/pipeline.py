"""Конвейер обезличивания (порт src/lib/anonymize/index.ts).

NER передается как функция: так конвейер не зависит от загрузки модели, а тесты
подставляют заглушку. Если функция не передана, работают только регулярные
выражения; если она бросает исключение (кроме отмены), результат помечается
ner_failed, и пользователь видит предупреждение.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from ..cancel import CancelToken, Cancelled, check
from .apply import build_anonymized_text
from .config import NER_CONFIRM_THRESHOLD, NER_GRAY_ZONE_LOW
from .ner_filter import filter_generic_ner_matches
from .normalize import normalize_value
from .regex_detectors import detect_structural
from .stitch_address import stitch_address_runs
from .types import AnonymizationResult, Entity, EntityType, Position, RawMatch

NerDetect = Callable[[str], list[RawMatch]]


def _overlaps(a: RawMatch, b: RawMatch) -> bool:
    return a.start < b.end and b.start < a.end


def _start_then_longer(m: RawMatch) -> tuple[int, int]:
    return (m.start, -(m.end - m.start))


def _is_confirmed_level(m: RawMatch) -> bool:
    # У регулярных выражений и ручных добавлений score нет: они всегда подтверждены.
    return not (m.score is not None and m.score < NER_CONFIRM_THRESHOLD)


def _max_defined_score(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def resolve_overlaps(structural: list[RawMatch], ner: list[RawMatch]) -> list[RawMatch]:
    """Структурные совпадения выигрывают у NER; внутри прохода — более ранний и длинный спан."""
    accepted: list[RawMatch] = []
    for m in sorted(structural, key=_start_then_longer):
        if not accepted or not _overlaps(m, accepted[-1]):
            accepted.append(m)
    accepted_ner: list[RawMatch] = []
    si = 0
    for m in sorted(ner, key=_start_then_longer):
        while si < len(accepted) and accepted[si].end <= m.start:
            si += 1
        if si < len(accepted) and _overlaps(m, accepted[si]):
            continue
        if not accepted_ner or not _overlaps(m, accepted_ner[-1]):
            accepted_ner.append(m)
    return sorted(accepted + accepted_ner, key=lambda m: m.start)


@dataclass
class _Group:
    type: EntityType
    first_value: str
    first_start: int
    positions: list[Position]
    confirmed: bool
    max_score: Optional[float]


def anonymize_markdown(
    markdown: str,
    ner_detect: Optional[NerDetect] = None,
    cancel: Optional[CancelToken] = None,
) -> AnonymizationResult:
    check(cancel)
    structural = detect_structural(markdown)
    check(cancel)

    ner: list[RawMatch] = []
    ner_failed = False
    ner_error: Optional[str] = None
    if ner_detect is not None:
        try:
            ner = ner_detect(markdown)
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 — любая ошибка NER не должна терять regex-результаты
            check(cancel)
            ner_failed = True
            ner_error = str(exc) or exc.__class__.__name__
            ner = []
    check(cancel)

    stitched = stitch_address_runs(filter_generic_ner_matches(ner), markdown)
    accepted = [
        m for m in resolve_overlaps(structural, stitched)
        if not (m.score is not None and m.score < NER_GRAY_ZONE_LOW)
    ]

    groups: dict[str, _Group] = {}
    for m in accepted:
        key = f"{m.type} {normalize_value(m.type, m.value)}"
        group = groups.get(key)
        if group is None:
            group = _Group(m.type, m.value, m.start, [], _is_confirmed_level(m), m.score)
            groups[key] = group
        group.confirmed = group.confirmed or _is_confirmed_level(m)
        group.max_score = _max_defined_score(group.max_score, m.score)
        group.positions.append(Position(m.start, m.end))

    # Вложенные адреса разной длины сливаются в длинный: NER на разных вхождениях
    # отдает «ROOM C05» и «ROOM C05, FLAT A, …». Кандидат и подтвержденный адрес
    # не сливаются, чтобы не менять уверенность молча.
    addr_groups = sorted(
        (g for g in groups.values() if g.type == "ADDRESS"), key=lambda g: -len(g.first_value)
    )
    absorbed: list[_Group] = []
    for i, long in enumerate(addr_groups):
        if any(long is a for a in absorbed):
            continue
        long_norm = normalize_value("ADDRESS", long.first_value)
        for short in addr_groups[i + 1:]:
            if any(short is a for a in absorbed):
                continue
            short_norm = normalize_value("ADDRESS", short.first_value)
            if short_norm in long_norm and long.confirmed == short.confirmed:
                for sp in short.positions:
                    if not any(sp.start < lp.end and lp.start < sp.end for lp in long.positions):
                        long.positions.append(sp)
                long.first_start = min(long.first_start, short.first_start)
                absorbed.append(short)
    for g in absorbed:
        groups.pop(f"{g.type} {normalize_value(g.type, g.first_value)}", None)

    # Подтвержденные нумеруются первыми, кандидаты следом по общему счетчику типа:
    # принятие кандидата не столкнется с существующим плейсхолдером.
    confirmed = sorted((g for g in groups.values() if g.confirmed), key=lambda g: g.first_start)
    candidates = sorted((g for g in groups.values() if not g.confirmed), key=lambda g: g.first_start)
    counters: dict[str, int] = {}

    def next_placeholder(type_: str) -> str:
        counters[type_] = counters.get(type_, 0) + 1
        return f"[{type_}_{counters[type_]}]"

    entities: list[Entity] = []
    for g in confirmed:
        entities.append(Entity(
            id=str(uuid.uuid4()), type=g.type, value=g.first_value, placeholder=next_placeholder(g.type),
            positions=sorted(g.positions, key=lambda p: p.start), anonymize=True, manual=False, score=g.max_score,
        ))
    for g in candidates:
        entities.append(Entity(
            id=str(uuid.uuid4()), type=g.type, value=g.first_value, placeholder=next_placeholder(g.type),
            positions=sorted(g.positions, key=lambda p: p.start), anonymize=False, manual=False,
            candidate=True, candidate_note="Низкая уверенность модели", score=g.max_score,
        ))

    return AnonymizationResult(
        markdown=build_anonymized_text(markdown, entities),
        entities=entities,
        ner_failed=ner_failed,
        ner_error=ner_error,
    )
