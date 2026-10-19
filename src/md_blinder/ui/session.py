"""Открытый документ и общий стек отмены для текста и разметки.

Порт useDocumentSession.ts и связки CodeMirror history + entityMetadataField:
каждая команда хранит снимок сущностей до и после, поэтому отмена удаления
текста возвращает и текст, и разметку. Действия панели (принять, объединить,
удалить) идут в тот же стек отдельными командами.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from ..anonymize.types import Entity
from ..editing import TextChange, apply_change, diff_texts, map_entities
from ..storage.history import HistoryDoc, now_ms

_TYPING_ID = 1001


class _EntitiesCommand(QUndoCommand):
    def __init__(self, session: "DocumentSession", before: list[Entity], after: list[Entity], label: str) -> None:
        super().__init__(label)
        self._session = session
        self._before = before
        self._after = after

    def redo(self) -> None:
        self._session._set_entities(self._after)

    def undo(self) -> None:
        self._session._set_entities(self._before)


class _TextCommand(QUndoCommand):
    def __init__(
        self, session: "DocumentSession", change: TextChange, before: list[Entity], after: list[Entity]
    ) -> None:
        super().__init__("Правка текста")
        self._session = session
        self.change = change
        self._before = before
        self._after = after
        self._first = True  # правка уже есть в редакторе

    def id(self) -> int:  # noqa: A003 — имя задано Qt
        return _TYPING_ID

    def redo(self) -> None:
        if self._first:
            self._first = False
            return
        self._session._apply_text(self.change, self._after)

    def undo(self) -> None:
        inverse = TextChange(self.change.start, self.change.inserted, self.change.removed)
        self._session._apply_text(inverse, self._before)

    def mergeWith(self, other: QUndoCommand) -> bool:  # noqa: N802
        if not isinstance(other, _TextCommand):
            return False
        a, b = self.change, other.change
        if "\n" in a.inserted + b.inserted + a.removed + b.removed:
            return False
        merged: Optional[TextChange] = None
        if not a.removed and not b.removed and b.start == a.start + len(a.inserted) and len(b.inserted) == 1:
            merged = TextChange(a.start, "", a.inserted + b.inserted)
        elif not a.inserted and not b.inserted and len(b.removed) == 1:
            if b.start + len(b.removed) == a.start:  # Backspace
                merged = TextChange(b.start, b.removed + a.removed, "")
            elif b.start == a.start:  # Delete
                merged = TextChange(a.start, a.removed + b.removed, "")
        if merged is None:
            return False
        self.change = merged
        self._after = other._after
        return True


class DocumentSession(QObject):
    """Источник правды об открытом документе.

    Сигналы:
    - doc_replaced: открыт другой документ (или закрыт);
    - text_changed(TextChange): текст изменился не из редактора (undo/redo), редактор должен применить правку;
    - entities_changed: изменилась разметка.
    """

    doc_replaced = Signal()
    text_changed = Signal(object)
    entities_changed = Signal()

    def __init__(self, on_dirty: Optional[Callable[[HistoryDoc], None]] = None) -> None:
        super().__init__()
        self.doc: Optional[HistoryDoc] = None
        self.revision = 0
        self.undo_stack = QUndoStack(self)
        self._on_dirty = on_dirty
        # Вызывается для каждой правки разметки или текста, например чтобы
        # согласовать метки с остальными документами пакета.
        self.prepare: Optional[Callable[[list[Entity], str], list[Entity]]] = None

    # --- чтение ---

    @property
    def text(self) -> str:
        return self.doc.source_text if self.doc else ""

    @property
    def entities(self) -> list[Entity]:
        return self.doc.entities if self.doc else []

    # --- замена документа ---

    def replace_doc(self, doc: Optional[HistoryDoc], dirty: bool = False) -> None:
        self.doc = doc
        self.revision += 1
        self.undo_stack.clear()
        self.doc_replaced.emit()
        if dirty and doc is not None:
            self._mark_dirty()

    # --- изменения, попадающие в стек отмены ---

    def update_entities(self, fn: Callable[[list[Entity]], list[Entity]], label: str = "Изменение разметки") -> None:
        if self.doc is None:
            return
        before = list(self.doc.entities)
        after = self._prepared(fn(before), self.doc.source_text)
        if after == before:
            return
        self.undo_stack.push(_EntitiesCommand(self, before, list(after), label))

    def editor_changed(self, new_text: str) -> None:
        """Редактор уже показывает new_text; фиксируем правку и сдвигаем разметку."""
        if self.doc is None:
            return
        change = diff_texts(self.doc.source_text, new_text)
        if change is None:
            return
        before = list(self.doc.entities)
        after = self._prepared(map_entities(before, change), new_text)
        self.doc.source_text = new_text
        self.doc.entities = after
        self._touch()
        self.undo_stack.push(_TextCommand(self, change, before, after))
        self.entities_changed.emit()

    def _prepared(self, entities: list[Entity], text: str) -> list[Entity]:
        return self.prepare(entities, text) if self.prepare else entities

    # --- внутреннее: вызывается командами ---

    def _set_entities(self, entities: list[Entity]) -> None:
        if self.doc is None:
            return
        self.doc.entities = entities
        self._touch()
        self.entities_changed.emit()

    def _apply_text(self, change: TextChange, entities: list[Entity]) -> None:
        if self.doc is None:
            return
        self.doc.source_text = apply_change(self.doc.source_text, change)
        self.doc.entities = entities
        self._touch()
        self.text_changed.emit(change)
        self.entities_changed.emit()

    def _touch(self) -> None:
        if self.doc is None:
            return
        self.doc.updated_at = now_ms()
        self.revision += 1
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        if self._on_dirty and self.doc is not None:
            # Сохраняется снимок: дальнейшие правки не меняют уже поставленный в очередь документ.
            self._on_dirty(replace(self.doc, entities=list(self.doc.entities)))
