"""Стартовый экран: приватность, выбор файла, история (стартовая часть App.tsx и HistoryPanel.tsx)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..storage.history import HistoryDocMeta
from ..text_utils import plural
from . import theme as t
from .icons import pixmap
from .widgets import icon_label, set_prop

MAX_WIDTH = 1320  # .app: max-width 1360px минус поля


def dropped_files(event) -> list[str]:
    urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
    return [url.toLocalFile() for url in urls if url.isLocalFile()]


def dropped_file(event) -> Optional[str]:
    files = dropped_files(event)
    return files[0] if files else None


class DropZone(QFrame):
    clicked = Signal()
    file_dropped = Signal(list)  # пути файлов

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("upload")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        t.add_shadow(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 44, 24, 36)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        circle = QLabel()
        circle.setObjectName("dropIcon")
        circle.setFixedSize(44, 44)
        circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        circle.setPixmap(pixmap("upload", t.ACCENT, 22))
        layout.addWidget(circle, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(10)
        title = QLabel("Перетащите .docx или .pdf сюда или нажмите, чтобы выбрать")
        title.setStyleSheet("font-size: 15.5px; font-weight: 500; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("Поддерживаются текстовые и сканированные PDF (распознавание через OCR)")
        hint.setStyleSheet(f"font-size: 13px; color: {t.INK_3}; background: transparent;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        batch_hint = QLabel(
            "Несколько файлов сразу обрабатываются пакетом: одна и та же сторона получает одинаковую метку "
            "во всех документах"
        )
        batch_hint.setStyleSheet(f"font-size: 13px; color: {t.INK_3}; background: transparent;")
        batch_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        batch_hint.setWordWrap(True)
        self.file_label = QLabel()
        self.file_label.setStyleSheet("font-weight: 600; background: transparent;")
        self.file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_label.hide()
        layout.addWidget(title)
        layout.addSpacing(6)
        layout.addWidget(hint)
        layout.addSpacing(4)
        layout.addWidget(batch_hint)
        layout.addSpacing(8)
        layout.addWidget(self.file_label)
        self.enabled_ = True

    def set_busy(self, busy: bool) -> None:
        self.enabled_ = not busy
        self.setCursor(Qt.CursorShape.BusyCursor if busy else Qt.CursorShape.PointingHandCursor)
        self.setEnabled(not busy)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.enabled_ and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self.enabled_ and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self.enabled_ and dropped_file(event):
            event.acceptProposedAction()
            set_prop(self, "dragging", "true")

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        set_prop(self, "dragging", "false")

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        set_prop(self, "dragging", "false")
        paths = dropped_files(event)
        if self.enabled_ and paths:
            event.acceptProposedAction()
            self.file_dropped.emit(paths)


class Spinner(QWidget):
    """Кольцо загрузки (.spinner)."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(16, 16)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)

    def _tick(self) -> None:
        self._angle = (self._angle + 14) % 360
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        from PySide6.QtGui import QColor, QPainter, QPen

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(t.LINE), 2))
        p.drawEllipse(1, 1, 14, 14)
        p.setPen(QPen(QColor(t.ACCENT), 2))
        p.drawArc(1, 1, 14, 14, (90 - self._angle) * 16, -90 * 16)
        p.end()


class LoadingBlock(QWidget):
    cancel = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(Spinner())
        self.label = QLabel()
        self.label.setWordWrap(True)
        row.addWidget(self.label, 1)
        layout.addLayout(row)
        self.hint = QLabel()
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        button = QPushButton("Отменить обработку")
        button.clicked.connect(self.cancel)
        layout.addSpacing(8)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)

    def set_status(self, label: str, hint: Optional[str]) -> None:
        self.label.setText(label)
        self.hint.setText(hint or "")
        self.hint.setVisible(bool(hint))


class HistoryPanel(QWidget):
    open_doc = Signal(str)
    delete_doc = Signal(str)
    export_history = Signal()
    import_history = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 24, 0, 0)
        layout.setSpacing(0)
        header = QHBoxLayout()
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 16px; font-weight: 600;")
        header.addWidget(self.title)
        header.addStretch(1)
        self.export_button = QPushButton("Экспортировать историю")
        self.export_button.setStyleSheet("padding: 6px 12px; font-size: 13px;")
        self.export_button.clicked.connect(self.export_history)
        import_button = QPushButton("Импортировать историю")
        import_button.setStyleSheet("padding: 6px 12px; font-size: 13px;")
        import_button.clicked.connect(self.import_history)
        header.addWidget(self.export_button)
        header.addWidget(import_button)
        layout.addLayout(header)
        warning = QLabel(
            "История хранится на этом компьютере вместе с исходным текстом, без шифрования. "
            "Экспорт истории тоже содержит исходные данные."
        )
        warning.setWordWrap(True)
        warning.setObjectName("warnText")
        warning.setContentsMargins(0, 6, 0, 4)
        layout.addWidget(warning)
        self.error = QLabel()
        self.error.setObjectName("errorText")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(0)
        layout.addLayout(self.rows)

    def set_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def render(self, docs: list[HistoryDocMeta], current_id: Optional[str]) -> None:
        self.title.setText(f"История ({len(docs)})")
        self.export_button.setEnabled(bool(docs))
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not docs:
            empty = QLabel("История пуста.")
            empty.setObjectName("muted")
            empty.setContentsMargins(0, 8, 0, 0)
            self.rows.addWidget(empty)
            return
        batch_sizes: dict[str, int] = {}
        for doc in docs:
            if doc.batch_id:
                batch_sizes[doc.batch_id] = batch_sizes.get(doc.batch_id, 0) + 1
        for doc in docs:
            row = QFrame()
            row.setObjectName("historyRow")
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 8, 0, 8)
            line.setSpacing(12)
            name = QLabel(doc.name)
            name.setStyleSheet("font-weight: 600; background: transparent;")
            name.setToolTip(doc.name)
            name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            when = dt.datetime.fromtimestamp(doc.updated_at / 1000).strftime("%d.%m.%Y, %H:%M")
            details = f"{when} · {plural(doc.entity_count, ('находка', 'находки', 'находок'))} · {doc.format}"
            if doc.batch_id:
                size = batch_sizes.get(doc.batch_id, 1)
                details += f" · пакет из {plural(size, ('документа', 'документов', 'документов'))}"
            meta = QLabel(details)
            meta.setStyleSheet(f"color: {t.INK_3}; font-size: 12px; background: transparent;")
            line.addWidget(name, 1)
            line.addWidget(meta)
            open_button = QPushButton("Открыт" if doc.id == current_id else "Открыть")
            open_button.setStyleSheet("padding: 4px 10px; font-size: 13px;")
            open_button.clicked.connect(lambda _=False, i=doc.id: self.open_doc.emit(i))
            delete_button = QPushButton("✕")
            delete_button.setObjectName("ghost")
            delete_button.setToolTip(f"Удалить «{doc.name}»")
            delete_button.clicked.connect(lambda _=False, i=doc.id: self.delete_doc.emit(i))
            line.addWidget(open_button)
            line.addWidget(delete_button)
            self.rows.addWidget(row)


def _fact(icon_name: str, bold: str, text: str, privacy: bool) -> QFrame:
    frame = QFrame()
    frame.setObjectName("factPrivacy" if privacy else "fact")
    row = QHBoxLayout(frame)
    row.setContentsMargins(16, 14, 16, 14)
    row.setSpacing(12)
    col = QVBoxLayout()
    col.setContentsMargins(0, 2, 0, 0)
    col.addWidget(icon_label(icon_name, t.READY if privacy else t.INK_3, 18))
    col.addStretch(1)
    row.addLayout(col)
    label = QLabel(f"<b style='color:{t.INK}'>{bold}</b> {text}")
    label.setWordWrap(True)
    label.setStyleSheet("background: transparent;")
    row.addWidget(label, 1)
    return frame


class StartScreen(QScrollArea):
    pick_file = Signal()
    file_dropped = Signal(list)  # пути файлов

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("page")
        outer = QHBoxLayout(body)
        outer.setContentsMargins(20, 32, 20, 64)
        container = QWidget()
        container.setMaximumWidth(MAX_WIDTH)
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        outer.addWidget(container)
        self.setWidget(body)

        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        mark = QLabel()
        mark.setPixmap(pixmap("mark", t.INK, 26))
        title_row.addWidget(mark)
        title = QLabel("MD Blinder")
        title.setObjectName("title")
        title_row.addWidget(title)
        title_row.addStretch(1)
        column.addLayout(title_row)
        column.addSpacing(4)
        subtitle = QLabel("Конвертация договоров (.docx, .pdf) в Markdown с обезличиванием.")
        subtitle.setObjectName("subtitle")
        column.addWidget(subtitle)
        column.addSpacing(20)

        facts = QGridLayout()
        facts.setSpacing(12)
        facts.addWidget(_fact(
            "shield", "Документы не покидают компьютер.",
            "Конвертация, распознавание сканов и поиск персональных данных выполняются на этом компьютере.", True,
        ), 0, 0)
        facts.addWidget(_fact(
            "download", "Первый запуск дольше.",
            "Программа предложит скачать модель распознавания имен (около 580 МБ), а для сканов — языковые "
            "файлы OCR (около 8 МБ). Это публичные файлы, текст документов не отправляется.", False,
        ), 0, 1)
        facts.setColumnStretch(0, 1)
        facts.setColumnStretch(1, 1)
        column.addLayout(facts)
        column.addSpacing(20)

        self.dropzone = DropZone()
        self.dropzone.clicked.connect(self.pick_file)
        self.dropzone.file_dropped.connect(self.file_dropped)
        column.addWidget(self.dropzone)
        self.loading = LoadingBlock()
        self.loading.hide()
        column.addWidget(self.loading)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {t.DANGER}; margin-top: 16px;")
        self.error.hide()
        column.addWidget(self.error)
        self.history = HistoryPanel()
        column.addWidget(self.history)
        column.addStretch(1)
        self.column = column

    def add_footer(self, widget: QWidget) -> None:
        self.column.addWidget(widget)

    def set_file_name(self, name: str) -> None:
        """Имя файла или готовая подпись вроде «3 файла»."""
        label = f"Файл: {Path(name).name}" if Path(name).suffix else name
        self.dropzone.file_label.setText(label if name else "")
        self.dropzone.file_label.setVisible(bool(name))

    def set_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))
