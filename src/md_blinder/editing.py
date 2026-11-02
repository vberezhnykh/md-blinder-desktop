"""Правка текста с сохранением разметки (порт src/lib/entityEditorState.ts).

Позиции сущностей сдвигаются так же, как mapPos в CodeMirror: начало с assoc=+1,
конец с assoc=-1. Поэтому текст, набранный вплотную к краю сущности, в нее не
попадает, а полностью удаленное вхождение исчезает.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .anonymize.types import Entity, Position
from .text_utils import JS_WHITESPACE, snap_to_word_edge


@dataclass(frozen=True)
class TextChange:
    start: int
    removed: str
    inserted: str

    @property
    def end(self) -> int:
        return self.start + len(self.removed)


def diff_texts(old: str, new: str) -> TextChange | None:
    """Одна непрерывная замена, превращающая old в new (общий префикс и суффикс)."""
    if old == new:
        return None
    limit = min(len(old), len(new))
    # Сравнение срезов идет в C и на больших документах быстрее посимвольного цикла.
    lo, hi = 0, limit
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if old[:mid] == new[:mid]:
            lo = mid
        else:
            hi = mid - 1
    prefix = lo
    lo, hi = 0, limit - prefix
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if old[len(old) - mid:] == new[len(new) - mid:]:
            lo = mid
        else:
            hi = mid - 1
    suffix = lo
    return TextChange(prefix, old[prefix:len(old) - suffix], new[prefix:len(new) - suffix])


def map_pos(pos: int, change: TextChange, assoc: int) -> int:
    start, removed, inserted = change.start, len(change.removed), len(change.inserted)
    end = start + removed
    if pos < start:
        return pos
    if end > pos:
        return start if pos == start or assoc < 0 else start + inserted
    if end == pos and assoc < 0 and removed == 0:
        return start
    return pos - removed + inserted


def map_entities(entities: list[Entity], change: TextChange) -> list[Entity]:
    result: list[Entity] = []
    for entity in entities:
        positions = [Position(map_pos(p.start, change, 1), map_pos(p.end, change, -1)) for p in entity.positions]
        positions = [p for p in positions if p.end > p.start]
        if positions:
            result.append(replace(entity, positions=positions))
    return result


def apply_change(text: str, change: TextChange) -> str:
    return text[:change.start] + change.inserted + text[change.end:]


def drag_span(
    entities: list[Entity],
    entity_id: str,
    anchor: Position,
    current: Position,
    side: str,
    pos: int,
    text: str,
) -> Position | None:
    """Новые границы вхождения при перетаскивании ручки (порт dragHandlers из EntityEditor.tsx).

    anchor — вхождение на момент начала перетаскивания (противоположный край
    неподвижен), current — его текущее положение. Левый край привязывается к началу
    слова, правый к концу и не заезжает на соседние видимые вхождения: иначе
    получилось бы пересечение, которое пришлось бы потом склеивать. Отклоненные
    находки в тексте не видны, поэтому край их не замечает (их вхождения под новой
    границей убирает resize_occurrence). None — двигать некуда.
    """
    snapped = snap_to_word_edge(text, pos, side)
    if side == "left":
        start, end = max(0, min(snapped, anchor.end - 1)), anchor.end
    else:
        start, end = anchor.start, min(len(text), max(snapped, anchor.start + 1))
    candidate = (start, end)
    for entity in entities:
        if entity.dismissed:
            continue
        for p in entity.positions:
            if entity.id == entity_id and p.start == current.start and p.end == current.end:
                continue
            if not (p.start < candidate[1] and candidate[0] < p.end):
                continue
            if side == "left":
                start = max(start, p.end)
            else:
                end = min(end, p.start)
    if start >= end:
        return None
    return Position(start, end)


def replace_position(entities: list[Entity], entity_id: str, old: Position, new: Position) -> list[Entity]:
    return [
        replace(e, positions=[new if p == old else p for p in e.positions]) if e.id == entity_id else e
        for e in entities
    ]


# Что не считается частью значения на краях вхождения: пробелы и знаки препинания,
# которые модель захватывает вместе с адресом («, P.O. Box 3159, Road Town,»).
_EDGE_JUNK = JS_WHITESPACE + ",;:"


def _core(text: str, p: Position) -> tuple[int, int]:
    start, end = p.start, p.end
    while start < end and text[start] in _EDGE_JUNK:
        start += 1
    while end > start and text[end - 1] in _EDGE_JUNK:
        end -= 1
    return start, end


def resize_occurrence(
    entities: list[Entity], text: str, entity_id: str, old: Position, new: Position,
) -> list[Entity]:
    """Новые границы вхождения после перетаскивания ручки.

    Остальные вхождения той же замены с тем же значением меняются так же, если
    текст вокруг них позволяет: расширили адрес до «Quijano Chambers, P.O. Box
    3159, Road Town» в одном месте, и он расширился во всех, где перед ним стоит
    «Quijano Chambers, ». Вхождение не трогается, если новая граница заехала бы на
    другую видимую находку. Знаки препинания и пробелы по краям при сравнении не
    учитываются. Отклоненные находки под новыми границами убираются: в тексте их
    не видно, и они не должны пересекаться с действующими.
    """
    target = next((e for e in entities if e.id == entity_id), None)
    if target is None or old not in target.positions:
        return entities
    old_start, old_end = _core(text, old)
    new_start, new_end = _core(text, new)
    old_value, new_value = text[old_start:old_end], text[new_start:new_end]
    moved: dict[Position, Position] = {old: new}
    if old_value and new_value and old_value != new_value:
        occupied = [
            (q.start, q.end) for e in entities if not e.dismissed for q in e.positions
            if not (e.id == entity_id and q == old)
        ]
        occupied.append((new.start, new.end))
        for p in target.positions:
            if p in moved:
                continue
            start, end = _core(text, p)
            if text[start:end] != old_value:
                continue
            shifted = Position(start + new_start - old_start, end + new_end - old_end)
            if shifted.start < 0 or shifted.end > len(text) or text[shifted.start:shifted.end] != new_value:
                continue
            others = [o for o in occupied if o != (p.start, p.end)]
            if any(a < shifted.end and shifted.start < b for a, b in others):
                continue
            occupied = [*others, (shifted.start, shifted.end)]
            moved[p] = shifted
    spans = list(moved.values())
    result: list[Entity] = []
    for e in entities:
        if e.id == entity_id:
            positions = sorted((moved.get(p, p) for p in e.positions), key=lambda p: p.start)
            result.append(replace(e, positions=positions))
        elif e.dismissed:
            kept = [p for p in e.positions if not any(s.start < p.end and p.start < s.end for s in spans)]
            if len(kept) == len(e.positions):
                result.append(e)
            elif kept:
                result.append(replace(e, positions=kept))
        else:
            result.append(e)
    return result


def active_span_at(entities: list[Entity], pos: int) -> tuple[str, Position] | None:
    """Вхождение под курсором: сначала строго внутренние, затем граничные; самое узкое."""
    best_inner = best_boundary = None
    for e in entities:
        for p in e.positions:
            width = p.end - p.start
            if p.start < pos < p.end:
                if best_inner is None or width < best_inner[1].end - best_inner[1].start:
                    best_inner = (e.id, p)
            elif pos in (p.start, p.end):
                if best_boundary is None or width < best_boundary[1].end - best_boundary[1].start:
                    best_boundary = (e.id, p)
    return best_inner or best_boundary


def hovered_entity_at(entities: list[Entity], pos: int) -> str | None:
    """Сущность под указателем: при вложенных — самая узкая (та, что видна сверху)."""
    best: tuple[str, int] | None = None
    for e in entities:
        if e.dismissed:
            continue
        for p in e.positions:
            if p.start <= pos <= p.end and (best is None or p.end - p.start < best[1]):
                best = (e.id, p.end - p.start)
    return best[0] if best else None
