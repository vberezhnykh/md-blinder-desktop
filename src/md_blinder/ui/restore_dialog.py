"""Окно «Вернуть настоящие данные»: ответ с метками превращается в текст с именами.

Слева пользователь вставляет ответ нейросети или юриста с метками [PERSON_1],
справа сразу виден тот же текст с настоящими значениями из таблицы замен
документа или всего пакета. Поправленный документ Word можно открыть файлом:
метки заменяются прямо в нем, и копия сохраняется с прежним оформлением.
Ничего не уходит с компьютера.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..anonymize.redacted_docx import RedactedDocxError
from ..anonymize.restore import RestoreMap, RestoreResult, restore_text
from ..anonymize.restore_docx import restore_docx, restored_file_name
from ..text_utils import plural
from . import theme as t
from .icons import icon
from .widgets import make_button


def _framed(title: str, editor: QPlainTextEdit) -> QWidget:
    box = QWidget()
    column = QVBoxLayout(box)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(6)
    caption = QLabel(title)
    caption.setObjectName("muted")
    column.addWidget(caption)
    card = QFrame()
    card.setObjectName("listCard")
    inner = QVBoxLayout(card)
    inner.setContentsMargins(10, 8, 10, 8)
    inner.addWidget(editor)
    column.addWidget(card, 1)
    return box


class RestoreDialog(QDialog):
    def __init__(
        self, parent: Optional[QWidget], mapping: RestoreMap, scope: str,
        save_path: Callable[[str, str], Optional[Path]],
    ) -> None:
        super().__init__(parent)
        self.setObjectName("dialog")
        self.setWindowTitle("Вернуть настоящие данные")
        self.resize(1040, 680)
        self.mapping = mapping
        self._save_path = save_path
        self.result = RestoreResult("")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(24, 20, 16, 12)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        title = QLabel("Вернуть настоящие данные")
        title.setObjectName("dialogTitle")
        summary = QLabel(
            "Вставьте ответ, в котором остались метки вроде [PERSON_1], или откройте поправленный файл Word. "
            f"Метки заменятся значениями из таблицы замен {scope}: "
            f"{plural(len(mapping), ('метка', 'метки', 'меток'))}. Текст не покидает компьютер."
        )
        summary.setObjectName("secondary")
        summary.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(summary)
        head.addLayout(titles, 1)
        close = make_button(kind="iconButton", tooltip="Закрыть")
        close.setIcon(icon("x", t.INK_2, 16))
        close.clicked.connect(self.close)
        head.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(head)

        columns = QHBoxLayout()
        columns.setContentsMargins(24, 4, 24, 12)
        columns.setSpacing(16)
        self.source = QPlainTextEdit()
        self.source.setPlaceholderText("Ответ с метками [PERSON_1], [COMPANY_2]…")
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Здесь появится текст с настоящими значениями")
        columns.addWidget(_framed("Ответ с метками", self.source), 1)
        columns.addWidget(_framed("С настоящими данными", self.output), 1)
        outer.addLayout(columns, 1)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(24, 0, 24, 12)
        self.status = QLabel()
        self.status.setObjectName("secondary")
        self.status.setWordWrap(True)
        status_row.addWidget(self.status, 1)
        outer.addLayout(status_row)

        footer = QFrame()
        footer.setObjectName("dialogFooter")
        foot = QHBoxLayout(footer)
        foot.setContentsMargins(24, 14, 24, 14)
        foot.setSpacing(8)
        paste = make_button("Вставить из буфера", icon_name="import")
        paste.clicked.connect(self.paste)
        foot.addWidget(paste)
        open_file = make_button("Открыть файл…", icon_name="file")
        open_file.setToolTip(
            "Поправленный Word (.docx): метки заменятся в самом файле, оформление и правки сохранятся. "
            "Файл .md или .txt откроется в поле слева."
        )
        open_file.clicked.connect(self.open_file)
        foot.addWidget(open_file)
        note = QLabel("Результат содержит исходные данные.")
        note.setObjectName("hint")
        foot.addWidget(note, 1)
        self.save_button = make_button("Сохранить .md…", icon_name="download")
        self.save_button.clicked.connect(self.save)
        self.copy_button = make_button("Копировать результат", kind="primary", icon_name="copy")
        self.copy_button.clicked.connect(self.copy)
        foot.addWidget(self.save_button)
        foot.addWidget(self.copy_button)
        outer.addWidget(footer)

        self.source.textChanged.connect(self._update)
        self._update()

    def paste(self) -> None:
        self.source.setPlainText(QApplication.clipboard().text())

    def _pick_file(self) -> Optional[Path]:
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть ответ с метками", "", "Word, Markdown, текст (*.docx *.md *.txt);;Все файлы (*)",
        )
        return Path(path) if path else None

    def open_file(self) -> None:
        path = self._pick_file()
        if path is None:
            return
        if path.suffix.lower() == ".docx":
            self.restore_word(path)
            return
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            self.status.setText(f"Не удалось открыть файл: {exc}")
            return
        self.source.setPlainText(text)

    def restore_word(self, source: Path) -> None:
        """Метки в документе Word заменяются значениями; копия сохраняется рядом."""
        target = self._save_path(str(source.parent / restored_file_name(source.name, self.mapping)), "Word (*.docx)")
        if target is None:
            return
        if target.resolve() == source.resolve():
            self.status.setText("Сохраните результат в другой файл: ответ с метками стоит оставить как есть.")
            return
        try:
            report = restore_docx(source, target, self.mapping)
        except RedactedDocxError as exc:
            self.status.setText(str(exc))
            return
        parts = [f"Файл «{target.name}» сохранен. Заменено: {plural(report.replaced, ('метка', 'метки', 'меток'))}."]
        if report.unknown:
            shown = ", ".join(report.unknown[:6]) + ("…" if len(report.unknown) > 6 else "")
            parts.append(f"Нет в таблице замен, остались как есть: {shown}.")
        self.status.setText(" ".join(parts))

    def _update(self) -> None:
        self.result = restore_text(self.source.toPlainText(), self.mapping)
        self.output.setPlainText(self.result.text)
        has_text = bool(self.source.toPlainText().strip())
        self.copy_button.setEnabled(has_text)
        self.save_button.setEnabled(has_text)
        if not has_text:
            self.status.setText("")
            return
        parts = [f"Заменено: {plural(self.result.replaced, ('метка', 'метки', 'меток'))}."]
        if self.result.unknown:
            shown = ", ".join(self.result.unknown[:6]) + ("…" if len(self.result.unknown) > 6 else "")
            parts.append(f"Нет в таблице замен, остались как есть: {shown}.")
        self.status.setText(" ".join(parts))

    def copy(self) -> None:
        QApplication.clipboard().setText(self.result.text)
        self.status.setText("Текст с настоящими данными скопирован.")

    def save(self) -> None:
        path = self._save_path("ответ.md", "Markdown (*.md)")
        if path is None:
            return
        try:
            path.write_text(self.result.text, encoding="utf-8", newline="\n")
        except OSError as exc:
            self.status.setText(f"Не удалось сохранить файл: {exc}")
            return
        self.status.setText(f"Файл «{path.name}» сохранен.")
