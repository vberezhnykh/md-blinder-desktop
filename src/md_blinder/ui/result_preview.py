"""Итоговый текст с подсветкой (порт AnonymizedPreview.tsx).

Метки замены показаны цветной плашкой, фрагменты без замены (непроверенные
кандидаты и выключенные замены) подчеркнуты волнистой линией. Текст виджета
посимвольно совпадает с тем, что копируется и экспортируется.
"""

from __future__ import annotations

import bisect
import re
from typing import Optional

from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import QTextBrowser

from ..anonymize.types import Entity
from .theme import entity_fill

_TABLE_LINE = re.compile(r"^\s*\|")


class ResultPreview(QTextBrowser):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setUndoRedoEnabled(False)
        self._src_starts: list[int] = []
        self._doc_positions: list[int] = []

    def render(self, text: str, source_text: str, segments: list, entities: list[Entity]) -> None:
        by_placeholder = {e.placeholder: e for e in entities}
        marks = sorted(
            (
                (p.start, p.end, e)
                for e in entities if not e.anonymize and not e.dismissed
                for p in e.positions
            ),
            key=lambda m: m[0],
        )

        pieces: list[tuple[str, str, int, Optional[Entity]]] = []  # (text, kind, src_start, entity)
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

        doc = QTextDocument(self)
        base_font = QFont(self.font())
        doc.setDefaultFont(base_font)
        cursor = QTextCursor(doc)
        plain = QTextCharFormat()
        self._src_starts = []
        self._doc_positions = []
        for chunk, kind, src_start, entity in pieces:
            fmt = QTextCharFormat(plain)
            if kind == "replaced":
                fmt.setBackground(entity_fill(entity.type if entity else "OTHER", 1.6))
                fmt.setFontWeight(QFont.Weight.DemiBold)
            elif kind == "unreplaced" and entity is not None:
                fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
                fmt.setUnderlineColor(QColor("#c4740a") if entity.candidate else QColor("#8a93a3"))
                fmt.setToolTip(
                    f"Не проверено: {entity.placeholder}" if entity.candidate else f"Замена выключена: {entity.placeholder}"
                )
            if kind != "plain":
                self._src_starts.append(src_start)
                self._doc_positions.append(cursor.position())
            cursor.insertText(chunk, fmt)
        self._apply_table_font(doc)
        self.setDocument(doc)

    def _apply_table_font(self, doc: QTextDocument) -> None:
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        block = doc.begin()
        while block.isValid():
            if _TABLE_LINE.match(block.text()):
                cursor = QTextCursor(block)
                cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                fmt = QTextCharFormat()
                fmt.setFontFamilies([mono.family(), "Courier New", "monospace"])
                cursor.mergeCharFormat(fmt)
            block = block.next()

    def focus_source_pos(self, pos: int) -> None:
        """Прокручивает к фрагменту, который в исходнике начинается в pos или раньше."""
        if not self._src_starts:
            return
        idx = bisect.bisect_right(self._src_starts, pos) - 1
        if idx < 0:
            idx = 0
        cursor = self.textCursor()
        cursor.setPosition(self._doc_positions[idx])
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
