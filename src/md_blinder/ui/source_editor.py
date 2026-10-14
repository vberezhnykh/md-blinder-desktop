"""Редактор исходника (порт EntityEditor.tsx).

Позиции внутри Qt считаются в единицах UTF-16, в модели — в кодовых точках;
перевод идет через OffsetMap. Встроенная отмена выключена: текст и разметка
отменяются общим стеком DocumentSession.

Оформление как у CodeMirror в веб-версии: шрифт IBM Plex Serif, межстрочный
интервал 1.7, номера строк, подсветка текущей строки. Рамки (текущий кандидат,
выбранное вхождение, наведение из списка) и ручки границ рисуются поверх текста
в paintEvent.
"""

from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QPainter,
    QPen,
    QSyntaxHighlighter,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..anonymize.apply import entity_current_value
from ..anonymize.types import ALL_ENTITY_TYPES, Entity, Position
from ..editing import TextChange, active_span_at, drag_span, hovered_entity_at, replace_position
from ..storage.offsets import OffsetMap
from . import theme as t
from .widgets import Select

_TABLE_LINE = re.compile(r"^\s*\|")
LINE_HEIGHT = 130  # процентов от высоты строки шрифта: так выходит line-height 1.7 из веб-версии
HANDLE_RADIUS = 5


def _raw_text(editor: QTextEdit) -> str:
    # toPlainText заменяет неразрывные пробелы обычными, а это изменило бы документ.
    return editor.document().toRawText().replace(" ", "\n")


def _truncate(value: str, n: int) -> str:
    return value if len(value) <= n else value[:n] + "…"


class _TableHighlighter(QSyntaxHighlighter):
    """Строки Markdown-таблиц — моноширинным шрифтом 12.5px, чтобы столбцы стояли ровно."""

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        if _TABLE_LINE.match(text):
            fmt = QTextCharFormat()
            fmt.setFont(t.font(t.MONO, 12.5), QTextCharFormat.FontPropertiesInheritanceBehavior.FontPropertiesSpecifiedOnly)
            self.setFormat(0, len(text), fmt)


class _LineNumbers(QWidget):
    def __init__(self, editor: "SourceEditor") -> None:
        super().__init__(editor)
        self.editor = editor
        self.setFont(t.font(t.MONO, 12))

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: N802
        self.editor.paint_gutter(self, event)


class AddEntityPopup(QFrame):
    """Попап над выделением (.cm-add-popup): тип, «новая замена» или существующая, «Добавить»."""

    add_requested = Signal(int, int, str)
    attach_requested = Signal(int, int, str)
    type_chosen = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("popup")
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 5, 6, 5)
        row.setSpacing(6)
        self.type_box = Select()
        self.type_box.setObjectName("compact")
        self.type_box.addItems(list(ALL_ENTITY_TYPES))
        self.target_box = Select()
        self.target_box.setObjectName("compact")
        self.target_box.setMinimumContentsLength(13)
        self.target_box.setSizeAdjustPolicy(Select.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.add_button = QPushButton("Добавить")
        self.add_button.setObjectName("primary")
        self.add_button.setStyleSheet("font-size: 12px; padding: 2px 8px;")
        row.addWidget(self.type_box)
        row.addWidget(self.target_box)
        row.addWidget(self.add_button)
        self._range = (0, 0)
        self._entities: list[Entity] = []
        self._text = ""
        self.type_box.currentTextChanged.connect(self._type_changed)
        self.target_box.currentIndexChanged.connect(self._target_changed)
        self.add_button.clicked.connect(self._submit)

    def open_for(self, start: int, end: int, text: str, entities: list[Entity], type_: str, at: QPoint) -> None:
        self._range = (start, end)
        self._entities = entities
        self._text = text
        self.type_box.blockSignals(True)
        self.type_box.setCurrentText(type_)
        self.type_box.blockSignals(False)
        self._fill_targets(type_)
        self.adjustSize()
        self.move(at)
        self.show()
        self.add_button.setFocus()

    def _type_changed(self, type_: str) -> None:
        self.type_chosen.emit(type_)
        self._fill_targets(type_)

    def _fill_targets(self, type_: str) -> None:
        self.target_box.blockSignals(True)
        self.target_box.clear()
        self.target_box.addItem("новая замена", "")
        for e in self._entities:
            if e.type == type_ and not e.candidate:
                self.target_box.addItem(f"{e.placeholder} · {_truncate(entity_current_value(self._text, e), 30)}", e.id)
        self.target_box.blockSignals(False)
        self.type_box.setEnabled(True)

    def _target_changed(self) -> None:
        # Как в вебе: при добавлении к существующей замене тип берется у нее.
        self.type_box.setEnabled(not self.target_box.currentData())

    def _submit(self) -> None:
        start, end = self._range
        target = self.target_box.currentData()
        self.hide()
        if target:
            self.attach_requested.emit(start, end, target)
        else:
            self.add_requested.emit(start, end, self.type_box.currentText())


class MergePopup(QFrame):
    """Кнопка «↦ Объединить» над вхождением и выбор цели (.cm-merge-popup)."""

    merge_requested = Signal(str, str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("popup")
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        self.open_button = QPushButton("↦ Объединить")
        self.open_button.setObjectName("mergeOpen")
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select = Select()
        self.select.setObjectName("compact")
        self.select.setMaximumWidth(220)
        row.addWidget(self.open_button)
        row.addWidget(self.select)
        self.select.hide()
        self.entity_id = ""
        self.open_button.clicked.connect(self._open_picker)
        self.select.activated.connect(self._chosen)

    def open_for(self, entity: Entity, entities: list[Entity], text: str, at: QPoint) -> None:
        self.entity_id = entity.id
        self.select.clear()
        self.select.addItem("объединить с…", "")
        for x in entities:
            if x.type == entity.type and not x.candidate and not x.dismissed and x.id != entity.id:
                self.select.addItem(f"{x.placeholder} · {_truncate(entity_current_value(text, x), 22)}", x.id)
        self.select.hide()
        self.open_button.show()
        self.adjustSize()
        self.move(at - QPoint(0, self.height() + 4))
        self.show()

    def _open_picker(self) -> None:
        self.open_button.hide()
        self.select.show()
        self.adjustSize()
        self.select.showPopup()

    def _chosen(self, index: int) -> None:
        target = self.select.itemData(index)
        if target:
            self.hide()
            self.merge_requested.emit(self.entity_id, target)


class SourceEditor(QTextEdit):
    edited = Signal(str)  # новый полный текст после правки пользователя
    entity_clicked = Signal(str)
    hovered = Signal(object)  # id сущности под указателем или None
    add_requested = Signal(int, int, str)
    attach_requested = Signal(int, int, str)
    merge_requested = Signal(str, str)
    span_changed = Signal(str, object, object)  # id, старое вхождение, новое
    undo_requested = Signal()
    redo_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("sourceEditor")
        self.setAcceptRichText(False)
        self.setUndoRedoEnabled(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.document().setDefaultFont(t.font(t.SERIF, 15))
        self.document().setDocumentMargin(18)
        self.setCursorWidth(1)
        self._highlighter = _TableHighlighter(self.document())
        self._gutter = _LineNumbers(self)
        self._entities: list[Entity] = []
        self._offsets = OffsetMap("")
        self._text = ""
        self._current_candidate: Optional[str] = None
        self._outlined: Optional[str] = None  # строка, на которую навели в списке
        self._active: Optional[tuple[str, Position]] = None  # вхождение с ручками
        self._drag: Optional[dict] = None
        self._hover_id: Optional[str] = None
        self._suppress = False
        self.selected_type = "PERSON"
        self.popup = AddEntityPopup(self)
        self.popup.add_requested.connect(self.add_requested)
        self.popup.attach_requested.connect(self.attach_requested)
        self.popup.type_chosen.connect(self._remember_type)
        self.merge_popup = MergePopup(self)
        self.merge_popup.merge_requested.connect(self._merge_chosen)
        self.textChanged.connect(self._on_text_changed)
        self.cursorPositionChanged.connect(self._render_highlights)
        self.document().blockCountChanged.connect(self._update_gutter_width)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self._update_gutter_width()

    # --- номера строк ---

    def gutter_width(self) -> int:
        digits = max(2, len(str(max(1, self.document().blockCount()))))
        return 16 + self._gutter.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        rect = self.contentsRect()
        self._gutter.setGeometry(QRect(rect.left(), rect.top(), self.gutter_width(), rect.height()))
        self.merge_popup.hide()

    def _scrolled(self) -> None:
        self._gutter.update()
        self.merge_popup.hide()

    def paint_gutter(self, widget: QWidget, event) -> None:
        painter = QPainter(widget)
        painter.fillRect(event.rect(), QColor(t.SUNK))
        painter.setPen(QColor(t.LINE))
        painter.drawLine(widget.width() - 1, event.rect().top(), widget.width() - 1, event.rect().bottom())
        layout = self.document().documentLayout()
        offset = self.verticalScrollBar().value()
        current = self.textCursor().blockNumber()
        block = self.document().begin()
        fm = widget.fontMetrics()
        while block.isValid():
            rect = layout.blockBoundingRect(block)
            top = rect.top() - offset
            if top > event.rect().bottom():
                break
            if top + rect.height() >= event.rect().top():
                first_line = block.layout().lineAt(0) if block.layout().lineCount() else None
                line_h = first_line.height() if first_line else fm.height()
                line_top = top + (first_line.y() if first_line else 0)
                if block.blockNumber() == current:
                    painter.fillRect(QRectF(0, line_top, widget.width() - 1, line_h), QColor(t.ACCENT_WASH))
                    painter.setPen(QColor(t.INK_2))
                else:
                    painter.setPen(QColor(t.INK_3))
                painter.drawText(
                    QRectF(0, line_top, widget.width() - 8, line_h),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    str(block.blockNumber() + 1),
                )
            block = block.next()
        painter.end()

    # --- загрузка и внешние правки ---

    def _apply_line_height(self) -> None:
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.SelectionType.Document)
        fmt = QTextBlockFormat()
        fmt.setLineHeight(LINE_HEIGHT, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.mergeBlockFormat(fmt)

    def set_content(self, text: str, entities: list[Entity]) -> None:
        self._suppress = True
        try:
            self.setPlainText(text)
            self._apply_line_height()
        finally:
            self._suppress = False
        self._text = text
        self._offsets = OffsetMap(text)
        self._active = None
        self.merge_popup.hide()
        self.set_entities(entities)
        self._update_gutter_width()

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
        self._active = None
        self.merge_popup.hide()
        self.popup.hide()
        self.edited.emit(text)

    # --- подсветка ---

    def set_entities(self, entities: list[Entity], current_candidate: Optional[str] = None) -> None:
        self._entities = entities
        self._current_candidate = current_candidate
        if self._active is not None:
            entity_id, span = self._active
            entity = next((e for e in entities if e.id == entity_id), None)
            if entity is None or span not in entity.positions:
                self._active = None
                self.merge_popup.hide()
        self._render_highlights()

    def set_current_candidate(self, entity_id: Optional[str]) -> None:
        if entity_id != self._current_candidate:
            self._current_candidate = entity_id
            self._render_highlights()

    def set_outlined(self, entity_id: Optional[str]) -> None:
        if entity_id != self._outlined:
            self._outlined = entity_id
            self.viewport().update()

    def _cursor_for(self, start: int, end: int) -> QTextCursor:
        cursor = QTextCursor(self.document())
        cursor.setPosition(self._offsets.to_utf16(start))
        cursor.setPosition(self._offsets.to_utf16(end), QTextCursor.MoveMode.KeepAnchor)
        return cursor

    def _render_highlights(self) -> None:
        selections = []
        line = QTextEdit.ExtraSelection()
        line.format.setBackground(t.mix(t.ACCENT_WASH, t.SURFACE, 0.55))
        line.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        line.cursor = self.textCursor()
        line.cursor.clearSelection()
        selections.append(line)
        ink_half = QColor(t.INK)
        ink_half.setAlphaF(0.5)
        for e in self._entities:
            if e.dismissed:
                continue
            fmt = QTextCharFormat()
            if e.candidate:
                if e.id == self._current_candidate:
                    fmt.setBackground(QColor(t.PENDING_WASH))
                fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.DashUnderline)
                fmt.setUnderlineColor(QColor(t.INK_3))
                fmt.setToolTip(f"Кандидат {e.placeholder}: {e.candidate_note or 'не проверен'}")
            elif e.anonymize:
                fmt.setBackground(t.entity_fill(e.type))
                fmt.setToolTip(f"{e.placeholder} · {e.type}")
            else:
                fmt.setFontStrikeOut(True)
                fmt.setForeground(ink_half)
                fmt.setToolTip(f"Замена выключена: {e.placeholder}")
            for p in e.positions:
                sel = QTextEdit.ExtraSelection()
                sel.cursor = self._cursor_for(p.start, p.end)
                sel.format = fmt
                selections.append(sel)
        self.setExtraSelections(selections)
        self.viewport().update()
        self._gutter.update()

    # --- рамки и ручки поверх текста ---

    def range_rects(self, start: int, end: int) -> list[QRectF]:
        """Прямоугольники строк, которые занимает диапазон, в координатах viewport."""
        doc = self.document()
        layout = doc.documentLayout()
        s16, e16 = self._offsets.to_utf16(start), self._offsets.to_utf16(end)
        dx = -self.horizontalScrollBar().value()
        dy = -self.verticalScrollBar().value()
        rects: list[QRectF] = []
        block = doc.findBlock(s16)
        while block.isValid() and block.position() <= e16:
            block_rect = layout.blockBoundingRect(block)
            text_layout = block.layout()
            for i in range(text_layout.lineCount()):
                line = text_layout.lineAt(i)
                line_start = block.position() + line.textStart()
                line_end = line_start + line.textLength()
                a, b = max(s16, line_start), min(e16, line_end)
                if a >= b and not (a == b and s16 == e16):
                    continue
                x1 = line.cursorToX(a - block.position())[0]
                x2 = line.cursorToX(b - block.position())[0]
                rects.append(QRectF(
                    block_rect.left() + x1 + dx, block_rect.top() + line.y() + dy, x2 - x1, line.height(),
                ))
            block = block.next()
        return rects

    def _handle_points(self) -> list[tuple[str, QPointF]]:
        if self._active is None:
            return []
        _, span = self._active
        rects = self.range_rects(span.start, span.end)
        if not rects:
            return []
        first, last = rects[0], rects[-1]
        return [
            ("left", QPointF(first.left() - 1, first.center().y())),
            ("right", QPointF(last.right() + 1, last.center().y())),
        ]

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        current = next((e for e in self._entities if e.id == self._current_candidate), None)
        if current is not None:
            painter.setPen(QPen(QColor(t.ACCENT), 2))
            for p in current.positions:
                for r in self.range_rects(p.start, p.end):
                    painter.drawRoundedRect(r.adjusted(-2, 1, 2, -1), 2, 2)
        outlined = next((e for e in self._entities if e.id == self._outlined), None)
        if outlined is not None:
            painter.setPen(QPen(QColor(t.ACCENT), 1.5))
            for p in outlined.positions:
                for r in self.range_rects(p.start, p.end):
                    painter.drawRoundedRect(r.adjusted(-1, 2, 1, -2), 3, 3)
        if self._active is not None:
            _, span = self._active
            painter.setPen(QPen(QColor(t.ACCENT), 1.5))
            for r in self.range_rects(span.start, span.end):
                painter.drawRoundedRect(r.adjusted(0, 3, 0, -3), 3, 3)
            for _, point in self._handle_points():
                painter.setPen(QPen(QColor(0, 0, 0, 90), 1))
                painter.setBrush(QColor(t.ACCENT))
                painter.drawEllipse(point, HANDLE_RADIUS + 1, HANDLE_RADIUS + 1)
                painter.setPen(QPen(QColor(t.SURFACE), 1.5))
                painter.drawEllipse(point, HANDLE_RADIUS, HANDLE_RADIUS)
                painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.end()

    # --- навигация ---

    def reveal(self, start: int, end: int) -> None:
        self.popup.hide()
        self.setTextCursor(self._cursor_for(start, end))
        self._center_cursor()

    def scroll_to(self, start: int) -> None:
        cursor = self._cursor_for(start, start)
        rect = self.document().documentLayout().blockBoundingRect(cursor.block())
        self.verticalScrollBar().setValue(int(rect.top() - self.viewport().height() / 2))

    def _center_cursor(self) -> None:
        rect = self.cursorRect()
        bar = self.verticalScrollBar()
        bar.setValue(int(bar.value() + rect.center().y() - self.viewport().height() / 2))

    def entity_at(self, pos: int) -> Optional[Entity]:
        for e in self._entities:
            if not e.dismissed and any(p.start <= pos < p.end for p in e.positions):
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

    def _cp_at(self, point: QPoint) -> int:
        return self._offsets.from_utf16(self.cursorForPosition(point).position())

    def _handle_at(self, point: QPoint) -> Optional[str]:
        for side, center in self._handle_points():
            if (QPointF(point) - center).manhattanLength() <= HANDLE_RADIUS * 2 + 2:
                return side
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            side = self._handle_at(event.position().toPoint())
            if side and self._active is not None:
                entity_id, span = self._active
                self._drag = {"side": side, "entity": entity_id, "anchor": span, "current": span, "original": span}
                self.merge_popup.hide()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        point = event.position().toPoint()
        if self._drag is not None:
            d = self._drag
            new = drag_span(self._entities, d["entity"], d["anchor"], d["current"], d["side"], self._cp_at(point), self._text)
            if new is not None and new != d["current"]:
                self._entities = replace_position(self._entities, d["entity"], d["current"], new)
                d["current"] = new
                self._active = (d["entity"], new)
                self._render_highlights()
            return
        self.viewport().setCursor(
            Qt.CursorShape.SizeHorCursor if self._handle_at(point) else Qt.CursorShape.IBeamCursor
        )
        hovered = hovered_entity_at(self._entities, self._cp_at(point)) if self.entity_at(self._cp_at(point)) else None
        if hovered != self._hover_id:
            self._hover_id = hovered
            self.hovered.emit(hovered)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover_id is not None:
            self._hover_id = None
            self.hovered.emit(None)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            d = self._drag
            self._drag = None
            if d["current"] != d["original"]:
                self.span_changed.emit(d["entity"], d["original"], d["current"])
            return
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        cursor = self.textCursor()
        if cursor.hasSelection():
            self._active = None
            self.merge_popup.hide()
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
            self._render_highlights()
            return
        pos = self._offsets.from_utf16(cursor.position())
        self._active = active_span_at(self._entities, pos)
        self.merge_popup.hide()
        if self._active is not None:
            entity_id, span = self._active
            entity = next(e for e in self._entities if e.id == entity_id)
            mergeable = (
                not entity.candidate and not entity.dismissed
                and sum(1 for x in self._entities if not x.candidate and not x.dismissed and x.type == entity.type) >= 2
            )
            if mergeable:
                rects = self.range_rects(span.start, span.end)
                if rects:
                    at = self.viewport().mapToGlobal(rects[0].topLeft().toPoint())
                    self.merge_popup.open_for(entity, self._entities, self._text, at)
            self.entity_clicked.emit(entity_id)
        self.viewport().update()

    def _merge_chosen(self, source: str, target: str) -> None:
        self._active = None
        self.merge_requested.emit(source, target)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        if not self.merge_popup.select.view().isVisible() and not self.merge_popup.underMouse():
            self.merge_popup.hide()


class EditorPane(QWidget):
    """Колонка «Исходник»: заголовок с подсказкой и редактор в рамке."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        head = QHBoxLayout()
        head.setContentsMargins(2, 0, 2, 0)
        self.title = QLabel("Исходник")
        self.title.setObjectName("paneTitle")
        hint = QLabel("Выделите текст, чтобы добавить его в замены")
        hint.setObjectName("hint")
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(hint)
        layout.addLayout(head)
        box = QFrame()
        box.setObjectName("editorBox")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(1, 1, 1, 1)
        self.editor = SourceEditor()
        inner.addWidget(self.editor)
        layout.addWidget(box, 1)
