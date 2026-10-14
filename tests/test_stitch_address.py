"""Порт src/lib/anonymize/stitchAddress.test.ts."""

from __future__ import annotations

from md_blinder.anonymize.stitch_address import stitch_address_runs
from md_blinder.anonymize.types import RawMatch


def _addr(text: str, frag: str) -> tuple[RawMatch, int]:
    start = text.index(frag)
    return RawMatch(type="ADDRESS", value=frag, start=start, end=start + len(frag)), start


def test_merges_two_address_fragments_separated_by_comma_into_one_span():
    text = "Located at ROOM C05, 123 Main Street in the annex."
    frag1, frag2 = "ROOM C05", "123 Main Street"
    m1, start1 = _addr(text, frag1)
    m2, start2 = _addr(text, frag2)

    result = stitch_address_runs([m1, m2], text)

    assert len(result) == 1
    assert result[0].type == "ADDRESS"
    assert result[0].start == start1
    assert result[0].end == start2 + len(frag2)
    assert result[0].value == text[result[0].start:result[0].end]


def test_merges_address_fragments_separated_by_a_short_numeric_gap():
    text = "Building at BLOCK A, 2-4 SECOND FLOOR for delivery"
    frag1, frag2 = "BLOCK A", "SECOND FLOOR"
    m1, start1 = _addr(text, frag1)
    m2, start2 = _addr(text, frag2)

    result = stitch_address_runs([m1, m2], text)

    assert len(result) == 1
    assert result[0].start == start1
    assert result[0].end == start2 + len(frag2)
    assert result[0].value == text[result[0].start:result[0].end]


def test_merges_across_an_abbreviation_dot_before_a_digit():
    text = "at Avenida Paulista, No. 1912, Sala 61 today"
    m1, _ = _addr(text, "Avenida Paulista")
    m2, _ = _addr(text, "Sala 61")

    result = stitch_address_runs([m1, m2], text)

    assert len(result) == 1
    assert result[0].value == "Avenida Paulista, No. 1912, Sala 61"


def test_does_not_merge_address_fragments_separated_by_a_paragraph_boundary():
    text = "MAIN ST\n\nSECOND AVE"
    frag1, frag2 = "MAIN ST", "SECOND AVE"
    m1, _ = _addr(text, frag1)
    m2, _ = _addr(text, frag2)

    result = stitch_address_runs([m1, m2], text)

    assert len(result) == 2
    assert sorted(m.value for m in result) == sorted([frag1, frag2])


def test_does_not_merge_address_fragments_separated_by_a_sentence_boundary():
    text = "MAIN ST. SECOND AVE"
    frag1, frag2 = "MAIN ST", "SECOND AVE"
    m1, _ = _addr(text, frag1)
    m2, _ = _addr(text, frag2)

    result = stitch_address_runs([m1, m2], text)

    assert len(result) == 2
    assert sorted(m.value for m in result) == sorted([frag1, frag2])


def test_merges_overlapping_address_fragments_into_one_span():
    text = "123 MAIN STREET APT 4B"
    frag1, frag2 = "123 MAIN STREET", "STREET APT 4B"
    m1, start1 = _addr(text, frag1)
    m2, start2 = _addr(text, frag2)

    result = stitch_address_runs([m1, m2], text)

    assert len(result) == 1
    assert result[0].start == start1
    assert result[0].end == start2 + len(frag2)
    assert result[0].value == text[result[0].start:result[0].end]


def test_passes_non_address_matches_through_unchanged():
    text = "John Doe lives at ROOM C05, 123 Main Street"
    person = "John Doe"
    frag1, frag2 = "ROOM C05", "123 Main Street"
    person_start = text.index(person)
    m1, start1 = _addr(text, frag1)
    m2, start2 = _addr(text, frag2)
    person_match = RawMatch(type="PERSON", value=person, start=person_start, end=person_start + len(person))

    result = stitch_address_runs([person_match, m1, m2], text)

    found = next(m for m in result if m.type == "PERSON")
    assert found == RawMatch(type="PERSON", value=person, start=person_start, end=person_start + len(person))

    address_matches = [m for m in result if m.type == "ADDRESS"]
    assert len(address_matches) == 1
    assert address_matches[0].start == start1
    assert address_matches[0].end == start2 + len(frag2)


# AUD-2.4: зазор со стоп-словом ("... to ...") разделяет два РАЗНЫХ адреса —
# его нельзя склеивать, даже если сам зазор короткий.
def test_does_not_merge_two_different_addresses_separated_by_to():
    text = "Deliver from 1 Main St, Springfield to 2 Oak Ave, Shelbyville please"
    frag1, frag2 = "1 Main St, Springfield", "2 Oak Ave, Shelbyville"
    m1, _ = _addr(text, frag1)
    m2, _ = _addr(text, frag2)

    result = stitch_address_runs([m1, m2], text)

    assert len(result) == 2
    assert sorted(m.value for m in result) == sorted([frag1, frag2])


def test_does_not_merge_two_different_addresses_separated_by_and():
    text = "Sites at 1 Main St, Springfield and 2 Oak Ave, Shelbyville are affected"
    frag1, frag2 = "1 Main St, Springfield", "2 Oak Ave, Shelbyville"
    m1, _ = _addr(text, frag1)
    m2, _ = _addr(text, frag2)

    result = stitch_address_runs([m1, m2], text)

    assert len(result) == 2
    assert sorted(m.value for m in result) == sorted([frag1, frag2])


def test_still_merges_address_fragments_separated_by_a_glue_gap_like_slash():
    text = "Located at BLOCK A / SECOND FLOOR for delivery"
    frag1, frag2 = "BLOCK A", "SECOND FLOOR"
    m1, start1 = _addr(text, frag1)
    m2, start2 = _addr(text, frag2)

    result = stitch_address_runs([m1, m2], text)

    assert len(result) == 1
    assert result[0].start == start1
    assert result[0].end == start2 + len(frag2)
    assert result[0].value == text[result[0].start:result[0].end]


def test_does_not_merge_address_fragments_separated_by_a_gap_longer_than_30_characters():
    text = "MAIN ST here is some unrelated filler text padding out the gap SECOND AVE"
    frag1, frag2 = "MAIN ST", "SECOND AVE"
    m1, start1 = _addr(text, frag1)
    m2, start2 = _addr(text, frag2)

    assert start2 - (start1 + len(frag1)) > 30

    result = stitch_address_runs([m1, m2], text)

    assert len(result) == 2
    assert sorted(m.value for m in result) == sorted([frag1, frag2])
