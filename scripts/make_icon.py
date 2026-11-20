"""Собирает иконку программы из рисунков SVG.

Источники лежат в src/md_blinder/ui/assets/icon: app.svg для размеров от 32 точек и
app-small.svg (без тонких строк) для 16–24 точек. Скрипт пишет app.ico для exe и
ярлыков и PNG всех размеров для окна и панели задач. Запускать после правки SVG:

    QT_QPA_PLATFORM=offscreen python scripts/make_icon.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ICON_DIR = Path(__file__).resolve().parents[1] / "src" / "md_blinder" / "ui" / "assets" / "icon"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SMALL_MAX = 24  # до этого размера включительно — упрощенный рисунок


def render(size: int) -> QImage:
    source = ICON_DIR / ("app-small.svg" if size <= SMALL_MAX else "app.svg")
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise SystemExit(f"Не удалось прочитать {source}")
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def dib_bytes(image: QImage) -> bytes:
    """Рисунок в формате BMP внутри ICO: 32 бита BGRA снизу вверх и маска прозрачности."""
    size = image.width()
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    rows = []
    for y in range(size - 1, -1, -1):
        rows.append(bytes(image.constScanLine(y))[: size * 4])
    mask_row = ((size + 31) // 32) * 4
    return header + b"".join(rows) + bytes(mask_row * size)


def build_ico(images: dict[int, QImage]) -> bytes:
    """ICO с рисунками всех размеров: 256 точек в PNG, остальные в BMP (так их читают все версии Windows)."""
    blobs = [(size, png_bytes(img) if size >= 256 else dib_bytes(img)) for size, img in images.items()]
    offset = 6 + 16 * len(blobs)
    out = [struct.pack("<HHH", 0, 1, len(blobs))]
    for size, blob in blobs:
        side = 0 if size >= 256 else size
        out.append(struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(blob), offset))
        offset += len(blob)
    out.extend(blob for _, blob in blobs)
    return b"".join(out)


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841
    images = {size: render(size) for size in SIZES}
    for size, image in images.items():
        image.save(str(ICON_DIR / f"app-{size}.png"), "PNG")
    (ICON_DIR / "app.ico").write_bytes(build_ico(images))
    print(f"Готово: {ICON_DIR / 'app.ico'} и PNG {', '.join(map(str, SIZES))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
