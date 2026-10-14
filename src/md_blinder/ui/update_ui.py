"""Плашка обновления и фоновая проверка/загрузка новой версии."""

from __future__ import annotations

import os
import threading
import webbrowser
from typing import Callable, Optional

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from .. import __version__, updater
from ..cancel import CancelToken, Cancelled
from . import theme as t

SETTING_CHECK = "checkUpdates"


class UpdateBar(QFrame):
    """Полоса над содержимым окна: доступна версия, загрузка, готово к перезапуску, ошибка."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("updateBar")
        self.setStyleSheet(
            f"QFrame#updateBar {{ background: {t.ACCENT_WASH}; border-bottom: 1px solid {t.LINE}; }}"
            f"QFrame#updateBar QLabel {{ color: {t.INK}; font-size: 13.5px; background: transparent; }}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(20, 8, 20, 8)
        row.setSpacing(10)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(self.label, 1)
        self.buttons: list[QPushButton] = []
        self._row = row
        self.hide()

    def show_state(self, text: str, actions: list[tuple[str, Callable[[], None], bool]]) -> None:
        for b in self.buttons:
            b.deleteLater()
        self.buttons = []
        self.label.setText(text)
        for title, handler, primary in actions:
            b = QPushButton(title)
            if primary:
                b.setObjectName("primary")
            b.setStyleSheet("padding: 4px 12px; font-size: 13px;")
            b.clicked.connect(handler)
            self._row.addWidget(b)
            self.buttons.append(b)
        self.show()


class UpdateController(QObject):
    """Проверка обновлений в фоне и установка. Сигналы приходят в главный поток."""

    _checked = Signal(object, object, bool)  # release | None, ошибка | None, ручная проверка
    _progress = Signal(int, int)
    _prepared = Signal(object, object)  # папка с новой версией | None, ошибка | None
    toast = Signal(str, str)  # текст, тон
    restart_requested = Signal(object)  # папка с новой версией

    def __init__(self, bar: UpdateBar, settings: QSettings, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.bar = bar
        self.settings = settings
        self.release: Optional[updater.Release] = None
        self._cancel: Optional[CancelToken] = None
        self._checking = False
        self._checked.connect(self._on_checked)
        self._progress.connect(self._on_progress)
        self._prepared.connect(self._on_prepared)

    @property
    def auto_check(self) -> bool:
        return self.settings.value(SETTING_CHECK, True, type=bool)

    def set_auto_check(self, value: bool) -> None:
        self.settings.setValue(SETTING_CHECK, value)

    def startup(self) -> None:
        updater.cleanup_old_updates()
        if self.auto_check and not os.environ.get("MD_BLINDER_NO_UPDATE_CHECK"):
            self.check(manual=False)

    def check(self, manual: bool = True) -> None:
        if self._checking:
            return
        self._checking = True

        def work() -> None:
            try:
                self._checked.emit(updater.check_for_update(), None, manual)
            except Exception as exc:  # noqa: BLE001 — сеть может быть закрыта, это не ошибка программы
                self._checked.emit(None, exc, manual)

        threading.Thread(target=work, daemon=True).start()

    def _on_checked(self, release, error, manual: bool) -> None:
        self._checking = False
        if error is not None:
            if manual:
                self.toast.emit(f"Не удалось проверить обновления: {error}", "error")
            return
        if release is None:
            if manual:
                self.toast.emit(f"У вас последняя версия ({__version__}).", "info")
            return
        self.release = release
        self._show_available()

    def _show_available(self) -> None:
        release = self.release
        if release is None:
            return
        ok, reason = updater.can_self_update()
        text = f"<b>Доступна версия {release.version}</b> (у вас {__version__})."
        actions: list[tuple[str, Callable[[], None], bool]] = []
        if ok:
            actions.append(("Обновить", self.download, True))
        else:
            text += f" <span style='color:{t.INK_3}'>{reason}</span>"
        if release.page_url:
            actions.append(("Что нового", lambda: webbrowser.open(release.page_url), False))
        actions.append(("Позже", self.bar.hide, False))
        self.bar.show_state(text, actions)

    def download(self) -> None:
        release = self.release
        if release is None:
            return
        self._cancel = CancelToken()
        cancel = self._cancel
        self.bar.show_state(f"Скачивание версии {release.version}… 0%", [("Отменить", cancel.cancel, False)])

        def work() -> None:
            try:
                staged = updater.prepare(release, on_progress=lambda got, total: self._progress.emit(got, total or 0), cancel=cancel)
            except Cancelled:
                self._prepared.emit(None, None)
            except Exception as exc:  # noqa: BLE001
                self._prepared.emit(None, exc)
            else:
                self._prepared.emit(staged, None)

        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, got: int, total: int) -> None:
        if self.release is None or not self.bar.buttons:
            return
        pct = int(got * 100 / total) if total else 0
        self.bar.label.setText(f"Скачивание версии {self.release.version}… {pct}%")

    def _on_prepared(self, staged, error) -> None:
        if staged is None and error is None:
            self._show_available()
            return
        if error is not None:
            self.bar.show_state(
                f"Не удалось скачать обновление: {error}",
                [("Повторить", self.download, True), ("Скрыть", self.bar.hide, False)],
            )
            return
        version = self.release.version if self.release else ""
        self.bar.show_state(
            f"<b>Версия {version} скачана.</b> Программа закроется, заменит файлы и откроется снова.",
            [("Перезапустить и обновить", lambda: self.restart_requested.emit(staged), True),
             ("Позже", self.bar.hide, False)],
        )


def version_footer(controller: UpdateController) -> QWidget:
    """Строка внизу стартового экрана: версия, проверка обновлений, выключатель."""
    from PySide6.QtWidgets import QCheckBox

    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 24, 0, 0)
    row.setSpacing(12)
    label = QLabel(f"MD Blinder {__version__}")
    label.setObjectName("muted")
    row.addWidget(label)
    check = QPushButton("Проверить обновления")
    check.setObjectName("link")
    check.setCursor(Qt.CursorShape.PointingHandCursor)
    check.clicked.connect(lambda: controller.check(manual=True))
    row.addWidget(check)
    auto = QCheckBox("Проверять при запуске")
    auto.setChecked(controller.auto_check)
    auto.setToolTip("Один запрос к GitHub за номером последней версии. Документы не отправляются.")
    auto.toggled.connect(controller.set_auto_check)
    row.addWidget(auto)
    row.addStretch(1)
    return box
