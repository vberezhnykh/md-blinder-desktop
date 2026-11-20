"""Порт src/lib/anonymize/relatedCandidates.test.ts."""

from __future__ import annotations

from dataclasses import replace

from md_blinder.anonymize.apply import (
    accept_all_related,
    accept_related_candidate,
    candidate_reason,
    compact_placeholder_numbers,
    merge_entities,
    restore_candidate,
)
from md_blinder.anonymize.types import Entity, Position as P

# «Иван Петров … Петров … Петрову … Анна Смирнова … Смирнова»
person = Entity(id="p1", type="PERSON", value="Иван Петров", placeholder="[PERSON_1]",
                positions=[P(0, 11)], anonymize=True, manual=False)
other = Entity(id="p2", type="PERSON", value="Анна Смирнова", placeholder="[PERSON_2]",
               positions=[P(40, 53)], anonymize=True, manual=False)


def related(id_: str, related_to: str, start: int, value: str, placeholder: str) -> Entity:
    return Entity(id=id_, type="PERSON", value=value, placeholder=placeholder,
                  positions=[P(start, start + len(value))], anonymize=False, manual=False,
                  candidate=True, related_to=related_to)


surname = related("c1", "p1", 14, "Петров", "[PERSON_3]")
dative = related("c2", "p1", 24, "Петрову", "[PERSON_4]")
her_surname = related("c3", "p2", 60, "Смирнова", "[PERSON_5]")


def find(entities, id_):
    return next((e for e in entities if e.id == id_), None)


# acceptRelatedCandidate

def test_merges_related_into_source_with_its_label():
    result = compact_placeholder_numbers(accept_related_candidate([person, other, surname, dative], "c1"))
    assert find(result, "c1") is None
    merged = find(result, "p1")
    assert merged.placeholder == "[PERSON_1]"
    assert merged.anonymize is True
    assert merged.positions == [P(0, 11), P(14, 20)]
    # Освободившийся номер кандидата занимает следующий кандидат.
    assert find(result, "c2").placeholder == "[PERSON_3]"


def test_without_source_accepts_separately_with_new_label():
    result = accept_related_candidate([other, surname], "c1")
    accepted = find(result, "c1")
    assert accepted.candidate is False
    assert accepted.anonymize is True
    # Номер следует за занятым [PERSON_2], а не сливается с ним.
    assert accepted.placeholder == "[PERSON_3]"
    assert find(result, "p2").positions == other.positions


def test_does_not_merge_after_type_change():
    recolored = replace(surname, type="COMPANY", placeholder="[COMPANY_1]")
    result = accept_related_candidate([person, recolored], "c1")
    assert find(result, "c1").candidate is False
    assert find(result, "p1").positions == person.positions


def test_does_not_enable_disabled_source():
    disabled = replace(person, anonymize=False)
    result = accept_related_candidate([disabled, surname], "c1")
    assert find(result, "p1").anonymize is False
    accepted = find(result, "c1")
    assert accepted.anonymize is True
    assert accepted.placeholder == "[PERSON_2]"


def test_noop_for_non_candidate():
    entities = [person, surname]
    assert accept_related_candidate(entities, "p1") is entities


# acceptAllRelated

def test_accept_all_related_only_unreviewed_of_that_entity():
    dismissed = replace(dative, candidate=False, dismissed=True)
    result = accept_all_related([person, other, surname, dismissed, her_surname], "p1")
    assert find(result, "c1") is None
    assert len(find(result, "p1").positions) == 2
    assert find(result, "c2").dismissed is True
    assert find(result, "c3").candidate is True


# restoreCandidate

def test_restore_dismissed_candidate():
    dismissed = replace(surname, candidate=False, dismissed=True)
    restored = find(restore_candidate([person, dismissed], "c1"), "c1")
    assert restored.dismissed is False
    assert restored.candidate is True
    assert restored.anonymize is False
    assert restored.related_to == "p1"


def test_restore_noop_for_not_dismissed():
    entities = [person, surname]
    assert restore_candidate(entities, "c1") is entities


# candidateReason

def test_reason_uses_current_source_label():
    renumbered = replace(person, placeholder="[PERSON_7]")
    assert candidate_reason([renumbered, surname], surname) == "Похоже на [PERSON_7]"


def test_reason_reports_deleted_source():
    assert candidate_reason([other, surname], surname) == "Похоже на удаленную замену"


def test_reason_without_related_to_shows_saved_note():
    low_score = replace(surname, related_to=None, candidate_note="Низкая уверенность модели")
    assert candidate_reason([person, low_score], low_score) == "Низкая уверенность модели"


# mergeEntities и relatedTo

def test_merge_moves_candidates_to_merged_target():
    second = replace(person, id="p3", value="И. Петров", placeholder="[PERSON_3]", positions=[P(30, 39)])
    result = merge_entities([person, second, related("c4", "p3", 70, "Петровым", "[PERSON_4]")], "p3", "p1")
    assert find(result, "c4").related_to == "p1"
