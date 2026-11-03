"""Индикатор моделей на стартовом экране: какая модель NER, скачана ли и загружена
ли она, на чем считает; ниже — скачана ли модель распознавания сканов.

Если модели NER нет, отсюда же ее можно скачать или указать скачанный заранее файл.
"""

from __future__ import annotations

import html
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ..components import model_asset, ocr_models
from ..model_holder import ModelStatus
from . import theme as t
from .icons import pixmap
from .widgets import make_button, set_prop

MODEL_MB = round(model_asset.MODEL_BYTES_FP16 / 1e6)  # как в диалоге скачивания
ERROR_CHARS = 140  # длинная причина ошибки обрезается, целиком она в подсказке

DEVICE_NAMES = {"gpu": "на видеокарте", "cpu": "на процессоре"}

# Тон плашки по состоянию: не скачана — предупреждение, ошибка — красная, готова — зеленая.
TONES = {
    "ready": "success",
    "loading": "info",
    "downloading": "info",
    "idle": "info",
    "missing": "warn",
    "error": "danger",
}


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= ERROR_CHARS else text[: ERROR_CHARS - 1].rstrip() + "…"


def ner_text(status: ModelStatus, convert_only: bool = False) -> str:
    """Строка о модели NER: жирное состояние, затем название и подробности."""
    title = model_asset.MODEL_TITLE
    where = [DEVICE_NAMES[status.device]] if status.device in DEVICE_NAMES else []
    if status.state == "ready":
        head, rest = "Модель NER готова", [title, *where]
    elif status.state == "loading":
        head, rest = "Модель NER загружается…", [title, *where]
    elif status.state == "downloading":
        verb = "Копирование" if status.importing else "Скачивание"
        head, rest = f"{verb} модели NER… {status.pct or 0}%", [title, f"около {MODEL_MB} МБ"]
    elif status.state == "idle":
        head = "Модель NER скачана"
        when = "не нужна в режиме «Только конвертировать»" if convert_only else "загрузится при первом документе"
        rest = [title, when]
    elif status.state == "error":
        head, rest = "Модель NER не загрузилась", [title] + ([_short(status.error)] if status.error else [])
    else:
        head = "Модель NER не скачана"
        if status.error:
            rest = [title, f"не удалось скачать: {_short(status.error)}"]
        else:
            rest = [title, f"около {MODEL_MB} МБ", "без нее имена, компании и адреса не ищутся"]
    return f"<b>{html.escape(head)}</b> · " + " · ".join(html.escape(part) for part in rest)


def ocr_text(present: bool) -> str:
    title = ocr_models.OCR_TITLE
    if present:
        return f"Распознавание сканов: {title}, скачана"
    return f"Распознавание сканов: {title}, скачается при первом скане (около {ocr_models.TOTAL_MB} МБ)"


class ModelStatusBar(QFrame):
    """Плашка общего вида (как Notice): значок по тону, две строки текста, действия справа."""

    download = Signal()
    choose_file = Signal()
    cancel = Signal()
    load = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("notice")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 10, 10)
        row.setSpacing(10)
        self.dot = QLabel()
        self.dot.setFixedSize(16, 16)
        dot_col = QVBoxLayout()
        dot_col.setContentsMargins(0, 1, 0, 0)
        dot_col.addWidget(self.dot)
        dot_col.addStretch(1)
        row.addLayout(dot_col)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.ner_label = QLabel()
        self.ner_label.setWordWrap(True)
        self.ner_label.setTextFormat(Qt.TextFormat.RichText)
        self.ocr_label = QLabel()
        self.ocr_label.setObjectName("secondary")
        self.ocr_label.setWordWrap(True)
        texts.addWidget(self.ner_label)
        texts.addWidget(self.ocr_label)
        row.addLayout(texts, 1)

        def button(text: str, signal: Signal, primary: bool = False) -> QPushButton:
            widget = make_button(text, kind="primary" if primary else "", small=True)
            widget.clicked.connect(signal)
            widget.hide()
            row.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
            return widget

        self.file_button = button("Указать файл…", self.choose_file)
        self.file_button.setToolTip("Если сайт закрыт в вашей сети: заранее скачанный model_fp16.onnx или папка модели")
        self.download_button = button("Скачать", self.download, primary=True)
        self.download_button.setToolTip(
            "Модель скачивается один раз с Hugging Face и хранится на этом компьютере. "
            "Это публичный файл, текст документов никуда не отправляется."
        )
        self.cancel_button = button("Отмена", self.cancel)
        self.load_button = button("Загрузить", self.load)
        self.load_button.setToolTip("Загрузить модель сейчас, чтобы первый документ не ждал ее")
        self.retry_button = button("Повторить", self.load)
        self.status: Optional[ModelStatus] = None
        self.set_status(ModelStatus("missing"), ocr_present=False)

    def set_status(self, status: ModelStatus, ocr_present: bool, convert_only: bool = False) -> None:
        self.status = status
        tone = TONES.get(status.state, "info")
        _bg, _line, ink, icon_name = t.TONES[tone]
        self.dot.setPixmap(pixmap(icon_name, ink, 16))
        set_prop(self, "tone", tone)
        for child in self.findChildren(QWidget):
            child.style().unpolish(child)
            child.style().polish(child)
        self.ner_label.setText(ner_text(status, convert_only))
        self.ner_label.setToolTip(html.escape(status.error) if status.error else "")
        self.ocr_label.setText(ocr_text(ocr_present))
        state = status.state
        self.download_button.setVisible(state == "missing")
        self.file_button.setVisible(state == "missing")
        self.cancel_button.setVisible(state == "downloading")
        self.load_button.setVisible(state == "idle" and not convert_only)
        self.retry_button.setVisible(state == "error")
