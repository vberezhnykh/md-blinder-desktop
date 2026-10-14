"""Боковая панель: очередь проверки и найденные данные.

Порт CandidateReview.tsx и EntityList.tsx. Панель ничего не меняет сама: она
сообщает о решениях сигналами, а MainWindow превращает их в команды сессии.
"""

from __future__ import annotations

import html
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..anonymize.apply import candidate_reason, entity_current_value, pending_candidates
from ..anonymize.types import ALL_ENTITY_TYPES, Entity
from ..text_utils import candidate_context
from .theme import entity_solid


def _min_start(e: Entity) -> int:
    return min((p.start for p in e.positions), default=1 << 60)


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


class CandidateCard(QFrame):
    """Карточка кандидата. Клавиши работают, только когда фокус на карточке."""

    accept = Signal()
    reject = Signal()
    step = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("candidateCard")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

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


class ReviewPanel(QScrollArea):
    activate = Signal(object)  # id кандидата или None
    accept = Signal(str)
    reject = Signal(str)
    accept_all_related = Signal(str)  # id целевой сущности
    restore = Signal(str)
    change_type = Signal(str, str)
    export = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.layout_ = QVBoxLayout(body)
        self.layout_.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(body)
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
        _clear(self.layout_)
        self._card = None
        pending = pending_candidates(entities)
        dismissed = sorted((e for e in entities if e.dismissed), key=_min_start)
        current = next((e for e in pending if e.id == self.active_id), pending[0] if pending else None)

        if current is None:
            done = QFrame()
            done.setObjectName("card")
            box = QVBoxLayout(done)
            title = QLabel("<b>✓ Все кандидаты разобраны</b>")
            box.addWidget(title)
            box.addWidget(QLabel("В результате не осталось непроверенных фрагментов."))
            export = QPushButton("Экспортировать .md")
            export.setObjectName("primary")
            export.clicked.connect(self.export)
            box.addWidget(export, 0, Qt.AlignmentFlag.AlignLeft)
            self.layout_.addWidget(done)
            self._add_dismissed(dismissed)
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
        head = QHBoxLayout()
        head.addWidget(QLabel(f"Кандидат {index + 1} из {len(pending)}"))
        head.addStretch(1)
        if len(pending) > 1:
            for label, delta, tip in (("▲", -1, "Предыдущий кандидат"), ("▼", 1, "Следующий кандидат")):
                b = QToolButton()
                b.setText(label)
                b.setToolTip(tip)
                b.clicked.connect(lambda _=False, d=delta: step(d))
                head.addWidget(b)
        box.addLayout(head)

        value = entity_current_value(text, current)
        value_label = QLabel(f"<span style='font-size:13pt;font-weight:600'>{html.escape(value)}</span>")
        value_label.setWordWrap(True)
        box.addWidget(value_label)
        position = current.positions[0] if current.positions else None
        if position is not None:
            ctx = candidate_context(text, position.start, position.end)
            snippet = (
                ("…" if ctx.cut_before else "") + html.escape(ctx.before)
                + f"<span style='background:#ffe9a8'>{html.escape(ctx.match)}</span>"
                + html.escape(ctx.after) + ("…" if ctx.cut_after else "")
            )
            context_label = QLabel(snippet.replace("\n", "<br>"))
            context_label.setWordWrap(True)
            context_label.setObjectName("muted")
            box.addWidget(context_label)
        reason = candidate_reason(entities, current)
        if reason:
            r = QLabel(reason)
            r.setObjectName("muted")
            box.addWidget(r)
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Тип"))
        type_box = QComboBox()
        type_box.addItems(list(ALL_ENTITY_TYPES))
        type_box.setCurrentText(current.type)
        type_box.currentTextChanged.connect(lambda t: self.change_type.emit(current.id, t))
        type_row.addWidget(type_box)
        type_row.addStretch(1)
        box.addLayout(type_row)
        actions = QHBoxLayout()
        accept = QPushButton("Принять  Enter")
        accept.setObjectName("primary")
        accept.clicked.connect(lambda: decide(self.accept))
        reject = QPushButton("Отклонить  ⌫")
        reject.clicked.connect(lambda: decide(self.reject))
        actions.addWidget(accept)
        actions.addWidget(reject)
        actions.addStretch(1)
        box.addLayout(actions)

        siblings = [e for e in pending if current.related_to and e.related_to == current.related_to]
        target = next((e for e in entities if e.id == current.related_to), None) if current.related_to else None
        if len(siblings) > 1 and target is not None and not target.candidate and not target.dismissed:
            all_button = QPushButton(f"Принять все похожие на {target.placeholder} ({len(siblings)})")
            all_button.setObjectName("link")

            def accept_all() -> None:
                self._keep_focus = True
                nxt = next_after({e.id for e in siblings})
                self.accept_all_related.emit(target.id)
                self.activate.emit(nxt)

            all_button.clicked.connect(accept_all)
            box.addWidget(all_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.layout_.addWidget(card)

        heading = QLabel("<b>Все кандидаты</b>")
        self.layout_.addWidget(heading)
        for i, e in enumerate(pending):
            row = QHBoxLayout()
            label = QLabel(
                f"{i + 1}. <b>{html.escape(_truncate(entity_current_value(text, e), 60))}</b><br>"
                f"<span style='color:#5b6474'>"
                f"{'Проверяется сейчас' if e.id == current.id else html.escape(candidate_reason(entities, e) or '')}</span>"
            )
            label.setWordWrap(True)
            row.addWidget(label, 1)
            if e.id != current.id:
                open_button = QPushButton("Открыть")
                open_button.clicked.connect(lambda _=False, eid=e.id: self.activate.emit(eid))
                row.addWidget(open_button)
            self.layout_.addLayout(row)

        self._add_dismissed(dismissed)
        hint = QLabel(
            "Пока кандидат не принят, он остается в тексте как есть. В карточке: Enter принимает, "
            "Backspace отклоняет, стрелки ↑ ↓ переходят к соседнему кандидату."
        )
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        self.layout_.addWidget(hint)
        if self._keep_focus:
            self._keep_focus = False
            card.setFocus()

    def focus_card(self) -> None:
        if self._card is not None:
            self._card.setFocus()

    def _add_dismissed(self, dismissed: list[Entity]) -> None:
        if not dismissed:
            return
        toggle = QToolButton()
        toggle.setText(f"{'▾' if self._dismissed_open else '▸'} Отклоненные ({len(dismissed)})")
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        toggle.setAutoRaise(True)
        self.layout_.addWidget(toggle)
        container = QWidget()
        box = QVBoxLayout(container)
        box.setContentsMargins(12, 0, 0, 0)
        for e in dismissed:
            row = QHBoxLayout()
            label = QLabel(
                f"<b>{html.escape(_truncate(entity_current_value(self._text, e), 60))}</b><br>"
                "<span style='color:#5b6474'>Остается в тексте как есть</span>"
            )
            row.addWidget(label, 1)
            back = QPushButton("Вернуть")
            back.clicked.connect(lambda _=False, eid=e.id: self.restore.emit(eid))
            row.addWidget(back)
            box.addLayout(row)
        container.setVisible(self._dismissed_open)
        self.layout_.addWidget(container)

        def flip() -> None:
            self._dismissed_open = not self._dismissed_open
            container.setVisible(self._dismissed_open)
            toggle.setText(f"{'▾' if self._dismissed_open else '▸'} Отклоненные ({len(dismissed)})")

        toggle.clicked.connect(flip)


class FoundPanel(QWidget):
    """Найденные данные по типам: флажок группы, флажок значения, действия выбранной строки."""

    set_anonymize = Signal(list, bool)  # ids, значение
    select = Signal(str)
    change_type = Signal(str, str)
    merge = Signal(str, str)  # источник, цель
    delete = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.empty = QLabel("Ничего не найдено. Выделите текст в редакторе, чтобы добавить замену вручную.")
        self.empty.setWordWrap(True)
        self.empty.setObjectName("muted")
        layout.addWidget(self.empty)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Значение", "Замена", "×"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, self.tree.header().ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, self.tree.header().ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, self.tree.header().ResizeMode.ResizeToContents)
        layout.addWidget(self.tree, 1)

        self.actions = QFrame()
        self.actions.setObjectName("card")
        actions = QVBoxLayout(self.actions)
        self.selected_label = QLabel()
        self.selected_label.setWordWrap(True)
        actions.addWidget(self.selected_label)
        row = QHBoxLayout()
        row.addWidget(QLabel("Тип"))
        self.type_box = QComboBox()
        self.type_box.addItems(list(ALL_ENTITY_TYPES))
        row.addWidget(self.type_box)
        row.addStretch(1)
        self.delete_button = QPushButton("Удалить")
        row.addWidget(self.delete_button)
        actions.addLayout(row)
        merge_row = QHBoxLayout()
        self.merge_box = QComboBox()
        self.merge_button = QPushButton("Объединить")
        merge_row.addWidget(self.merge_box, 1)
        merge_row.addWidget(self.merge_button)
        actions.addLayout(merge_row)
        layout.addWidget(self.actions)
        self.actions.hide()

        self._entities: list[Entity] = []
        self._text = ""
        self._selected: Optional[str] = None
        self._updating = False
        self._collapsed: set[str] = set()
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemCollapsed.connect(lambda item: self._collapsed.add(item.data(0, Qt.ItemDataRole.UserRole + 1)))
        self.tree.itemExpanded.connect(lambda item: self._collapsed.discard(item.data(0, Qt.ItemDataRole.UserRole + 1)))
        self.type_box.currentTextChanged.connect(self._on_type)
        self.delete_button.clicked.connect(self._on_delete)
        self.merge_button.clicked.connect(self._on_merge)

    def confirmed(self, entities: list[Entity]) -> list[Entity]:
        return [e for e in entities if not e.dismissed and not e.candidate]

    def render(self, entities: list[Entity], text: str) -> None:
        self._entities = entities
        self._text = text
        confirmed = self.confirmed(entities)
        self.empty.setVisible(not confirmed)
        self.tree.setVisible(bool(confirmed))
        by_type: dict[str, list[Entity]] = {}
        for e in confirmed:
            by_type.setdefault(e.type, []).append(e)
        self._updating = True
        scroll = self.tree.verticalScrollBar().value()
        self.tree.clear()
        selected_item = None
        for type_ in ALL_ENTITY_TYPES:
            items = sorted(by_type.get(type_, []), key=_min_start)
            if not items:
                continue
            active = sum(1 for e in items if e.anonymize)
            group = QTreeWidgetItem(self.tree)
            group.setText(0, type_)
            group.setText(2, str(len(items)) if active == len(items) else f"{active} из {len(items)}")
            group.setForeground(0, QBrush(entity_solid(type_)))
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            group.setData(0, Qt.ItemDataRole.UserRole + 1, type_)
            group.setFlags(group.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            group.setCheckState(0, Qt.CheckState.Checked if active == len(items)
                                else Qt.CheckState.Unchecked if active == 0 else Qt.CheckState.PartiallyChecked)
            group.setToolTip(0, "Заменять все значения этого типа")
            for e in items:
                child = QTreeWidgetItem(group)
                child.setText(0, _truncate(entity_current_value(text, e).replace("\n", " "), 80))
                child.setText(1, e.placeholder)
                child.setText(2, str(len(e.positions)))
                child.setToolTip(0, "Показать в тексте")
                child.setData(0, Qt.ItemDataRole.UserRole, e.id)
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked if e.anonymize else Qt.CheckState.Unchecked)
                if e.id == self._selected:
                    selected_item = child
            group.setExpanded(type_ not in self._collapsed)
        self._updating = False
        self.tree.verticalScrollBar().setValue(scroll)
        if selected_item is not None:
            self.tree.setCurrentItem(selected_item)
            self._show_actions(self._selected)
        else:
            self._selected = None
            self.actions.hide()

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        entity_id = item.data(0, Qt.ItemDataRole.UserRole)
        checked = item.checkState(0) == Qt.CheckState.Checked
        if entity_id:
            self.set_anonymize.emit([entity_id], checked)
            return
        type_ = item.data(0, Qt.ItemDataRole.UserRole + 1)
        items = [e for e in self.confirmed(self._entities) if e.type == type_]
        active = sum(1 for e in items if e.anonymize)
        # Как в браузерной версии: если включены не все, включаем все; иначе выключаем.
        self.set_anonymize.emit([e.id for e in items], active < len(items))

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        entity_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not entity_id:
            return
        if self._selected == entity_id:
            self._selected = None
            self.actions.hide()
            self.tree.clearSelection()
            return
        self._selected = entity_id
        self._show_actions(entity_id)
        self.select.emit(entity_id)

    def select_entity(self, entity_id: str) -> None:
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            for j in range(group.childCount()):
                child = group.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) == entity_id:
                    group.setExpanded(True)
                    self.tree.setCurrentItem(child)
                    self.tree.scrollToItem(child)
                    self._selected = entity_id
                    self._show_actions(entity_id)
                    return

    def _show_actions(self, entity_id: Optional[str]) -> None:
        entity = next((e for e in self._entities if e.id == entity_id), None)
        if entity is None:
            self.actions.hide()
            return
        self.selected_label.setText(
            f"<b>{html.escape(_truncate(entity_current_value(self._text, entity), 80))}</b> → {entity.placeholder}"
        )
        self.type_box.blockSignals(True)
        self.type_box.setCurrentText(entity.type)
        self.type_box.blockSignals(False)
        same = [e for e in self.confirmed(self._entities) if e.type == entity.type and e.id != entity.id]
        self.merge_box.clear()
        self.merge_box.addItem("объединить с…", "")
        for t in same:
            self.merge_box.addItem(f"{t.placeholder} · {_truncate(entity_current_value(self._text, t), 24)}", t.id)
        self.merge_box.setEnabled(bool(same))
        self.merge_button.setEnabled(bool(same))
        self.actions.show()

    def _on_type(self, type_: str) -> None:
        if self._selected:
            self.change_type.emit(self._selected, type_)

    def _on_delete(self) -> None:
        if self._selected:
            entity_id = self._selected
            self._selected = None
            self.actions.hide()
            self.delete.emit(entity_id)

    def _on_merge(self) -> None:
        target = self.merge_box.currentData()
        if self._selected and target:
            source = self._selected
            self._selected = None
            self.merge.emit(source, target)


class SidePanel(QTabWidget):
    def __init__(self) -> None:
        super().__init__()
        self.review = ReviewPanel()
        self.found = FoundPanel()
        self.addTab(self.review, "Проверка")
        self.addTab(self.found, "Найденные данные")
        self.user_tab: Optional[int] = None  # None — вкладка выбирается автоматически
        self.tabBarClicked.connect(self._remember)

    def _remember(self, index: int) -> None:
        self.user_tab = index

    def render(self, entities: list[Entity], text: str) -> None:
        pending = len(pending_candidates(entities))
        confirmed = len(self.found.confirmed(entities))
        self.setTabText(0, f"Проверка ({pending})" if pending else "Проверка ✓")
        self.setTabText(1, f"Найденные данные ({confirmed})")
        self.review.render(entities, text)
        self.found.render(entities, text)
        target = self.user_tab if self.user_tab is not None else (0 if pending else 1)
        if self.currentIndex() != target:
            self.setCurrentIndex(target)
