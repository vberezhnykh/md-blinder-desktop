"""Отложенное автосохранение истории (порт useHistoryStore.ts).

Правки в течение 800 мс объединяются в одну запись. Перед любым переходом
(открыть, удалить, импорт, новый файл, закрытие окна) вызывается flush(). При
ошибке очередь сохраняется, а интерфейс показывает кнопку повтора.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from ..storage.history import HistoryDoc, HistoryStore

DEBOUNCE_MS = 800


class Autosave(QObject):
    state_changed = Signal()  # saving / error изменились
    saved = Signal()  # запись прошла, список истории можно обновить

    def __init__(self, store: HistoryStore, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self._pending: dict[str, HistoryDoc] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self._flush_quietly)
        self.error = ""

    @property
    def saving(self) -> bool:
        return bool(self._pending)

    def queue(self, doc: HistoryDoc) -> None:
        self._pending[doc.id] = doc
        self._timer.start()
        self.state_changed.emit()

    def discard(self, doc_id: str) -> None:
        self._pending.pop(doc_id, None)
        self.state_changed.emit()

    def flush(self) -> bool:
        """Пишет все отложенные документы. Возвращает False при ошибке."""
        self._timer.stop()
        try:
            while self._pending:
                doc_id, doc = next(iter(self._pending.items()))
                self.store.put_doc(doc)
                if self._pending.get(doc_id) is doc:
                    del self._pending[doc_id]
        except Exception as exc:  # noqa: BLE001
            self.error = f"Не удалось сохранить изменения: {exc}"
            self.state_changed.emit()
            return False
        self.error = ""
        self.state_changed.emit()
        self.saved.emit()
        return True

    def _flush_quietly(self) -> None:
        self.flush()
