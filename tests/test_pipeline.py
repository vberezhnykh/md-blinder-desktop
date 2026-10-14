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
