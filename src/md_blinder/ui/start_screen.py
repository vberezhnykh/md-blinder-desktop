"""Стартовый экран: состояние моделей, выбор файлов, история с пакетами документов."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..storage.history import HistoryDocMeta
from ..text_utils import plural
from . import theme as t
from .icons import icon, pixmap
from .model_status import ModelStatusBar
from .widgets import ElidedLabel, Notice, icon_label, make_button, set_prop

MAX_WIDTH = 1040  # ширина колонки стартового экрана
HISTORY_VISIBLE = 10  # столько строк истории видно, пока список не раскрыт


def dropped_files(event) -> list[str]:
    urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
    return [url.toLocalFile() for url in urls if url.isLocalFile()]


def dropped_file(event) -> Optional[str]:
    files = dropped_files(event)
    return files[0] if files else None


FILE_TYPE_TAGS = {"docx": "DOC", "pdf": "PDF", "xlsx": "XLS"}


class DropZone(QFrame):
    clicked = Signal()
    pick_folder = Signal()
    file_dropped = Signal(list)  # пути файлов

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("upload")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 32, 24, 32)
        layout.setSpacing(0)
        circle = QLabel()
        circle.setObjectName("dropIcon")
        circle.setFixedSize(40, 40)
        circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        circle.setPixmap(pixmap("upload", t.ACCENT, 20))
        layout.addWidget(circle, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(12)
        title = QLabel("Перетащите файлы или папку сюда")
        title.setObjectName("sectionTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel(
            ".docx, .xlsx, .pdf, в том числе сканы. Несколько файлов или папка, в том числе с вложенными "
            "папками, обрабатываются пакетом: одна и та же сторона получает одинаковую метку во всех документах."
        )
        hint.setObjectName("secondary")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        self.file_label = QLabel()
        self.file_label.setObjectName("strong")
        self.file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_label.hide()
        layout.addWidget(title)
        layout.addSpacing(8)
        # Перенос строк работает, только если подпись растягивается по ширине строки раскладки.
        hint_row = QHBoxLayout()
        hint_row.addStretch(1)
        hint_row.addWidget(hint, 6)
        hint_row.addStretch(1)
        layout.addLayout(hint_row)
        layout.addSpacing(16)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.files_button = make_button("Выбрать файлы", kind="primary", icon_name="file", tooltip="Ctrl+O")
        self.files_button.clicked.connect(self.clicked)
        self.folder_button = make_button(
            "Выбрать папку", icon_name="folder",
            tooltip="Все файлы .docx, .xlsx и .pdf из папки и ее подпапок обрабатываются одним пакетом (Ctrl+Shift+O)",
        )
        self.folder_button.clicked.connect(self.pick_folder)
        buttons.addWidget(self.files_button)
        buttons.addWidget(self.folder_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
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
    """Карточка обработки: что происходит сейчас, подсказка и «Отменить»."""

    cancel = Signal()

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 16, 0, 0)
        card = QFrame()
        card.setObjectName("progressCard")
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 14, 14)
        layout.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(Spinner())
        self.label = QLabel()
        self.label.setObjectName("strong")
        self.label.setWordWrap(True)
        row.addWidget(self.label, 1)
        button = make_button("Отменить", small=True, tooltip="Отменить обработку")
        button.clicked.connect(self.cancel)
        row.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(row)
        self.hint = QLabel()
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        self.hint.setContentsMargins(26, 0, 0, 0)
        layout.addWidget(self.hint)

    def set_status(self, label: str, hint: Optional[str]) -> None:
        self.label.setText(label)
        self.hint.setText(hint or "")
        self.hint.setVisible(bool(hint))


def _batch_folder(names: list[str]) -> str:
    """Общая папка документов пакета («Клиент» у «Клиент/акт.docx» и «Клиент/2024/счет.pdf»)."""
    heads = {name.split("/", 1)[0] for name in names if "/" in name}
    return heads.pop() if len(heads) == 1 and all("/" in name for name in names) else ""


def _when(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%d.%m.%Y, %H:%M")


class HistoryPanel(QWidget):
    open_doc = Signal(str)
    delete_doc = Signal(str)
    show_batch = Signal(str)  # id любого документа пакета: открыть его и «Все замены пакета»
    export_history = Signal()
    import_history = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 28, 0, 0)
        layout.setSpacing(0)
        header = QHBoxLayout()
        header.setSpacing(8)
        self.title = QLabel("Недавние документы")
        self.title.setObjectName("sectionTitle")
        self.count = QLabel()
        self.count.setObjectName("badge")
        header.addWidget(self.title)
        header.addWidget(self.count)
        header.addStretch(1)
        self.export_button = make_button("Экспорт истории", kind="ghost", small=True, icon_name="download")
        self.export_button.setToolTip("Файл .json со всеми документами. Содержит исходный текст.")
        self.export_button.clicked.connect(self.export_history)
        import_button = make_button("Импорт истории", kind="ghost", small=True, icon_name="import")
        import_button.setToolTip("Документы из файла .json добавятся к этим")
        import_button.clicked.connect(self.import_history)
        header.addWidget(self.export_button)
        header.addWidget(import_button)
        layout.addLayout(header)
        layout.addSpacing(10)
        self.error = QLabel()
        self.error.setObjectName("errorText")
        self.error.setWordWrap(True)
        self.error.setContentsMargins(0, 0, 0, 8)
        self.error.hide()
        layout.addWidget(self.error)
        card = QFrame()
        card.setObjectName("listCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(1, 0, 1, 1)
        card_layout.setSpacing(0)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(0)
        card_layout.addLayout(self.rows)
        footer = QFrame()
        footer.setObjectName("listFooter")
        foot = QHBoxLayout(footer)
        foot.setContentsMargins(16, 10, 16, 10)
        foot.setSpacing(8)
        foot.addWidget(icon_label("lock", t.INK_3, 14), 0, Qt.AlignmentFlag.AlignTop)
        warning = QLabel(
            "История хранится на этом компьютере вместе с исходным текстом, без шифрования. "
            "Экспорт истории тоже содержит исходные данные."
        )
        warning.setObjectName("hint")
        warning.setWordWrap(True)
        foot.addWidget(warning, 1)
        card_layout.addWidget(footer)
        layout.addWidget(card)
        self.more_button = make_button(kind="link")
        self.more_button.clicked.connect(lambda: self.set_expanded(not self.expanded))
        self.more_button.hide()
        more_row = QHBoxLayout()
        more_row.setContentsMargins(0, 8, 0, 0)
        more_row.addWidget(self.more_button)
        more_row.addStretch(1)
        layout.addLayout(more_row)
        self.expanded = False
        self._docs: list[HistoryDocMeta] = []
        self._current_id: Optional[str] = None

    def set_expanded(self, expanded: bool) -> None:
        """Показать всю историю или только последние HISTORY_VISIBLE документов."""
        if expanded == self.expanded:
            return
        self.expanded = expanded
        self.render(self._docs, self._current_id)

    def set_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def render(self, docs: list[HistoryDocMeta], current_id: Optional[str]) -> None:
        self._docs, self._current_id = docs, current_id
        self.count.setText(str(len(docs)))
        self.export_button.setEnabled(bool(docs))
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        hidden = 0 if self.expanded else max(0, len(docs) - HISTORY_VISIBLE)
        self.more_button.setVisible(len(docs) > HISTORY_VISIBLE)
        more = f"Показать еще {plural(hidden, ('документ', 'документа', 'документов'))}"
        self.more_button.setText(more if hidden else "Свернуть")
        if not docs:
            empty = QLabel("Здесь появятся обработанные документы.")
            empty.setObjectName("muted")
            empty.setContentsMargins(16, 14, 16, 14)
            self.rows.addWidget(empty)
            return
        # Документы пакета идут подряд под общим заголовком, на месте самого свежего из них.
        batches: dict[str, list[HistoryDocMeta]] = {}
        for doc in docs:
            if doc.batch_id:
                batches.setdefault(doc.batch_id, []).append(doc)
        ordered: list[HistoryDocMeta] = []
        placed: set[str] = set()
        for doc in docs:
            if doc.batch_id and len(batches[doc.batch_id]) > 1:
                if doc.batch_id not in placed:
                    placed.add(doc.batch_id)
                    ordered.extend(batches[doc.batch_id])
            else:
                ordered.append(doc)
        shown_groups: set[str] = set()
        previous_grouped = False
        for doc in ordered[:len(docs) - hidden]:
            grouped = bool(doc.batch_id) and len(batches.get(doc.batch_id or "", [])) > 1
            new_group = grouped and doc.batch_id not in shown_groups
            if new_group:
                shown_groups.add(doc.batch_id)
                self.rows.addWidget(self._group(batches[doc.batch_id]))
            row = self._row(doc, current_id, grouped)
            # Граница пакета: строка сразу после него отделена линией цвета пакета.
            row.setProperty("afterBatch", previous_grouped and not grouped)
            self.rows.addWidget(row)
            previous_grouped = grouped

    def _group(self, members: list[HistoryDocMeta]) -> QFrame:
        group = QFrame()
        group.setObjectName("historyGroup")
        group.setFixedHeight(40)
        line = QHBoxLayout(group)
        line.setContentsMargins(16, 0, 12, 0)
        line.setSpacing(10)
        line.addWidget(icon_label("layers", t.ACCENT, 16))
        folder = _batch_folder([d.name for d in members])
        title = QLabel(f"Пакет «{folder}»" if folder else "Пакет документов")
        title.setObjectName("batchTitle")
        meta = QLabel(
            f"{plural(len(members), ('документ', 'документа', 'документов'))} · "
            f"{plural(sum(d.entity_count for d in members), ('находка', 'находки', 'находок'))} · "
            f"{_when(max(d.updated_at for d in members))}"
        )
        meta.setObjectName("batchMeta")
        line.addWidget(title)
        line.addWidget(meta)
        line.addStretch(1)
        overview = make_button("Все замены пакета", small=True, icon_name="table")
        overview.setToolTip("Метки всех документов пакета в одном списке")
        overview.clicked.connect(lambda _=False, i=members[0].id: self.show_batch.emit(i))
        line.addWidget(overview)
        return group

    def _row(self, doc: HistoryDocMeta, current_id: Optional[str], grouped: bool) -> QFrame:
        row = QFrame()
        row.setObjectName("historyRow")
        row.setProperty("grouped", grouped)
        row.setMinimumHeight(52)
        line = QHBoxLayout(row)
        line.setContentsMargins(42 if grouped else 16, 8, 12, 8)
        line.setSpacing(12)
        tag = QLabel(FILE_TYPE_TAGS.get(doc.format, doc.format.upper()[:3]))
        tag.setObjectName("fileType")
        tag.setProperty("kind", doc.format)
        tag.setFixedSize(28, 28)
        tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        line.addWidget(tag)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        name = ElidedLabel(doc.name, Qt.TextElideMode.ElideMiddle)
        name.setObjectName("historyName")
        name.setToolTip(doc.name)
        found = plural(doc.entity_count, ("находка", "находки", "находок"))
        meta = QLabel(found if grouped else f"{_when(doc.updated_at)} · {found}")
        meta.setObjectName("hint")
        texts.addWidget(name)
        texts.addWidget(meta)
        line.addLayout(texts, 1)
        if doc.ner_missing:
            chip = QLabel("Имена не искались")
            chip.setObjectName("chipPending")
            chip.setToolTip(
                "Модель поиска имен, компаний и адресов не работала при обработке. Откройте документ и нажмите "
                "«Найти имена заново»"
            )
            line.addWidget(chip)
        elif doc.pending_count:
            chip = QLabel(f"Не проверено: {doc.pending_count}")
            chip.setObjectName("chipPending")
            chip.setToolTip("Кандидаты, которые еще не приняты и не отклонены: в тексте они без замены")
            line.addWidget(chip)
        elif doc.pending_count == 0 and doc.entity_count:
            chip = QLabel("Проверено")
            chip.setObjectName("chipReady")
            line.addWidget(chip)
        is_current = doc.id == current_id
        open_button = make_button("Открыт" if is_current else "Открыть", small=True)
        open_button.clicked.connect(lambda _=False, i=doc.id: self.open_doc.emit(i))
        delete_button = make_button(kind="iconButton", small=True, tooltip=f"Удалить «{doc.name}» из истории")
        delete_button.setIcon(icon("x", t.INK_3, 14))
        delete_button.clicked.connect(lambda _=False, i=doc.id: self.delete_doc.emit(i))
        line.addWidget(open_button)
        line.addWidget(delete_button)
        return row


class StartScreen(QScrollArea):
    pick_file = Signal()
    pick_folder = Signal()
    file_dropped = Signal(list)  # пути файлов и папок

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("page")
        outer = QHBoxLayout(body)
        outer.setContentsMargins(24, 32, 24, 48)
        container = QWidget()
        container.setMaximumWidth(MAX_WIDTH)
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        outer.addWidget(container)
        self.setWidget(body)

        title = QLabel("Документы")
        title.setObjectName("screenTitle")
        column.addWidget(title)
        column.addSpacing(4)
        subtitle = QLabel("Конвертация .docx, .xlsx и .pdf в Markdown с обезличиванием. Все работает на этом компьютере.")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        column.addWidget(subtitle)
        column.addSpacing(16)

        self.model_status = ModelStatusBar()
        column.addWidget(self.model_status)
        column.addSpacing(16)

        self.dropzone = DropZone()
        self.dropzone.clicked.connect(self.pick_file)
        self.dropzone.pick_folder.connect(self.pick_folder)
        self.dropzone.file_dropped.connect(self.file_dropped)
        self.folder_button = self.dropzone.folder_button
        column.addWidget(self.dropzone)
        column.addSpacing(12)
        self.convert_only_box = QCheckBox("Только конвертировать в Markdown, без поиска персональных данных")
        self.convert_only_box.setToolTip(
            "Текст документа выгружается как есть, без замен. Модель распознавания имен не нужна; "
            "данные можно отметить вручную в исходнике."
        )
        column.addWidget(self.convert_only_box)
        self.loading = LoadingBlock()
        self.loading.hide()
        column.addWidget(self.loading)
        self.error_notice = Notice(tone="danger")
        self.error = self.error_notice.label
        error_slot = QWidget()
        error_wrap = QVBoxLayout(error_slot)
        error_wrap.setContentsMargins(0, 16, 0, 0)
        error_wrap.addWidget(self.error_notice)
        self.error_notice.slot = error_slot
        self.error_notice.hide()
        column.addWidget(error_slot)
        self.history = HistoryPanel()
        column.addWidget(self.history)
        column.addSpacing(24)
        self.column = column
        column.addStretch(1)

    def add_footer(self, widget: QWidget) -> None:
        self.column.addWidget(widget)

    def set_file_name(self, name: str, plain: bool = False) -> None:
        """Имя файла или готовая подпись вроде «3 файла» (plain — подпись как есть)."""
        label = f"Файл: {Path(name).name}" if Path(name).suffix and not plain else name
        self.dropzone.file_label.setText(label if name else "")
        self.dropzone.file_label.setVisible(bool(name))

    def set_error(self, text: str) -> None:
        self.error.setText(text)
        self.error_notice.setVisible(bool(text))
