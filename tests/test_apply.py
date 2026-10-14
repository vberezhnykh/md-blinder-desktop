"""Порт src/lib/anonymize/apply.test.ts и тех случаев imageSafety.test.ts,
которые вызывают buildAnonymizedText / buildAnonymizedTextWithMap."""

from __future__ import annotations

import logging
import re

import pytest

from md_blinder.anonymize.apply import (
    accept_candidate,
    add_entity_from_selection,
    add_occurrence_to_entity,
    build_anonymized_text,
    build_anonymized_text_with_map,
    change_entity_type,
    compact_placeholder_numbers,
    dedupe_entity_ids,
    entity_current_value,
    merge_entities,
    next_placeholder_number,
)
from md_blinder.anonymize.image_safety import IMAGE_REMOVED_MARKER
from md_blinder.anonymize.placeholder_guard import has_placeholder_collision
from md_blinder.anonymize.types import Entity, Position as P


def make_entity(type: str, placeholder: str, positions: list, **overrides) -> Entity:
    data = dict(id=placeholder, value="", anonymize=True, manual=False)
    data.update(overrides)
    return Entity(
        type=type,
        placeholder=placeholder,
        positions=[p if isinstance(p, P) else P(*p) for p in positions],
        **data,
    )


def find(entities: list[Entity], pred):
    return next((e for e in entities if pred(e)), None)


# buildAnonymizedText

def test_replaces_every_position_of_an_active_entity():
    source_text = "John met John yesterday"
    person = make_entity("PERSON", "[PERSON_1]", [(0, 4), (9, 13)], value="John")
    assert build_anonymized_text(source_text, [person]) == "[PERSON_1] met [PERSON_1] yesterday"


def test_does_not_replace_an_inactive_entity():
    source_text = "John met John yesterday"
    person = make_entity("PERSON", "[PERSON_1]", [(0, 4), (9, 13)], value="John")
    date_word = make_entity("ADDRESS", "[ADDRESS_1]", [(14, 23)], value="yesterday", anonymize=False)
    result = build_anonymized_text(source_text, [person, date_word])
    assert result == "[PERSON_1] met [PERSON_1] yesterday"
    assert "[ADDRESS_1]" not in result


def test_splices_entities_of_different_lengths():
    source_text = "Email john.doe@acme.co and phone +44 20 7946 0958 today"
    email_value = "john.doe@acme.co"
    phone_value = "+44 20 7946 0958"
    es = source_text.index(email_value)
    ps = source_text.index(phone_value)
    email = make_entity("EMAIL", "[EMAIL_1]", [(es, es + len(email_value))], value=email_value)
    phone = make_entity("PHONE", "[PHONE_1]", [(ps, ps + len(phone_value))], value=phone_value)
    assert build_anonymized_text(source_text, [email, phone]) == "Email [EMAIL_1] and phone [PHONE_1] today"


def test_unions_overlapping_replacements(caplog):
    source_text = "Contact Jane Doe today"
    # Пересекающиеся позиции: нарушенный инвариант, который не должен портить вывод.
    person = make_entity("PERSON", "[PERSON_1]", [(8, 16)], value="Jane Doe")
    other = make_entity("OTHER", "[OTHER_1]", [(13, 22)], value="Doe today")
    with caplog.at_level(logging.WARNING, logger="md_blinder.anonymize.apply"):
        result = build_anonymized_text(source_text, [person, other])
    has_person = "[PERSON_1]" in result
    has_other = "[OTHER_1]" in result
    assert has_person != has_other
    assert result == "Contact [PERSON_1]"
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_b4_escapes_preexisting_placeholder_literal():
    source_text = "Existing note [PERSON_1] refers to an earlier draft."
    assert has_placeholder_collision(build_anonymized_text(source_text, [])) is False


def test_b4_real_entity_still_gets_placeholder():
    source_text = "John Doe signed the agreement."
    s = source_text.index("John Doe")
    john = make_entity("PERSON", "[PERSON_1]", [(s, s + len("John Doe"))], value="John Doe")
    assert "[PERSON_1]" in build_anonymized_text(source_text, [john])


# entityCurrentValue

def test_entity_current_value_returns_slice_at_first_position():
    entity = make_entity("PERSON", "[PERSON_1]", [(8, 16)], value="stale value")
    assert entity_current_value("Contact Jane Doe today", entity) == "Jane Doe"


def test_entity_current_value_falls_back_to_value():
    entity = make_entity("PERSON", "[PERSON_1]", [], value="fallback value")
    assert entity_current_value("Contact Jane Doe today", entity) == "fallback value"


# nextPlaceholderNumber

def test_next_placeholder_number():
    entities = [
        make_entity("PERSON", "[PERSON_1]", [(0, 1)]),
        make_entity("PERSON", "[PERSON_2]", [(2, 3)]),
        make_entity("COMPANY", "[COMPANY_1]", [(4, 5)]),
    ]
    assert next_placeholder_number(entities, "PERSON") == 3
    assert next_placeholder_number(entities, "COMPANY") == 2
    assert next_placeholder_number(entities, "IBAN") == 1


def test_next_placeholder_number_empty():
    assert next_placeholder_number([], "PERSON") == 1


# addEntityFromSelection

def test_add_entity_absorbs_partial_overlap():
    source_text = "221B Baker Street, London"
    s = source_text.index("Baker Street")
    address = make_entity("ADDRESS", "[ADDRESS_1]", [(s, s + len("Baker Street"))], value="Baker Street")
    result = add_entity_from_selection([address], source_text, 0, len(source_text), "ADDRESS")
    assert len(result) == 1
    assert result[0].type == "ADDRESS"
    assert result[0].value == "221B Baker Street, London"
    assert result[0].positions == [P(0, len(source_text))]
    assert not any(e.value == "Baker Street" for e in result)


def test_add_entity_keeps_unrelated_entity():
    source_text = "John lives at 221B Baker Street"
    a = source_text.index("Baker Street")
    person = make_entity("PERSON", "[PERSON_1]", [(0, 4)], value="John")
    address = make_entity("ADDRESS", "[ADDRESS_1]", [(a, a + len("Baker Street"))], value="Baker Street")
    sel_start = source_text.index("221B")
    sel_end = a + len("Baker Street")
    result = add_entity_from_selection([person, address], source_text, sel_start, sel_end, "ADDRESS")

    kept = find(result, lambda e: e.type == "PERSON")
    assert kept.value == "John"
    assert kept.positions == [P(0, 4)]
    addresses = [e for e in result if e.type == "ADDRESS"]
    assert len(addresses) == 1
    assert addresses[0].value == "221B Baker Street"
    assert addresses[0].positions == [P(sel_start, sel_end)]


def test_add_entity_auto_adds_exact_duplicates():
    result = add_entity_from_selection([], "Acme Ltd and Acme Ltd", 0, 8, "COMPANY")
    assert len(result) == 1
    assert result[0].placeholder == "[COMPANY_1]"
    assert len(result[0].positions) == 2
    assert result[0].positions == [P(0, 8), P(13, 21)]


def test_partially_absorbed_entity_survives():
    source_text = "Baker Street then Baker Street"
    first = source_text.index("Baker Street")
    second = source_text.index("Baker Street", first + 1)
    n = len("Baker Street")
    address = make_entity("ADDRESS", "[ADDRESS_1]", [(first, first + n), (second, second + n)], value="Baker Street")
    sel_end = source_text.index("then") + len("then")
    result = add_entity_from_selection([address], source_text, 0, sel_end, "ADDRESS")

    survivor = find(result, lambda e: e.placeholder == "[ADDRESS_1]")
    assert survivor is not None
    assert survivor.positions == [P(second, second + n)]
    added = find(result, lambda e: e.value == "Baker Street then")
    assert added is not None
    assert added.positions == [P(0, sel_end)]


def test_add_entity_noop_on_collapsed_selection():
    address = make_entity("ADDRESS", "[ADDRESS_1]", [(5, 17)], value="Baker Street")
    entities = [address]
    assert add_entity_from_selection(entities, "221B Baker Street, London", 5, 5, "ADDRESS") == entities


def test_add_entity_numbering_does_not_collide():
    source_text = "Baker Street office, Main Street office"
    b = source_text.index("Baker Street")
    m = source_text.index("Main Street")
    baker = make_entity("ADDRESS", "[ADDRESS_1]", [(b, b + len("Baker Street"))], value="Baker Street")
    main = make_entity("ADDRESS", "[ADDRESS_2]", [(m, m + len("Main Street"))], value="Main Street")
    result = add_entity_from_selection([baker, main], source_text, b, b + len("Baker Street"), "ADDRESS")
    new_entity = find(result, lambda e: e.value == "Baker Street")
    assert re.fullmatch(r"\[ADDRESS_\d+\]", new_entity.placeholder)
    placeholders = [e.placeholder for e in result]
    assert len(set(placeholders)) == len(placeholders)


def test_add_entity_uuid_id():
    result = add_entity_from_selection([], "Acme Ltd works with Beta Ltd", 0, 8, "COMPANY")
    assert len(result) == 1
    assert result[0].placeholder == "[COMPANY_1]"
    assert result[0].id != result[0].placeholder
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", result[0].id, re.I
    )


# addOccurrenceToEntity

def test_add_occurrence_expands_over_neighbouring_redaction():
    source_text = "Acme Beta"
    target = make_entity("COMPANY", "[COMPANY_1]", [(0, 4)])
    neighbour = make_entity("COMPANY", "[COMPANY_2]", [(5, 9)])
    result = add_occurrence_to_entity([target, neighbour], source_text, 0, 9, target.id)
    assert len(result) == 1
    assert result[0].positions == [P(0, 9)]
    assert build_anonymized_text(source_text, result) == "[COMPANY_1]"
    assert target.positions == [P(0, 4)]


def test_add_occurrence_retains_active_neighbour_coverage():
    source_text = "Acme Beta Gamma"
    target = make_entity("COMPANY", "[COMPANY_1]", [(0, 4)], anonymize=False, candidate=True)
    neighbour = make_entity("COMPANY", "[COMPANY_2]", [(5, 15)])
    result = add_occurrence_to_entity([target, neighbour], source_text, 2, 7, target.id)
    assert result[0].positions == [P(0, 15)]
    assert result[0].anonymize is True
    assert result[0].candidate is False
    assert build_anonymized_text(source_text, result) == "[COMPANY_1]"


def test_add_occurrence_attaches_span_sharing_placeholder():
    source_text = "Acme Ltd works with Acme Corp"
    target = make_entity("COMPANY", "[COMPANY_1]", [(0, 8)], id="target-1", value="Acme Ltd")
    s = source_text.index("Acme Corp")
    e = s + len("Acme Corp")
    result = add_occurrence_to_entity([target], source_text, s, e, "target-1")
    assert len(result) == 1
    assert result[0].placeholder == "[COMPANY_1]"
    assert result[0].positions == [P(0, 8), P(s, e)]


def test_add_occurrence_removes_span_from_other_entity():
    source_text = "Baker Street here, Baker Street there"
    second = source_text.index("Baker Street", 1)
    target = make_entity("ADDRESS", "[ADDRESS_1]", [(34, 39)], id="target-1", value="existing target span")  # "there"
    other = make_entity(
        "ADDRESS", "[ADDRESS_2]", [(0, 12), (second, second + len("Baker Street"))],
        id="other-1", value="Baker Street",
    )
    result = add_occurrence_to_entity([target, other], source_text, 0, 12, "target-1")
    surviving_other = find(result, lambda e: e.id == "other-1")
    assert surviving_other is not None
    assert surviving_other.positions == [P(second, second + len("Baker Street"))]
    assert find(result, lambda e: e.id == "target-1").positions == [P(0, 12), P(34, 39)]


def test_add_occurrence_drops_emptied_entity():
    source_text = "Acme Ltd and Acme Ltd again"
    target = make_entity("COMPANY", "[COMPANY_1]", [(100, 108)], id="target-1", value="Acme Ltd")
    other = make_entity("COMPANY", "[COMPANY_2]", [(0, 8)], id="other-1", value="Acme Ltd")
    result = add_occurrence_to_entity([target, other], source_text, 0, 8, "target-1")
    assert not any(e.id == "other-1" for e in result)
    assert find(result, lambda e: e.id == "target-1").positions == [P(0, 8), P(100, 108)]


def test_add_occurrence_noop_when_span_overlaps_target():
    target = make_entity("COMPANY", "[COMPANY_1]", [(0, 8)], id="target-1", value="Acme Ltd")
    result = add_occurrence_to_entity([target], "Acme Ltd works with Acme Corp", 0, 8, "target-1")
    assert result[0].positions == [P(0, 8)]


def test_add_occurrence_noop_when_id_missing():
    target = make_entity("COMPANY", "[COMPANY_1]", [(0, 8)], id="target-1", value="Acme Ltd")
    entities = [target]
    assert add_occurrence_to_entity(entities, "Acme Ltd works with Acme Corp", 21, 30, "no-such-id") is entities


def test_add_occurrence_noop_when_collapsed():
    target = make_entity("COMPANY", "[COMPANY_1]", [(0, 8)], id="target-1", value="Acme Ltd")
    entities = [target]
    assert add_occurrence_to_entity(entities, "Acme Ltd works with Acme Corp", 10, 10, "target-1") is entities


def test_add_occurrence_keeps_positions_sorted():
    source_text = "Acme Corp works with Acme Ltd"
    s = source_text.index("Acme Ltd")
    target = make_entity("COMPANY", "[COMPANY_1]", [(s, s + len("Acme Ltd"))], id="target-1", value="Acme Ltd")
    result = add_occurrence_to_entity([target], source_text, 0, 9, "target-1")
    assert find(result, lambda e: e.id == "target-1").positions == [P(0, 9), P(s, s + len("Acme Ltd"))]


# changeEntityType

def test_change_entity_type_updates_target_only():
    source_text = "Acme Ltd and John Smith"
    with_company = add_entity_from_selection([], source_text, 0, 8, "COMPANY")
    with_person = add_entity_from_selection(with_company, source_text, 13, 23, "PERSON")
    company = find(with_person, lambda e: e.type == "COMPANY")
    person = find(with_person, lambda e: e.type == "PERSON")

    result = change_entity_type(with_person, company.id, "PERSON")
    changed = find(result, lambda e: e.id == company.id)
    assert changed.type == "PERSON"
    assert changed.placeholder == "[PERSON_2]"
    assert changed.id == company.id
    assert find(result, lambda e: e.id == person.id) == person


def test_change_entity_type_missing_id():
    entities = add_entity_from_selection([], "Acme Ltd", 0, 8, "COMPANY")
    assert change_entity_type(entities, "no-such-id", "PERSON") is entities


def test_aud_1_2_recolor_then_add_gives_unique_ids():
    source_text = "Acme Ltd works with Globex Inc"
    entities = add_entity_from_selection([], source_text, 0, 8, "COMPANY")
    entities = change_entity_type(entities, entities[0].id, "PERSON")
    g = source_text.index("Globex Inc")
    entities = add_entity_from_selection(entities, source_text, g, g + len("Globex Inc"), "COMPANY")
    assert len(entities) == 2
    ids = [e.id for e in entities]
    assert len(set(ids)) == len(ids)


# mergeEntities

def test_merge_folds_source_into_target():
    source_text = "Tripath Technology, Inc. sold assets. Tripath announced the deal."
    inc_start = source_text.index("Tripath Technology, Inc.")
    inc_end = inc_start + len("Tripath Technology, Inc.")
    short_start = source_text.index("Tripath announced")
    short_end = short_start + len("Tripath")
    target = make_entity("COMPANY", "[COMPANY_1]", [(inc_start, inc_end)], id="target-1", value="Tripath Technology, Inc.")
    source = make_entity("COMPANY", "[COMPANY_2]", [(short_start, short_end)], id="source-1", value="Tripath")

    result = merge_entities([target, source], "source-1", "target-1")
    assert not any(e.id == "source-1" for e in result)
    merged = find(result, lambda e: e.id == "target-1")
    assert merged.placeholder == "[COMPANY_1]"
    assert merged.value == "Tripath Technology, Inc."
    assert merged.positions == [P(inc_start, inc_end), P(short_start, short_end)]
    assert len(merged.positions) == 2


def test_merge_unions_overlapping_positions():
    target = make_entity("COMPANY", "[COMPANY_1]", [(0, 4), (20, 24)], id="target-1", value="Acme")
    # точный дубль первой позиции и пересекающийся диапазон
    source = make_entity("COMPANY", "[COMPANY_2]", [(0, 4), (21, 25)], id="source-1", value="Acme")
    merged = find(merge_entities([target, source], "source-1", "target-1"), lambda e: e.id == "target-1")
    assert merged.positions == [P(0, 4), P(20, 25)]


def test_merge_keeps_touching_ranges_separate():
    target = make_entity("COMPANY", "[COMPANY_1]", [(0, 4)], id="target-1", value="Acme")
    source = make_entity("COMPANY", "[COMPANY_2]", [(4, 8)], id="source-1", value="Acme")
    merged = find(merge_entities([target, source], "source-1", "target-1"), lambda e: e.id == "target-1")
    assert merged.positions == [P(0, 4), P(4, 8)]


def test_merge_active_into_inactive_keeps_anonymize():
    target = make_entity("COMPANY", "[COMPANY_1]", [(0, 4)], id="target-1", value="Acme", anonymize=False)
    source = make_entity("COMPANY", "[COMPANY_2]", [(20, 24)], id="source-1", value="Acme", anonymize=True)
    merged = find(merge_entities([target, source], "source-1", "target-1"), lambda e: e.id == "target-1")
    assert merged.anonymize is True
    assert merged.positions == [P(0, 4), P(20, 24)]


def test_merge_two_inactive_stays_inactive():
    target = make_entity("COMPANY", "[COMPANY_1]", [(0, 4)], id="target-1", value="Acme", anonymize=False)
    source = make_entity("COMPANY", "[COMPANY_2]", [(20, 24)], id="source-1", value="Acme", anonymize=False)
    merged = find(merge_entities([target, source], "source-1", "target-1"), lambda e: e.id == "target-1")
    assert merged.anonymize is False


def test_merge_noop_for_different_types():
    target = make_entity("PERSON", "[PERSON_1]", [(0, 4)], id="target-1", value="John")
    source = make_entity("COMPANY", "[COMPANY_1]", [(20, 24)], id="source-1", value="Acme")
    entities = [target, source]
    assert merge_entities(entities, "source-1", "target-1") is entities


def test_merge_unions_wider_source():
    target = make_entity("COMPANY", "[COMPANY_1]", [(2, 5)], id="target-1", value="Acme")
    source = make_entity("COMPANY", "[COMPANY_2]", [(0, 10)], id="source-1", value="Acme")
    merged = find(merge_entities([target, source], "source-1", "target-1"), lambda e: e.id == "target-1")
    assert merged.positions == [P(0, 10)]


def test_merge_noop_same_id():
    entities = [make_entity("COMPANY", "[COMPANY_1]", [(0, 4)], id="target-1", value="Acme")]
    assert merge_entities(entities, "target-1", "target-1") is entities


def test_merge_noop_missing_source():
    entities = [make_entity("COMPANY", "[COMPANY_1]", [(0, 4)], id="target-1", value="Acme")]
    assert merge_entities(entities, "no-such-id", "target-1") is entities


def test_merge_noop_missing_target():
    entities = [make_entity("COMPANY", "[COMPANY_2]", [(0, 4)], id="source-1", value="Acme")]
    assert merge_entities(entities, "source-1", "no-such-id") is entities


def test_merge_keeps_positions_sorted():
    target = make_entity("COMPANY", "[COMPANY_1]", [(50, 54)], id="target-1", value="Acme")
    source = make_entity("COMPANY", "[COMPANY_2]", [(0, 4), (25, 29)], id="source-1", value="Acme")
    merged = find(merge_entities([target, source], "source-1", "target-1"), lambda e: e.id == "target-1")
    assert merged.positions == [P(0, 4), P(25, 29), P(50, 54)]


def test_merge_preserves_order_of_remaining():
    a = make_entity("PERSON", "[PERSON_1]", [(0, 1)], id="a")
    target = make_entity("COMPANY", "[COMPANY_1]", [(10, 14)], id="target-1", value="Acme")
    source = make_entity("COMPANY", "[COMPANY_2]", [(30, 34)], id="source-1", value="Acme")
    b = make_entity("PERSON", "[PERSON_2]", [(40, 41)], id="b")
    result = merge_entities([a, target, source, b], "source-1", "target-1")
    assert [e.id for e in result] == ["a", "target-1", "b"]


# dedupeEntityIds

def test_dedupe_assigns_fresh_id_to_later_duplicate():
    shared = Entity(id="dup-id", type="PERSON", value="John", placeholder="[PERSON_1]",
                    positions=[P(0, 4)], anonymize=True, manual=False)
    colliding = Entity(id="dup-id", type="COMPANY", value="Acme", placeholder="[COMPANY_1]",
                       positions=[P(10, 14)], anonymize=True, manual=False)
    result = dedupe_entity_ids([shared, colliding])
    assert len(result) == 2
    assert result[0].id == "dup-id"
    ids = [e.id for e in result]
    assert len(set(ids)) == len(ids)
    assert result[1].id != "dup-id"
    assert colliding.id == "dup-id"  # исходный объект не изменен


def test_dedupe_leaves_unique_ids():
    a = Entity(id="id-a", type="PERSON", value="John", placeholder="[PERSON_1]",
               positions=[P(0, 4)], anonymize=True, manual=False)
    b = Entity(id="id-b", type="COMPANY", value="Acme", placeholder="[COMPANY_1]",
               positions=[P(10, 14)], anonymize=True, manual=False)
    assert [e.id for e in dedupe_entity_ids([a, b])] == ["id-a", "id-b"]


# acceptCandidate

def _confirmed() -> Entity:
    return Entity(id="confirmed-1", type="PERSON", value="John Doe", placeholder="[PERSON_1]",
                  positions=[P(0, 8)], anonymize=True, manual=False)


def test_accept_candidate():
    candidate = Entity(
        id="candidate-1", type="PERSON", value="Jane Roe", placeholder="[PERSON_2]",
        positions=[P(20, 28)], anonymize=False, manual=False, candidate=True,
        candidate_note="Низкая уверенность модели", score=0.2,
    )
    result = accept_candidate([_confirmed(), candidate], "candidate-1")
    accepted = find(result, lambda e: e.id == "candidate-1")
    assert accepted.candidate is False
    assert accepted.candidate_note is None
    assert accepted.anonymize is True
    assert accepted.placeholder == "[PERSON_2]"
    placeholders = [e.placeholder for e in result]
    assert len(set(placeholders)) == len(placeholders)
    assert candidate.candidate is True  # исходный объект не изменен


def test_accept_candidate_noop_for_non_candidate():
    entities = [_confirmed()]
    assert accept_candidate(entities, "confirmed-1") is entities


def test_accept_candidate_noop_for_missing_id():
    entities = [_confirmed()]
    assert accept_candidate(entities, "no-such-id") is entities


# compactPlaceholderNumbers

def test_compact_collapses_gap_preserving_order():
    entities = [
        make_entity("PERSON", "[PERSON_1]", [(0, 1)]),
        make_entity("PERSON", "[PERSON_4]", [(2, 3)]),
        make_entity("PERSON", "[PERSON_5]", [(4, 5)]),
    ]
    result = compact_placeholder_numbers(entities)
    assert [e.placeholder for e in result] == ["[PERSON_1]", "[PERSON_2]", "[PERSON_3]"]
    assert [e.id for e in result] == [e.id for e in entities]


def test_compact_renumbers_each_type_independently():
    c1 = make_entity("COMPANY", "[COMPANY_1]", [(0, 1)])
    p2 = make_entity("PERSON", "[PERSON_2]", [(2, 3)])
    c3 = make_entity("COMPANY", "[COMPANY_3]", [(4, 5)])
    result = compact_placeholder_numbers([c1, p2, c3])
    assert find(result, lambda e: e.id == c1.id).placeholder == "[COMPANY_1]"
    assert find(result, lambda e: e.id == p2.id).placeholder == "[PERSON_1]"
    assert find(result, lambda e: e.id == c3.id).placeholder == "[COMPANY_2]"


def test_compact_keeps_candidates_after_confirmed():
    confirmed_a = make_entity("PERSON", "[PERSON_1]", [(0, 1)])
    confirmed_b = make_entity("PERSON", "[PERSON_5]", [(2, 3)])
    candidate = make_entity("PERSON", "[PERSON_7]", [(4, 5)], anonymize=False, candidate=True)
    dismissed = make_entity("PERSON", "[PERSON_9]", [(6, 7)], anonymize=False, dismissed=True)
    result = compact_placeholder_numbers([confirmed_a, confirmed_b, candidate, dismissed])
    assert find(result, lambda e: e.id == confirmed_a.id).placeholder == "[PERSON_1]"
    assert find(result, lambda e: e.id == confirmed_b.id).placeholder == "[PERSON_2]"
    assert find(result, lambda e: e.id == candidate.id).placeholder == "[PERSON_3]"
    assert find(result, lambda e: e.id == dismissed.id).placeholder == "[PERSON_4]"


def test_compact_returns_same_list_when_dense():
    entities = [
        make_entity("PERSON", "[PERSON_1]", [(0, 1)]),
        make_entity("PERSON", "[PERSON_2]", [(2, 3)]),
        make_entity("COMPANY", "[COMPANY_1]", [(4, 5)]),
    ]
    assert compact_placeholder_numbers(entities) is entities


def test_compact_empty():
    assert compact_placeholder_numbers([]) == []


# imageSafety.test.ts: случаи с buildAnonymizedText / buildAnonymizedTextWithMap

@pytest.mark.parametrize("source_image", [
    "![Alice signature](data:image/png;base64,QWxpY2U=)",
    '![Alice](https://example.com/signature(1).png "signed by Alice")',
    "![Alice [signature]](<https://example.com/image(1).png>)",
    '<IMG src="data:image/png;base64,QWxpY2U=" alt="Alice > Smith">',
    '<picture><source srcset="https://example.com/private.png"><img src="fallback.png"></picture>',
    '<svg><image href="data:image/png;base64,QWxpY2U=" /></svg>',
])
def test_image_removed_with_metadata(source_image):
    source = f"Before {source_image} After alice@example.com"
    text = build_anonymized_text_with_map(source, []).text
    assert text == f"Before {IMAGE_REMOVED_MARKER} After alice@example.com"
    assert source_image in source


@pytest.mark.parametrize("image", ["![Alice][signature]", "![signature][]", "![signature]"])
def test_image_reference_definitions_removed(image):
    source = f'{image}\n\n[signature]: data:image/png;base64,QWxpY2U= "Alice signature"\n[other]: https://example.com/info'
    assert build_anonymized_text(source, []) == (
        f"{IMAGE_REMOVED_MARKER}\n\n{IMAGE_REMOVED_MARKER}\n[other]: https://example.com/info"
    )


def test_image_payload_not_restored_by_partial_entity():
    source = "![private](data:image/png;base64,QWxpY2U=) after"
    entity = Entity(id="1", type="PERSON", value="private", positions=[P(2, 9)],
                    placeholder="[PERSON_1]", anonymize=True, manual=True)
    assert build_anonymized_text(source, [entity]) == f"{IMAGE_REMOVED_MARKER} after"


def test_multiline_image_reference_removed():
    source = '![signature][sig]\n\n[sig]:\n  data:image/png;base64,QWxpY2U=\n  "Alice Smith"\n\nAfter'
    assert build_anonymized_text(source, []) == f"{IMAGE_REMOVED_MARKER}\n\n{IMAGE_REMOVED_MARKER}\n\nAfter"


def test_ordinary_links_unchanged():
    source = "A [reference](https://example.com) and ! punctuation."
    assert build_anonymized_text(source, []) == source


# Дополнительно (нет в TS): карта сегментов согласована с текстом.

def test_segments_map_is_consistent():
    source = "John [PERSON_1] met John"
    person = make_entity("PERSON", "[PERSON_9]", [(0, 4), (20, 24)])
    text, segments = build_anonymized_text_with_map(source, [person])
    assert text == "[PERSON_9] [​PERSON_1] met [PERSON_9]"
    assert segments[0].out_start == 0 and segments[-1].out_end == len(text)
    for a, b in zip(segments, segments[1:]):
        assert a.out_end == b.out_start and a.src_end == b.src_start
    for s in segments:
        if not s.opaque:
            assert text[s.out_start:s.out_end] == source[s.src_start:s.src_end]
    assert [s.opaque for s in segments] == [True, False, True, False, True]
