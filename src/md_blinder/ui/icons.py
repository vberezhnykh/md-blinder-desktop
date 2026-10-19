"""Контурные значки из src/components/icons.tsx, нарисованные через QtSvg."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_PATHS = {
    "upload": '<path d="M12 15V3"/><path d="m7 8 5-5 5 5"/><path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
    "home": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9"/><path d="M10 21v-6h4v6"/>',
    "layers": '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-up": '<path d="m6 15 6-6 6 6"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "alert": '<circle cx="12" cy="12" r="9"/><path d="M12 8v4"/><path d="M12 16h.01"/>',
    "download": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
    "copy": '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
    "table": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M10 4v16"/>',
    "shield": '<path d="M12 3 4.5 6v5.5c0 4.6 3.2 8.4 7.5 9.5 4.3-1.1 7.5-4.9 7.5-9.5V6L12 3Z"/><path d="m9 12 2 2 4-4"/>',
    "backspace": '<path d="M21 5H9l-6 7 6 7h12a1 1 0 0 0 1-1V6a1 1 0 0 0-1-1Z"/><path d="m16 10-4 4"/><path d="m12 10 4 4"/>',
}
_STROKE = {"check": 2.4, "alert": 2.2}

_MARK = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 22 22" fill="{color}">'
    '<rect x="2" y="3" width="18" height="3.5" rx="1"/><rect x="2" y="9.25" width="11" height="3.5" rx="1"/>'
    '<rect x="2" y="15.5" width="15" height="3.5" rx="1"/></svg>'
)


def svg(name: str, color: str) -> str:
    if name == "mark":
        return _MARK.format(color=color)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="{_STROKE.get(name, 2)}" stroke-linecap="round" stroke-linejoin="round">{_PATHS[name]}</svg>'
    )


@lru_cache(maxsize=256)
def pixmap(name: str, color: str, size: int = 16, ratio: float = 2.0) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg(name, color).encode()))
    pm = QPixmap(round(size * ratio), round(size * ratio))
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter, QRectF(0, 0, pm.width(), pm.height()))
    painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def icon(name: str, color: str, size: int = 16, hover_color: str | None = None) -> QIcon:
    result = QIcon(pixmap(name, color, size))
    if hover_color:
        result.addPixmap(pixmap(name, hover_color, size), QIcon.Mode.Active)
    return result
