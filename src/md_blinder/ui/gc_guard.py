"""Сборка мусора только в главном потоке.

Python запускает сборщик в том потоке, где набралось достаточно новых объектов.
Если это рабочий поток обработки (конвертация, OCR, NER), сборщик может удалить
там Qt-объекты интерфейса из какого-нибудь цикла ссылок, а Qt удаляет виджеты
только в GUI-потоке: на Windows это падение с access violation. Поэтому
автоматическая сборка выключается, а таймер в главном потоке запускает ее по
тем же порогам, что и стандартный сборщик (прием из pyqtgraph).
"""

from __future__ import annotations

import gc

from PySide6.QtCore import QObject, QTimer

INTERVAL_MS = 1000


class MainThreadGc(QObject):
    def __init__(self, parent: QObject | None = None, interval_ms: int = INTERVAL_MS) -> None:
        super().__init__(parent)
        self.threshold = gc.get_threshold()
        gc.disable()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check)
        self.timer.start(interval_ms)

    def check(self) -> None:
        l0, l1, l2 = gc.get_count()
        if l0 > self.threshold[0]:
            gc.collect(0)
            if l1 > self.threshold[1]:
                gc.collect(1)
                if l2 > self.threshold[2]:
                    gc.collect(2)


def install(app: QObject) -> MainThreadGc:
    """Объект живет, пока жив app: таймер принадлежит ему."""
    return MainThreadGc(app)
