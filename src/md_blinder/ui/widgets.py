"""Мелкие виджеты: полоса статуса, тост, уведомления."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QWidget,
)

from ..text_utils import plural


def replaced_summary(values: int, places: int) -> str:
    if values == 0:
        return "Замен пока нет"
    return (
        f"Заменено {plural(values, ('значение', 'значения', 'значений'))} "
        f"в {plural(places, ('месте', 'местах', 'местах'))}"
    )


class StatusStrip(QFrame):
    """Где пользователь в работе с документом и как получить результат (порт StatusStrip.tsx)."""

    show_candidates = Signal()
    export_md = Signal()
    copy_text = Signal()
    export_csv = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        self.steps = QLabel()
        layout.addWidget(self.steps)
        layout.addSpacing(12)
        self.pending = QPushButton()
        self.pending.setObjectName("chipPending")
        self.pending.clicked.connect(self.show_candidates)
        self.ready = QLabel("✓ Готово к выдаче")
        self.ready.setObjectName("chipReady")
        layout.addWidget(self.pending)
        layout.addWidget(self.ready)
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        layout.addWidget(self.summary)
        layout.addStretch(1)

        self.export_button = QToolButton()
        self.export_button.setObjectName("primary")
        self.export_button.setText("Экспортировать .md")
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.export_button.clicked.connect(self.export_md)
        menu = QMenu(self.export_button)
        copy = menu.addAction("Копировать текст — обезличенный Markdown в буфер обмена")
        copy.triggered.connect(self.copy_text)
        self.export_action = menu.addAction("Экспортировать .md")
        self.export_action.triggered.connect(self.export_md)
        menu.addSeparator()
        csv = menu.addAction("Таблица замен (.csv) — содержит исходные данные, не передавайте ее с текстом")
        csv.triggered.connect(self.export_csv)
        self.export_button.setMenu(menu)
        layout.addWidget(self.export_button)

    def update_state(self, unreviewed: int, values: int, places: int, export_name: str) -> None:
        ready = unreviewed == 0
        mark = lambda state, n, label: (  # noqa: E731
            f"<span style='color:#1a7f37'>✓ {label}</span>" if state == "done"
            else f"<b>{n}. {label}</b>" if state == "active"
            else f"<span style='color:#8a93a3'>{n}. {label}</span>"
        )
        self.steps.setText(" → ".join([
            mark("done", 1, "Загрузка"),
            mark("done" if ready else "active", 2, "Проверка"),
            mark("active" if ready else "todo", 3, "Выдача"),
        ]))
        self.pending.setVisible(not ready)
        self.ready.setVisible(ready)
        self.pending.setText(f"⚠ Не проверено: {plural(unreviewed, ('кандидат', 'кандидата', 'кандидатов'))}")
        self.summary.setText(replaced_summary(values, places))
        self.export_action.setText(f"Экспортировать .md — файл «{export_name}»")


class Notice(QFrame):
    def __init__(self, text: str = "", error: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("noticeError" if error else "notice")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.label, 1)
        self.button: Optional[QPushButton] = None

    def add_button(self, text: str, handler: Callable[[], None], link: bool = False) -> QPushButton:
        button = QPushButton(text)
        if link:
            button.setObjectName("link")
        button.clicked.connect(handler)
        self.layout().addWidget(button)
        self.button = button
        return button


class Toast(QFrame):
    """Одно сообщение внизу окна поверх содержимого: не сдвигает разметку."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet("QFrame#card { background: #222831; border-radius: 8px; } QLabel { color: white; }")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 8, 8)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(560)
        layout.addWidget(self.label, 1)
        self.action = QPushButton()
        self.action.setObjectName("link")
        self.action.setStyleSheet("color: #9cc4ff;")
        layout.addWidget(self.action)
        close = QToolButton()
        close.setText("✕")
        close.setAutoRaise(True)
        close.setStyleSheet("color: white;")
        close.clicked.connect(self.hide)
        layout.addWidget(close)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self._handler: Optional[Callable[[], None]] = None
        self.action.clicked.connect(self._run_action)
        self.hide()

    def show_message(
        self, text: str, duration_ms: int = 4000, action: Optional[tuple[str, Callable[[], None]]] = None,
        tone: str = "info",
    ) -> None:
        colors = {"info": "#222831", "warn": "#7a4a00", "error": "#8c1d18"}
        self.setStyleSheet(
            f"QFrame#card {{ background: {colors.get(tone, colors['info'])}; border-radius: 8px; }} QLabel {{ color: white; }}"
        )
        self.label.setText(text)
        if action:
            self.action.setText(action[0])
            self._handler = action[1]
            self.action.show()
        else:
            self._handler = None
            self.action.hide()
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._timer.start(duration_ms)

    def _place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        x = (parent.width() - self.width()) // 2
        y = parent.height() - self.height() - 24
        self.move(max(8, x), max(8, y))

    def _run_action(self) -> None:
        handler = self._handler
        self.hide()
        if handler:
            handler()
