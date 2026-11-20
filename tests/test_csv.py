"""Порт src/lib/anonymize/csv.test.ts и проверка BOM (App.tsx handleExportCsv)."""

from __future__ import annotations

from md_blinder.anonymize.csv_export import csv_bytes, entities_to_csv
from md_blinder.anonymize.types import Entity, Position as P


def make_entity(type: str, placeholder: str, positions: list, **overrides) -> Entity:
    data = dict(id=placeholder, value="", anonymize=True, manual=False)
    data.update(overrides)
    return Entity(type=type, placeholder=placeholder, positions=[P(*p) for p in positions], **data)


def _single(source_text: str, value: str, type_: str, placeholder: str) -> Entity:
    start = source_text.index(value)
    return make_entity(type_, placeholder, [(start, start + len(value))])


def test_starts_with_sep_hint():
    assert entities_to_csv([], "").split("\r\n")[0] == "sep=,"


def test_exact_header_row():
    assert entities_to_csv([], "").split("\r\n")[1] == "Плейсхолдер,Тип,Оригинальное значение,Вхождений"


def test_includes_only_active_entities():
    source_text = "Acme Corp works with Beta Inc"
    a = source_text.index("Acme Corp")
    b = source_text.index("Beta Inc")
    active = make_entity("COMPANY", "[COMPANY_1]", [(a, a + len("Acme Corp"))])
    inactive = make_entity("COMPANY", "[COMPANY_2]", [(b, b + len("Beta Inc"))], anonymize=False)
    csv = entities_to_csv([active, inactive], source_text)
    assert "[COMPANY_1]" in csv
    assert "[COMPANY_2]" not in csv


def test_uses_current_slice_and_positions_count():
    source_text = "John Smith met John Smith twice"
    value = "John Smith"
    first = source_text.index(value)
    second = source_text.index(value, first + 1)
    entity = make_entity("PERSON", "[PERSON_1]", [(first, first + len(value)), (second, second + len(value))],
                         value="stale cached value")
    assert entities_to_csv([entity], source_text).split("\r\n")[2] == "[PERSON_1],PERSON,John Smith,2"


def test_quotes_value_with_comma():
    source_text = "Client: Acme, Ltd signed today"
    entity = _single(source_text, "Acme, Ltd", "COMPANY", "[COMPANY_1]")
    assert entities_to_csv([entity], source_text).split("\r\n")[2] == '[COMPANY_1],COMPANY,"Acme, Ltd",1'


def test_doubles_internal_quotes():
    source_text = 'Known as "Acme" locally'
    entity = _single(source_text, '"Acme"', "COMPANY", "[COMPANY_1]")
    assert entities_to_csv([entity], source_text).split("\r\n")[2] == '[COMPANY_1],COMPANY,"""Acme""",1'


def test_formula_injection_guard():
    source_text = "Formula: =SUM(A1) end"
    entity = _single(source_text, "=SUM(A1)", "ACCOUNT", "[ACCOUNT_1]")
    row = entities_to_csv([entity], source_text).split("\r\n")[2]
    value_field = row.split(",")[2]
    assert value_field.startswith("'")
    assert value_field == "'=SUM(A1)"


def test_joins_rows_with_crlf():
    source_text = "Alpha Beta"
    entity = _single(source_text, "Alpha", "PERSON", "[PERSON_1]")
    csv = entities_to_csv([entity], source_text)
    assert len(csv.split("\r\n")) == 3
    assert "\n\n" not in csv


# Дополнительно (нет в TS): BOM добавляется так же, как в App.tsx.

def test_csv_bytes_adds_utf8_bom():
    source_text = "Alpha Beta"
    entity = _single(source_text, "Alpha", "PERSON", "[PERSON_1]")
    data = csv_bytes(source_text, [entity])
    assert data.startswith(b"\xef\xbb\xbf")
    assert data[3:].decode("utf-8") == entities_to_csv([entity], source_text)
