"""Поиск по тексту исходника (Ctrl+F) и замена всех найденных совпадений одной меткой.

Нужен, чтобы найти пропущенное имя или номер, не прокручивая длинный договор: все
совпадения подсвечиваются в тексте, Enter и Shift+Enter переходят между ними, а
кнопка «Заменить все» добавляет их в замены выбранного типа.
"""

from __future__ import annotations

from typing import Optional

import regex
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit

from ..anonymize.image_safety import mask_images_for_detection
from ..anonymize.types import ALL_ENTITY_TYPES, Position
from . import theme as t
from .icons import icon
from .widgets import Select, make_button

MAX_MATCHES = 2000


def find_matches(text: str, query: str) -> list[Position]:
    """Совпадения без учета регистра. Пробелы запроса совпадают с любыми пробелами и
    переносами строк: имя в договоре бывает разорвано переносом. Картинки не ищутся."""
    words = query.split()
    if not words:
        return []
    pattern = regex.compile(r"\s+".join(regex.escape(w) for w in words), regex.IGNORECASE | regex.V0)
    searchable = mask_images_for_detection(text)
    matches: list[Position] = []
    for m in pattern.finditer(searchable):
        if m.end() > m.start():
            matches.append(Position(m.start(), m.end()))
            if len(matches) >= MAX_MATCHES:
                break
    return matches


class _QueryEdit(QLineEdit):
    step = Signal(int)
    closed = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.step.emit(-1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
            return
        if event.key() == Qt.Key.Key_Escape:
            self.closed.emit()
            return
        super().keyPressEvent(event)


class SearchBar(QFrame):
    """Строка поиска над текстом исходника. Сама ищет; что подсветить и куда
    прокрутить, сообщает сигналом changed (совпадения и номер текущего)."""

    changed = Signal(object, object, bool)  # совпадения, номер текущего, прокрутить ли к нему
    replace_all = Signal(object, str)  # list[Position], тип
    closed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("searchBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)
        self.query = _QueryEdit()
        self.query.setPlaceholderText("Найти в тексте")
        self.query.addAction(icon("search", t.INK_3, 16), QLineEdit.ActionPosition.LeadingPosition)
        self.query.setClearButtonEnabled(True)
        self.count = QLabel()
        self.count.setObjectName("muted")
        self.count.setMinimumWidth(64)
        self.prev_button = make_button(kind="iconButton", small=True, icon_name="chevron-up", tooltip="Предыдущее (Shift+Enter)")
        self.next_button = make_button(kind="iconButton", small=True, icon_name="chevron-down", tooltip="Следующее (Enter)")
        self.type_box = Select()
        self.type_box.setObjectName("compact")
        self.type_box.addItems(list(ALL_ENTITY_TYPES))
        self.type_box.setToolTip("Тип замены для найденного")
        self.replace_button = make_button("Заменить все", kind="primary", small=True)
        self.replace_button.setToolTip("Добавить все найденные совпадения в замены выбранного типа")
        close = make_button(kind="iconButton", small=True, icon_name="x", tooltip="Закрыть поиск (Esc)")
        row.addWidget(self.query, 1)
        row.addWidget(self.count)
        row.addWidget(self.prev_button)
        row.addWidget(self.next_button)
        row.addWidget(self.type_box)
        row.addWidget(self.replace_button)
        row.addWidget(close)
        self._text = ""
        self.matches: list[Position] = []
        self.current: Optional[int] = None
        self.query.textChanged.connect(self._search)
        self.query.step.connect(self.step)
        self.query.closed.connect(self.close_bar)
        self.prev_button.clicked.connect(lambda: self.step(-1))
        self.next_button.clicked.connect(lambda: self.step(1))
        self.replace_button.clicked.connect(self._replace_all)
        close.clicked.connect(self.close_bar)
        self.hide()
        self._sync()

    def open_with(self, text: str, query: str = "", type_: Optional[str] = None) -> None:
        self._text = text
        if type_:
            self.type_box.setCurrentText(type_)
        self.show()
        if query:
            self.query.setText(query)
        else:
            self._search()
        self.query.setFocus()
        self.query.selectAll()

    def set_text(self, text: str) -> None:
        """Текст документа изменился (правка, другой документ): совпадения ищутся заново."""
        self._text = text
        if self.isVisible():
            self._search(keep_position=True)

    def close_bar(self) -> None:
        self.hide()
        self.matches, self.current = [], None
        self.changed.emit([], None, False)
        self.closed.emit()

    def step(self, delta: int) -> None:
        if not self.matches:
            return
        self.current = 0 if self.current is None else (self.current + delta) % len(self.matches)
        self._sync()
        self.changed.emit(self.matches, self.current, True)

    def _search(self, *_args, keep_position: bool = False) -> None:
        previous = self.matches[self.current].start if keep_position and self.current is not None else None
        self.matches = find_matches(self._text, self.query.text())
        if not self.matches:
            self.current = None
        elif previous is not None:
            self.current = next((i for i, m in enumerate(self.matches) if m.start >= previous), 0)
        else:
            self.current = 0
        self._sync()
        # После правки разметки текст не прокручивается: пользователь мог уйти к другому месту.
        self.changed.emit(self.matches, self.current, not keep_position)

    def _sync(self) -> None:
        has_query = bool(self.query.text().strip())
        if not has_query:
            self.count.setText("")
        elif not self.matches:
            self.count.setText("Не найдено")
        else:
            more = "+" if len(self.matches) >= MAX_MATCHES else ""
            self.count.setText(f"{(self.current or 0) + 1} из {len(self.matches)}{more}")
        for button in (self.prev_button, self.next_button, self.replace_button):
            button.setEnabled(bool(self.matches))

    def _replace_all(self) -> None:
        if self.matches:
            self.replace_all.emit(list(self.matches), self.type_box.currentText())
