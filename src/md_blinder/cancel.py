"""Отмена длительной обработки: аналог AbortSignal из браузерной версии."""

from __future__ import annotations

import threading
from typing import Optional


class Cancelled(Exception):
    """Обработка отменена пользователем или заменена новой задачей."""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled()


def check(token: Optional[CancelToken]) -> None:
    if token is not None:
        token.check()


class AnyToken(CancelToken):
    """Срабатывает, когда сработал любой из токенов."""

    def __init__(self, *tokens: CancelToken) -> None:
        super().__init__()
        self._tokens = tokens

    @property
    def cancelled(self) -> bool:
        return self._event.is_set() or any(t.cancelled for t in self._tokens)

    def check(self) -> None:
        if self.cancelled:
            raise Cancelled()
