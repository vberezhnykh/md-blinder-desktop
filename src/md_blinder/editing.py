"""Правка текста с сохранением разметки (порт src/lib/entityEditorState.ts).

Позиции сущностей сдвигаются так же, как mapPos в CodeMirror: начало с assoc=+1,
конец с assoc=-1. Поэтому текст, набранный вплотную к краю сущности, в нее не
попадает, а полностью удаленное вхождение исчезает.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .anonymize.types import Entity, Position


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
