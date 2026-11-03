"""Окна с вопросом в общем виде программы вместо стандартных окон Windows.

Заголовок, текст, пояснение мельче и кнопки внизу: тихие слева, основная справа.
"""

from __future__ import annotations

from typing import Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .widgets import make_button

# Кнопка окна: ключ ответа, подпись, вид (primary, dangerPrimary, ghost или обычная), слева ли она.
Choice = tuple[str, str, str, bool]


class Dialog(QDialog):
    def __init__(
        self, parent: Optional[QWidget], title: str, text: str, info: str = "",
        choices: Sequence[Choice] = (), width: int = 520,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("dialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(width)
        self.answer: Optional[str] = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QVBoxLayout()
        body.setContentsMargins(24, 20, 24, 20)
        body.setSpacing(10)
        heading = QLabel(title)
        heading.setObjectName("dialogTitle")
        heading.setWordWrap(True)
        body.addWidget(heading)
        self.text = QLabel(text)
        self.text.setObjectName("body")
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.text)
        if info:
            note = QLabel(info)
            note.setObjectName("secondary")
            note.setWordWrap(True)
            body.addWidget(note)
        outer.addLayout(body)
        footer = QFrame()
        footer.setObjectName("dialogFooter")
        row = QHBoxLayout(footer)
        row.setContentsMargins(24, 14, 24, 14)
        row.setSpacing(8)
        left = [c for c in choices if c[3]]
        right = [c for c in choices if not c[3]]
        self.buttons = {}
        for key, label, kind, _left in left:
            row.addWidget(self._button(key, label, kind))
        row.addStretch(1)
        for key, label, kind, _left in right:
            row.addWidget(self._button(key, label, kind))
        outer.addWidget(footer)
        default = next((c for c in choices if c[2] in ("primary", "dangerPrimary")), None)
        if default is not None:
            self.buttons[default[0]].setDefault(True)
            self.buttons[default[0]].setFocus()

    def _button(self, key: str, label: str, kind: str):
        button = make_button(label, kind=kind)
        button.clicked.connect(lambda _=False, k=key: self._choose(k))
        self.buttons[key] = button
        return button

    def _choose(self, key: str) -> None:
        self.answer = key
        self.accept()


def choose(
    parent: Optional[QWidget], title: str, text: str, info: str, choices: Sequence[Choice],
) -> Optional[str]:
    """Показывает окно и возвращает ключ нажатой кнопки или None, если окно закрыли."""
    dialog = Dialog(parent, title, text, info, choices)
    dialog.exec()
    return dialog.answer


def ask(
    parent: Optional[QWidget], title: str, text: str, accept: str, danger: bool = False, cancel: str = "Отмена",
) -> bool:
    """Подтверждение: True, если нажата кнопка accept."""
    kind = "dangerPrimary" if danger else "primary"
    return choose(parent, title, text, "", [("cancel", cancel, "", False), ("ok", accept, kind, False)]) == "ok"
