"""Стартовый экран: приватность, выбор файла, история (порт стартовой части App.tsx и HistoryPanel.tsx)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..storage.history import HistoryDocMeta
from ..text_utils import plural

SUPPORTED = (".docx", ".pdf", ".doc")


def dropped_file(event) -> Optional[str]:
    urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
    for url in urls:
        if url.isLocalFile():
            return url.toLocalFile()
    return None


class DropZone(QFrame):
    clicked = Signal()
    file_dropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(150)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("<b>Перетащите .docx или .pdf сюда или нажмите, чтобы выбрать</b>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("Поддерживаются текстовые и сканированные PDF (распознавание через OCR)")
        hint.setObjectName("muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_label = QLabel()
        self.file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.file_label)
        self.enabled_ = True

    def set_busy(self, busy: bool) -> None:
        self.enabled_ = not busy
        self.setCursor(Qt.CursorShape.BusyCursor if busy else Qt.CursorShape.PointingHandCursor)

    def _set_dragging(self, value: bool) -> None:
        self.setProperty("dragging", "true" if value else "false")
        self.style().unpolish(self)
        self.style().polish(self)

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
            self._set_dragging(True)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_dragging(False)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._set_dragging(False)
        path = dropped_file(event)
        if self.enabled_ and path:
            event.acceptProposedAction()
            self.file_dropped.emit(path)


class LoadingBlock(QFrame):
    cancel = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.hint = QLabel()
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        button = QPushButton("Отменить обработку")
        button.clicked.connect(self.cancel)
        layout.addWidget(self.label)
        layout.addWidget(self.hint)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)

    def set_status(self, label: str, hint: Optional[str]) -> None:
        self.label.setText(f"⏳ {label}")
        self.hint.setText(hint or "")
        self.hint.setVisible(bool(hint))


class HistoryPanel(QFrame):
    open_doc = Signal(str)
    delete_doc = Signal(str)
    export_history = Signal()
    import_history = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title = QLabel()
        header.addWidget(self.title)
        header.addStretch(1)
        self.export_button = QPushButton("Экспортировать историю")
        self.export_button.clicked.connect(self.export_history)
        import_button = QPushButton("Импортировать историю")
        import_button.clicked.connect(self.import_history)
        header.addWidget(self.export_button)
        header.addWidget(import_button)
        layout.addLayout(header)
        warning = QLabel(
            "История хранится на этом компьютере вместе с исходным текстом, без шифрования. "
            "Экспорт истории тоже содержит исходные данные."
        )
        warning.setWordWrap(True)
        warning.setObjectName("muted")
        layout.addWidget(warning)
        self.error = QLabel()
        self.error.setStyleSheet("color: #b42318;")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        self.rows = QVBoxLayout()
        layout.addLayout(self.rows)

    def set_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def render(self, docs: list[HistoryDocMeta], current_id: Optional[str]) -> None:
        self.title.setText(f"<b>История ({len(docs)})</b>")
        self.export_button.setEnabled(bool(docs))
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not docs:
            empty = QLabel("История пуста.")
            empty.setObjectName("muted")
            self.rows.addWidget(empty)
            return
        for doc in docs:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 2, 0, 2)
            name = QLabel(f"<b>{doc.name}</b>")
            name.setToolTip(doc.name)
            when = dt.datetime.fromtimestamp(doc.updated_at / 1000).strftime("%d.%m.%Y %H:%M")
            meta = QLabel(f"{when} · {plural(doc.entity_count, ('находка', 'находки', 'находок'))} · {doc.format}")
            meta.setObjectName("muted")
            line.addWidget(name, 1)
            line.addWidget(meta)
            open_button = QPushButton("Открыт" if doc.id == current_id else "Открыть")
            open_button.clicked.connect(lambda _=False, i=doc.id: self.open_doc.emit(i))
            delete_button = QPushButton("✕")
            delete_button.setToolTip(f"Удалить «{doc.name}»")
            delete_button.setFixedWidth(32)
            delete_button.clicked.connect(lambda _=False, i=doc.id: self.delete_doc.emit(i))
            line.addWidget(open_button)
            line.addWidget(delete_button)
            self.rows.addWidget(row)


class StartScreen(QScrollArea):
    pick_file = Signal()
    file_dropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("page")
        outer = QHBoxLayout(body)
        column = QVBoxLayout()
        column.setSpacing(12)
        container = QWidget()
        container.setLayout(column)
        container.setMaximumWidth(860)
        outer.addWidget(container)
        self.setWidget(body)

        title = QLabel("MD Blinder")
        title.setObjectName("title")
        column.addWidget(title)
        column.addWidget(QLabel("Конвертация договоров (.docx, .pdf) в Markdown с обезличиванием."))
        for text in (
            "<b>Документы не покидают компьютер.</b> Конвертация, распознавание сканов и поиск персональных "
            "данных выполняются локально.",
            "<b>Первый запуск дольше.</b> Программа предложит скачать модель распознавания имен (около 580 МБ), "
            "а для сканов — языковые файлы OCR (около 8 МБ). Это публичные файлы, текст документов не отправляется.",
        ):
            fact = QLabel(text)
            fact.setWordWrap(True)
            frame = QFrame()
            frame.setObjectName("fact")
            box = QVBoxLayout(frame)
            box.addWidget(fact)
            column.addWidget(frame)

        self.dropzone = DropZone()
        self.dropzone.clicked.connect(self.pick_file)
        self.dropzone.file_dropped.connect(self.file_dropped)
        column.addWidget(self.dropzone)
        self.loading = LoadingBlock()
        self.loading.hide()
        column.addWidget(self.loading)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #b42318;")
        self.error.hide()
        column.addWidget(self.error)
        self.history = HistoryPanel()
        column.addWidget(self.history)
        column.addStretch(1)

    def set_file_name(self, name: str) -> None:
        self.dropzone.file_label.setText(f"Файл: {Path(name).name}" if name else "")

    def set_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))
