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
