"""Окно «Замены пакета»: метки всех документов пакета в одном списке.

Сверху — замены, похожие по написанию (ошибки OCR, опечатки), с кнопкой
«Объединить». Ниже — все метки по типам: значения из всех документов и где
метка встречается; любую можно объединить с другой меткой того же типа.
Окно ничего не меняет само: решение уходит сигналом merge в MainWindow.
"""

from __future__ import annotations

from typing import Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..anonymize.batch import BatchMember, BatchTarget, batch_targets, similar_target_pairs
from ..anonymize.types import ALL_ENTITY_TYPES
from .widgets import Select


def _short_name(name: str) -> str:
    return name.rsplit("/", 1)[-1] or name


def _docs_text(docs: Sequence[str]) -> str:
    names = ", ".join(_short_name(d) for d in docs[:2])
    return names + (f" и еще {len(docs) - 2}" if len(docs) > 2 else "")


def _wrap(label: QLabel) -> QLabel:
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


class BatchOverview(QDialog):
    merge = Signal(str, str)  # метка, которая остается; метка, которая вливается в нее

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Замены пакета")
        self.resize(760, 720)
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(10)
        self.summary = _wrap(QLabel())
        self.summary.setObjectName("subtitle")
        outer.addWidget(self.summary)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Найти метку или значение")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(lambda _t: self._render_list())
        outer.addWidget(self.filter)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("panelBody")
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(0, 4, 8, 4)
        self.body.setSpacing(4)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.area.setWidget(body)
        outer.addWidget(self.area, 1)
        self.targets: list[BatchTarget] = []
        self.pairs: list[tuple[BatchTarget, BatchTarget]] = []
        self._merging: Optional[str] = None
        self._doc_count = 0

    def set_docs(self, docs: Sequence[BatchMember]) -> None:
        self.targets = batch_targets(docs)
        self.pairs = similar_target_pairs(self.targets)
        self._doc_count = len(docs)
        self._merging = None
        self._render_list()

    # --- отрисовка ---

    def _render_list(self) -> None:
        scroll = self.area.verticalScrollBar().value()
        _clear(self.body)
        query = self.filter.text().strip().lower()
        shown = [t for t in self.targets if self._matches(t, query)]
        pairs = [p for p in self.pairs if self._matches(p[0], query) or self._matches(p[1], query)]
        shared = sum(1 for t in self.targets if len(t.docs) > 1)
        self.summary.setText(
            f"Документов в пакете: {self._doc_count}. Меток: {len(self.targets)}, "
            f"из них в нескольких документах: {shared}. Одинаковые значения получают одну метку во всем пакете."
        )
        if pairs:
            self.body.addWidget(self._section("ПОХОЖЕ НА ОДНО ЗНАЧЕНИЕ"))
            note = _wrap(QLabel(
                "Эти значения написаны почти одинаково, но получили разные метки. Обычно это ошибка "
                "распознавания скана или опечатка. Объедините, если это одно и то же."
            ))
            note.setObjectName("muted")
            self.body.addWidget(note)
            for first, second in pairs:
                self.body.addWidget(self._pair_card(first, second))
            self.body.addSpacing(12)
        if not shown:
            empty = QLabel("Ничего не найдено." if query else "В документах пакета пока нет замен.")
            empty.setObjectName("muted")
            self.body.addWidget(empty)
        for type_ in ALL_ENTITY_TYPES:
            items = [t for t in shown if t.type == type_]
            if not items:
                continue
            self.body.addWidget(self._section(f"{type_} · {len(items)}"))
            for target in items:
                self.body.addWidget(self._row(target))
            self.body.addSpacing(8)
        self.area.verticalScrollBar().setValue(scroll)

    @staticmethod
    def _matches(target: BatchTarget, query: str) -> bool:
        if not query:
            return True
        return query in target.placeholder.lower() or any(query in f.lower() for f in target.forms)

    @staticmethod
    def _section(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionHead")
        label.setContentsMargins(0, 8, 0, 2)
        return label

    def _value_line(self, target: BatchTarget) -> QWidget:
        line = QWidget()
        row = QHBoxLayout(line)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        placeholder = QLabel(target.placeholder)
        placeholder.setObjectName("cellPlaceholder")
        row.addWidget(placeholder, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(1)
        value = _wrap(QLabel("; ".join(target.forms[:4]) + ("…" if len(target.forms) > 4 else "")))
        value.setObjectName("cellValue")
        value.setToolTip("\n".join(target.forms))
        docs = _wrap(QLabel(_docs_text(target.docs)))
        docs.setObjectName("hint")
        docs.setToolTip("\n".join(target.docs))
        text.addWidget(value)
        text.addWidget(docs)
        row.addLayout(text, 1)
        return line

    def _pair_card(self, first: BatchTarget, second: BatchTarget) -> QFrame:
        card = QFrame()
        card.setObjectName("candidateCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(12, 10, 12, 10)
        box.setSpacing(6)
        box.addWidget(self._value_line(first))
        box.addWidget(self._value_line(second))
        buttons = QHBoxLayout()
        merge = QPushButton(f"Объединить в {first.placeholder}")
        merge.setObjectName("primary")
        merge.setToolTip(f"Значения {second.placeholder} получат метку {first.placeholder} во всех документах")
        merge.clicked.connect(lambda _=False, a=first.placeholder, b=second.placeholder: self.merge.emit(a, b))
        buttons.addWidget(merge)
        buttons.addStretch(1)
        box.addLayout(buttons)
        return card

    def _row(self, target: BatchTarget) -> QFrame:
        frame = QFrame()
        frame.setObjectName("entityRow")
        box = QVBoxLayout(frame)
        box.setContentsMargins(6, 6, 6, 6)
        box.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(self._value_line(target), 1)
        others = [t for t in self.targets if t.type == target.type and t.placeholder != target.placeholder]
        if others and self._merging != target.placeholder:
            open_button = QPushButton("Объединить…")
            open_button.setObjectName("small")
            open_button.setToolTip("Объединить с другой меткой того же типа во всех документах пакета")
            open_button.clicked.connect(lambda _=False, p=target.placeholder: self._start_merge(p))
            head.addWidget(open_button, 0, Qt.AlignmentFlag.AlignTop)
        box.addLayout(head)
        if self._merging == target.placeholder:
            row = QHBoxLayout()
            choice = Select()
            choice.setObjectName("compact")
            choice.addItem("объединить с…", "")
            for other in others:
                choice.addItem(f"{other.placeholder} · {other.value[:40]}", other.placeholder)
            ok = QPushButton("Объединить")
            ok.setObjectName("primary")
            ok.setEnabled(False)
            choice.currentIndexChanged.connect(lambda _i: ok.setEnabled(bool(choice.currentData())))
            cancel = QPushButton("Отмена")
            cancel.setObjectName("small")
            # Остается метка, с которой объединяют: так же, как в списке находок документа.
            ok.clicked.connect(lambda _=False: choice.currentData() and self.merge.emit(choice.currentData(),
                                                                                         target.placeholder))
            cancel.clicked.connect(lambda _=False: self._start_merge(None))
            row.addWidget(choice, 1)
            row.addWidget(ok)
            row.addWidget(cancel)
            box.addLayout(row)
        return frame

    def _start_merge(self, placeholder: Optional[str]) -> None:
        self._merging = placeholder
        self._render_list()
