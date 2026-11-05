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

import regex

from ..cancel import CancelToken, Cancelled, check
from .apply import build_anonymized_text
from .config import NER_CONFIRM_THRESHOLD, NER_GRAY_ZONE_LOW
from .cross_script import TextIndex, find_counterpart, same_value
from .image_safety import mask_images_for_detection
from .ner_filter import filter_generic_ner_matches
from .normalize import normalize_value
from .regex_detectors import detect_structural
from .stitch_address import close_company_quotes, stitch_address_runs, stitch_person_runs
from .types import AnonymizationResult, Entity, EntityType, Position, RawMatch

NerDetect = Callable[[str], list[RawMatch]]


def _overlaps(a: RawMatch, b: RawMatch) -> bool:
    return a.start < b.end and b.start < a.end


def _start_then_longer(m: RawMatch) -> tuple[int, int]:
    return (m.start, -(m.end - m.start))


def _is_number_as_address(m: RawMatch) -> bool:
    """Адрес без букв из 7 и более цифр — номер, который модель приняла за адрес
    («TIN: 9909747220», БИК). Индексы (5–6 цифр) остаются: модель отдает их
    отдельным куском, и он нужен, чтобы адрес с тем же индексом закрылся целиком.
    Проверка идет после склейки, когда индекс уже приклеен к остальному адресу."""
    if m.type != "ADDRESS" or any(ch.isalpha() for ch in m.value):
        return False
    return sum(ch.isdigit() for ch in m.value) >= 7


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


def _absorb(target: "_Group", other: "_Group") -> None:
    for sp in other.positions:
        if not any(sp.start < p.end and p.start < sp.end for p in target.positions):
            target.positions.append(sp)
    target.first_start = min(target.first_start, other.first_start)
    target.max_score = _max_defined_score(target.max_score, other.max_score)


def _link_cross_script(groups: dict[str, "_Group"], markdown: str) -> None:
    """Имя и адрес латиницей и их перевод кириллицей в двуязычном договоре — одно значение
    с одной меткой. Неуверенный перевод уверенной находки становится ее вхождением. Затем
    ищутся переводы, которые модель не нашла вовсе (cross_script.find_counterpart)."""
    linkable = sorted((g for g in groups.values() if g.type in ("PERSON", "ADDRESS")),
                      key=lambda g: (not g.confirmed, g.first_start))
    merged: list[_Group] = []
    for i, group in enumerate(linkable):
        if any(group is m for m in merged):
            continue
        for other in linkable[i + 1:]:
            if other.type != group.type or any(other is m for m in merged):
                continue
            if (group.confirmed or not other.confirmed) and same_value(group.first_value, other.first_value, group.type):
                _absorb(group, other)
                merged.append(other)
    for key in [k for k, g in groups.items() if any(g is m for m in merged)]:
        del groups[key]

    # Неуверенные находки внутри найденного перевода («№ 12») поглощаются им.
    index: Optional[TextIndex] = None
    taken = [(p.start, p.end) for g in groups.values() if g.confirmed for p in g.positions]
    for group in sorted(groups.values(), key=lambda g: g.first_start):
        if not group.confirmed or group.type not in ("PERSON", "ADDRESS"):
            continue
        if index is None:
            index = TextIndex(mask_images_for_detection(markdown))
        for start, end in find_counterpart(index, group.first_value, group.type, taken):
            group.positions.append(Position(start, end))
            taken.append((start, end))
            for other in groups.values():
                if not other.confirmed:
                    other.positions = [p for p in other.positions if not (p.start < end and start < p.end)]
    for key in [k for k, g in groups.items() if not g.positions]:
        del groups[key]


# Короткий адрес без цифр сливается с длинным, только если сам по себе похож на адрес:
# город («Львів», «Lviv») — не часть адреса стороны, даже если встречается в нем.
MIN_NESTED_ADDRESS_LENGTH = 12


def _address_inside(long: str, short: str) -> bool:
    """short входит в long целыми словами: «львів» не входит в «львівська обл.»."""
    if len(short) < MIN_NESTED_ADDRESS_LENGTH and not any(ch.isdigit() for ch in short):
        return False
    pattern = rf"(?<![\p{{L}}\p{{N}}]){regex.escape(short)}(?![\p{{L}}\p{{N}}])"
    return regex.search(pattern, long) is not None


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

    stitched = stitch_person_runs(stitch_address_runs(filter_generic_ner_matches(ner), markdown), markdown)
    stitched = close_company_quotes(stitched, markdown)
    stitched = [m for m in stitched if not _is_number_as_address(m)]
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
            if _address_inside(long_norm, short_norm) and long.confirmed == short.confirmed:
                for sp in short.positions:
                    if not any(sp.start < lp.end and lp.start < sp.end for lp in long.positions):
                        long.positions.append(sp)
                long.first_start = min(long.first_start, short.first_start)
                absorbed.append(short)
    for g in absorbed:
        groups.pop(f"{g.type} {normalize_value(g.type, g.first_value)}", None)
    _link_cross_script(groups, markdown)

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
