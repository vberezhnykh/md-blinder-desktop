"""Текст исключения для сообщения пользователю."""

from __future__ import annotations

import sys

_MAX_MESSAGE_BYTES = 4096


def system_encodings() -> tuple[str, ...]:
    """Кодировка сообщений Windows: ANSI-кодовая страница системы (mbcs), на русской
    Windows это cp1251. Вне Windows ее нет, и берется cp1251."""
    return ("mbcs", "cp1251") if sys.platform == "win32" else ("cp1251",)


def error_text(error: BaseException) -> str:
    """Текст ошибки ONNX Runtime, даже если Windows вернула его не в UTF-8.

    Сообщения Windows (о нехватке памяти видеокарты, сбросе драйвера и т. п.) ORT
    вставляет в текст исключения в кодировке системы, на русской Windows — cp1251.
    Python ждет UTF-8 и вместо исключения ORT получает UnicodeDecodeError
    («'utf-8' codec can't decode byte 0xcd…», 0xcd — «Н» из «Не…»). Исходный текст
    лежит в error.object: здесь он раскодируется в кодировке системы.
    """
    raw = error.object if isinstance(error, UnicodeDecodeError) else None
    # Длинные байты — это уже не сообщение, а данные (файл, ответ сервера): их не показываем.
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > _MAX_MESSAGE_BYTES:
        return str(error) or type(error).__name__
    for encoding in system_encodings():
        try:
            return bytes(raw).decode(encoding).strip()
        except (UnicodeDecodeError, LookupError):
            continue
    return bytes(raw).decode("utf-8", "replace").strip()
