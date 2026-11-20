"""Текст плашек, уведомлений и строки модели выделяется и копируется."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QPushButton, QWidget

from md_blinder.model_holder import ModelStatus
from md_blinder.ui.model_status import ModelStatusBar
from md_blinder.ui.widgets import Notice, Toast, copy_label_text, label_plain_text

SELECTABLE = Qt.TextInteractionFlag.TextSelectableByMouse


def _menu_titles(label) -> list[str]:
    from PySide6.QtWidgets import QLabel

    return [w.text() for w in label.copy_menu.findChildren(QLabel) if w.objectName() == "menuTitle"]


def test_notice_text_is_selectable_and_copied_without_markup(qtbot) -> None:
    notice = Notice(closable=True)
    qtbot.addWidget(notice)
    notice.label.setText("Модель не сработала.<br><span style='color:#888'>Причина: Недостаточно памяти</span>")
    assert notice.label.textInteractionFlags() & SELECTABLE
    assert label_plain_text(notice.label) == "Модель не сработала.\nПричина: Недостаточно памяти"
    assert copy_label_text(notice.label) == "Модель не сработала.\nПричина: Недостаточно памяти"
    assert QGuiApplication.clipboard().text() == "Модель не сработала.\nПричина: Недостаточно памяти"


def test_selection_is_copied_and_context_menu_offers_copy(qtbot) -> None:
    notice = Notice()
    qtbot.addWidget(notice)
    notice.label.setText("Причина: Недостаточно памяти")
    notice.show()
    notice.label.customContextMenuRequested.emit(QPoint(2, 2))
    assert _menu_titles(notice.label) == ["Копировать"]
    QGuiApplication.clipboard().clear()
    notice.label.copy_menu.findChildren(QPushButton)[0].click()
    assert QGuiApplication.clipboard().text() == "Причина: Недостаточно памяти"

    notice.label.setSelection(0, len("Причина"))
    assert copy_label_text(notice.label) == "Причина"
    notice.label.customContextMenuRequested.emit(QPoint(2, 2))
    assert _menu_titles(notice.label) == ["Копировать", "Копировать весь текст"]


def test_toast_text_is_selectable_and_stays_while_hovered(qtbot) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.resize(800, 600)
    parent.show()
    toast = Toast(parent)
    toast.show_message("Не удалось сохранить файл: доступ запрещен", 6000, tone="error")
    assert toast.label.textInteractionFlags() & SELECTABLE
    toast.enterEvent(None)
    assert not toast._timer.isActive()
    toast.leaveEvent(None)
    assert toast._timer.isActive()


def test_model_status_copies_full_error(qtbot) -> None:
    bar = ModelStatusBar()
    qtbot.addWidget(bar)
    reason = "Сбой модели NER (gpu): " + "очень длинная причина " * 20
    bar.set_status(ModelStatus("error", error=reason), ocr_present=True)
    assert bar.ner_label.textInteractionFlags() & SELECTABLE
    assert "…" in bar.ner_label.text()  # в строке причина сокращена
    copied = copy_label_text(bar.ner_label, selected_only=False, full_text=bar._full_ner_text)
    assert reason.strip() in copied
