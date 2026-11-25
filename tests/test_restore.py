"""Обратная подстановка: метки в ответе заменяются значениями из таблицы замен."""

from __future__ import annotations

from md_blinder.anonymize.restore import restore_map, restore_text
from md_blinder.anonymize.types import Entity, Position


def entity(id_: str, type_: str, placeholder: str, start: int, end: int, **kwargs) -> Entity:
    fields = {"anonymize": True, **kwargs}
    return Entity(id=id_, type=type_, value="", placeholder=placeholder, positions=[Position(start, end)], **fields)


MAPPING = {"[PERSON_1]": "Иван Петров", "[COMPANY_2]": "ООО «Альфа»", "[Поставщик]": "ООО «Бета»"}


def test_restores_labels_including_escaped_markdown_and_custom_labels() -> None:
    answer = "По договору \\[PERSON\\_1\\] и [company_2] ([ PERSON_1 ]). [Поставщик] обязан."
    result = restore_text(answer, MAPPING)
    assert result.text == "По договору Иван Петров и ООО «Альфа» (Иван Петров). ООО «Бета» обязан."
    assert result.replaced == 4
    assert result.unknown == []


def test_reports_labels_missing_from_the_table() -> None:
    result = restore_text("[PERSON_7] и [ADDRESS_1], ссылка [1], [REF_2].", MAPPING)
    assert result.text == "[PERSON_7] и [ADDRESS_1], ссылка [1], [REF_2]."
    assert result.unknown == ["[PERSON_7]", "[ADDRESS_1]"]


def test_map_uses_current_values_and_skips_inactive_entities() -> None:
    text = "Иван  Петров и Acme, Bob, Carl."
    first = [
        entity("p", "PERSON", "[PERSON_1]", 0, 12),
        entity("c", "COMPANY", "[COMPANY_1]", 15, 19),
        entity("off", "PERSON", "[PERSON_2]", 21, 24, anonymize=False),
        entity("cand", "PERSON", "[PERSON_3]", 26, 30, anonymize=False, candidate=True),
    ]
    other = [entity("p2", "PERSON", "[PERSON_1]", 0, 4), entity("x", "PERSON", "[PERSON_4]", 26, 30)]
    mapping = restore_map([(text, first), (text, other)])
    # Первым идет написание из первого документа, пробелы внутри схлопываются.
    assert mapping == {"[PERSON_1]": ["Иван Петров", "Иван"], "[COMPANY_1]": ["Acme"], "[PERSON_4]": ["Carl"]}


def test_bilingual_label_gets_spelling_of_surrounding_text() -> None:
    text = "Адрес: г. Минск, ул. Лесная, 5. Address: 5 Lesnaya St, Minsk."
    address = Entity(
        id="a", type="ADDRESS", value="", placeholder="[ADDRESS_1]", anonymize=True,
        positions=[Position(text.index("г. Минск"), text.index(". Address")),
                   Position(text.index("5 Lesnaya"), len(text) - 1)],
    )
    mapping = restore_map([(text, [address])])
    answer = "| Адрес: [ADDRESS_1] | Address: [ADDRESS_1] |\nСм. [ADDRESS_1].\nSee [ADDRESS_1].\n[ADDRESS_1]"
    assert restore_text(answer, mapping).text == (
        "| Адрес: г. Минск, ул. Лесная, 5 | Address: 5 Lesnaya St, Minsk |\n"
        "См. г. Минск, ул. Лесная, 5.\nSee 5 Lesnaya St, Minsk.\nг. Минск, ул. Лесная, 5"
    )
