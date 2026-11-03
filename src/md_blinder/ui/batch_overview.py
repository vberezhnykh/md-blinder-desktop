"""Окно «Все замены пакета»: метки всех документов пакета в одном списке.

Сверху — замены, похожие по написанию (ошибки OCR, опечатки), с кнопкой
«Объединить». Ниже — все метки по типам: значения из всех документов и где
метка встречается; любую можно объединить с другой меткой того же типа,
а щелчок по метке открывает поле, где ее можно сменить.
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
from ..text_utils import plural
from . import theme as t
from .icons import icon
from .side_panel import _label_order
from .widgets import LabelEditor, Select, icon_label, make_button


def _short_name(name: str) -> str:
    return name.rsplit("/", 1)[-1] or name


def _docs_text(docs: Sequence[str]) -> str:
    names = ", ".join(_short_name(d) for d in docs[:2])
    return names + (f" и еще {len(docs) - 2}" if len(docs) > 2 else "")


def _ellipsis(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


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
    relabel = Signal(str, str)  # текущая метка, новая (своя или другая метка того же типа)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Все замены пакета")
        self.resize(820, 760)
        self.setObjectName("dialog")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(24, 20, 16, 12)
        head.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        title = QLabel("Все замены пакета")
        title.setObjectName("dialogTitle")
        self.summary = _wrap(QLabel())
        self.summary.setObjectName("secondary")
        titles.addWidget(title)
        titles.addWidget(self.summary)
        head.addLayout(titles, 1)
        close = make_button(kind="iconButton", tooltip="Закрыть")
        close.setIcon(icon("x", t.INK_2, 16))
        close.clicked.connect(self.close)
        head.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(head)
        search_row = QHBoxLayout()
        search_row.setContentsMargins(24, 0, 24, 12)
        self.filter = QLineEdit()
        self.filter.setObjectName("search")
        self.filter.addAction(icon("search", t.INK_3, 16), QLineEdit.ActionPosition.LeadingPosition)
        self.filter.setPlaceholderText("Найти метку или значение")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(lambda _t: self._render_list())
        search_row.addWidget(self.filter)
        outer.addLayout(search_row)
        self.area = QScrollArea()
        self.area.setObjectName("dialogBody")
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("panelBody")
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(24, 16, 24, 16)
        self.body.setSpacing(0)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.area.setWidget(body)
        outer.addWidget(self.area, 1)
        footer = QFrame()
        footer.setObjectName("dialogFooter")
        foot = QHBoxLayout(footer)
        foot.setContentsMargins(24, 14, 24, 14)
        foot.setSpacing(8)
        note = QLabel("Изменения сохраняются сразу, отменить последнее можно в уведомлении внизу окна.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        foot.addWidget(note, 1)
        done = make_button("Закрыть")
        done.clicked.connect(self.close)
        foot.addWidget(done)
        outer.addWidget(footer)
        self.targets: list[BatchTarget] = []
        self.pairs: list[tuple[BatchTarget, BatchTarget]] = []
        self._merging: Optional[str] = None
        self._editing: Optional[str] = None
        self._doc_count = 0

    def set_docs(self, docs: Sequence[BatchMember]) -> None:
        self.targets = batch_targets(docs)
        self.pairs = similar_target_pairs(self.targets)
        self._doc_count = len(docs)
        self._merging = None
        self._editing = None
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
            f"{plural(self._doc_count, ('документ', 'документа', 'документов'))}, "
            f"{plural(len(self.targets), ('метка', 'метки', 'меток'))}, в нескольких документах: {shared}. "
            "Одинаковые значения получают одну метку во всем пакете."
        )
        if pairs:
            self.body.addWidget(self._pairs_card(pairs))
            self.body.addSpacing(8)
        if not shown:
            empty = QLabel("Ничего не найдено." if query else "В документах пакета пока нет замен.")
            empty.setObjectName("muted")
            empty.setContentsMargins(0, 8, 0, 0)
            self.body.addWidget(empty)
        for type_ in ALL_ENTITY_TYPES:
            items = [t for t in shown if t.type == type_]
            if not items:
                continue
            self.body.addWidget(self._group_head(type_, len(items)))
            for target in items:
                self.body.addWidget(self._row(target))
        self.area.verticalScrollBar().setValue(scroll)

    @staticmethod
    def _matches(target: BatchTarget, query: str) -> bool:
        if not query:
            return True
        return query in target.placeholder.lower() or any(query in f.lower() for f in target.forms)

    @staticmethod
    def _group_head(type_: str, count: int) -> QWidget:
        head = QWidget()
        row = QHBoxLayout(head)
        row.setContentsMargins(0, 14, 0, 6)
        row.setSpacing(8)
        dot = QLabel()
        dot.setObjectName("typeDot")
        dot.setStyleSheet(f"background: {t.entity_solid(type_).name()};")
        name = QLabel(type_)
        name.setObjectName("strongSmall")
        number = QLabel(str(count))
        number.setObjectName("hint")
        row.addWidget(dot)
        row.addWidget(name)
        row.addWidget(number)
        row.addStretch(1)
        return head

    @staticmethod
    def _chip(placeholder: str) -> QLabel:
        chip = QLabel(placeholder)
        chip.setObjectName("cellPlaceholder")
        return chip

    def _pairs_card(self, pairs: list[tuple[BatchTarget, BatchTarget]]) -> QFrame:
        card = QFrame()
        card.setObjectName("pairCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(12, 10, 12, 12)
        box.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(icon_label("info", t.INFO_INK, 16))
        title = QLabel(f"Похоже на одно и то же · {len(pairs)}")
        title.setObjectName("pairTitle")
        head.addWidget(title)
        head.addStretch(1)
        box.addLayout(head)
        note = _wrap(QLabel(
            "Значения написаны почти одинаково, но получили разные метки. Обычно это ошибка распознавания "
            "скана или опечатка. Объедините, если это одно и то же."
        ))
        note.setObjectName("pairNote")
        box.addWidget(note)
        for first, second in pairs:
            row = QHBoxLayout()
            row.setSpacing(8)
            for i, target in enumerate((first, second)):
                if i:
                    joint = QLabel("и")
                    joint.setObjectName("hint")
                    row.addWidget(joint)
                row.addWidget(self._chip(target.placeholder))
                value = QLabel(_ellipsis(target.value, 40))
                value.setObjectName("body")
                value.setToolTip(f"{'; '.join(target.forms)}\n{_docs_text(target.docs)}")
                row.addWidget(value)
            row.addStretch(1)
            merge = make_button(f"Объединить в {first.placeholder}", small=True)
            merge.setToolTip(f"Значения {second.placeholder} получат метку {first.placeholder} во всех документах")
            merge.clicked.connect(lambda _=False, a=first.placeholder, b=second.placeholder: self.merge.emit(a, b))
            row.addWidget(merge)
            box.addLayout(row)
        return card

    def _label_cell(self, target: BatchTarget) -> QWidget:
        """Метка; щелчок по ней открывает поле, где ее можно поменять."""
        cell = QWidget()
        cell.setFixedWidth(170)
        row = QHBoxLayout(cell)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        button = QPushButton()
        button.setObjectName("labelChip")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip("Сменить метку: другая метка того же типа или своя, например «Поставщик»")
        inner = QHBoxLayout(button)
        inner.setContentsMargins(6, 2, 6, 2)
        inner.setSpacing(4)
        text = QLabel(target.placeholder)
        text.setObjectName("labelChipText")
        inner.addWidget(text)
        inner.addWidget(icon_label("pencil", t.INK_3, 12))
        for child in button.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        button.setFixedSize(inner.sizeHint())
        button.clicked.connect(lambda _=False, p=target.placeholder: self._start_edit(p))
        row.addWidget(button)
        row.addStretch(1)
        return cell

    def _row(self, target: BatchTarget) -> QFrame:
        frame = QFrame()
        frame.setObjectName("overviewRow")
        box = QVBoxLayout(frame)
        box.setContentsMargins(8, 8, 4, 8)
        box.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(12)
        if self._editing == target.placeholder:
            labels = sorted((x.placeholder for x in self.targets if x.type == target.type), key=_label_order)
            editor = LabelEditor(target.placeholder, labels)
            editor.setFixedWidth(260)
            editor.chosen.connect(lambda text, old=target.placeholder: self.relabel.emit(old, text))
            head.addWidget(editor, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            head.addWidget(self._label_cell(target), 0, Qt.AlignmentFlag.AlignVCenter)
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
        head.addLayout(text, 1)
        others = [x for x in self.targets if x.type == target.type and x.placeholder != target.placeholder]
        if self._editing == target.placeholder:
            cancel = make_button("Отмена", kind="ghost", small=True)
            cancel.clicked.connect(lambda _=False: self._start_edit(None))
            head.addWidget(cancel, 0, Qt.AlignmentFlag.AlignVCenter)
        elif others and self._merging != target.placeholder:
            open_button = make_button("Объединить…", kind="ghost", small=True)
            open_button.setToolTip("Объединить с другой меткой того же типа во всех документах пакета")
            open_button.clicked.connect(lambda _=False, p=target.placeholder: self._start_merge(p))
            head.addWidget(open_button, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addLayout(head)
        if self._editing == target.placeholder:
            hint = _wrap(QLabel(
                "Впишите свою метку, например «Поставщик», или выберите другую метку этого типа, "
                "чтобы поменять их местами."
            ))
            hint.setObjectName("hint")
            box.addWidget(hint)
        if self._merging == target.placeholder:
            row = QHBoxLayout()
            row.setSpacing(8)
            choice = Select()
            choice.setObjectName("compact")
            choice.addItem("объединить с…", "")
            for other in others:
                choice.addItem(f"{other.placeholder} · {other.value[:40]}", other.placeholder)
            ok = make_button("Объединить", kind="primary", small=True)
            ok.setEnabled(False)
            choice.currentIndexChanged.connect(lambda _i: ok.setEnabled(bool(choice.currentData())))
            cancel = make_button("Отмена", kind="ghost", small=True)
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
        self._editing = None
        self._render_list()

    def _start_edit(self, placeholder: Optional[str]) -> None:
        self._editing = placeholder
        self._merging = None
        self._render_list()
