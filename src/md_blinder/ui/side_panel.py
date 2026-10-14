"""Боковая панель: очередь проверки и найденные данные (CandidateReview.tsx, EntityList.tsx).

Панель ничего не меняет сама: она сообщает о решениях сигналами, а MainWindow
превращает их в команды сессии.
"""

from __future__ import annotations

import html
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..anonymize.apply import candidate_reason, entity_current_value, pending_candidates
from ..anonymize.types import ALL_ENTITY_TYPES, Entity
from ..text_utils import candidate_context
from . import theme as t
from .icons import icon, pixmap
from .widgets import Select, icon_label, set_prop


def _min_start(e: Entity) -> int:
    return min((p.start for p in e.positions), default=1 << 60)


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


def _transparent_labels(widget: QWidget) -> None:
    for child in widget.findChildren(QLabel):
        child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def _kbd_button(text: str, key: str, primary: bool) -> QPushButton:
    """Кнопка с подписью клавиши в рамке, как <kbd> в карточке кандидата."""
    button = QPushButton()
    if primary:
        button.setObjectName("primary")
    row = QHBoxLayout(button)
    row.setContentsMargins(13, 7, 13, 7)
    row.setSpacing(8)
    row.addStretch(1)
    color = t.ACCENT_INK if primary else t.INK
    label = QLabel(text)
    label.setStyleSheet(f"color: {color}; font-size: 14px; font-weight: 500; background: transparent;")
    kbd = QLabel(key)
    kbd.setStyleSheet(
        f"color: {color}; border: 1px solid {color}; border-radius: 4px; padding: 0 5px; font-size: 11px;"
        " background: transparent;"
    )
    kbd.setGraphicsEffect(None)
    row.addWidget(label)
    row.addWidget(kbd)
    row.addStretch(1)
    _transparent_labels(button)
    button.setMinimumHeight(36)
    # QPushButton считает размер по своему тексту, а не по вложенной раскладке.
    button.setMinimumWidth(row.sizeHint().width())
    return button


class CandidateCard(QFrame):
    """Карточка кандидата. Клавиши работают, только когда фокус на карточке."""

    accept = Signal()
    reject = Signal()
    step = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("candidateCard")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def focusInEvent(self, event) -> None:  # noqa: N802
        set_prop(self, "focused", "true")
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        set_prop(self, "focused", "false")
        super().focusOutEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accept.emit()
        elif key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.reject.emit()
        elif key == Qt.Key.Key_Down:
            self.step.emit(1)
        elif key == Qt.Key.Key_Up:
            self.step.emit(-1)
        else:
            super().keyPressEvent(event)


def _scroll_area() -> tuple[QScrollArea, QVBoxLayout]:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    body = QWidget()
    body.setObjectName("panelBody")
    layout = QVBoxLayout(body)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    area.setWidget(body)
    return area, layout


class ReviewPanel(QWidget):
    activate = Signal(object)  # id кандидата или None
    accept = Signal(str)
    reject = Signal(str)
    accept_all_related = Signal(str)  # id целевой сущности
    restore = Signal(str)
    change_type = Signal(str, str)
    export = Signal()

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.area, self.layout_ = _scroll_area()
        self.layout_.setSpacing(16)
        outer.addWidget(self.area)
        self._entities: list[Entity] = []
        self._text = ""
        self.active_id: Optional[str] = None
        self._keep_focus = False
        self._card: Optional[CandidateCard] = None
        self._dismissed_open = False

    def current_id(self) -> Optional[str]:
        pending = pending_candidates(self._entities)
        current = next((e for e in pending if e.id == self.active_id), pending[0] if pending else None)
        return current.id if current else None

    def render(self, entities: list[Entity], text: str) -> None:
        self._entities = entities
        self._text = text
        scroll = self.area.verticalScrollBar().value()
        _clear(self.layout_)
        self._card = None
        pending = pending_candidates(entities)
        dismissed = sorted((e for e in entities if e.dismissed), key=_min_start)
        current = next((e for e in pending if e.id == self.active_id), pending[0] if pending else None)

        if current is None:
            done = QFrame()
            done.setObjectName("reviewDone")
            box = QVBoxLayout(done)
            box.setContentsMargins(16, 14, 16, 14)
            box.setSpacing(8)
            title_row = QHBoxLayout()
            title_row.setSpacing(8)
            title_row.addWidget(icon_label("check", t.READY, 16))
            title = QLabel("Все кандидаты разобраны")
            title.setObjectName("doneTitle")
            title_row.addWidget(title)
            title_row.addStretch(1)
            box.addLayout(title_row)
            note = QLabel("В результате не осталось непроверенных фрагментов.")
            note.setStyleSheet(f"color: {t.INK_2}; font-size: 13.5px; background: transparent;")
            box.addWidget(note)
            export = QPushButton("Экспортировать .md")
            export.setObjectName("primary")
            export.setIcon(icon("download", t.ACCENT_INK))
            export.clicked.connect(self.export)
            box.addWidget(export, 0, Qt.AlignmentFlag.AlignLeft)
            self.layout_.addWidget(done)
            self._add_dismissed(dismissed)
            self.area.verticalScrollBar().setValue(scroll)
            return

        index = pending.index(current)

        def next_after(ids: set[str]) -> Optional[str]:
            rest = [e for e in pending if e.id not in ids]
            after = next((e for e in rest if pending.index(e) > index), rest[0] if rest else None)
            return after.id if after else None

        def decide(signal) -> None:
            self._keep_focus = True
            nxt = next_after({current.id})
            signal.emit(current.id)
            self.activate.emit(nxt)

        def step(delta: int) -> None:
            self._keep_focus = True
            self.activate.emit(pending[(index + delta) % len(pending)].id)

        card = CandidateCard()
        self._card = card
        card.accept.connect(lambda: decide(self.accept))
        card.reject.connect(lambda: decide(self.reject))
        card.step.connect(step)
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 14)
        box.setSpacing(10)
        head = QHBoxLayout()
        count = QLabel(f"Кандидат {index + 1} из {len(pending)}")
        count.setObjectName("cardCount")
        head.addWidget(count)
        head.addStretch(1)
        if len(pending) > 1:
            for name, delta, tip in (("chevron-up", -1, "Предыдущий кандидат"), ("chevron-down", 1, "Следующий кандидат")):
                b = QPushButton()
                b.setObjectName("iconButton")
                b.setIcon(icon(name, t.INK_3, 16))
                b.setToolTip(tip)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, d=delta: step(d))
                head.addWidget(b)
        box.addLayout(head)

        value = QLabel(entity_current_value(text, current))
        value.setObjectName("cardValue")
        value.setWordWrap(True)
        box.addWidget(value)
        position = current.positions[0] if current.positions else None
        if position is not None:
            ctx = candidate_context(text, position.start, position.end)
            snippet = (
                ("…" if ctx.cut_before else "") + html.escape(ctx.before)
                + f"<span style='background:{t.MARK}; color:{t.INK}'>{html.escape(ctx.match)}</span>"
                + html.escape(ctx.after) + ("…" if ctx.cut_after else "")
            )
            context = QLabel(snippet.replace("\n", "<br>"))
            context.setObjectName("cardContext")
            context.setWordWrap(True)
            context.setTextFormat(Qt.TextFormat.RichText)
            box.addWidget(context)
        reason = candidate_reason(entities, current)
        if reason:
            r = QLabel(reason)
            r.setObjectName("cardReason")
            r.setWordWrap(True)
            box.addWidget(r)
        type_row = QHBoxLayout()
        type_row.setSpacing(10)
        type_label = QLabel("Тип")
        type_label.setObjectName("fieldLabel")
        type_row.addWidget(type_label)
        type_box = Select()
        type_box.setObjectName("mono")
        type_box.addItems(list(ALL_ENTITY_TYPES))
        type_box.setCurrentText(current.type)
        type_box.currentTextChanged.connect(lambda tp: self.change_type.emit(current.id, tp))
        type_row.addWidget(type_box)
        type_row.addStretch(1)
        box.addLayout(type_row)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        accept = _kbd_button("Принять", "Enter", True)
        accept.clicked.connect(lambda: decide(self.accept))
        reject = _kbd_button("Отклонить", "⌫", False)
        reject.clicked.connect(lambda: decide(self.reject))
        actions.addWidget(accept, 1)
        actions.addWidget(reject, 1)
        box.addLayout(actions)

        siblings = [e for e in pending if current.related_to and e.related_to == current.related_to]
        target = next((e for e in entities if e.id == current.related_to), None) if current.related_to else None
        if len(siblings) > 1 and target is not None and not target.candidate and not target.dismissed:
            all_button = QPushButton(f"Принять все похожие на {target.placeholder} ({len(siblings)})")
            all_button.setObjectName("link")
            all_button.setCursor(Qt.CursorShape.PointingHandCursor)

            def accept_all() -> None:
                self._keep_focus = True
                nxt = next_after({e.id for e in siblings})
                self.accept_all_related.emit(target.id)
                self.activate.emit(nxt)

            all_button.clicked.connect(accept_all)
            box.addWidget(all_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.layout_.addWidget(card)

        section = QVBoxLayout()
        section.setSpacing(2)
        heading = QLabel("ВСЕ КАНДИДАТЫ")
        heading.setObjectName("sectionHead")
        heading.setContentsMargins(4, 0, 0, 4)
        section.addWidget(heading)
        for i, e in enumerate(pending):
            is_current = e.id == current.id
            reason_text = "Проверяется сейчас" if is_current else (candidate_reason(entities, e) or "")
            section.addWidget(self._item(
                str(i + 1), entity_current_value(text, e), reason_text, is_current,
                None if is_current else ("Открыть", lambda _=False, eid=e.id: self.activate.emit(eid)),
            ))
        self.layout_.addLayout(section)

        self._add_dismissed(dismissed)
        hint = QLabel(
            "Пока кандидат не принят, он остается в тексте как есть. В карточке: Enter принимает, "
            "Backspace отклоняет, стрелки ↑ ↓ переходят к соседнему кандидату."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {t.INK_3}; font-size: 12.5px; background: transparent;")
        hint.setContentsMargins(4, 0, 4, 0)
        self.layout_.addWidget(hint)
        self.area.verticalScrollBar().setValue(scroll)
        if self._keep_focus:
            self._keep_focus = False
            card.setFocus()

    def _item(
        self, num: Optional[str], value: str, reason: str, current: bool,
        action: Optional[tuple[str, Callable]],
    ) -> QFrame:
        item = QFrame()
        item.setObjectName("candidateItem")
        item.setProperty("current", "true" if current else "false")
        row = QHBoxLayout(item)
        row.setContentsMargins(6, 7, 6, 7)
        row.setSpacing(10)
        if num is not None:
            n = QLabel(num)
            n.setObjectName("itemNum")
            n.setFixedSize(22, 22)
            n.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(n)
        col = QVBoxLayout()
        col.setSpacing(0)
        v = QLabel(_truncate(value.replace("\n", " "), 60))
        v.setObjectName("itemValue")
        r = QLabel(reason)
        r.setObjectName("itemReasonCurrent" if current else "itemReason")
        r.setWordWrap(True)
        col.addWidget(v)
        col.addWidget(r)
        row.addLayout(col, 1)
        if action is not None:
            b = QPushButton(action[0])
            b.setObjectName("small")
            b.clicked.connect(action[1])
            row.addWidget(b)
        return item

    def focus_card(self) -> None:
        if self._card is not None:
            self._card.setFocus()

    def _add_dismissed(self, dismissed: list[Entity]) -> None:
        if not dismissed:
            return
        wrap = QVBoxLayout()
        wrap.setSpacing(2)
        toggle = QPushButton()
        toggle.setObjectName("disclosure")
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        wrap.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
        container = QWidget()
        box = QVBoxLayout(container)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        for e in dismissed:
            box.addWidget(self._item(
                None, entity_current_value(self._text, e), "Остается в тексте как есть", False,
                ("Вернуть", lambda _=False, eid=e.id: self.restore.emit(eid)),
            ))
        wrap.addWidget(container)
        self.layout_.addLayout(wrap)

        def sync() -> None:
            container.setVisible(self._dismissed_open)
            toggle.setText(f"{'▾' if self._dismissed_open else '▸'}  Отклоненные ({len(dismissed)})")

        def flip() -> None:
            self._dismissed_open = not self._dismissed_open
            sync()

        toggle.clicked.connect(flip)
        sync()


class _EntityRow(QFrame):
    hovered = Signal(object)

    def __init__(self, entity_id: str) -> None:
        super().__init__()
        self.entity_id = entity_id
        self.setObjectName("entityRow")

    def enterEvent(self, event) -> None:  # noqa: N802
        self.hovered.emit(self.entity_id)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hovered.emit(None)
        super().leaveEvent(event)


class _Clickable(QWidget):
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("clickable")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def enterEvent(self, event) -> None:  # noqa: N802
        set_prop(self, "hover", "true")

    def leaveEvent(self, event) -> None:  # noqa: N802
        set_prop(self, "hover", "false")


class FoundPanel(QWidget):
    """Найденные данные по типам: флажок группы, флажок значения, действия выбранной строки."""

    set_anonymize = Signal(list, bool)  # ids, значение
    select = Signal(str)
    change_type = Signal(str, str)
    merge = Signal(str, str)  # источник, цель
    delete = Signal(str)
    row_hovered = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.area, self.layout_ = _scroll_area()
        self.layout_.setSpacing(0)
        outer.addWidget(self.area)
        self._entities: list[Entity] = []
        self._text = ""
        self._selected: Optional[str] = None
        self._merging: Optional[str] = None
        self._collapsed: set[str] = set()
        self._highlighted: Optional[str] = None
        self._rows: dict[str, _EntityRow] = {}
        self._group_headers: dict[str, QFrame] = {}

    def confirmed(self, entities: list[Entity]) -> list[Entity]:
        return [e for e in entities if not e.dismissed and not e.candidate]

    def render(self, entities: list[Entity], text: str) -> None:
        self._entities = entities
        self._text = text
        if self._selected and not any(e.id == self._selected for e in self.confirmed(entities)):
            self._selected = None
            self._merging = None
        scroll = self.area.verticalScrollBar().value()
        _clear(self.layout_)
        self._rows = {}
        self._group_headers = {}
        confirmed = self.confirmed(entities)
        if not confirmed:
            empty = QLabel("Ничего не найдено. Выделите текст в редакторе, чтобы добавить замену вручную.")
            empty.setWordWrap(True)
            empty.setObjectName("muted")
            self.layout_.addWidget(empty)
            return
        by_type: dict[str, list[Entity]] = {}
        for e in confirmed:
            by_type.setdefault(e.type, []).append(e)
        groups = [(tp, sorted(by_type[tp], key=_min_start)) for tp in ALL_ENTITY_TYPES if tp in by_type]
        for gi, (type_, items) in enumerate(groups):
            if gi > 0:
                sep = QFrame()
                sep.setObjectName("groupSep")
                sep.setFixedHeight(1)
                self.layout_.addWidget(sep)
            self.layout_.addWidget(self._group_header(type_, items))
            if type_ not in self._collapsed:
                for e in items:
                    self.layout_.addWidget(self._row(e, by_type[type_]))
                self.layout_.addSpacing(6)
        self.area.verticalScrollBar().setValue(scroll)
        self.set_highlighted(self._highlighted)

    def _group_header(self, type_: str, items: list[Entity]) -> QFrame:
        active = sum(1 for e in items if e.anonymize)
        header = QFrame()
        header.setObjectName("groupHeader")
        header.setMinimumHeight(40)
        row = QHBoxLayout(header)
        row.setContentsMargins(6, 0, 2, 0)
        row.setSpacing(10)
        box = QCheckBox()
        box.setTristate(True)
        box.setCheckState(
            Qt.CheckState.Checked if active == len(items)
            else Qt.CheckState.Unchecked if active == 0 else Qt.CheckState.PartiallyChecked
        )
        box.setToolTip("Заменять все значения этого типа")
        # Как в вебе: если включены не все, включаем все; иначе выключаем.
        box.clicked.connect(lambda _=False, ids=[e.id for e in items], value=active < len(items):
                            self.set_anonymize.emit(ids, value))
        row.addWidget(box)
        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(
            f"background: {t.entity_solid(type_).name()}; border-radius: 5px; border: 1px solid rgba(0,0,0,30);"
        )
        row.addWidget(dot)
        toggle = _Clickable()
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        trow = QHBoxLayout(toggle)
        trow.setContentsMargins(0, 8, 4, 8)
        trow.setSpacing(8)
        name = QLabel(type_)
        name.setObjectName("groupName")
        count = QLabel(str(len(items)) if active == len(items) else f"{active} из {len(items)}")
        count.setObjectName("groupCount")
        chevron = QLabel()
        chevron.setPixmap(pixmap("chevron-down" if type_ not in self._collapsed else "chevron-up", t.INK_3, 16))
        trow.addWidget(name)
        trow.addWidget(count)
        trow.addStretch(1)
        trow.addWidget(chevron)
        _transparent_labels(toggle)
        toggle.clicked.connect(lambda tp=type_: self._toggle_group(tp))
        row.addWidget(toggle, 1)
        self._group_headers[type_] = header
        return header

    def _toggle_group(self, type_: str) -> None:
        if type_ in self._collapsed:
            self._collapsed.discard(type_)
        else:
            self._collapsed.add(type_)
        self.render(self._entities, self._text)

    def _row(self, entity: Entity, same_type: list[Entity]) -> _EntityRow:
        value = entity_current_value(self._text, entity)
        selected = entity.id == self._selected
        row = _EntityRow(entity.id)
        row.setProperty("selected", "true" if selected else "false")
        row.hovered.connect(self.row_hovered)
        box = QVBoxLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        line = QHBoxLayout()
        line.setContentsMargins(6, 0, 0, 0)
        line.setSpacing(8)
        check = QCheckBox()
        check.setChecked(entity.anonymize)
        check.setToolTip("Заменять в итоговом тексте")
        check.clicked.connect(lambda checked, eid=entity.id: self.set_anonymize.emit([eid], checked))
        line.addWidget(check)
        button = _Clickable()
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip("Показать в тексте")
        brow = QHBoxLayout(button)
        brow.setContentsMargins(6, 7, 6, 7)
        brow.setSpacing(8)
        cell = QLabel(_truncate(value.replace("\n", " "), 80))
        cell.setObjectName("cellValue" if entity.anonymize else "cellValueOff")
        cell.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        placeholder = QLabel(entity.placeholder)
        placeholder.setObjectName("cellPlaceholder")
        cnt = QLabel(f"×{len(entity.positions)}")
        cnt.setObjectName("cellCount")
        cnt.setMinimumWidth(22)
        cnt.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        brow.addWidget(cell, 1)
        brow.addWidget(placeholder)
        brow.addWidget(cnt)
        _transparent_labels(button)
        button.clicked.connect(lambda eid=entity.id: self._click(eid))
        line.addWidget(button, 1)
        box.addLayout(line)
        if selected:
            box.addWidget(self._actions(entity, same_type))
        self._rows[entity.id] = row
        return row

    def _actions(self, entity: Entity, same_type: list[Entity]) -> QWidget:
        area = QWidget()
        rows = QVBoxLayout(area)
        rows.setContentsMargins(36, 2, 8, 10)
        rows.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        rows.addLayout(row)
        others = [x for x in same_type if x.id != entity.id]
        if self._merging == entity.id:
            target = Select()
            target.setObjectName("compact")
            target.setMinimumContentsLength(8)
            target.setSizeAdjustPolicy(Select.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            target.addItem("объединить с…", "")
            for x in others:
                target.addItem(f"{x.placeholder} · {_truncate(entity_current_value(self._text, x), 24)}", x.id)
            ok = QPushButton("Объединить")
            ok.setObjectName("primary")
            ok.setStyleSheet("padding: 3px 10px; font-size: 13px;")
            ok.setEnabled(False)
            target.currentIndexChanged.connect(lambda _i: ok.setEnabled(bool(target.currentData())))
            cancel = QPushButton("Отмена")
            cancel.setObjectName("small")

            def do_merge() -> None:
                if target.currentData():
                    self._merging = None
                    self._selected = None
                    self.merge.emit(entity.id, target.currentData())

            def do_cancel() -> None:
                self._merging = None
                self.render(self._entities, self._text)

            ok.clicked.connect(do_merge)
            cancel.clicked.connect(do_cancel)
            row.addWidget(target, 1)
            row.addWidget(ok)
            row.addWidget(cancel)
            return area
        label = QLabel("Тип")
        label.setObjectName("fieldLabel")
        row.addWidget(label)
        type_box = Select()
        type_box.setObjectName("mono")
        type_box.addItems(list(ALL_ENTITY_TYPES))
        type_box.setCurrentText(entity.type)
        type_box.currentTextChanged.connect(lambda tp, eid=entity.id: self.change_type.emit(eid, tp))
        row.addWidget(type_box)
        if others:
            merge = QPushButton("Объединить…")
            merge.setObjectName("small")
            merge.setToolTip("Объединить с другой заменой того же типа")

            def start_merge() -> None:
                self._merging = entity.id
                self.render(self._entities, self._text)

            merge.clicked.connect(start_merge)
            row.addWidget(merge)
        row.addStretch(1)
        delete = QPushButton("Удалить")
        delete.setObjectName("danger")

        def do_delete() -> None:
            self._selected = None
            self.delete.emit(entity.id)

        delete.clicked.connect(do_delete)
        rows.addWidget(delete, 0, Qt.AlignmentFlag.AlignLeft)
        return area

    def _click(self, entity_id: str) -> None:
        self._merging = None
        if self._selected == entity_id:
            self._selected = None
            self.render(self._entities, self._text)
            return
        self._selected = entity_id
        self.render(self._entities, self._text)
        self.select.emit(entity_id)

    def select_entity(self, entity_id: str) -> None:
        entity = next((e for e in self._entities if e.id == entity_id), None)
        if entity is None:
            return
        self._collapsed.discard(entity.type)
        self._selected = entity_id
        self._merging = None
        self.render(self._entities, self._text)
        row = self._rows.get(entity_id)
        if row is not None:
            self.area.ensureWidgetVisible(row, 0, 40)

    def set_highlighted(self, entity_id: Optional[str]) -> None:
        """Подсветка строки сущности, на которую навели в редакторе."""
        previous = self._rows.get(self._highlighted) if self._highlighted else None
        if previous is not None:
            set_prop(previous, "highlighted", "false")
        for header in self._group_headers.values():
            set_prop(header, "highlighted", "false")
        self._highlighted = entity_id
        if not entity_id:
            return
        row = self._rows.get(entity_id)
        if row is not None:
            set_prop(row, "highlighted", "true")
            self.area.ensureWidgetVisible(row, 0, 40)
            return
        entity = next((e for e in self._entities if e.id == entity_id), None)
        if entity is not None and entity.type in self._group_headers:
            header = self._group_headers[entity.type]
            set_prop(header, "highlighted", "true")
            self.area.ensureWidgetVisible(header, 0, 40)


class _Tab(QPushButton):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("sideTab")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(6)
        row.addStretch(1)
        self.title = QLabel(title)
        self.title.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        self.badge = QLabel()
        self.badge.setObjectName("badge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.title, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        _transparent_labels(self)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(44)
        self._row = row

    def _fit(self) -> None:
        self.setMinimumWidth(self._row.sizeHint().width())

    def set_selected(self, selected: bool) -> None:
        set_prop(self, "selected", "true" if selected else "false")
        self.title.setStyleSheet(
            f"background: transparent; font-size: 13.5px; color: {t.INK if selected else t.INK_3};"
            f" font-weight: {600 if selected else 400};"
        )
        self._fit()

    def set_badge(self, text: str, kind: str = "badge") -> None:
        self.badge.setText(text)
        self.badge.setObjectName(kind)
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)
        self._fit()


class SidePanel(QFrame):
    """Карточка боковой панели: вкладки «Проверка» и «Найденные данные»."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("entityPane")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        tabs = QFrame()
        tabs.setObjectName("sideTabs")
        tab_row = QHBoxLayout(tabs)
        tab_row.setContentsMargins(8, 0, 8, 0)
        tab_row.setSpacing(0)
        self.tab_review = _Tab("Проверка")
        self.tab_found = _Tab("Найденные данные")
        tab_row.addWidget(self.tab_review)
        tab_row.addWidget(self.tab_found)
        layout.addWidget(tabs)
        self.stack = QStackedWidget()
        self.review = ReviewPanel()
        self.found = FoundPanel()
        self.stack.addWidget(self.review)
        self.stack.addWidget(self.found)
        layout.addWidget(self.stack, 1)
        self.user_tab: Optional[int] = None  # None — вкладка выбирается автоматически
        self.tab_review.clicked.connect(lambda: self._choose(0))
        self.tab_found.clicked.connect(lambda: self._choose(1))
        self.setCurrentIndex(0)

    def _choose(self, index: int) -> None:
        self.user_tab = index
        self.setCurrentIndex(index)

    def currentIndex(self) -> int:  # noqa: N802
        return self.stack.currentIndex()

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        self.stack.setCurrentIndex(index)
        self.tab_review.set_selected(index == 0)
        self.tab_found.set_selected(index == 1)

    def render(self, entities: list[Entity], text: str) -> None:
        pending = len(pending_candidates(entities))
        confirmed = len(self.found.confirmed(entities))
        if pending:
            self.tab_review.set_badge(str(pending), "badgePending")
        else:
            self.tab_review.set_badge("✓", "badgeDone")
        self.tab_found.set_badge(str(confirmed), "badge")
        self.review.render(entities, text)
        self.found.render(entities, text)
        target = self.user_tab if self.user_tab is not None else (0 if pending else 1)
        if self.currentIndex() != target:
            self.setCurrentIndex(target)
