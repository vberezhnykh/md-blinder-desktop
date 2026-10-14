"""Итоговый текст с подсветкой (порт AnonymizedPreview.tsx).

Метки замены — темные плашки цензуры моноширинным шрифтом, фрагменты без замены
(непроверенные кандидаты и выключенные замены) — светлая заливка с волнистым
подчеркиванием. Текст виджета посимвольно совпадает с тем, что копируется и
экспортируется: плашки рисуются под текстом, а не вставляются в него.
"""

from __future__ import annotations

import bisect
import re
from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QTextBrowser, QVBoxLayout, QWidget

from ..anonymize.types import Entity
from . import theme as t

_TABLE_LINE = re.compile(r"^\s*\|")
BADGE_GAP = 8

_GAP = QTextCharFormat()
_GAP.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
_GAP.setFontLetterSpacing(BADGE_GAP)


class ResultPreview(QTextBrowser):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("resultPreview")
        self.setOpenLinks(False)
        self.setUndoRedoEnabled(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._src_starts: list[int] = []
        self._doc_positions: list[tuple[int, int]] = []  # диапазоны отмеченных фрагментов в документе
        self._badges: list[tuple[int, int]] = []  # диапазоны плашек в документе (UTF-16)
        self._focused: Optional[tuple[int, int]] = None

    def render(self, text: str, source_text: str, segments: list, entities: list[Entity]) -> None:
        by_placeholder = {e.placeholder: e for e in entities}
        marks = sorted(
            ((p.start, p.end, e) for e in entities if not e.anonymize and not e.dismissed for p in e.positions),
            key=lambda m: m[0],
        )
        pieces: list[tuple[str, str, int, Optional[Entity]]] = []
        for seg in segments:
            if seg.opaque:
                chunk = text[seg.out_start:seg.out_end]
                pieces.append((chunk, "replaced", seg.src_start, by_placeholder.get(chunk)))
                continue
            cur = seg.src_start
            for start, end, entity in marks:
                start = max(start, cur)
                end = min(end, seg.src_end)
                if start >= end:
                    continue
                if start > cur:
                    pieces.append((source_text[cur:start], "plain", cur, None))
                pieces.append((source_text[start:end], "unreplaced", start, entity))
                cur = end
            if cur < seg.src_end:
                pieces.append((source_text[cur:seg.src_end], "plain", cur, None))

        scroll = self.verticalScrollBar().value()
        doc = QTextDocument(self)
        doc.setDefaultFont(t.font(t.SERIF, 15))
        doc.setDocumentMargin(18)
        cursor = QTextCursor(doc)
        block_fmt = QTextBlockFormat()
        block_fmt.setLineHeight(130, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.setBlockFormat(block_fmt)
        plain = QTextCharFormat()
        badge = QTextCharFormat()
        badge.setFont(t.font(t.MONO, 11.7, 500), QTextCharFormat.FontPropertiesInheritanceBehavior.FontPropertiesSpecifiedOnly)
        badge.setForeground(QColor(t.REDACT_INK))
        unreplaced = QTextCharFormat()
        unreplaced.setBackground(QColor(t.PENDING_WASH))
        unreplaced.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
        unreplaced.setUnderlineColor(QColor(t.PENDING_LINE))
        self._src_starts, self._doc_positions, self._badges = [], [], []
        self._focused = None
        for chunk, kind, src_start, entity in pieces:
            fmt = plain
            if kind == "replaced":
                fmt = badge
            elif kind == "unreplaced" and entity is not None:
                fmt = QTextCharFormat(unreplaced)
                fmt.setToolTip(
                    f"Не проверено: {entity.placeholder}" if entity.candidate else f"Замена выключена: {entity.placeholder}"
                )
            start = cursor.position()
            if kind == "replaced" and chunk:
                # Отступы плашки (padding 6px в вебе) делаются межбуквенным интервалом
                # у символа перед плашкой и у последнего символа плашки: текст не меняется.
                if start > cursor.block().position():
                    prev = QTextCursor(doc)
                    prev.setPosition(start - 1)
                    prev.setPosition(start, QTextCursor.MoveMode.KeepAnchor)
                    prev.mergeCharFormat(_GAP)
                cursor.insertText(chunk[:-1], fmt)
                tail = QTextCharFormat(fmt)
                tail.merge(_GAP)
                cursor.insertText(chunk[-1], tail)
            else:
                cursor.insertText(chunk, fmt)
            if kind != "plain":
                self._src_starts.append(src_start)
                self._doc_positions.append((start, cursor.position()))
            if kind == "replaced":
                self._badges.append((start, cursor.position()))
        self._apply_table_font(doc)
        self.setDocument(doc)
        self.verticalScrollBar().setValue(scroll)

    def _apply_table_font(self, doc: QTextDocument) -> None:
        block = doc.begin()
        mono = QTextCharFormat()
        mono.setFont(t.font(t.MONO, 12.5), QTextCharFormat.FontPropertiesInheritanceBehavior.FontPropertiesSpecifiedOnly)
        while block.isValid():
            if _TABLE_LINE.match(block.text()):
                cursor = QTextCursor(block)
                cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                cursor.mergeCharFormat(mono)
            block = block.next()

    def _rects(self, start: int, end: int) -> list[QRectF]:
        doc = self.document()
        layout = doc.documentLayout()
        dy = -self.verticalScrollBar().value()
        rects: list[QRectF] = []
        block = doc.findBlock(start)
        while block.isValid() and block.position() < end:
            block_rect = layout.blockBoundingRect(block)
            tl = block.layout()
            for i in range(tl.lineCount()):
                line = tl.lineAt(i)
                ls = block.position() + line.textStart()
                le = ls + line.textLength()
                a, b = max(start, ls), min(end, le)
                if a >= b:
                    continue
                x1 = line.cursorToX(a - block.position())[0]
                x2 = line.cursorToX(b - block.position())[0]
                # Плашка по высоте шрифта метки, а не всей строки с межстрочным интервалом.
                h = line.ascent() + line.descent()
                y = block_rect.top() + line.y() + (line.height() - h) / 2 + dy
                rects.append(QRectF(block_rect.left() + x1, y, x2 - x1, h))
            block = block.next()
        return rects

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.REDACT_BG))
        visible = QRectF(self.viewport().rect())
        for start, end in self._badges:
            for r in self._rects(start, end):
                if r.intersects(visible):
                    painter.drawRoundedRect(r.adjusted(-BADGE_GAP / 2 - 1, -1, -BADGE_GAP / 2 + 1, 1), 3, 3)
        painter.end()
        super().paintEvent(event)
        if self._focused is not None:
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(t.ACCENT), 2))
            for r in self._rects(*self._focused):
                painter.drawRoundedRect(r.adjusted(-5, -3, 5, 3), 4, 4)
            painter.end()

    def focus_source_pos(self, pos: int) -> None:
        """Прокручивает к фрагменту, который в исходнике начинается в pos, и обводит его."""
        if not self._src_starts:
            return
        idx = bisect.bisect_right(self._src_starts, pos) - 1
        idx = max(idx, 0)
        self._focused = self._doc_positions[idx]
        rects = self._rects(*self._focused)
        if rects:
            bar = self.verticalScrollBar()
            bar.setValue(int(bar.value() + rects[0].center().y() - self.viewport().height() / 2))
        self.viewport().update()


def _legend() -> QWidget:
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    sample = QLabel("[ЗАМЕНА]")
    sample.setStyleSheet(
        f"background: {t.REDACT_BG}; color: {t.REDACT_INK}; font-family: '{t.MONO}'; font-size: 10px;"
        " font-weight: 500; padding: 1px 6px; border-radius: 3px;"
    )
    row.addWidget(sample)
    closed = QLabel("закрыто")
    closed.setObjectName("hint")
    row.addWidget(closed)
    row.addSpacing(10)
    text = QLabel("текст")
    text.setStyleSheet(
        f"background: {t.PENDING_WASH}; font-size: 12px; padding: 0 1px; border-bottom: 1.5px dotted {t.PENDING_LINE};"
    )
    row.addWidget(text)
    left = QLabel("осталось как есть")
    left.setObjectName("hint")
    row.addWidget(left)
    return box


class ResultPane(QWidget):
    """Колонка «Результат»: заголовок с легендой и текст в рамке."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        head = QHBoxLayout()
        head.setContentsMargins(2, 0, 2, 0)
        self.title = QLabel("Результат")
        self.title.setObjectName("paneTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(_legend())
        layout.addLayout(head)
        box = QFrame()
        box.setObjectName("previewBox")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(1, 1, 1, 1)
        self.preview = ResultPreview()
        inner.addWidget(self.preview)
        layout.addWidget(box, 1)
