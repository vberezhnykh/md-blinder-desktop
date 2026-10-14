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
