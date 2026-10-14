"""Правка списка сущностей и сборка обезличенного текста (порт src/lib/anonymize/apply.ts).

Сущности неизменяемы: каждая функция возвращает новый список (или тот же объект,
если ничего не поменялось) и не трогает переданные объекты.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, replace
from functools import reduce
from typing import NamedTuple, Optional

from .image_safety import IMAGE_REMOVED_MARKER, find_image_ranges
from .placeholder_guard import escape_placeholder_literal, find_placeholder_ranges
from .types import Entity, EntityType, Position

_log = logging.getLogger(__name__)

# JS: /_(\d+)\]$/ — \d только ASCII, $ без флага m только конец строки.
_PLACEHOLDER_NUM_RE = re.compile(r"_([0-9]+)\]\Z")


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class AnonSegment:
    out_start: int  # смещение в обезличенном тексте (включительно)
    out_end: int  # не включая
    src_start: int  # смещение в исходном тексте
    src_end: int  # не включая
    opaque: bool  # True: вставленный плейсхолдер или экранированный литерал


class AnonymizedWithMap(NamedTuple):
    text: str
    segments: list[AnonSegment]


def build_anonymized_text(source_text: str, entities: list[Entity]) -> str:
    """Заменяет каждое вхождение АКТИВНОЙ сущности плейсхолдером.

    Пересекающиеся замены объединяются, чтобы нарушенный инвариант в импортированных
    данных не открыл непокрытую часть сущности.
    """
    return build_anonymized_text_with_map(source_text, entities).text


def build_anonymized_text_with_map(source_text: str, entities: list[Entity]) -> AnonymizedWithMap:
    """Как build_anonymized_text, но дополнительно возвращает карту сегментов
    исходник → результат. Порядок разрешения пересечений тот же, поэтому текст
    совпадает с build_anonymized_text."""
    repl: list[list] = []  # [start, end, placeholder]
    for image in find_image_ranges(source_text):
        repl.append([image.start, image.end, IMAGE_REMOVED_MARKER])
    for e in entities:
        if not e.anonymize:
            continue
        for p in e.positions:
            repl.append([p.start, p.end, e.placeholder])

    # B4: экранируем плейсхолдер-подобные литералы из исходника, чтобы в выводе
    # они не путались с настоящими плейсхолдерами. Пропускаем те, что перекрывают
    # активную сущность (маловероятно, но исключаем двойную замену).
    for rng in find_placeholder_ranges(source_text):
        overlaps_entity = any(
            e.anonymize and any(rng.start < p.end and p.start < rng.end for p in e.positions)
            for e in entities
        )
        if overlaps_entity:
            continue
        repl.append([rng.start, rng.end, escape_placeholder_literal(source_text[rng.start:rng.end])])

    # Объединяем пересечения; картинка главнее любой сущности или литерала внутри нее.
    repl.sort(key=lambda r: (r[0], -r[1]))
    accepted: list[list] = []
    merged_overlap = False
    for r in repl:
        prev = accepted[-1] if accepted else None
        if prev is not None and r[0] < prev[1]:
            prev[1] = max(prev[1], r[1])
            if r[2] == IMAGE_REMOVED_MARKER:
                prev[2] = IMAGE_REMOVED_MARKER
            merged_overlap = True
        else:
            accepted.append(list(r))
    if merged_overlap:
        _log.warning("build_anonymized_text: merged overlapping redactions to preserve coverage")

    parts: list[str] = []
    out_len = 0
    src = 0
    segments: list[AnonSegment] = []
    for start, end, placeholder in accepted:
        if start > src:
            segments.append(AnonSegment(out_len, out_len + (start - src), src, start, False))
            parts.append(source_text[src:start])
            out_len += start - src
        segments.append(AnonSegment(out_len, out_len + len(placeholder), start, end, True))
        parts.append(placeholder)
        out_len += len(placeholder)
        src = end
    if src < len(source_text):
        segments.append(AnonSegment(out_len, out_len + (len(source_text) - src), src, len(source_text), False))
        parts.append(source_text[src:])
    return AnonymizedWithMap("".join(parts), segments)


def entity_current_value(source_text: str, e: Entity) -> str:
    """Текущее исходное значение сущности: срез ее первого вхождения в документе."""
    if e.positions:
        p = e.positions[0]
        return source_text[p.start:p.end]
    return e.value


def _parse_placeholder_number(placeholder: str) -> Optional[int]:
    m = _PLACEHOLDER_NUM_RE.search(placeholder)
    return int(m.group(1)) if m else None


def next_placeholder_number(entities: list[Entity], type_: EntityType) -> int:
    """Следующий свободный номер для типа (разбирает плейсхолдеры [TYPE_n])."""
    max_n = 0
    for e in entities:
        if e.type != type_:
            continue
        n = _parse_placeholder_number(e.placeholder)
        if n is not None:
            max_n = max(max_n, n)
    return max_n + 1


def add_entity_from_selection(
    entities: list[Entity],
    source_text: str,
    from_: int,
    to: int,
    type_: EntityType,
) -> list[Entity]:
    """Создает ручную сущность на [from_, to) типа type_.

    «Поглощение»: вхождения, пересекающие выделение, удаляются из своих сущностей
    (сущности без вхождений исчезают). Поглощение происходит ТОЛЬКО на самом
    выделении; точные повторы в других местах добавляются, лишь если ни с чем не
    пересекаются, чтобы короткое выделение не выпотрошило чужие сущности.
    """
    if to <= from_:
        return entities
    selected = source_text[from_:to]
    if not selected:
        return entities

    working: list[Entity] = []
    for e in entities:
        positions = [p for p in e.positions if not (from_ < p.end and p.start < to)]
        if positions:
            working.append(replace(e, positions=positions))

    positions: list[Position] = [Position(from_, to)]
    search_from = 0
    while True:
        found = source_text.find(selected, search_from)
        if found == -1:
            break
        found_end = found + len(selected)
        search_from = found_end
        if found == from_:
            continue
        if found < to and from_ < found_end:
            continue
        overlaps_working = any(
            found < p.end and p.start < found_end for e in working for p in e.positions
        )
        if overlaps_working:
            continue
        positions.append(Position(found, found_end))
    positions.sort(key=lambda p: p.start)

    placeholder = f"[{type_}_{next_placeholder_number(working, type_)}]"
    new_entity = Entity(
        id=_new_id(),
        type=type_,
        value=selected,
        placeholder=placeholder,
        positions=positions,
        anonymize=True,
        manual=True,
    )
    return [*working, new_entity]


def add_occurrence_to_entity(
    entities: list[Entity],
    _source_text: str,
    from_: int,
    to: int,
    entity_id: str,
) -> list[Entity]:
    """Присоединяет вхождение [from_, to) к СУЩЕСТВУЮЩЕЙ сущности (общий плейсхолдер).

    Сначала диапазон расширяется на все связанные пересекающиеся вхождения, включая
    активных соседей, чтобы уже скрытый текст не стал видимым.
    """
    if to <= from_:
        return entities
    target = next((e for e in entities if e.id == entity_id), None)
    if target is None:
        return entities

    start = from_
    end = to
    anonymize = target.anonymize
    # Связные компоненты объединяются за один проход; повторное расширение выделения
    # по цепочке пересечений потребовало бы квадратичного числа проходов.
    spans = sorted(
        ([p.start, p.end, e.anonymize] for e in entities for p in e.positions),
        key=lambda s: s[0],
    )
    components: list[list] = []
    for span in spans:
        previous = components[-1] if components else None
        if previous is not None and span[0] < previous[1]:
            previous[1] = max(previous[1], span[1])
            previous[2] = previous[2] or span[2]
        else:
            components.append(list(span))
    for s_start, s_end, s_active in components:
        if s_start >= end:
            break
        if start < s_end:
            start = min(start, s_start)
            end = max(end, s_end)
            anonymize = anonymize or s_active

    result: list[Entity] = []
    for e in entities:
        positions = [p for p in e.positions if not (start < p.end and p.start < end)]
        if e.id != entity_id:
            updated = e if len(positions) == len(e.positions) else replace(e, positions=positions)
        else:
            positions.append(Position(start, end))
            positions.sort(key=lambda p: p.start)
            if anonymize:
                updated = replace(e, positions=positions, anonymize=anonymize, candidate=False, dismissed=False)
            else:
                updated = replace(e, positions=positions, anonymize=anonymize)
        if updated.positions:
            result.append(updated)
    return result


def change_entity_type(entities: list[Entity], id_: str, new_type: EntityType) -> list[Entity]:
    """Меняет тип сущности и пересчитывает плейсхолдер против ОСТАЛЬНЫХ сущностей.
    id стабилен."""
    if not any(e.id == id_ for e in entities):
        return entities
    others = [e for e in entities if e.id != id_]
    placeholder = f"[{new_type}_{next_placeholder_number(others, new_type)}]"
    return [replace(e, type=new_type, placeholder=placeholder) if e.id == id_ else e for e in entities]


def accept_candidate(entities: list[Entity], id_: str) -> list[Entity]:
    """Принять кандидата «серой зоны»: снять флаг candidate, включить анонимизацию и
    пересчитать плейсхолдер против ПОДТВЕРЖДЕННЫХ сущностей того же типа, чтобы
    нумерация подтвержденных оставалась плотной. id стабилен."""
    target = next((e for e in entities if e.id == id_), None)
    if target is None or not target.candidate:
        return entities
    others = [e for e in entities if e.id != id_ and not e.candidate]
    placeholder = f"[{target.type}_{next_placeholder_number(others, target.type)}]"
    return [
        replace(e, candidate=False, candidate_note=None, anonymize=True, placeholder=placeholder)
        if e.id == id_
        else e
        for e in entities
    ]


def pending_candidates(entities: list[Entity]) -> list[Entity]:
    """Непроверенные кандидаты в порядке появления в тексте: очередь проверки."""

    def first(e: Entity) -> float:
        return min(p.start for p in e.positions) if e.positions else float("inf")

    return sorted((e for e in entities if e.candidate and not e.dismissed), key=first)


def _related_target(entities: list[Entity], candidate: Entity) -> Optional[Entity]:
    """Сущность, на которую похож кандидат, если она еще есть и того же типа."""
    if not candidate.related_to:
        return None
    target = next((e for e in entities if e.id == candidate.related_to), None)
    if target is None or target.candidate or target.dismissed or target.type != candidate.type:
        return None
    return target


def candidate_reason(entities: list[Entity], candidate: Entity) -> Optional[str]:
    """Причина, по которой фрагмент попал в кандидаты.

    Для родственного вхождения метка берется у живой исходной сущности, поэтому не
    устаревает после перенумерации; старые документы без related_to показывают
    сохраненную строку.
    """
    if candidate.related_to:
        target = next((e for e in entities if e.id == candidate.related_to), None)
        if target is not None and not target.candidate and not target.dismissed:
            return f"Похоже на {target.placeholder}"
        return "Похоже на удаленную замену"
    return candidate.candidate_note


def accept_related_candidate(entities: list[Entity], id_: str) -> list[Entity]:
    """Принять кандидата.

    Родственное вхождение («Иванов П. С.» рядом с [PERSON_1] «Иванов Петр Сергеевич»)
    сливается с исходной сущностью и получает ее метку. Если исходной сущности нет,
    тип кандидата сменили или замена исходной выключена, кандидат принимается как
    отдельная сущность с новой меткой: включать чужую выключенную замену молча нельзя.
    Номера меток после слияния выравнивает compact_placeholder_numbers.
    """
    candidate = next((e for e in entities if e.id == id_), None)
    if candidate is None or not candidate.candidate:
        return entities
    target = _related_target(entities, candidate)
    if target is None or not target.anonymize:
        return accept_candidate(entities, id_)
    unflagged = [
        replace(e, candidate=False, candidate_note=None, anonymize=True) if e.id == id_ else e
        for e in entities
    ]
    return merge_entities(unflagged, id_, target.id)


def accept_all_related(entities: list[Entity], target_id: str) -> list[Entity]:
    """Принять всех непроверенных кандидатов, похожих на одну сущность."""
    ids = [e.id for e in entities if e.candidate and not e.dismissed and e.related_to == target_id]
    return reduce(accept_related_candidate, ids, entities)


def restore_candidate(entities: list[Entity], id_: str) -> list[Entity]:
    """Вернуть отклоненного кандидата в список непроверенных."""
    target = next((e for e in entities if e.id == id_), None)
    if target is None or not target.dismissed:
        return entities
    return [
        replace(e, dismissed=False, candidate=True, anonymize=False) if e.id == id_ else e
        for e in entities
    ]


def merge_entities(entities: list[Entity], source_id: str, target_id: str) -> list[Entity]:
    """Вливает вхождения сущности source в target (тип, значение и плейсхолдер — от target).

    Пересекающиеся диапазоны объединяются (не отбрасываются: иначе часть исходного
    текста осталась бы открытой), соприкасающиеся остаются раздельными. anonymize —
    логическое ИЛИ обеих. Ничего не делает при равных id, отсутствии любой из
    сущностей или разных типах.
    """
    if source_id == target_id:
        return entities
    source = next((e for e in entities if e.id == source_id), None)
    target = next((e for e in entities if e.id == target_id), None)
    if source is None or target is None:
        return entities
    if source.type != target.type:
        return entities

    merged = sorted([*target.positions, *source.positions], key=lambda p: (p.start, p.end))
    positions: list[Position] = []
    cur: Optional[Position] = None
    for nxt in merged:
        if cur is None:
            cur = nxt
        elif nxt.start < cur.end:
            cur = Position(cur.start, max(cur.end, nxt.end))
        else:
            positions.append(cur)
            cur = nxt
    if cur is not None:
        positions.append(cur)

    # Кандидаты, похожие на исчезающую сущность, теперь похожи на ту, в которую она влилась.
    result: list[Entity] = []
    for e in entities:
        if e.id == source_id:
            continue
        if e.id == target_id:
            result.append(replace(e, positions=positions, anonymize=target.anonymize or source.anonymize))
        elif e.related_to == source_id:
            result.append(replace(e, related_to=target_id))
        else:
            result.append(e)
    return result


def compact_placeholder_numbers(entities: list[Entity]) -> list[Entity]:
    """Схлопывает дыры в нумерации плейсхолдеров (PERSON_1, PERSON_4 → PERSON_1, PERSON_2).

    Перенумерация идет потипно и раздельно по двум группам: сначала подтвержденные
    (не кандидат и не отклонены), затем кандидаты и отклоненные, чтобы номер
    кандидата всегда был больше номеров подтвержденных того же типа. Внутри группы
    порядок по текущему номеру сохраняется; сущности без номера уходят в конец своей
    группы. Порядок элементов списка не меняется. Возвращает тот же список (is),
    если ни один плейсхолдер не поменялся.
    """

    def sort_by_number(group: list[Entity]) -> list[Entity]:
        # Стабильная сортировка по номеру; сущности без номера — в конец.
        keyed = [(e, idx, _parse_placeholder_number(e.placeholder)) for idx, e in enumerate(group)]
        keyed.sort(key=lambda x: (x[2] is None, x[2] if x[2] is not None else 0, x[1]))
        return [x[0] for x in keyed]

    new_placeholder: dict[str, str] = {}
    types = list(dict.fromkeys(e.type for e in entities))
    for type_ in types:
        of_type = [e for e in entities if e.type == type_]
        main = [e for e in of_type if not e.candidate and not e.dismissed]
        rest = [e for e in of_type if e.candidate or e.dismissed]
        n = 1
        for e in sort_by_number(main):
            new_placeholder[e.id] = f"[{type_}_{n}]"
            n += 1
        for e in sort_by_number(rest):
            new_placeholder[e.id] = f"[{type_}_{n}]"
            n += 1

    changed = False
    result: list[Entity] = []
    for e in entities:
        nxt = new_placeholder[e.id]
        if nxt == e.placeholder:
            result.append(e)
        else:
            changed = True
            result.append(replace(e, placeholder=nxt))
    return result if changed else entities


def dedupe_entity_ids(entities: list[Entity]) -> list[Entity]:
    """Защита от старых сохраненных документов с повторяющимися id: первое вхождение
    id сохраняется, каждому следующему дубликату выдается новый UUID."""
    seen: set[str] = set()
    result: list[Entity] = []
    for e in entities:
        if e.id in seen:
            result.append(replace(e, id=_new_id()))
        else:
            seen.add(e.id)
            result.append(e)
    return result
