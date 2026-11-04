"""Имя выгружаемого файла: значения из документа заменяются метками."""

from __future__ import annotations

import pytest

from md_blinder.anonymize.file_name import anonymize_file_name
from md_blinder.anonymize.types import Entity, Position
from md_blinder.folders import export_plan


def entity(type_: str, value: str, placeholder: str, **kwargs) -> Entity:
    fields = {"anonymize": True, **kwargs}
    return Entity(id=value, type=type_, value=value, placeholder=placeholder, positions=[Position(0, 1)], **fields)


ENTITIES = [
    entity("PERSON", "Иванов Иван Иванович", "[PERSON_1]"),
    entity("COMPANY", "ООО «Альфа Трейд»", "[COMPANY_1]"),
    entity("COMPANY", "Acme Corp", "[COMPANY_2]"),
    entity("PERSON", "John Smith", "[PERSON_2]", anonymize=False),
    entity("PERSON", "Петров Петр", "[PERSON_3]", candidate=True),
]


@pytest.mark.parametrize("name, expected", [
    ("Договор аренды Иванов И.И.docx", "Договор аренды [PERSON_1].docx"),
    ("Договор с Ивановым.docx", "Договор с [PERSON_1].docx"),
    ("NDA_Acme_Corp.pdf", "NDA_[COMPANY_2].pdf"),
    ("Альфа Трейд/2024/акт.docx", "[COMPANY_1]/2024/акт.docx"),
    ("Договор 1.docx", "Договор 1.docx"),
    # Выключенная замена и непроверенный кандидат имя не меняют.
    ("John Smith NDA.docx", "John Smith NDA.docx"),
    ("Петров Петр.docx", "Петров Петр.docx"),
])
def test_file_name_values_become_labels(name: str, expected: str) -> None:
    assert anonymize_file_name(name, ENTITIES) == expected


def test_batch_folder_keeps_structure_with_labels() -> None:
    names = ["Альфа Трейд/2024/акт.docx", "Альфа Трейд/2024/счет.pdf"]
    plan = export_plan([anonymize_file_name(n, ENTITIES) for n in names])
    assert [str(p) for p in plan] == ["[COMPANY_1]/2024/акт.md", "[COMPANY_1]/2024/счет.md"]
