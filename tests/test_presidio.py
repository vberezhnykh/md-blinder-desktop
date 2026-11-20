"""Порт src/lib/anonymize/presidioDetectors.test.ts."""

from __future__ import annotations

from datetime import datetime, timezone

from md_blinder.anonymize.presidio import detect_presidio_ids, is_za_id_number
from md_blinder.anonymize.regex_detectors import detect_structural


def found(text: str) -> list[list[str]]:
    return [[m.type, m.value] for m in detect_presidio_ids(text)]


# Повторяет resolveOverlaps из index.ts: раньше начавшийся и более длинный спан
# побеждает, при равенстве — первый в выдаче detect_structural.
def structural(text: str) -> list[list[str]]:
    accepted = []
    for m in sorted(detect_structural(text), key=lambda m: (m.start, -(m.end - m.start))):
        if not accepted or accepted[-1].end <= m.start:
            accepted.append(m)
    return [[m.type, m.value] for m in accepted]


class TestSouthAfricanIdentifiers:
    def test_validates_id_numbers_by_birth_date_status_digits_and_luhn(self) -> None:
        assert is_za_id_number("8001015009087") is True
        assert is_za_id_number("8001015009088") is False  # контрольная сумма
        assert is_za_id_number("8013015009081") is False  # месяц 13
        assert is_za_id_number("8001015009387") is False  # цифра гражданства 3
        assert is_za_id_number("8001015009787") is False  # устаревшая цифра 7
        # контрольная сумма не сходится
        assert is_za_id_number("9912315009087", datetime(2026, 10, 7, tzinfo=timezone.utc)) is False

    def test_detects_an_id_number_as_id_doc_instead_of_a_card_with_or_without_grouping(self) -> None:
        assert structural("ID Number: 8001015009087") == [["ID_DOC", "8001015009087"]]
        assert found("identity 800101 5009 087 of the signatory") == [["ID_DOC", "800101 5009 087"]]

    def test_detects_cipc_company_registration_numbers_in_modern_and_legacy_form(self) -> None:
        assert found("Codencium (Pty) Ltd, 2025/717145/07") == [["REG_NUMBER", "2025/717145/07"]]
        assert found("close corporation CK1995/012345") == [["REG_NUMBER", "CK1995/012345"]]
        assert found("reference 2999/123456/07") == []

    def test_detects_a_sars_tax_number_only_after_a_label(self) -> None:
        assert found("Income tax reference number: 0123456789") == [["REG_NUMBER", "0123456789"]]
        assert found("SARS tax number 9123456780") == [["REG_NUMBER", "9123456780"]]
        assert found("The amount of 0123456789 units") == []
        assert found("Tax No. 4123456789") == []


class TestUsAndUkIdentifiers:
    def test_detects_a_valid_ssn_and_drops_known_invalid_ones(self) -> None:
        assert structural("SSN 536-90-4399") == [["REG_NUMBER", "536-90-4399"]]
        assert found("SSN 123-45-6789") == []
        assert found("SSN 666-12-3456 or 536-00-4399 or 536-90-0000") == []
        assert found("536-90.4399") == []

    def test_detects_a_uk_national_insurance_number(self) -> None:
        assert found("NI number JG 12 34 56 C") == [["REG_NUMBER", "JG 12 34 56 C"]]
        assert found("QQ 12 34 56 C") == []
        assert found("NINO AB123456D") == [["REG_NUMBER", "AB123456D"]]
        assert found("GB123456A") == []


class TestNetworkIdentifiers:
    def test_detects_labelled_ipv4_addresses_including_lists(self) -> None:
        assert found("IP address 192.168.10.25") == [["OTHER", "192.168.10.25"]]
        assert found("IP addresses 10.0.0.1, 10.0.0.2 and 10.0.0.3") == [
            ["OTHER", "10.0.0.1"], ["OTHER", "10.0.0.2"], ["OTHER", "10.0.0.3"],
        ]

    def test_does_not_take_clause_numbering_times_dates_or_amounts_for_addresses(self) -> None:
        assert found("See clause 12.1.3.4 and 1.2.3.4 of the Agreement") == []
        assert found("Meeting at 10:30 on 10.09.2026, fee EUR 5 000.00") == []

    def test_detects_ipv6_and_uuids(self) -> None:
        assert found("host 2001:db8::8a2e:370:7334 is allowed") == [["OTHER", "2001:db8::8a2e:370:7334"]]
        assert found("record 123e4567-e89b-42d3-a456-426614174000") == [
            ["OTHER", "123e4567-e89b-42d3-a456-426614174000"],
        ]
        assert found("nil 00000000-0000-0000-0000-000000000000") == []

    def test_a_uuid_is_no_longer_split_into_a_phone_number(self) -> None:
        assert structural("UUID 123e4567-e89b-42d3-a456-426614174000") == [
            ["OTHER", "123e4567-e89b-42d3-a456-426614174000"],
        ]
