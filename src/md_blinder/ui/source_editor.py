"""Редактор исходника с подсветкой сущностей (порт EntityEditor.tsx, MVP).

Внутри Qt позиции считаются в единицах UTF-16, в модели — в кодовых точках;
перевод идет через OffsetMap. Встроенная отмена редактора выключена: текст и
разметка отменяются общим стеком DocumentSession.
"""

from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont, QKeySequence, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..anonymize.apply import entity_current_value
from ..anonymize.types import ALL_ENTITY_TYPES, Entity
from ..editing import TextChange
from ..storage.offsets import OffsetMap
from .theme import entity_fill, entity_solid

_TABLE_LINE = re.compile(r"^\s*\|")


def _raw_text(editor: QPlainTextEdit) -> str:
    # toPlainText заменяет неразрывные пробелы обычными, а это изменило бы документ.
    return editor.document().toRawText().replace(" ", "\n")


class _TableHighlighter(QSyntaxHighlighter):
    """Строки Markdown-таблиц набираются моноширинным шрифтом."""

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        if _TABLE_LINE.match(text):
            fmt = QTextCharFormat()
            fmt.setFontFamilies(["Consolas", "Courier New", "monospace"])
            self.setFormat(0, len(text), fmt)


class AddEntityPopup(QFrame):
    """Попап над выделением: тип и «новая замена» или «добавить к существующей»."""

    add_requested = Signal(int, int, str)  # start, end (кодовые точки), тип
    attach_requested = Signal(int, int, str)  # start, end, id сущности

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        self.value_label = QLabel()
        self.value_label.setWordWrap(True)
        self.value_label.setMaximumWidth(360)
        layout.addWidget(self.value_label)
        row = QHBoxLayout()
        self.type_box = QComboBox()
        self.type_box.addItems(list(ALL_ENTITY_TYPES))
        self.target_box = QComboBox()
        self.add_button = QPushButton("Добавить")
        self.add_button.setObjectName("primary")
        row.addWidget(QLabel("Тип"))
        row.addWidget(self.type_box)
        row.addWidget(self.target_box, 1)
        row.addWidget(self.add_button)
        layout.addLayout(row)
        self._range = (0, 0)
        self._entities: list[Entity] = []
        self._text = ""
        self.type_box.currentTextChanged.connect(self._fill_targets)
        self.add_button.clicked.connect(self._submit)

    def open_for(self, start: int, end: int, text: str, entities: list[Entity], type_: str, at: QPoint) -> None:
        self._range = (start, end)
        self._entities = entities
        self._text = text
        value = text[start:end]
        self.value_label.setText(f"«{value if len(value) <= 80 else value[:79] + '…'}»")
        self.type_box.blockSignals(True)
        self.type_box.setCurrentText(type_)
        self.type_box.blockSignals(False)
        self._fill_targets(type_)
        self.adjustSize()
        self.move(at)
        self.show()
        self.add_button.setFocus()

    def _fill_targets(self, type_: str) -> None:
        self.target_box.clear()
        self.target_box.addItem("Новая замена", "")
        for e in self._entities:
            if e.type == type_ and not e.candidate and not e.dismissed:
                value = entity_current_value(self._text, e)
                short = value if len(value) <= 24 else value[:23] + "…"
                self.target_box.addItem(f"Добавить к {e.placeholder} · {short}", e.id)

    def _submit(self) -> None:
        start, end = self._range
        target = self.target_box.currentData()
        self.hide()
        if target:
            self.attach_requested.emit(start, end, target)
        else:
            self.add_requested.emit(start, end, self.type_box.currentText())


class SourceEditor(QPlainTextEdit):
    edited = Signal(str)  # новый полный текст после правки пользователя
    entity_clicked = Signal(str)
    add_requested = Signal(int, int, str)
    attach_requested = Signal(int, int, str)
    undo_requested = Signal()
    redo_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() + 1)
        self.setFont(font)
        self._highlighter = _TableHighlighter(self.document())
        self._entities: list[Entity] = []
        self._offsets = OffsetMap("")
        self._text = ""
        self._current_candidate: Optional[str] = None
        self.selected_type = "PERSON"
        self.popup = AddEntityPopup(self)
        self.popup.add_requested.connect(self.add_requested)
        self.popup.attach_requested.connect(self.attach_requested)
        self.popup.type_box.currentTextChanged.connect(self._remember_type)
        self.textChanged.connect(self._on_text_changed)
        self._suppress = False

    # --- загрузка и внешние правки ---

    def set_content(self, text: str, entities: list[Entity]) -> None:
        self._suppress = True
        try:
            self.setPlainText(text)
        finally:
            self._suppress = False
        self._text = text
        self._offsets = OffsetMap(text)
        self.set_entities(entities)

    def apply_change(self, change: TextChange) -> None:
        """Применяет правку из undo/redo без повторной записи в стек."""
        cursor = QTextCursor(self.document())
        cursor.setPosition(self._offsets.to_utf16(change.start))
        cursor.setPosition(self._offsets.to_utf16(change.end), QTextCursor.MoveMode.KeepAnchor)
        self._suppress = True
        try:
            cursor.insertText(change.inserted)
        finally:
            self._suppress = False
        self._text = self._text[:change.start] + change.inserted + self._text[change.end:]
        self._offsets = OffsetMap(self._text)
        self.setTextCursor(cursor)

    def _on_text_changed(self) -> None:
        if self._suppress:
            return
        text = _raw_text(self)
        if text == self._text:
            return
        self._text = text
        self._offsets = OffsetMap(text)
        self.edited.emit(text)

    # --- подсветка ---

    def set_entities(self, entities: list[Entity], current_candidate: Optional[str] = None) -> None:
        self._entities = entities
        self._current_candidate = current_candidate
        self._render_highlights()

    def set_current_candidate(self, entity_id: Optional[str]) -> None:
        self._current_candidate = entity_id
        self._render_highlights()

    def _render_highlights(self) -> None:
        selections = []
        doc = self.document()
        for e in self._entities:
            if e.dismissed:
                continue
            fmt = QTextCharFormat()
            if e.candidate:
                strong = e.id == self._current_candidate
                fmt.setBackground(entity_fill(e.type, 1.6 if strong else 0.5))
                fmt.setUnderlineStyle(
                    QTextCharFormat.UnderlineStyle.SingleUnderline if strong else QTextCharFormat.UnderlineStyle.DashUnderline
                )
                fmt.setUnderlineColor(entity_solid(e.type))
                fmt.setToolTip(f"Кандидат {e.placeholder}: {e.candidate_note or 'не проверен'}")
            elif e.anonymize:
                fmt.setBackground(entity_fill(e.type))
                fmt.setToolTip(f"{e.placeholder} · {e.type}")
            else:
                fmt.setBackground(entity_fill(e.type, 0.35))
                fmt.setFontStrikeOut(False)
                fmt.setToolTip(f"Замена выключена: {e.placeholder}")
            for p in e.positions:
                cursor = QTextCursor(doc)
                cursor.setPosition(self._offsets.to_utf16(p.start))
                cursor.setPosition(self._offsets.to_utf16(p.end), QTextCursor.MoveMode.KeepAnchor)
                sel = QTextEdit.ExtraSelection()
                sel.cursor = cursor
                sel.format = fmt
                selections.append(sel)
        self.setExtraSelections(selections)

    # --- навигация ---

    def reveal(self, start: int, end: int) -> None:
        cursor = QTextCursor(self.document())
        cursor.setPosition(self._offsets.to_utf16(start))
        cursor.setPosition(self._offsets.to_utf16(end), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)
        self.centerCursor()

    def entity_at(self, pos: int) -> Optional[Entity]:
        for e in self._entities:
            if e.dismissed:
                continue
            if any(p.start <= pos < p.end for p in e.positions):
                return e
        return None

    # --- клавиатура и мышь ---

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Встроенная отмена выключена; Ctrl+Z/Ctrl+Y ведут в общий стек сессии.
        if event.matches(QKeySequence.StandardKey.Undo):
            self.undo_requested.emit()
            return
        if event.matches(QKeySequence.StandardKey.Redo):
            self.redo_requested.emit()
            return
        super().keyPressEvent(event)

    def _remember_type(self, type_: str) -> None:
        self.selected_type = type_

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        cursor = self.textCursor()
        if cursor.hasSelection():
            start = self._offsets.from_utf16(cursor.selectionStart())
            end = self._offsets.from_utf16(cursor.selectionEnd())
            # Края выделения без пробелов: так же поступает браузерная версия.
            while start < end and self._text[start].isspace():
                start += 1
            while end > start and self._text[end - 1].isspace():
                end -= 1
            if end > start:
                rect = self.cursorRect(cursor)
                at = self.viewport().mapToGlobal(rect.bottomLeft() + QPoint(0, 6))
                self.popup.open_for(start, end, self._text, self._entities, self.selected_type, at)
            return
        entity = self.entity_at(self._offsets.from_utf16(cursor.position()))
        if entity is not None:
            self.entity_clicked.emit(entity.id)
