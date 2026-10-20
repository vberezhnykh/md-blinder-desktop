import datetime as dt
import zipfile

import pytest

from md_blinder.convert.file_type import detect_file_kind
from md_blinder.convert.xlsx import cell_text, xlsx_to_markdown
from md_blinder.jobs import ProcessingJob, UserFacingError

from fixtures import make_xlsx


def test_detect_xlsx_and_legacy_xls(tmp_path):
    book = make_xlsx(tmp_path / "book.xlsx", {"Лист1": {"A1": "x"}})
    assert detect_file_kind(book) == "xlsx"
    legacy = tmp_path / "old.xls"
    legacy.write_bytes(b"\xd0\xcf\x11\xe0")
    assert detect_file_kind(legacy) == "legacy-xls"


def test_sheets_become_sections_with_tables(tmp_path):
    path = make_xlsx(tmp_path / "b.xlsx", {
        "Стороны": {"A1": "Имя", "B1": "Телефон", "A2": "Company Alpha", "B2": "+7 916 765-43-21"},
        "Платежи": {"A1": "Сумма", "A2": 1500},
    })
    md = xlsx_to_markdown(path)
    assert md == (
        "## Стороны\n\n| Имя | Телефон |\n| --- | --- |\n| Company Alpha | +7 916 765-43-21 |\n\n"
        "## Платежи\n\n| Сумма |\n| --- |\n| 1500 |"
    )


def test_empty_rows_and_columns_are_dropped(tmp_path):
    path = make_xlsx(tmp_path / "b.xlsx", {"S": {"B2": "Имя", "D2": "Город", "B5": "Анна", "D5": "Тверь"}})
    assert xlsx_to_markdown(path) == "## S\n\n| Имя | Город |\n| --- | --- |\n| Анна | Тверь |"


def test_hidden_sheet_marked_and_empty_sheet_skipped(tmp_path):
    path = make_xlsx(tmp_path / "b.xlsx", {"Пусто": {}, "Тайное": {"A1": "secret"}}, hidden=("Тайное",))
    assert xlsx_to_markdown(path) == "## Тайное (скрытый лист)\n\n| secret |\n| --- |"


def test_cell_breaks_and_pipes(tmp_path):
    path = make_xlsx(tmp_path / "b.xlsx", {"S": {"A1": "Адрес", "A2": "г. Тверь,\nул. Лесная | д. 1"}})
    assert "| г. Тверь,<br>ул. Лесная \\| д. 1 |" in xlsx_to_markdown(path)


def test_cell_text_formats():
    assert cell_text(None) == ""
    assert cell_text(3.0) == "3"
    assert cell_text(0.1 + 0.2) == "0.3"
    assert cell_text(79161234567) == "79161234567"
    assert cell_text(0.25, "0%") == "25%"
    assert cell_text(True) == "ИСТИНА"
    assert cell_text(dt.datetime(2026, 1, 2)) == "02.01.2026"
    assert cell_text(dt.datetime(2026, 1, 2, 9, 30)) == "02.01.2026 09:30"
    assert cell_text(dt.time(9, 30)) == "09:30"
    assert cell_text("  текст ") == "текст"


def test_dates_and_percent_from_file(tmp_path):
    path = make_xlsx(tmp_path / "b.xlsx", {"S": {
        "A1": "Дата", "B1": "Доля",
        "A2": (dt.datetime(2026, 3, 15), "dd.mm.yyyy"), "B2": (0.125, "0.0%"),
    }})
    assert "| 15.03.2026 | 12.5% |" in xlsx_to_markdown(path)


def test_formula_uses_cached_value(tmp_path):
    path = make_xlsx(tmp_path / "b.xlsx", {"S": {"A1": "Итого", "A2": "=1+1"}})
    # openpyxl не считает формулы; дописываем значение, которое сохранил бы Excel.
    saved = tmp_path / "saved.xlsx"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(saved, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                assert b"<f>1+1</f><v />" in data
                data = data.replace(b"<f>1+1</f><v />", b"<f>1+1</f><v>2</v>")
            zout.writestr(item, data)
    assert xlsx_to_markdown(saved) == "## S\n\n| Итого |\n| --- |\n| 2 |"


def run_job(path):
    return ProcessingJob(path)._run()


def test_job_rejects_empty_book_xls_and_password_protected(tmp_path):
    empty = make_xlsx(tmp_path / "empty.xlsx", {"S": {}})
    with pytest.raises(UserFacingError, match="нет заполненных ячеек"):
        run_job(empty)
    legacy = tmp_path / "old.xls"
    legacy.write_bytes(b"\xd0\xcf\x11\xe0" + b"\0" * 100)
    with pytest.raises(UserFacingError, match="Формат .xls не поддерживается"):
        run_job(legacy)
    protected = tmp_path / "secret.xlsx"
    protected.write_bytes(b"\xd0\xcf\x11\xe0" + b"\0" * 100)
    with pytest.raises(UserFacingError, match="защищен паролем"):
        run_job(protected)
