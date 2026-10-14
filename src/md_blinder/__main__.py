"""Точка входа: python -m md_blinder.

Ключ --self-check запускает проверку без окна: импорт зависимостей, OCR
(если языковые файлы скачаны) и калибровку модели (если она скачана).
Результат пишется в self-check.txt в каталоге данных, код возврата 0 — все в порядке.
"""

from __future__ import annotations

import sys


def self_check() -> int:
    import traceback

    from .paths import data_dir

    lines: list[str] = []
    ok = True
    try:
        import numpy  # noqa: F401
        import onnxruntime
        import pymupdf
        import tokenizers  # noqa: F401
        from PySide6 import QtWidgets  # noqa: F401

        lines.append(f"onnxruntime {onnxruntime.__version__}, PyMuPDF {pymupdf.VersionBind}: OK")
        from .ui.theme import FONTS_DIR

        fonts = len(list(FONTS_DIR.glob("*.ttf")))
        ok &= fonts >= 11
        lines.append(f"Шрифты IBM Plex: {'OK' if fonts >= 11 else 'не найдены'} ({fonts})")
        from .components import model_asset, tessdata

        if tessdata.is_tessdata_present():
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((50, 100), "Self check 12345", fontsize=24)
            pix = page.get_pixmap(dpi=144)
            scan = pymupdf.open()
            scan_page = scan.new_page()
            scan_page.insert_image(scan_page.rect, pixmap=pix)
            textpage = scan_page.get_textpage_ocr(
                language="rus+ukr+eng", dpi=144, full=True, tessdata=str(tessdata.tessdata_dir()))
            text = scan_page.get_text(textpage=textpage)
            ok &= "12345" in text
            lines.append(f"OCR: {'OK' if '12345' in text else 'не распознал тестовый текст'}")
        else:
            lines.append("OCR: языковые файлы не скачаны, проверка пропущена")
        if model_asset.is_model_present():
            from .ner.engine import load_model

            load_model(model_asset.model_dir())
            lines.append("Модель NER: калибровка пройдена")
        else:
            lines.append("Модель NER: не скачана, проверка пропущена")
    except Exception:  # noqa: BLE001
        ok = False
        lines.append(traceback.format_exc())
    report = "\n".join(lines)
    print(report)
    try:
        target = data_dir()
        target.mkdir(parents=True, exist_ok=True)
        # BOM нужен Windows PowerShell 5.1: без него Get-Content читает файл как ANSI.
        (target / "self-check.txt").write_text(report + "\n", encoding="utf-8-sig")
    except OSError:
        pass
    return 0 if ok else 1


def main() -> int:
    if "--self-check" in sys.argv:
        return self_check()

    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow
    from .ui.theme import STYLESHEET, register_fonts

    app = QApplication(sys.argv)
    register_fonts()
    from .ui.gc_guard import install as install_gc

    install_gc(app)
    app.setApplicationName("MD Blinder")
    app.setOrganizationName("md-blinder")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    window.show_startup_notice(sys.argv)
    window.updates.startup()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
