"""Порт src/lib/anonymize/batch.test.ts: согласованные метки в пакете документов."""

from dataclasses import dataclass, field

from md_blinder.anonymize.apply import build_anonymized_text
from md_blinder.anonymize.batch import (
    align_batch,
    batch_entities_to_csv,
    batch_to_markdown,
    reconcile_batch_placeholders,
    seed_from_batch,
)
from md_blinder.anonymize.types import Entity, Position

_seq = 0


def entity(text: str, type_: str, value: str, placeholder: str, **kw) -> Entity:
    """Сущность по всем вхождениям значения в тексте."""
    global _seq
    _seq += 1
    positions = []
    i = text.find(value)
    while i != -1:
        positions.append(Position(i, i + len(value)))
        i = text.find(value, i + len(value))
    return Entity(id=f"e{_seq}", type=type_, value=value, placeholder=placeholder, positions=positions, **kw)


@dataclass
class Doc:
    source_text: str
    entities: list[Entity] = field(default_factory=list)
    name: str = "doc.docx"


def doc(text: str, parties: list[tuple[str, str, str]], name: str = "doc.docx") -> Doc:
    return Doc(text, [entity(text, t, v, p) for t, v, p in parties], name)


FIRST_TEXT = "Договор между ООО «Альфа» и ООО «Бета». ООО «Бета» обязуется…"


def first() -> Doc:
    return doc(FIRST_TEXT, [("COMPANY", "ООО «Альфа»", "[COMPANY_1]"), ("COMPANY", "ООО «Бета»", "[COMPANY_2]")])


def placeholders(entities: list[Entity]) -> list[str]:
    return [e.placeholder for e in entities]


def test_shared_party_takes_label_from_other_document():
    second = doc("Договор между ООО «Бета» и ООО «Гамма».",
                 [("COMPANY", "ООО «Бета»", "[COMPANY_1]"), ("COMPANY", "ООО «Гамма»", "[COMPANY_2]")])
    result = reconcile_batch_placeholders(second.entities, second.source_text, [first()])
    assert [(e.value, e.placeholder) for e in result] == [("ООО «Бета»", "[COMPANY_2]"), ("ООО «Гамма»", "[COMPANY_3]")]
    assert build_anonymized_text(second.source_text, result) == "Договор между [COMPANY_2] и [COMPANY_3]."


def test_links_by_any_occurrence_form_ignoring_case():
    text = "БЕТА и ООО «Бета»"
    beta = entity(text, "COMPANY", "БЕТА", "[COMPANY_1]")
    beta.positions.extend(entity(text, "COMPANY", "ООО «Бета»", "").positions)
    assert reconcile_batch_placeholders([beta], text, [first()])[0].placeholder == "[COMPANY_2]"


def test_links_company_by_name_without_legal_form():
    second = doc("Бета и АО «Гамма»", [("COMPANY", "Бета", "[COMPANY_1]"), ("COMPANY", "АО «Гамма»", "[COMPANY_2]")])
    result = reconcile_batch_placeholders(second.entities, second.source_text, [first()])
    assert placeholders(result) == ["[COMPANY_2]", "[COMPANY_3]"]


def test_ambiguous_name_without_legal_form_does_not_link():
    parent = doc("ООО «Бета» и АО «Бета»",
                 [("COMPANY", "ООО «Бета»", "[COMPANY_1]"), ("COMPANY", "АО «Бета»", "[COMPANY_2]")])
    second = doc("Бета", [("COMPANY", "Бета", "[COMPANY_1]")])
    assert reconcile_batch_placeholders(second.entities, second.source_text, [parent])[0].placeholder == "[COMPANY_3]"


def test_two_entities_of_one_document_never_share_a_label():
    text = "ООО «Бета» и Бета"
    full = entity(text, "COMPANY", "ООО «Бета»", "[COMPANY_1]")
    short = entity(text, "COMPANY", "Бета", "[COMPANY_2]")
    short.positions = [Position(13, 17)]
    assert placeholders(reconcile_batch_placeholders([full, short], text, [first()])) == ["[COMPANY_2]", "[COMPANY_3]"]


def test_types_numbered_independently_and_candidates_after_used_numbers():
    text = "ООО «Дельта», Иван Петров, Петров"
    delta = entity(text, "COMPANY", "ООО «Дельта»", "[COMPANY_1]")
    person = entity(text, "PERSON", "Иван Петров", "[PERSON_1]")
    candidate = entity(text, "COMPANY", "Петров", "[COMPANY_2]", anonymize=False, candidate=True)
    candidate.positions = [Position(27, 33)]
    result = reconcile_batch_placeholders([delta, person, candidate], text, [first()])
    assert placeholders(result) == ["[COMPANY_3]", "[PERSON_1]", "[COMPANY_4]"]


def test_free_label_kept_and_same_list_returned():
    second = doc("ООО «Бета» и ООО «Сигма»",
                 [("COMPANY", "ООО «Бета»", "[COMPANY_2]"), ("COMPANY", "ООО «Сигма»", "[COMPANY_5]")])
    assert reconcile_batch_placeholders(second.entities, second.source_text, [first()]) is second.entities


def test_align_chain_of_contracts():
    a = first()
    second = doc("ООО «Бета» и ООО «Гамма»",
                 [("COMPANY", "ООО «Бета»", "[COMPANY_1]"), ("COMPANY", "ООО «Гамма»", "[COMPANY_2]")])
    third = doc("ООО «Гамма» и ООО «Альфа»",
                [("COMPANY", "ООО «Гамма»", "[COMPANY_1]"), ("COMPANY", "ООО «Альфа»", "[COMPANY_2]")])
    x, y, z = align_batch([a, second, third])
    assert x is a
    assert placeholders(y.entities) == ["[COMPANY_2]", "[COMPANY_3]"]
    assert placeholders(z.entities) == ["[COMPANY_3]", "[COMPANY_1]"]


def test_seed_marks_party_missed_by_ner():
    text = "Поставщик ООО «Бета» и покупатель Иванов. Бета-версия."
    result = seed_from_batch(text, [], [first()])
    beta = next(e for e in result if e.value == "ООО «Бета»")
    assert beta.anonymize and not beta.candidate
    assert beta.positions == [Position(10, 20)]
    aligned = reconcile_batch_placeholders(result, text, [first()])
    assert build_anonymized_text(text, aligned) == "Поставщик [COMPANY_2] и покупатель Иванов. Бета-версия."


def test_seed_extends_entity_with_same_form_and_leaves_other_markup():
    text = "ООО «Бета», ООО «БЕТА» и ООО «Бета Плюс»"
    beta = entity(text, "COMPANY", "ООО «Бета»", "[COMPANY_1]")
    beta.positions = [Position(0, 10)]
    plus = entity(text, "COMPANY", "ООО «Бета Плюс»", "[COMPANY_2]")
    result = seed_from_batch(text, [beta, plus], [first()])
    assert len(result) == 2
    assert result[0].positions == [Position(0, 10), Position(12, 22)]
    assert result[1] == plus
    assert beta.positions == [Position(0, 10)]  # входной список не меняется


def test_seed_accepts_candidate_covering_confirmed_form():
    text = "Стороны: ООО «Бета»."
    candidate = entity(text, "COMPANY", "ООО «Бета»", "[COMPANY_1]", anonymize=False, candidate=True,
                       candidate_note="Низкая уверенность модели")
    [result] = seed_from_batch(text, [candidate], [first()])
    assert (result.candidate, result.anonymize, result.candidate_note) == (False, True, None)
    assert candidate.candidate  # входной список не меняется


def test_seed_ignores_disabled_and_unreviewed_values():
    off = Doc("ООО «Бета»", [entity("ООО «Бета»", "COMPANY", "ООО «Бета»", "[COMPANY_1]", anonymize=False)])
    assert seed_from_batch("ООО «Бета»", [], [off]) == []


def test_batch_markdown_uses_neutral_headings():
    second = doc("Договор с Гаммой.", [("COMPANY", "Гаммой", "[COMPANY_3]")], "Договор с Гаммой.docx")
    md = batch_to_markdown([first(), second])
    assert md == (
        "# Документ 1\n\nДоговор между [COMPANY_1] и [COMPANY_2]. [COMPANY_2] обязуется…\n\n---\n\n"
        "# Документ 2\n\nДоговор с [COMPANY_3].\n"
    )
    assert "docx" not in md


def test_batch_csv_groups_by_label():
    a = first()
    a.name = "alpha-beta.docx"
    second = doc("Договор с Гаммой.", [("COMPANY", "Гаммой", "[COMPANY_3]")], "Договор с Гаммой.docx")
    assert batch_entities_to_csv([a, second]).split("\r\n") == [
        "sep=,",
        "Плейсхолдер,Тип,Оригинальные значения,Документы,Вхождений",
        "[COMPANY_1],COMPANY,ООО «Альфа»,Документ 1 (alpha-beta.docx),1",
        "[COMPANY_2],COMPANY,ООО «Бета»,Документ 1 (alpha-beta.docx),2",
        "[COMPANY_3],COMPANY,Гаммой,Документ 2 (Договор с Гаммой.docx),1",
    ]
