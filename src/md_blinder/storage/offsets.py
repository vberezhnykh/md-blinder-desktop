"""Пересчет смещений между UTF-16 (браузерная версия) и кодовыми точками Python.

Различаются они только для символов вне BMP (эмодзи и т. п.), поэтому для
обычного текста пересчет ничего не меняет.
"""

from __future__ import annotations

from bisect import bisect_right


class OffsetMap:
    def __init__(self, text: str) -> None:
        # Индексы (в кодовых точках) символов, занимающих в UTF-16 две единицы.
        self._astral = [i for i, ch in enumerate(text) if ord(ch) > 0xFFFF]
        self._length = len(text)

    @property
    def trivial(self) -> bool:
        return not self._astral

    def to_utf16(self, offset: int) -> int:
        if not self._astral:
            return offset
        return offset + bisect_right(self._astral, offset - 1)

    def from_utf16(self, offset: int) -> int:
        if not self._astral:
            return offset
        # Ищем кодовую точку cp, для которой to_utf16(cp) == offset (или ближайшую слева).
        lo, hi = 0, self._length
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.to_utf16(mid) <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo
