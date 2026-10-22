"""Общие виджеты в стиле веб-версии: меню, шаги, строка статуса, уведомления, тост."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..text_utils import plural
from . import theme as t
from .icons import icon, pixmap


def icon_label(name: str, color: str, size: int = 16) -> QLabel:
    label = QLabel()
    label.setPixmap(pixmap(name, color, size))
    label.setFixedSize(size, size)
    label.setStyleSheet("background: transparent;")
    return label


def set_prop(widget: QWidget, name: str, value) -> None:
    """Меняет свойство для QSS и перерисовывает стиль."""
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def replaced_summary(values: int, places: int) -> str:
    if values == 0:
        return "Замен пока нет"
    return (
        f"Заменено {plural(values, ('значение', 'значения', 'значений'))} "
        f"в {plural(places, ('месте', 'местах', 'местах'))}"
    )


class ElidedLabel(QLabel):
    """Однострочная подпись, которая не раздвигает панель: что не влезает по ширине,
    заменяется многоточием (text-overflow: ellipsis в вебе)."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def elided_text(self) -> str:
        return self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.contentsRect().width())

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        self.style().drawItemText(
            painter, self.contentsRect(), int(self.alignment()), self.palette(), self.isEnabled(),
            self.elided_text(), self.foregroundRole(),
        )


class Select(QComboBox):
    """Выпадающий список в стиле меню программы.

    У QComboBox в стиле Fusion список открывается поверх поля, со стрелками
    прокрутки и системной рамкой. Здесь список раскрывается под полем как
    PopupMenu: та же рамка, скругление и подсветка пункта. Пункты рисует
    QStyledItemDelegate, иначе таблица стилей к ним не применяется.
    """

    # Все типы сущностей помещаются без прокрутки.
    MAX_VISIBLE = 16

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setItemDelegate(QStyledItemDelegate(self))
        self.setMaxVisibleItems(self.MAX_VISIBLE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        view = self.view()
        view.setObjectName("selectList")
        view.setVerticalScrollMode(view.ScrollMode.ScrollPerPixel)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        popup = view.window()
        popup.setWindowFlags(popup.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        # Рамку рисует сам список (QListView#selectList в теме): контейнер — внутренний
        # класс Qt и рамку из таблицы стилей не рисует.
        if popup.layout() is not None:
            popup.layout().setContentsMargins(0, 0, 0, 0)

    def showPopup(self) -> None:  # noqa: N802
        super().showPopup()
        # Список встает под полем, а не поверх него, и не уже поля.
        popup = self.view().window()
        width = max(self.width(), self.view().sizeHintForColumn(0) + 28)
        popup.resize(width, popup.height())
        popup.move(self.mapToGlobal(QPoint(0, self.height() + 4)))

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        arrow = pixmap("chevron-down", t.INK_3 if self.isEnabled() else t.LINE_2, 14)
        size = arrow.deviceIndependentSize()
        x = self.width() - size.width() - 8
        y = (self.height() - size.height()) / 2
        painter.drawPixmap(QPoint(round(x), round(y)), arrow)
        painter.end()


class PopupMenu(QFrame):
    """Раскрывающееся меню как Menu.tsx: пункты с заголовком и подсказкой, заметки, разделители."""

    def __init__(self, parent: QWidget, width: int = 340) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setObjectName("menuPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setFixedWidth(width)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(6, 6, 6, 6)
        self.box.setSpacing(0)

    def clear(self) -> None:
        while self.box.count():
            item = self.box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

    def add_item(
        self, title: str, handler: Callable[[], None], hint: str = "", icon_name: Optional[str] = None,
        current: bool = False, sensitive: bool = False, enabled: bool = True,
    ) -> QPushButton:
        button = QPushButton()
        button.setObjectName("menuItem")
        button.setProperty("current", "true" if current else "false")
        button.setEnabled(enabled)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(button)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(10)
        if icon_name:
            ic = icon_label(icon_name, t.WARN if sensitive else t.INK_2)
            col_icon = QVBoxLayout()
            col_icon.setContentsMargins(0, 3, 0, 0)
            col_icon.addWidget(ic)
            col_icon.addStretch(1)
            row.addLayout(col_icon)
        col = QVBoxLayout()
        col.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("menuTitle")
        col.addWidget(title_label)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("menuHintWarn" if sensitive else "menuHint")
            hint_label.setWordWrap(True)
            col.addWidget(hint_label)
        row.addLayout(col, 1)
        for child in button.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        def run() -> None:
            self.hide()
            handler()

        button.clicked.connect(run)
        self.box.addWidget(button)
        return button

    def add_separator(self) -> None:
        line = QFrame()
        line.setObjectName("divider")
        line.setFixedHeight(1)
        wrap = QVBoxLayout()
        wrap.setContentsMargins(0, 6, 0, 6)
        wrap.addWidget(line)
        self.box.addLayout(wrap)

    def add_note(self, text: str, warn: bool = False) -> None:
        label = QLabel(text)
        label.setObjectName("menuHintWarn" if warn else "menuHint")
        label.setWordWrap(True)
        label.setContentsMargins(10, 4, 10, 8)
        self.box.addWidget(label)

    def add_row(self, buttons: list[tuple[str, Callable[[], None], bool]]) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 4)
        row.setSpacing(6)
        for text, handler, enabled in buttons:
            b = QPushButton(text)
            b.setObjectName("small")
            b.setEnabled(enabled)

            def run(_=False, h=handler) -> None:
                self.hide()
                h()

            b.clicked.connect(run)
            row.addWidget(b)
        row.addStretch(1)
        self.box.addLayout(row)

    def show_under(self, anchor: QWidget, align_right: bool = True) -> None:
        self.adjustSize()
        pos = anchor.mapToGlobal(QPoint(0, anchor.height() + 6))
        if align_right:
            pos.setX(pos.x() + anchor.width() - self.width())
        self.move(pos)
        self.show()


class StepIndicator(QWidget):
    """Шаги «Загрузка → Проверка → Выдача» с кружками и линиями (.status-steps)."""

    LABELS = ("Загрузка", "Проверка", "Выдача")

    def __init__(self) -> None:
        super().__init__()
        self.states = ["done", "active", "todo"]
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._normal = t.font(t.SANS, 13.5)
        self._bold = t.font(t.SANS, 13.5, 600)
        self._mark = t.font(t.SANS, 12, 600)

    def set_states(self, states: list[str]) -> None:
        self.states = states
        self.updateGeometry()
        self.update()

    def _layout(self) -> list[tuple[int, int, int]]:
        from PySide6.QtGui import QFontMetrics

        x = 0
        out = []
        for i, label in enumerate(self.LABELS):
            if i > 0:
                x += 10 + 24 + 2  # промежуток и линия между шагами
            fm = QFontMetrics(self._bold if self.states[i] == "active" else self._normal)
            width = 22 + 8 + fm.horizontalAdvance(label)
            out.append((x, width, fm.horizontalAdvance(label)))
            x += width
        return out

    def sizeHint(self) -> QSize:  # noqa: N802
        parts = self._layout()
        last = parts[-1]
        return QSize(last[0] + last[1] + 2, 26)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cy = self.height() / 2
        parts = self._layout()
        for i, (x, _width, _tw) in enumerate(parts):
            state = self.states[i]
            if i > 0:
                painter.setPen(QPen(QColor(t.LINE_2), 1))
                painter.drawLine(int(x - 26), int(cy), int(x - 2), int(cy))
            circle = QRectF(x + 0.75, cy - 10.25, 20.5, 20.5)
            if state == "done":
                painter.setPen(QPen(QColor(t.READY), 1.5))
                painter.setBrush(QColor(t.READY))
                painter.drawEllipse(circle)
                painter.drawPixmap(int(x + 5), int(cy - 6), pixmap("check", "#ffffff", 12))
            elif state == "active":
                painter.setPen(QPen(QColor(t.ACCENT), 1.5))
                painter.setBrush(QColor(t.ACCENT))
                painter.drawEllipse(circle)
                painter.setPen(QColor(t.ACCENT_INK))
                painter.setFont(self._mark)
                painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, str(i + 1))
            else:
                painter.setPen(QPen(QColor(t.LINE_2), 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(circle)
                painter.setPen(QColor(t.INK_3))
                painter.setFont(self._mark)
                painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, str(i + 1))
            painter.setFont(self._bold if state == "active" else self._normal)
            painter.setPen(QColor(t.INK if state == "active" else t.INK_3))
            painter.drawText(QRectF(x + 30, 0, 200, self.height()), Qt.AlignmentFlag.AlignVCenter, self.LABELS[i])
        painter.end()


class StatusStrip(QWidget):
    """Где пользователь в работе с документом и как получить результат (StatusStrip.tsx)."""

    show_candidates = Signal()
    export_md = Signal()
    copy_text = Signal()
    export_csv = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        self.steps = StepIndicator()
        layout.addWidget(self.steps)
        info = QHBoxLayout()
        info.setSpacing(12)
        self.pending = QPushButton()
        self.pending.setObjectName("chipPending")
        self.pending.setIcon(icon("alert", t.PENDING, 14))
        self.pending.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pending.clicked.connect(self.show_candidates)
        self.ready = QPushButton("Готово к выдаче")
        self.ready.setObjectName("chipPending")
        self.ready.setStyleSheet(
            f"QPushButton {{ background: {t.READY_WASH}; border: 1px solid {t.READY_LINE}; color: {t.READY}; }}"
        )
        self.ready.setIcon(icon("check", t.READY, 14))
        self.ready.setEnabled(True)
        self.ready.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.ready.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        info.addWidget(self.pending)
        info.addWidget(self.ready)
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        info.addWidget(self.summary)
        info.addStretch(1)
        layout.addLayout(info, 1)

        split = QHBoxLayout()
        split.setSpacing(0)
        self.export_button = QPushButton("Экспортировать .md")
        self.export_button.setObjectName("primary")
        self.export_button.setProperty("class", "splitMain")
        self.export_button.setStyleSheet("border-top-right-radius: 0; border-bottom-right-radius: 0;")
        self.export_button.setIcon(icon("download", t.ACCENT_INK, 16))
        self.export_button.clicked.connect(self.export_md)
        self.toggle = QPushButton()
        self.toggle.setObjectName("primary")
        self.toggle.setStyleSheet(
            "border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: 1px solid rgba(255,255,255,90);"
            " padding-left: 9px; padding-right: 9px;"
        )
        self.toggle.setIcon(icon("chevron-down", t.ACCENT_INK, 14))
        self.toggle.setToolTip("Другие варианты выдачи")
        self.toggle.clicked.connect(self._open_menu)
        split.addWidget(self.export_button)
        split.addWidget(self.toggle)
        layout.addLayout(split)
        self.menu = PopupMenu(self, 340)
        self._export_name = "document.md"

    def _open_menu(self) -> None:
        m = self.menu
        m.clear()
        m.add_item("Копировать текст", self.copy_text.emit, "Обезличенный Markdown в буфер обмена", "copy")
        m.add_item("Экспортировать .md", self.export_md.emit, f"Файл «{self._export_name}»", "download")
        m.add_separator()
        m.add_item(
            "Таблица замен (.csv)", self.export_csv.emit,
            "Содержит исходные данные. Не передавайте ее вместе с обезличенным текстом.", "table", sensitive=True,
        )
        m.show_under(self.toggle)

    def update_state(self, unreviewed: int, values: int, places: int, export_name: str) -> None:
        ready = unreviewed == 0
        self.steps.set_states(["done", "done" if ready else "active", "active" if ready else "todo"])
        self.pending.setVisible(not ready)
        self.ready.setVisible(ready)
        self.pending.setText(f"Не проверено: {plural(unreviewed, ('кандидат', 'кандидата', 'кандидатов'))}")
        self.summary.setText(replaced_summary(values, places))
        self._export_name = export_name


class Notice(QFrame):
    """Уведомление под строкой статуса (.ner-notice, .image-notice, .collision-notice)."""

    def __init__(self, text: str = "", error: bool = False, plain: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("plainNotice" if plain else "noticeError" if error else "notice")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0 if plain else 14, 0 if plain else 10, 0 if plain else 14, 0 if plain else 10)
        layout.setSpacing(8)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.label, 1)
        self.button: Optional[QPushButton] = None
        self.slot: Optional[QWidget] = None  # обертка с отступом сверху, скрывается вместе с уведомлением

    def setVisible(self, visible: bool) -> None:  # noqa: N802
        super().setVisible(visible)
        if self.slot is not None:
            self.slot.setVisible(visible)

    def add_button(self, text: str, handler: Callable[[], None], link: bool = False) -> QPushButton:
        button = QPushButton(text)
        if link:
            button.setObjectName("link")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(handler)
        self.layout().addWidget(button)
        self.button = button
        return button


class Toast(QFrame):
    """Одно уведомление внизу окна поверх содержимого (Toast.tsx)."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 10, 10)
        layout.setSpacing(12)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(520)
        layout.addWidget(self.label, 1)
        self.action = QPushButton()
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.action)
        self.close_button = QPushButton("✕")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.hide)
        layout.addWidget(self.close_button)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self._handler: Optional[Callable[[], None]] = None
        self.action.clicked.connect(self._run_action)
        self._apply_style("info")
        t.add_shadow(self, pop=True)
        self.hide()

    def _apply_style(self, tone: str) -> None:
        border = {"warn": t.WARN, "error": t.DANGER}.get(tone)
        left = f"border-left: 4px solid {border};" if border else ""
        self.setStyleSheet(
            f"QFrame#toast {{ background: {t.INK}; border-radius: {t.RADIUS_SM}px; {left} }}"
            f"QFrame#toast QLabel {{ color: {t.PAPER}; font-size: 14px; background: transparent; }}"
            f"QFrame#toast QPushButton {{ background: transparent; color: {t.PAPER}; font-size: 13px;"
            f" border: 1px solid rgba(244,243,238,100); padding: 4px 10px; border-radius: {t.RADIUS_SM}px; }}"
            f"QFrame#toast QPushButton:hover {{ background: rgba(244,243,238,36); border-color: {t.PAPER}; }}"
        )
        self.close_button.setStyleSheet(
            f"QPushButton {{ border: none; color: rgba(244,243,238,180); padding: 2px 6px; background: transparent; }}"
            f"QPushButton:hover {{ color: {t.PAPER}; background: transparent; }}"
        )

    def show_message(
        self, text: str, duration_ms: int = 4000, action: Optional[tuple[str, Callable[[], None]]] = None,
        tone: str = "info",
    ) -> None:
        self._apply_style(tone)
        self.label.setText(text)
        if action:
            self.action.setText(action[0])
            self._handler = action[1]
            self.action.show()
        else:
            self._handler = None
            self.action.hide()
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
