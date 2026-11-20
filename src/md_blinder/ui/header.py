"""Шапка окна, общая для стартового экрана и экрана документа.

Слева логотип, на экране документа за ним путь «Документы › имя файла» и состояние
сохранения. Справа на стартовом экране версия и проверка обновлений, на экране
документа — «Пакет», «История» и «Новый файл».
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from . import theme as t
from .icons import icon, pixmap
from .widgets import ElidedLabel, PopupMenu, icon_label, make_button, set_prop

HEIGHT = 56


def menu_button(icon_name: str, text: str, tooltip: str = "") -> tuple[QPushButton, QLabel]:
    """Кнопка со значком, подписью и стрелкой вниз: открывает меню."""
    button = QPushButton()
    button.setObjectName("menuButton")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if tooltip:
        button.setToolTip(tooltip)
    row = QHBoxLayout(button)
    row.setContentsMargins(12, 0, 10, 0)
    row.setSpacing(6)
    label = QLabel(text)
    label.setObjectName("buttonText")
    row.addWidget(icon_label(icon_name, t.INK_2, 16))
    row.addWidget(label)
    row.addWidget(icon_label("chevron-down", t.INK_3, 14))
    for child in button.findChildren(QLabel):
        child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    button.setMinimumWidth(row.sizeHint().width())
    return button, label


class AppHeader(QFrame):
    home = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("appHeader")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(HEIGHT)
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 0, 24, 0)
        row.setSpacing(8)

        self.brand = QPushButton()
        self.brand.setObjectName("brandButton")
        self.brand.setCursor(Qt.CursorShape.PointingHandCursor)
        self.brand.setToolTip("Стартовый экран со всеми документами")
        brand_row = QHBoxLayout(self.brand)
        brand_row.setContentsMargins(6, 0, 8, 0)
        brand_row.setSpacing(10)
        mark = QLabel()
        mark.setPixmap(pixmap("mark", t.INK, 20))
        name = QLabel("MD Blinder")
        name.setObjectName("brand")
        brand_row.addWidget(mark)
        brand_row.addWidget(name)
        for child in self.brand.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.brand.setMinimumWidth(brand_row.sizeHint().width())
        self.brand.clicked.connect(self.home)
        row.addWidget(self.brand)

        # Путь к документу: виден только на экране документа.
        self.crumbs = QWidget()
        crumbs = QHBoxLayout(self.crumbs)
        crumbs.setContentsMargins(0, 0, 0, 0)
        crumbs.setSpacing(6)
        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedSize(1, 20)
        crumbs.addWidget(divider)
        crumbs.addSpacing(2)
        self.home_button = make_button("Документы", kind="crumbLink", tooltip="Стартовый экран со всеми документами")
        self.home_button.clicked.connect(self.home)
        crumbs.addWidget(self.home_button)
        crumbs.addWidget(icon_label("chevron-right", t.INK_3, 14))
        self.file_label = ElidedLabel("", Qt.TextElideMode.ElideMiddle)
        self.file_label.setObjectName("crumb")
        self.file_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        crumbs.addWidget(self.file_label, 1)
        crumbs.addSpacing(6)
        self.save_icon = QLabel()
        self.save_icon.setFixedSize(14, 14)
        self.save_label = QLabel()
        self.save_label.setObjectName("saveState")
        crumbs.addWidget(self.save_icon)
        crumbs.addWidget(self.save_label)
        row.addWidget(self.crumbs, 1)
        self.spacer = QWidget()
        self.spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row.addWidget(self.spacer)

        # Действия стартового экрана: версия и обновления (их добавляет MainWindow).
        self.start_actions = QWidget()
        self.start_row = QHBoxLayout(self.start_actions)
        self.start_row.setContentsMargins(0, 0, 0, 0)
        self.start_row.setSpacing(8)
        row.addWidget(self.start_actions)

        # Действия экрана документа.
        self.doc_actions = QWidget()
        doc_row = QHBoxLayout(self.doc_actions)
        doc_row.setContentsMargins(0, 0, 0, 0)
        doc_row.setSpacing(8)
        self.batch_button, self.batch_label = menu_button(
            "layers", "Пакет", "Связанные документы с одинаковыми метками сторон",
        )
        self.history_button, _ = menu_button("clock", "История", "Недавние документы")
        self.new_button = make_button(
            "Новый файл", icon_name="plus", tooltip="Или перетащите файлы или папку в окно (Ctrl+O)",
        )
        self.new_button.setIcon(icon("plus", t.INK_2, 16, t.ACCENT))
        for widget in (self.batch_button, self.history_button, self.new_button):
            doc_row.addWidget(widget)
        row.addWidget(self.doc_actions)

        self.history_menu = PopupMenu(self, 340)
        self.batch_menu = PopupMenu(self, 340)
        self.set_save_state("saved")
        self.set_mode("start")

    def set_mode(self, mode: str) -> None:
        """start — стартовый экран, doc — экран документа."""
        doc = mode == "doc"
        self.crumbs.setVisible(doc)
        self.spacer.setVisible(not doc)
        self.doc_actions.setVisible(doc)
        self.start_actions.setVisible(not doc)
        self.brand.setEnabled(doc)

    def set_batch_size(self, count: int) -> None:
        self.batch_label.setText(f"Пакет · {count}" if count else "Пакет")
        self.batch_button.setMinimumWidth(self.batch_button.layout().sizeHint().width())

    def set_save_state(self, state: str) -> None:
        if state == "error":
            self.save_icon.setPixmap(pixmap("alert", t.DANGER, 14))
            self.save_icon.show()
            text = "Ошибка сохранения"
        elif state == "saving":
            self.save_icon.hide()
            text = "Сохранение…"
        else:
            self.save_icon.setPixmap(pixmap("check", t.READY, 14))
            self.save_icon.show()
            text = "Сохранено"
            self.save_label.setToolTip("Документ сохранен в истории на этом компьютере")
        self.save_label.setText(text)
        set_prop(self.save_label, "state", state)
