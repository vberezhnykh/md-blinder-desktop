"""Сбор файлов из папок с любой вложенностью и пути выгрузки пакета в папку."""

from pathlib import Path, PurePosixPath

from md_blinder.folders import collect_files, export_plan, export_relative_path


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_folder_expands_to_supported_files_at_any_depth(tmp_path):
    root = tmp_path / "Клиент"
    touch(root / "b.docx")
    touch(root / "A.PDF")
    touch(root / "2024" / "март" / "глубоко" / "акт.xlsx")
    touch(root / "2024" / "счет.pdf")
    touch(root / "readme.txt")
    touch(root / "old.doc")
    touch(root / "2024" / "old.xls")
    touch(root / "~$b.docx")  # временный файл Word
    touch(root / ".hidden" / "x.docx")
    collected = collect_files([root])
    names = [collected.name(p) for p in collected.paths]
    assert names == [
        "Клиент/A.PDF",
        "Клиент/b.docx",
        "Клиент/2024/счет.pdf",
        "Клиент/2024/март/глубоко/акт.xlsx",
    ]
    assert all(p.is_file() for p in collected.paths)
    assert collected.legacy == 2
    assert collected.folders == [root]


def test_loose_files_keep_plain_names_and_duplicates_are_dropped(tmp_path):
    root = tmp_path / "папка"
    inner = touch(root / "договор.docx")
    loose = touch(tmp_path / "отдельно.pdf")
    other = touch(tmp_path / "заметка.txt")  # выбран напрямую: тип определит обработка
    collected = collect_files([loose, root, inner, other])
    assert collected.paths == [loose, inner, other]
    assert [collected.name(p) for p in collected.paths] == ["отдельно.pdf", "папка/договор.docx", "заметка.txt"]


def test_empty_folder(tmp_path):
    (tmp_path / "пусто" / "внутри").mkdir(parents=True)
    collected = collect_files([tmp_path / "пусто"])
    assert collected.paths == [] and collected.legacy == 0


def test_export_path_keeps_subfolders_and_stays_inside_target():
    assert export_relative_path("Клиент/2024/акт.docx") == PurePosixPath("Клиент/2024/акт.md")
    assert export_relative_path("договор.pdf") == PurePosixPath("договор.md")
    assert export_relative_path("../../etc/passwd.pdf") == PurePosixPath("etc/passwd.md")
    assert export_relative_path("C:\\Users\\a.docx") == PurePosixPath("C_/Users/a.md")
    assert export_relative_path('a?b/con.docx') == PurePosixPath("a_b/_con.md")
    assert export_relative_path("") == PurePosixPath("document.md")
    assert export_relative_path("/") == PurePosixPath("document.md")


def test_export_plan_numbers_clashing_names():
    plan = export_plan(["П/акт.docx", "П/акт.pdf", "П/Акт.xlsx", "П/sub/акт.pdf"])
    assert plan == [
        PurePosixPath("П/акт.md"),
        PurePosixPath("П/акт (2).md"),
        PurePosixPath("П/Акт (3).md"),
        PurePosixPath("П/sub/акт.md"),
    ]
