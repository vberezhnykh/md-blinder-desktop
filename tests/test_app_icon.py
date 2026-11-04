import struct
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from md_blinder.ui.app_icon import ICON_DIR, SIZES, app_icon

ROOT = Path(__file__).resolve().parents[1]
ACCENT = (0x3A, 0x3D, 0x8F)


def _ico_entries(data: bytes) -> list[tuple[int, bytes]]:
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1)
    entries = []
    for i in range(count):
        width, _h, _c, _r, _planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        assert bpp == 32
        entries.append((width or 256, data[offset:offset + size]))
    return entries


def test_ico_has_every_size_windows_asks_for():
    entries = _ico_entries((ICON_DIR / "app.ico").read_bytes())
    assert sorted(size for size, _ in entries) == sorted(SIZES)
    for size, blob in entries:
        if size == 256:
            assert blob.startswith(b"\x89PNG")
        else:
            # BMP без заголовка файла: высота удвоена (рисунок + маска).
            header_size, width, height = struct.unpack_from("<Iii", blob, 0)
            assert (header_size, width, height) == (40, size, size * 2)


def test_png_sizes_match_names(qapp):
    for size in SIZES:
        image = QImage(str(ICON_DIR / f"app-{size}.png"))
        assert (image.width(), image.height()) == (size, size)
        # Фон иконки — фирменный сиреневый, углы прозрачные.
        edge = image.pixelColor(max(2, size // 16), size // 2)
        assert (edge.red(), edge.green(), edge.blue()) == ACCENT
        assert image.pixelColor(0, 0).alpha() == 0


def test_app_icon_offers_small_and_large_sizes(qapp):
    sizes = app_icon().availableSizes()
    assert QSize(16, 16) in sizes and QSize(256, 256) in sizes


def test_build_embeds_icon_into_exe():
    spec = (ROOT / "md_blinder.spec").read_text(encoding="utf-8")
    assert 'icon="src/md_blinder/ui/assets/icon/app.ico"' in spec
    assert "src/md_blinder/ui/assets/icon/*.png" in spec
