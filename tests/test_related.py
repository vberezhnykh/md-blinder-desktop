"""Порт src/lib/anonymize/relatedOccurrences.test.ts."""

from __future__ import annotations

import uuid

from md_blinder.anonymize.apply import candidate_reason
from md_blinder.anonymize.related import find_related_occurrences
from md_blinder.anonymize.types import Entity, Position as P


def make_entity(type: str, value: str, placeholder: str, positions: list, **overrides) -> Entity:
    data = dict(id=str(uuid.uuid4()), anonymize=True, manual=False)
    data.update(overrides)
    return Entity(type=type, value=value, placeholder=placeholder,
                  positions=[P(*p) for p in positions], **data)


def find(entities, pred):
    return next((e for e in entities if pred(e)), None)


def _check_candidates(source_text, entity, result, candidates, reason):
    for candidate in candidates:
        assert candidate.candidate is True
        assert candidate.anonymize is False
        assert candidate.related_to == entity.id
        assert candidate.candidate_note is None
        assert candidate_reason([entity, *result], candidate) == reason
        for pos in candidate.positions:
            assert source_text[pos.start:pos.end] == candidate.value


def test_person_surname_and_initials():
    source_text = (
        "Иванов Иван Иванович подписал договор. Позже Иванова видели в офисе. "
        "Завизировал И.И. Иванов лично."
    )
    full_name = "Иванов Иван Иванович"
    start = source_text.index(full_name)
    entity = make_entity("PERSON", full_name, "[PERSON_1]", [(start, start + len(full_name))])

    result = find_related_occurrences(source_text, [entity])
    surname_only = find(result, lambda e: e.value == "Иванова")
    initials_form = find(result, lambda e: e.value == "И.И. Иванов")
    assert surname_only is not None
    assert initials_form is not None
    _check_candidates(source_text, entity, result, [surname_only, initials_form], "Похоже на [PERSON_1]")


def test_company_short_name_and_acronym():
    source_text = "Acme Industrial Holdings Ltd (далее — Acme). Годовой отчёт AIH утверждён."
    full_name = "Acme Industrial Holdings Ltd"
    start = source_text.index(full_name)
    entity = make_entity("COMPANY", full_name, "[COMPANY_1]", [(start, start + len(full_name))])

    result = find_related_occurrences(source_text, [entity])
    short_name = find(result, lambda e: e.value == "Acme")
    acronym = find(result, lambda e: e.value == "AIH")
    assert short_name is not None
    assert acronym is not None
    _check_candidates(source_text, entity, result, [short_name, acronym], "Похоже на [COMPANY_1]")


def test_no_candidate_when_only_occurrence_is_claimed():
    source_text = "Beta Corp"
    entity = make_entity("COMPANY", "Beta Corp", "[COMPANY_1]", [(0, len(source_text))])
    assert find_related_occurrences(source_text, [entity]) == []


def test_repeat_of_known_value_on_uncovered_position():
    source_text = "Иванов Иван Иванович подписал акт. Отчёт передала Иванова. Позже Иванова заходила снова."
    full_name = "Иванов Иван Иванович"
    full_start = source_text.index(full_name)
    entity1 = make_entity("PERSON", full_name, "[PERSON_1]", [(full_start, full_start + len(full_name))])
    first_ivanova = source_text.index("Иванова")
    entity2 = make_entity("PERSON", "Иванова", "[PERSON_2]", [(first_ivanova, first_ivanova + len("Иванова"))])

    result = find_related_occurrences(source_text, [entity1, entity2])
    repeat = find(result, lambda e: e.value == "Иванова")
    assert repeat is not None
    last = source_text.rindex("Иванова")
    assert repeat.positions == [P(last, last + 7)]


def test_full_repeats_keep_dismissed_positions():
    text = "Acme approved. Acme signed. Acme confirmed."
    active = make_entity("COMPANY", "Acme", "[COMPANY_1]", [(0, 4)])
    dismissed = make_entity("COMPANY", "Acme", "[COMPANY_2]", [(15, 19)], anonymize=False, dismissed=True)
    result = find_related_occurrences(text, [active, dismissed])
    assert len(result) == 1
    assert result[0].positions == [P(28, 32)]
    assert result[0].value == "Acme"

    person_text = "Alice Smith. Alice Smith."
    person = make_entity("PERSON", "Alice Smith", "[PERSON_1]", [(0, 11)])
    assert find_related_occurrences(person_text, [person])[0].positions == [P(13, 24)]


def test_candidate_placeholder_follows_existing_numbers():
    source_text = "Иванов Иван Иванович. Иванова тоже здесь."
    full_name = "Иванов Иван Иванович"
    start = source_text.index(full_name)
    entity = make_entity("PERSON", full_name, "[PERSON_1]", [(start, start + len(full_name))])
    surname_only = find(find_related_occurrences(source_text, [entity]), lambda e: e.value == "Иванова")
    assert surname_only is not None
    assert surname_only.placeholder == "[PERSON_2]"


def test_generic_and_lowercase_words_of_a_name_are_not_searched_alone():
    # Ошибочная находка вроде «Senior Executives of the Parties» не должна порождать
    # кандидатов «Parties» и «Executives» по всему тексту, а «Upstream Providers»
    # (аббревиатура «UP») — предлог «up».
    source_text = "Senior Executives of the Parties met. The Parties agreed to set up Upstream Providers."
    person = make_entity("PERSON", "Senior Executives of the Parties", "[PERSON_1]", [(0, 32)])
    start = source_text.index("Upstream Providers")
    company = make_entity("COMPANY", "Upstream Providers", "[COMPANY_1]", [(start, start + 18)])
    result = find_related_occurrences(source_text, [person, company])
    assert [e.value for e in result] == []


def test_acronym_matches_only_in_capitals():
    source_text = "Upstream Partners Group (UPG) will upg the lines; UPG pays."
    entity = make_entity("COMPANY", "Upstream Partners Group", "[COMPANY_1]", [(0, 23)])
    result = find_related_occurrences(source_text, [entity])
    acronym = find(result, lambda e: e.value == "UPG")
    assert acronym is not None and len(acronym.positions) == 2
    assert find(result, lambda e: e.value == "upg") is None



def test_phone_repeat_with_other_separators_is_found():
    source_text = "Тел.: +7 (903) 796-12-34. Звоните по номеру 8 - 903 - 796 12 34 или +7 903 7961234."
    entity = make_entity("PHONE", "+7 (903) 796-12-34", "[PHONE_1]", [(6, 24)])
    result = find_related_occurrences(source_text, [entity])
    values = sorted(v for e in result for v in [source_text[p.start:p.end] for p in e.positions])
    assert values == ["+7 903 7961234"]


def test_long_masked_image_does_not_stall_the_search():
    # Картинка документа (data URI в сотни тысяч символов) маскируется пробелами той же
    # длины. Неограниченный разделитель телефона перебирал такую строку квадратично:
    # документ на 350 КБ с одной картинкой искался 9 минут.
    import time

    head = "Тел. +7 (903) 796-12-34, e-mail ivan.petrov@example.com, И. И. Иванов.\n"
    image = "![фото](data:image/jpeg;base64," + "A" * 300_000 + ")"
    source_text = head + image + "\n7 ivan Иванов\n"
    entities = [
        make_entity("PHONE", "+7 (903) 796-12-34", "[PHONE_1]", [(5, 23)]),
        make_entity("EMAIL", "ivan.petrov@example.com", "[EMAIL_1]", [(32, 55)]),
        make_entity("PERSON", "Иванов Иван Иванович", "[PERSON_1]", [(63, 69)]),
    ]
    started = time.perf_counter()
    find_related_occurrences(source_text, entities)
    assert time.perf_counter() - started < 3
