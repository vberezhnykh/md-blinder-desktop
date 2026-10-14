# -*- mode: python ; coding: utf-8 -*-
# Сборка onedir: папка с MD Blinder.exe. Модель NER и языки OCR в сборку не входят,
# программа скачивает их по запросу в %LOCALAPPDATA%\md-blinder.

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("src/md_blinder/anonymize/presidio.LICENSE", "md_blinder/anonymize"),
    # Шрифты IBM Plex (OFL) — как в веб-версии, без загрузки со сторонних серверов.
    ("src/md_blinder/ui/assets/fonts", "md_blinder/ui/assets/fonts"),
    # Модели ONNX и настройки pymupdf_layout (около 50 МБ): поиск таблиц в PDF.
    # Пакет ищет их рядом со своими модулями.
    *collect_data_files("pymupdf.layout"),
]

# Модули Qt, которые программа не использует: без них папка сборки заметно меньше.
excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQml",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
    "PySide6.QtBluetooth", "PySide6.QtNetwork", "PySide6.QtSql", "PySide6.QtTest",
    "tkinter", "matplotlib", "pytest",
]

a = Analysis(
    ["launcher.py"],
    pathex=["src"],
    datas=datas,
    hiddenimports=["md_blinder.ner.engine", *collect_submodules("pymupdf.layout")],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MD Blinder",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="MD Blinder", upx=False)
