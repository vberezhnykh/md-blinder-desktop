"""Порт src/lib/anonymize/index.test.ts (NER заменен заглушкой)."""

import re

import pytest

from md_blinder.anonymize.pipeline import anonymize_markdown
from md_blinder.anonymize.types import RawMatch
from md_blinder.cancel import Cancelled, CancelToken


def ner(matches):
    return lambda _md: list(matches)


def test_cancellation_passes_through():
    token = CancelToken()
    token.cancel()
    called = []
    with pytest.raises(Cancelled):
        anonymize_markdown("Acme", lambda md: called.append(md) or [], token)
    assert called == []

    def cancelled(_md):
        raise Cancelled()

    with pytest.raises(Cancelled):
        anonymize_markdown("Acme", cancelled)


def test_large_interleaved_coverage():
    markdown = "\n".join(f"Person{i} person{i}@example.com" for i in range(1000))
    matches = [RawMatch("PERSON", m.group(0), m.start(), m.end()) for m in re.finditer(r"Person\d+", markdown)]
    matches.append(RawMatch("PERSON", markdown, 0, len(markdown)))
    result = anonymize_markdown(markdown, ner(matches + matches))
    assert len([e for e in result.entities if e.type == "EMAIL"]) == 1000
    assert len([e for e in result.entities if e.type == "PERSON"]) == 1000
    assert "@example.com" not in result.markdown
    assert not re.search(r"Person\d+", result.markdown)


def test_duplicate_ner_matches_merge():
    markdown = "Acme Ltd pays Acme Ltd via john@x.com"
    first = markdown.index("Acme Ltd")
    second = markdown.index("Acme Ltd", first + 1)
    result = anonymize_markdown(markdown, ner([
        RawMatch("COMPANY", "Acme Ltd", first, first + 8),
        RawMatch("COMPANY", "Acme Ltd", second, second + 8),
    ]))
    assert not result.ner_failed
    company = next(e for e in result.entities if e.type == "COMPANY")
    assert company.placeholder == "[COMPANY_1]" and len(company.positions) == 2
    email = next(e for e in result.entities if e.type == "EMAIL")
    assert email.placeholder == "[EMAIL_1]" and email.value == "john@x.com"
    assert result.markdown.count("[COMPANY_1]") == 2
    assert result.markdown.count("[EMAIL_1]") == 1


def test_structural_wins_overlap():
    markdown = "mail john@x.com"
    start = markdown.index("john@x.com")
    result = anonymize_markdown(markdown, ner([RawMatch("PERSON", "john@x.com", start, start + 10)]))
    overlapping = [e for e in result.entities if any(p.start < start + 10 and start < p.end for p in e.positions)]
    assert len(overlapping) == 1 and overlapping[0].type == "EMAIL"
    assert not any(e.type == "PERSON" for e in result.entities)


def test_regex_survives_ner_failure():
    def broken(_md):
        raise RuntimeError("no worker")

    result = anonymize_markdown("reach me at a@b.com", broken)
    assert result.ner_failed and result.ner_error == "no worker"
    email = next(e for e in result.entities if e.type == "EMAIL")
    assert email.value == "a@b.com"
    assert "[EMAIL_1]" in result.markdown


def test_cnpj():
    result = anonymize_markdown("Registered under CNPJ 53.200.745/0001-60 in Brazil", ner([]))
    reg = next(e for e in result.entities if e.type == "REG_NUMBER")
    assert reg.value == "53.200.745/0001-60"
    assert not any(e.type == "PHONE" for e in result.entities)
    assert "[REG_NUMBER_1]" in result.markdown and "53.200.745" not in result.markdown


def test_generic_role_noun_filtered():
    markdown = "The Director shall notify Acme Ltd of any change."
    d = markdown.index("Director")
    c = markdown.index("Acme Ltd")
    result = anonymize_markdown(markdown, ner([
        RawMatch("PERSON", "Director", d, d + 8), RawMatch("COMPANY", "Acme Ltd", c, c + 8),
    ]))
    assert not any(e.type == "PERSON" and e.value.lower() == "director" for e in result.entities)
    assert next(e for e in result.entities if e.type == "COMPANY").value == "Acme Ltd"


def test_confirm_level_score():
    markdown = "Meeting with Ann Baker tomorrow."
    s = markdown.index("Ann Baker")
    result = anonymize_markdown(markdown, ner([RawMatch("PERSON", "Ann Baker", s, s + 9, 0.4)]))
    person = next(e for e in result.entities if e.value == "Ann Baker")
    assert not person.candidate and person.anonymize
    assert person.placeholder in result.markdown


def test_gray_zone_candidate():
    markdown = "Perhaps Cy Vance was mentioned."
    s = markdown.index("Cy Vance")
    result = anonymize_markdown(markdown, ner([RawMatch("PERSON", "Cy Vance", s, s + 8, 0.2)]))
    person = next(e for e in result.entities if e.value == "Cy Vance")
    assert person.candidate and not person.anonymize
    assert person.placeholder not in result.markdown


def test_confirmed_numbered_before_candidates():
    markdown = "Signed by Tom Reed. Possibly Sam Kerr too."
    a = markdown.index("Tom Reed")
    b = markdown.index("Sam Kerr")
    result = anonymize_markdown(markdown, ner([
        RawMatch("PERSON", "Tom Reed", a, a + 8, 0.5), RawMatch("PERSON", "Sam Kerr", b, b + 8, 0.2),
    ]))
    confirmed = next(e for e in result.entities if e.value == "Tom Reed")
    candidate = next(e for e in result.entities if e.value == "Sam Kerr")
    assert confirmed.placeholder == "[PERSON_1]"
    assert candidate.placeholder == "[PERSON_2]" and candidate.candidate


def test_passport_beats_phone():
    result = anonymize_markdown("Иванов И.И., паспорт 4506 123456, проживает в Москве", ner([]))
    passport = next(e for e in result.entities if e.type == "ID_DOC")
    assert passport.value == "4506 123456"
    assert not any(e.type == "PHONE" for e in result.entities)
    assert "[ID_DOC_1]" in result.markdown and "4506 123456" not in result.markdown


def test_snils_passport_website():
    result = anonymize_markdown("СНИЛС 123-456-789 01, паспорт 4506 123456, сайт https://acme-legal.ru", ner([]))
    assert next(e for e in result.entities if e.value == "123-456-789 01").type == "REG_NUMBER"
    assert next(e for e in result.entities if e.type == "URL").placeholder == "[URL_1]"
    assert result.markdown == "СНИЛС [REG_NUMBER_1], паспорт [ID_DOC_1], сайт [URL_1]"


def test_url_spellings_grouped():
    result = anonymize_markdown(
        "Публикуется на https://example.com, зеркало www.example.com, кратко example.com", ner([])
    )
    urls = [e for e in result.entities if e.type == "URL"]
    assert len(urls) == 1 and len(urls[0].positions) == 3


def test_without_ner_is_not_failure():
    result = anonymize_markdown("reach me at a@b.com")
    assert not result.ner_failed


def test_address_found_with_commas_is_one_clean_entity():
    text = (
        "registered office at Quijano Chambers, P.O. Box 3159, Road Town, Tortola.\n\n"
        "Physical: Quijano Chambers, P.O. Box 3159, Road Town, Tortola."
    )
    first = text.index(", P.O.")
    second = text.index("P.O.", first + 10)
    value = "P.O. Box 3159, Road Town"
    matches = [
        RawMatch("ADDRESS", text[first:first + len(value) + 3], first, first + len(value) + 3, 0.9),
        RawMatch("ADDRESS", value, second, second + len(value), 0.9),
    ]
    [address] = [e for e in anonymize_markdown(text, ner(matches)).entities if e.type == "ADDRESS"]
    assert [text[p.start:p.end] for p in address.positions] == [value, value]


def test_tax_id_wins_over_ner_address():
    markdown = "LLC Romashka<br>TIN: 9909747220<br>KPP: 774751001"
    start = markdown.index("9909747220")
    result = anonymize_markdown(markdown, ner([RawMatch("ADDRESS", "9909747220", start, start + 10, 0.41)]))
    assert [(e.type, e.value) for e in result.entities] == [("REG_NUMBER", "9909747220"), ("REG_NUMBER", "774751001")]


@pytest.mark.parametrize("value, kept", [
    ("044525225", False),  # БИК без метки, которую знают правила
    ("12 345 678", False),
    ("125009", True),  # индекс
])
def test_number_without_letters_is_not_an_address(value, kept):
    markdown = f"Bank code {value} end"
    start = markdown.index(value)
    result = anonymize_markdown(markdown, ner([RawMatch("ADDRESS", value, start, start + len(value), 0.6)]))
    assert [e.value for e in result.entities if e.type == "ADDRESS"] == ([value] if kept else [])


def test_postal_code_piece_is_stitched_before_number_check():
    markdown = "Address: 125009, Moscow, Tverskaya str., 7"
    zip_start = markdown.index("125009")
    rest_start = markdown.index("Moscow")
    result = anonymize_markdown(markdown, ner([
        RawMatch("ADDRESS", "125009", zip_start, zip_start + 6, 0.6),
        RawMatch("ADDRESS", markdown[rest_start:], rest_start, len(markdown), 0.6),
    ]))
    assert [e.value for e in result.entities if e.type == "ADDRESS"] == ["125009, Moscow, Tverskaya str., 7"]


def test_city_is_not_merged_into_address_that_contains_it_as_letters():
    # «Львів» в оговорке об арбитраже — не адрес стороны «… Львівська обл., …».
    markdown = "Сторона проживає: Україна, 79000, Львівська обл., село Вишнєве.\n\nМісце арбітражу – Львів, Україна."
    address = "Україна, 79000, Львівська обл., село Вишнєве"
    start = markdown.index(address)
    city = markdown.index("Львів,")
    result = anonymize_markdown(markdown, ner([
        RawMatch("ADDRESS", address, start, start + len(address), 0.9),
        RawMatch("ADDRESS", "Львів", city, city + 5, 0.9),
    ]))
    addresses = sorted(e.placeholder for e in result.entities if e.type == "ADDRESS")
    assert addresses == ["[ADDRESS_1]", "[ADDRESS_2]"]
    assert "Місце арбітражу – [ADDRESS_2], Україна." in result.markdown


def test_street_mislabeled_as_reg_number_joins_the_address():
    # Модель отдала начало адреса рег. номером, а индекс с городом — адресом.
    markdown = "with its registered office at 365 Agiou Andreou, 3035 Limassol, Cyprus, with"
    street = markdown.index("365 Agiou Andreou")
    city = markdown.index("3035 Limassol")
    result = anonymize_markdown(markdown, ner([
        RawMatch("REG_NUMBER", "365 Agiou Andreou", street, street + 17, 0.8),
        RawMatch("ADDRESS", "3035 Limassol", city, city + 13, 0.75),
    ]))
    assert [(e.type, e.value) for e in result.entities] == [("ADDRESS", "365 Agiou Andreou, 3035 Limassol")]
    assert result.markdown == "with its registered office at [ADDRESS_1], Cyprus, with"
