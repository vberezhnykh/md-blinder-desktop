"""Точка входа: python -m md_blinder.

Ключ --self-check запускает проверку без окна: импорт зависимостей, чтение XLSX, поиск таблиц в PDF, OCR
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

        providers = onnxruntime.get_available_providers()
        lines.append(
            f"onnxruntime {onnxruntime.__version__} ({', '.join(providers)}), PyMuPDF {pymupdf.VersionBind}: OK"
        )
        # Собранная программа для Windows должна уметь считать на видеокарте.
        if sys.platform == "win32" and getattr(sys, "frozen", False) and "DmlExecutionProvider" not in providers:
            ok = False
            lines.append("onnxruntime собран без DirectML: расчет на видеокарте недоступен")
        from .convert.pdf_tables import find_page_tables, layout_available

        ok &= layout_available()
        table_doc = pymupdf.open()
        table_page = table_doc.new_page()
        for row in range(3):
            for col in range(2):
                cell = pymupdf.Rect(50 + col * 150, 100 + row * 30, 200 + col * 150, 130 + row * 30)
                table_page.draw_rect(cell, color=(0, 0, 0), width=0.7)
                table_page.insert_text((cell.x0 + 5, cell.y1 - 10), f"Cell {row}{col}", fontsize=10)
        spans = [(w[4], pymupdf.Rect(w[:4])) for w in table_page.get_text("words")]
        tables_ok = len(find_page_tables(table_page, spans)) == 1
        ok &= tables_ok
        lines.append(
            "Поиск таблиц в PDF: " + ("OK" if tables_ok else "тестовая таблица не найдена")
            + ("" if layout_available() else ", модель разметки pymupdf_layout не загрузилась")
        )
        import tempfile
        from pathlib import Path

        import openpyxl

        from .convert.xlsx import xlsx_to_markdown

        with tempfile.TemporaryDirectory() as tmp:
            book = openpyxl.Workbook()
            book.active.append(["Cell 00", "Cell 01"])
            book.save(Path(tmp) / "check.xlsx")
            xlsx_ok = "| Cell 00 | Cell 01 |" in xlsx_to_markdown(Path(tmp) / "check.xlsx")
        ok &= xlsx_ok
        lines.append(f"Чтение XLSX: {'OK' if xlsx_ok else 'тестовая таблица не прочитана'}")
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

            model = load_model(model_asset.model_dir())
            where = "видеокарта" if model.device == "gpu" else "CPU"
            lines.append(f"Модель NER: калибровка пройдена ({where}, граф {model.optimization})")
            model.release()
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

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .shell import file_argument
    from .ui.main_window import MainWindow
    from .ui.single_instance import InstanceServer, send_to_running
    from .ui.theme import STYLESHEET, register_fonts

    app = QApplication(sys.argv)
    file_path = file_argument(sys.argv)
    # Программа уже открыта: файл из меню проводника уходит в ее окно.
    if send_to_running(str(file_path) if file_path else None):
        return 0
    instance = InstanceServer()
    instance.listen()
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
    instance.received.connect(window.open_external)
    if file_path:
        QTimer.singleShot(0, lambda: window.open_external(str(file_path)))
    window.updates.startup()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
