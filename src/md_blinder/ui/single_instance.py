"""Один экземпляр программы.

Если программа уже открыта, новый запуск (например, из меню проводника)
передает ей путь к файлу через именованный канал и сразу завершается. Два
окна писали бы в одну историю и могли затереть правки друг друга.
"""

from __future__ import annotations

import getpass
import json
import sys
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return f"md-blinder-{user}"


def send_to_running(path: Optional[str], name: Optional[str] = None, timeout_ms: int = 500) -> bool:
    """Отдает путь открытой программе. False — программа не запущена."""
    socket = QLocalSocket()
    socket.connectToServer(name or server_name())
    if not socket.waitForConnected(timeout_ms):
        return False
    if sys.platform == "win32":
        # Разрешает открытой программе вывести свое окно на передний план:
        # без этого Windows только мигает кнопкой на панели задач.
        try:
            import ctypes

            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        except Exception:  # noqa: BLE001
            pass
    # Сообщение — одна строка JSON с переводом строки в конце.
    socket.write(json.dumps({"path": path}).encode("utf-8") + b"\n")
    socket.waitForBytesWritten(timeout_ms)
    socket.disconnectFromServer()
    if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        socket.waitForDisconnected(timeout_ms)
    return True


class InstanceServer(QObject):
    """Принимает пути от новых запусков. received(path) — путь или пустая строка (просто показать окно)."""

    received = Signal(str)

    def __init__(self, name: Optional[str] = None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.name = name or server_name()
        self.server.newConnection.connect(self._connection)

    def listen(self) -> bool:
        # После аварийного завершения на Linux и macOS остается файл канала.
        QLocalServer.removeServer(self.name)
        return self.server.listen(self.name)

    def _connection(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            buffer = bytearray()

            def read(s=socket, buf=buffer) -> None:
                buf.extend(bytes(s.readAll()))
                if b"\n" not in buf:
                    return
                # Сокет принадлежит серверу и удаляется вместе с ним. deleteLater
                # здесь нельзя: PySide удаляет объект и сам, и программа падает
                # в деструкторе. Сигналы глушатся, чтобы закрытие сокета при
                # выходе не вызвало обработчик снова.
                s.blockSignals(True)
                s.disconnectFromServer()
                line = bytes(buf).split(b"\n", 1)[0]
                try:
                    message = json.loads(line.decode("utf-8"))
                except ValueError:
                    return
                self.received.emit(message.get("path") or "")

            socket.readyRead.connect(read)
            # Сообщение могло прийти раньше подписки.
            if socket.bytesAvailable():
                read()
