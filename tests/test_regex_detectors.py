"""Порт src/lib/anonymize/regexDetectors.test.ts и проверок detectStructural из imageSafety.test.ts."""

from __future__ import annotations

import re
import time

import pytest

from md_blinder.anonymize.image_safety import IMAGE_REMOVED_MARKER, mask_images_for_detection
from md_blinder.anonymize.regex_detectors import detect_email, detect_structural
from md_blinder.anonymize.types import RawMatch


def of_type(text: str, type_: str) -> list[RawMatch]:
    return [m for m in detect_structural(text) if m.type == type_]


def values(text: str, type_: str) -> list[str]:
    return [m.value for m in of_type(text, type_)]


def has_type(text: str, type_: str) -> bool:
    return any(m.type == type_ for m in detect_structural(text))


@pytest.mark.parametrize("text", [
    "Телефон: +79161234567", "Тел.: 89161234567", "Телефон: 0671234567",
    "Мобільний: 0671234567", "+79161234567", "+380671234567",
])
def test_detects_compact_contact_phones(text: str) -> None:
    phone = of_type(text, "PHONE")
    assert len(phone) == 1
    assert phone[0].value == re.split(r": ?", text)[-1]
    assert text[phone[0].start:phone[0].end] == phone[0].value


@pytest.mark.parametrize("text", [
    "Оплата: 89161234567", "Телефон: 1234567890123456", "+1234567890123456",
    "Телефонизация: 89161234567", "Счёт: 40702810338000246070", "Дата: 06.09.2026",
])
def test_does_not_invent_a_compact_telephone_or_truncate_a_longer_number(text: str) -> None:
    assert not has_type(text, "PHONE")


@pytest.mark.parametrize("iban", [
    "UA21 3223 1300 0002 6007 2335 6600 1", "ua21 3223 1300 0002 6007 2335 6600 1",
    "gb82 west 1234 5698 7654 32", "DE89 3704 0044 0532 0130 00",
])
def test_covers_complete_printed_iban_without_partial_lower_priority_matches(iban: str) -> None:
    text = f"IBAN: {iban} end"
    matches = detect_structural(text)
    assert [m for m in matches if m.type == "IBAN"] == [RawMatch("IBAN", iban, 6, 6 + len(iban))]
    assert not any(m.type != "IBAN" and m.start < 6 + len(iban) and m.end > 6 for m in matches)


def test_never_accepts_a_checksum_valid_prefix_of_a_longer_iban_or_an_oversized_account() -> None:
    assert not has_type("IBAN GB82WEST123456987654321", "IBAN")
    assert not has_type(f"Account: {'1234 ' * 10}1234", "ACCOUNT")
    text = "IBAN: UA00 3223 1300 0002 6007 2335 6600 1"
    account = next((m for m in detect_structural(text) if m.type == "ACCOUNT"), None)
    assert account is not None and account.value == text[6:]


def test_scans_long_ocr_fragments_around_at_with_exact_offsets() -> None:
    source = f"{'x' * 64000} no-email {'a' * 64000}@invalid; alice@example.com."
    started = time.perf_counter()
    assert detect_email(source) == [
        RawMatch("EMAIL", "alice@example.com", source.index("alice@example.com"), len(source) - 1),
    ]
    # Очень мягкая граница (старый неякорный поиск занимал больше 1,8 с на половине входа).
    assert time.perf_counter() - started < 1.0


def test_detects_an_email_address() -> None:
    assert values("Contact john.doe@acme.co for details", "EMAIL") == ["john.doe@acme.co"]


def test_detects_a_valid_iban() -> None:
    assert "GB82WEST12345698765432" in values("IBAN GB82WEST12345698765432 end", "IBAN")


def test_rejects_an_iban_with_an_invalid_checksum() -> None:
    assert not has_type("GB00WEST12345698765432", "IBAN")


def test_rejects_an_iban_with_an_invalid_country_code() -> None:
    assert not has_type("ZZ82WEST12345698765432", "IBAN")


def test_detects_a_valid_card_number_with_spaces() -> None:
    assert values("Card 4111 1111 1111 1111 ok", "CARD") == ["4111 1111 1111 1111"]


def test_detects_a_valid_card_number_without_spaces() -> None:
    assert values("4111111111111111", "CARD") == ["4111111111111111"]


def test_rejects_a_card_number_with_an_invalid_luhn_checksum() -> None:
    assert not has_type("4111 1111 1111 1112", "CARD")


def test_detects_a_valid_8_char_swift_code() -> None:
    assert values("SWIFT DEUTDEFF here", "SWIFT") == ["DEUTDEFF"]


def test_detects_a_valid_11_char_swift_code() -> None:
    assert "DEUTDEFF500" in values("DEUTDEFF500", "SWIFT")


def test_rejects_a_swift_code_with_an_invalid_country_code() -> None:
    assert not has_type("ABCDZZFF", "SWIFT")


def test_detects_a_valid_phone_number() -> None:
    assert values("Call +44 20 7946 0958 now", "PHONE") == ["+44 20 7946 0958"]


def test_rejects_a_too_short_numeric_fragment_as_a_phone_number() -> None:
    assert not has_type("ref 12.34", "PHONE")


def test_detects_an_account_number_from_context() -> None:
    assert values("Account No: 12345678", "ACCOUNT") == ["12345678"]


def test_detects_a_company_registration_number_from_context() -> None:
    assert values("Company No. 08145531", "REG_NUMBER") == ["08145531"]


def test_detects_a_hyphenated_company_registration_number_from_context() -> None:
    assert "01-09-391554" in values("company registration number: 01-09-391554", "REG_NUMBER")


def test_detects_an_ein_as_a_registration_number() -> None:
    assert values("EIN 12-3456789", "REG_NUMBER") == ["12-3456789"]


@pytest.mark.parametrize("text, cnpj", [
    ("CNPJ: 53.200.745/0001-60", "53.200.745/0001-60"),  # числовой
    ("CNPJ: 12.ABC.345/01DE-35", "12.ABC.345/01DE-35"),  # алфавитно-цифровой
])
def test_detects_a_cnpj_as_a_registration_number(text: str, cnpj: str) -> None:
    found = next((m for m in of_type(text, "REG_NUMBER") if m.value == cnpj), None)
    assert found is not None
    start = text.index(cnpj)
    assert (found.start, found.end) == (start, start + len(cnpj))


def test_does_not_treat_a_bare_dotted_number_as_a_cnpj_registration_number() -> None:
    assert "53.200.745" not in values("Reference 53.200.745 only", "REG_NUMBER")


def test_does_not_treat_a_dotted_date_as_a_phone_number() -> None:
    phones = values("Signed on 06.05.2026 by both", "PHONE")
    assert "06.05.2026" not in phones
    assert phones == []


def test_does_not_treat_an_iso_format_date_as_a_phone_number() -> None:
    assert "2026-05-06" not in values("Date: 2026-05-06 end", "PHONE")


def test_still_detects_a_real_phone_number_with_international_format() -> None:
    assert "+44 20 7946 0018" in values("Call +44 20 7946 0018 now", "PHONE")


def test_still_detects_a_real_phone_number_without_country_code() -> None:
    assert "020 7946 0018" in values("Call 020 7946 0018 now", "PHONE")


def test_detects_a_phone_number_with_a_5_digit_group() -> None:
    assert "55 11 96074-1591" in values("Phone #: 55 11 96074-1591", "PHONE")


def test_does_not_treat_a_brazilian_cep_postal_code_as_a_phone_number() -> None:
    assert not has_type("CEP 95.010-040, Caxias do Sul", "PHONE")


def test_does_not_treat_an_8_letter_word_as_a_swift_code_without_context() -> None:
    assert not has_type("TECHVISE LIMITED", "SWIFT")


def test_detects_a_swift_code_with_bic_context_keyword() -> None:
    assert "DEUTDEFF" in values("BIC: DEUTDEFF", "SWIFT")


# C2: 11-буквенные заглавные заголовки, у которых символы 5–6 — код страны
# («IN» = Индия), без метки SWIFT/BIC не считаются SWIFT.
def test_does_not_treat_the_11_letter_word_appointment_as_a_swift_code() -> None:
    assert not has_type("APPOINTMENT OF CONSULTANT", "SWIFT")


def test_does_not_treat_the_11_letter_word_termination_as_a_swift_code() -> None:
    assert not has_type("7. TERMINATION AND SURVIVAL", "SWIFT")


def test_still_detects_an_all_letter_11_char_bic_when_a_swift_bic_label_is_present() -> None:
    assert "DEUTDEFFXXX" in values("SWIFT: DEUTDEFFXXX", "SWIFT")


def test_detects_a_labeled_country_led_address() -> None:
    text = "Registered office address: Ruritania, Zenda, Central district, Rassendyll st. N 51 b. 121\n"
    addrs = of_type(text, "ADDRESS")
    assert len(addrs) == 1
    assert addrs[0].value == "Ruritania, Zenda, Central district, Rassendyll st. N 51 b. 121"
    assert text[addrs[0].start:addrs[0].end] == addrs[0].value


def test_trims_a_labeled_address_at_the_next_field_keyword() -> None:
    text = "Business address: 12 Baker Street, London, company registration number 12345678"
    assert values(text, "ADDRESS") == ["12 Baker Street, London"]


def test_ignores_an_address_mentioned_in_prose_without_a_colon() -> None:
    assert not has_type("the registered office address of the other Party shall prevail", "ADDRESS")


def test_does_not_treat_an_email_address_label_as_a_postal_address() -> None:
    assert not has_type("Email address: john@acme.co", "ADDRESS")


# C2: адреса США без метки (код штата или полное название + ZIP).
@pytest.mark.parametrize("text, expected", [
    ("Notices to: 3900 Freedom Circle, Suite # 200, Santa Clara, CA 95054\n",
     "3900 Freedom Circle, Suite # 200, Santa Clara, CA 95054"),
    ("If to Sponsor: 2791 Loker Avenue West, Carlsbad, California 92008.",
     "2791 Loker Avenue West, Carlsbad, California 92008"),
    # город и штат оба «New York»
    ("Attn: Legal, 22 East 71st Street, New York, New York 10021",
     "22 East 71st Street, New York, New York 10021"),
    ("11 Hurley Street, Cambridge, MA 02141-1234 end", "11 Hurley Street, Cambridge, MA 02141-1234"),
])
def test_detects_an_unlabeled_us_address(text: str, expected: str) -> None:
    assert expected in values(text, "ADDRESS")


def test_does_not_flag_prose_numbers_as_a_us_address_without_a_state_zip_tail() -> None:
    assert not has_type("The Company shall deliver 5000 units, within 30 days of the order.", "ADDRESS")


# C2: многострочные адреса — улица и «City, ST ZIP» на разных строках, иногда с
# табличным шумом. norm_addr схлопывает разделители, как и в TS-тесте.
def norm_addr(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


@pytest.mark.parametrize("text, expected", [
    ("Notices to:\n1300 Oakside Circle\nChanhassen, MN 55317\n12.8 Fees.", "1300 oakside circle chanhassen mn 55317"),
    # через строку-разделитель таблицы
    ("Address:\n11 Hurley Street\n\n--------------------------- Cambridge, MA 02141\nTel",
     "11 hurley street cambridge ma 02141"),
    # через пустую строку, полное название штата
    ("2791 Loker Avenue West\n\nCarlsbad, California 92008 Telephone: (619) 438-6611",
     "2791 loker avenue west carlsbad california 92008"),
])
def test_detects_a_multiline_us_address(text: str, expected: str) -> None:
    assert any(norm_addr(v) == expected for v in values(text, "ADDRESS"))


def test_does_not_multi_line_join_a_non_street_number_line_into_an_address() -> None:
    assert not has_type("500 units\nDelivered to Boston, MA 02101 promptly", "ADDRESS")


def test_bounds_a_labeled_address_at_a_docx_table_cell_boundary_and_keeps_the_phone() -> None:
    text = "Business address: Avenida Paulista, No. 1912, São Paulo<br>Phone #: 55 11 96074-1591<br>Fernando"
    matches = detect_structural(text)
    addrs = [m.value for m in matches if m.type == "ADDRESS"]
    # Адрес останавливается на <br>, а не глотает весь ряд.
    assert "Avenida Paulista, No. 1912, São Paulo" in addrs
    assert not any("96074" in a for a in addrs)
    # Телефон уцелел: он больше не внутри спана ADDRESS.
    assert any(m.type == "PHONE" and m.value == "55 11 96074-1591" for m in matches)


def test_bounds_a_labeled_address_at_an_underscore_signature_run() -> None:
    text = "Address: 12 Baker Street, London\\_\\_\\_\\_\\_\\_ John Smith"
    assert "12 Baker Street, London" in values(text, "ADDRESS")


def test_recovers_a_multi_line_address_behind_a_doubled_address_label() -> None:
    text = "Address: Address: 11 Hurley Street\n\n--------------------------- Cambridge, MA 02141\n\nTel"
    addrs = values(text, "ADDRESS")
    # Побеждает полный адрес; усеченного фрагмента «Address: 11 Hurley Street» нет.
    assert any(norm_addr(a) == "11 hurley street cambridge ma 02141" for a in addrs)
    assert not any(re.match(r"address", a.strip(), re.I) for a in addrs)


# AUD-2.1: телефоны из сплошных цифр по метке.
@pytest.mark.parametrize("text, expected", [
    ("Tel: 0612345678 for support", "0612345678"),
    ("Phone #: 12025550123 for support", "12025550123"),
])
def test_detects_a_labeled_contiguous_digit_phone_number(text: str, expected: str) -> None:
    assert expected in values(text, "PHONE")


def test_does_not_treat_a_bare_unlabeled_contiguous_number_as_a_phone_number() -> None:
    assert not has_type("Invoice No. 12025550123 is due", "PHONE")


@pytest.mark.parametrize("text, expected", [
    ("Call +36 1 234 5678 now", "+36 1 234 5678"),  # венгерский
    ("Call (202) 555-0123 now", "(202) 555-0123"),  # США со скобками
    ("Call 20 7946 0958 now", "20 7946 0958"),  # Великобритания
    ("Call +1 202 555 0123 now", "+1 202 555 0123"),  # с плюсом и разделителями
    # группировка 3-3-3 после метки остается телефоном
    ("Tel: 123 456 789 for support", "123 456 789"),
])
def test_still_detects_separated_phone_numbers(text: str, expected: str) -> None:
    assert expected in values(text, "PHONE")


# AUD-2.2: денежные суммы из прохода с разделителями отбрасываются.
@pytest.mark.parametrize("text, value", [
    ("Total items: 1 000 000 units", "1 000 000"),
    ("Population: 12 345 678 people", "12 345 678"),
])
def test_does_not_treat_an_unlabeled_thousands_grouped_number_as_a_phone_number(text: str, value: str) -> None:
    assert value not in values(text, "PHONE")


@pytest.mark.parametrize("text", [
    "Total: 123 456 789.00 due",  # сумма с копейками
    "Price: € 1 234 567 total",  # маркер евро
    "Budget: USD 2 500 000 approved",  # маркер USD
])
def test_does_not_treat_a_money_amount_as_a_phone_number(text: str) -> None:
    assert not has_type(text, "PHONE")


# Stage 2: русские структурные детекторы — ИНН/ОГРН(ИП)/КПП и р/с, к/с.
class TestRussianIdsAndAccounts:
    @pytest.mark.parametrize("text, expected", [
        ("ИНН 7736324582", "7736324582"),  # 10 цифр
        ("ИНН 500100732259", "500100732259"),  # 12 цифр (физлицо/ИП)
        ("ОГРН 1197746606232", "1197746606232"),
        ("ОГРНИП 315237500002317", "315237500002317"),
        ("КПП 773601001", "773601001"),
    ])
    def test_detects_id(self, text: str, expected: str) -> None:
        assert expected in values(text, "REG_NUMBER")

    def test_splits_an_inn_glued_directly_to_the_following_kpp_label_into_two_matches(self) -> None:
        regs = values("ИНН 7736363158КПП 773601001", "REG_NUMBER")
        assert "7736363158" in regs
        assert "773601001" in regs

    def test_detects_a_combined_inn_kpp_form_as_two_matches_without_duplicating_kpp(self) -> None:
        regs = values("ИНН / КПП 7736321479 / 773601001", "REG_NUMBER")
        assert len(regs) == 2
        assert "7736321479" in regs
        assert "773601001" in regs

    # Регрессия: ИНН — строго 10 или 12 цифр; 11-значное число в совмещенной
    # форме не принимается за ИНН.
    def test_does_not_treat_an_11_digit_number_as_inn_in_the_combined_form(self) -> None:
        regs = values("ИНН / КПП 12345678901 / 773601001", "REG_NUMBER")
        assert "12345678901" not in regs
        assert not any(len(v) == 11 for v in regs)

    @pytest.mark.parametrize("text, expected", [
        ("р/с 40702810338000246070", "40702810338000246070"),
        ("к/с 30101810400000000225", "30101810400000000225"),
    ])
    def test_detects_an_account_number(self, text: str, expected: str) -> None:
        assert expected in values(text, "ACCOUNT")

    def test_detects_a_russian_format_phone_number(self) -> None:
        phones = values("Телефон: +7 (903) 796-17-03", "PHONE")
        assert any("903" in v and "796-17-03" in v for v in phones)

    def test_reports_exact_offsets_for_a_plain_inn_match(self) -> None:
        text = "ИНН 7736324582"
        inn = next((m for m in of_type(text, "REG_NUMBER") if m.value == "7736324582"), None)
        assert inn is not None
        assert text[inn.start:inn.end] == "7736324582"

    def test_does_not_treat_a_bik_as_a_reg_number_or_account(self) -> None:
        matches = detect_structural("БИК 044525225")
        assert not any(m.type in ("REG_NUMBER", "ACCOUNT") and m.value == "044525225" for m in matches)

    def test_does_not_swallow_a_cadastral_number_as_a_reg_number(self) -> None:
        regs = values("77:06:0002019:1128-77/005/2020-12", "REG_NUMBER")
        assert not any("77:06:0002019:1128" in v for v in regs)


# Украинские идентификаторы: ЄДРПОУ (8 цифр), ІПН/РНОКПП (10 цифр).
class TestUkrainianIds:
    @pytest.mark.parametrize("text, expected", [
        ("ЄДРПОУ 12345678", "12345678"),
        ("код за ЄДРПОУ 12345678", "12345678"),
        ("ІПН 1234567890", "1234567890"),
        ("РНОКПП 1234567890", "1234567890"),
    ])
    def test_detects_id(self, text: str, expected: str) -> None:
        assert expected in values(text, "REG_NUMBER")

    @pytest.mark.parametrize("text, expected", [
        ("ЄДРПОУ 12345678", "12345678"),
        ("ІПН 1234567890", "1234567890"),
    ])
    def test_reports_exact_offsets(self, text: str, expected: str) -> None:
        reg = next((m for m in of_type(text, "REG_NUMBER") if m.value == expected), None)
        assert reg is not None
        assert text[reg.start:reg.end] == expected

    def test_does_not_emit_a_reg_number_for_a_bare_label_without_a_number(self) -> None:
        assert not has_type("Код ЄДРПОУ додається окремим листом.", "REG_NUMBER")

    def test_does_not_treat_a_random_8_digit_number_without_a_label_as_yedrpou(self) -> None:
        assert "12345678" not in values("Номер накладної 12345678 від учора", "REG_NUMBER")


class TestRussianAndUkrainianAddressLabels:
    @staticmethod
    def addresses(text: str) -> list[str]:
        return values(text, "ADDRESS")

    def test_detects_a_value_after_a_bare_label(self) -> None:
        assert self.addresses("Адрес: г. Москва, ул. Тверская, д. 1, кв. 5") == ["г. Москва, ул. Тверская, д. 1, кв. 5"]

    def test_detects_qualified_noun_labels(self) -> None:
        assert self.addresses("Юридический адрес: 125009, г. Москва, ул. Тверская, д. 1") == [
            "125009, г. Москва, ул. Тверская, д. 1",
        ]
        assert self.addresses("Фактический адрес: 125009, г. Москва, ул. Тверская, д. 1") == [
            "125009, г. Москва, ул. Тверская, д. 1",
        ]
        assert self.addresses("Адрес регистрации: г. Казань, ул. Баумана, д. 7") == ["г. Казань, ул. Баумана, д. 7"]

    # Фразовая метка — единственная, где двоеточие необязательно.
    def test_detects_a_phrase_label_with_and_without_a_colon(self) -> None:
        assert self.addresses("Зарегистрирован по адресу: г. Москва, ул. Тверская, д. 1") == [
            "г. Москва, ул. Тверская, д. 1",
        ]
        assert self.addresses("проживающий по адресу г. Москва, ул. Тверская, д. 1") == [
            "г. Москва, ул. Тверская, д. 1",
        ]

    def test_detects_ukrainian_labels(self) -> None:
        assert self.addresses("Юридична адреса: м. Київ, вул. Хрещатик, буд. 1") == ["м. Київ, вул. Хрещатик, буд. 1"]
        assert self.addresses("зареєстрований за адресою м. Київ, вул. Хрещатик, буд. 1") == [
            "м. Київ, вул. Хрещатик, буд. 1",
        ]

    def test_detects_mesto_nakhozhdeniya(self) -> None:
        assert self.addresses("Место нахождения: г. Москва, ул. Тверская, д. 1") == ["г. Москва, ул. Тверская, д. 1"]

    @pytest.mark.parametrize("text", [
        "Адрес: г. Москва, ул. Тверская, д. 1, ИНН 7712345678",
        "Адрес: г. Москва, ул. Тверская, д. 1, тел. +7 495 1234567",
        "Адрес: г. Москва, ул. Тверская, д. 1, в лице директора",
    ])
    def test_trims_the_value_at_the_next_requisites_field(self, text: str) -> None:
        assert self.addresses(text) == ["г. Москва, ул. Тверская, д. 1"]

    # Без двоеточия именная метка в прозе не превращает полпредложения в адрес.
    def test_ignores_a_noun_label_used_in_prose_without_a_colon(self) -> None:
        assert self.addresses("Юридический адрес Стороны определяется приложением № 1 к договору") == []

    def test_does_not_treat_an_electronic_address_label_as_a_postal_one(self) -> None:
        assert self.addresses("Электронный адрес: ivanov@acme.ru") == []
        assert self.addresses("Адрес электронной почты: ivanov@acme.ru") == []

    # Идентификатор, введенный словом «код», обрезает адрес и остается своим типом.
    def test_trims_before_an_identifier_introduced_by_kod_za(self) -> None:
        text = "Юридична адреса: м. Київ, вул. Хрещатик, буд. 1, код за ЄДРПОУ 12345678"
        assert self.addresses(text) == ["м. Київ, вул. Хрещатик, буд. 1"]
        assert "12345678" in values(text, "REG_NUMBER")

    def test_drops_a_trailing_requisites_separator_from_the_value(self) -> None:
        assert self.addresses("Адрес: г. Казань, ул. Баумана, д. 7, кв. 12,\nСНИЛС 123-456-789 01") == [
            "г. Казань, ул. Баумана, д. 7, кв. 12",
        ]

    def test_reports_exact_offsets(self) -> None:
        text = "Сторона, зарегистрирован по адресу: г. Москва, ул. Тверская, д. 1\n"
        m = next((x for x in detect_structural(text) if x.type == "ADDRESS"), None)
        assert m is not None
        assert text[m.start:m.end] == "г. Москва, ул. Тверская, д. 1"


class TestSnils:
    def test_detects_a_canonical_snils_after_its_label(self) -> None:
        assert "123-456-789 01" in values("СНИЛС 123-456-789 01", "REG_NUMBER")

    def test_detects_a_run_together_snils_after_its_label(self) -> None:
        assert "12345678901" in values("СНИЛС: 12345678901", "REG_NUMBER")

    def test_detects_the_hyphenated_format_without_a_label(self) -> None:
        assert "123-456-789 01" in values("123-456-789 01", "REG_NUMBER")

    def test_does_not_claim_a_bare_11_digit_number_as_snils(self) -> None:
        assert "12345678901" not in values("Инвентарный 12345678901", "REG_NUMBER")

    # Иначе СНИЛС и телефон дают одинаковые спаны, и победитель зависел бы от порядка.
    def test_is_not_also_claimed_by_the_phone_detector(self) -> None:
        assert not has_type("СНИЛС 123-456-789 01", "PHONE")

    def test_reports_exact_offsets(self) -> None:
        text = "Работник, СНИЛС 123-456-789 01, принят"
        m = next((x for x in of_type(text, "REG_NUMBER") if x.value == "123-456-789 01"), None)
        assert m is not None
        assert text[m.start:m.end] == "123-456-789 01"


class TestIdentityDocuments:
    @pytest.mark.parametrize("text, expected", [
        ("Паспорт 4506 123456", ["4506 123456"]),  # одним прогоном
        ("паспорт 45 06 123456", ["45 06 123456"]),  # серия разбита
        # Слово «номер» остается в тексте: под плейсхолдер уходят только цифры,
        # двумя отдельными спанами.
        ("Паспорт серия 4506 номер 123456", ["4506", "123456"]),
        ("Passport No. AB1234567", ["AB1234567"]),
        ("Паспорт АВ 123456", ["АВ 123456"]),  # кириллическая буквенная серия
        ("Паспорт 4506-123456", ["4506-123456"]),  # через дефис — один спан
        ("Паспорт 4506 123456, выдан 12.05.2010 ОВД Москвы", ["4506 123456"]),  # без даты и органа
        # Документ, удостоверяющий личность, — не только паспорт.
        ("Водительское удостоверение 77 12 345678", ["77 12 345678"]),
        ("водительские права 7712345678", ["7712345678"]),
        ("Driver's licence SMITH751125AB9CD", ["SMITH751125AB9CD"]),
        ("ВНЖ 82 1234567", ["82 1234567"]),
        ("вид на жительство № 1234567", ["1234567"]),
        ("Residence permit AB1234567", ["AB1234567"]),
        ("РВП 1234567", ["1234567"]),
        ("ID-картка 123456789", ["123456789"]),
        ("Военный билет АА 1234567", ["АА 1234567"]),
        ("код подразделения 770-053", ["770-053"]),
        # Метка соседнего поля коротка и заглавна, то есть неотличима от серии.
        ("Паспорт АВ 123456, ІПН 123456789012", ["АВ 123456"]),
        ("ВНЖ 82 1234567, РВП 1234567", ["82 1234567", "1234567"]),
    ])
    def test_detects(self, text: str, expected: list[str]) -> None:
        assert values(text, "ID_DOC") == expected

    @pytest.mark.parametrize("text", [
        # «Права» и «удостоверение» сами по себе стоят в каждом договоре.
        "Права и обязанности сторон 1234567 определены разделом 5",
        "Удостоверение 1234567 приложено",
        # «Паспорт сделки» — валютный контроль, не документ человека.
        "Паспорт сделки № 12/34567890 от 01.02.2020",
        "Технический паспорт 1234567 на оборудование",
        "Кадастровый паспорт 7712345678 помещения",
        # Нет правдоподобного номера после метки.
        "Паспорт № 5 приложен к делу",
        "Паспорт предъявлен при подписании",
        # Метка следующего поля не читается как серия, когда номера нет.
        "Паспорт предъявлен, ИНН 771234567890",
    ])
    def test_ignores(self, text: str) -> None:
        assert values(text, "ID_DOC") == []

    def test_leaves_the_neighbouring_identifier_to_its_own_detector(self) -> None:
        assert "123456789012" in values("Паспорт АВ 123456, ІПН 123456789012", "REG_NUMBER")

    def test_reports_exact_offsets(self) -> None:
        text = "Стороны: Иванов И.И., паспорт 4506 123456, действует лично"
        m = next((x for x in detect_structural(text) if x.type == "ID_DOC"), None)
        assert m is not None
        assert text[m.start:m.end] == "4506 123456"


class TestWebsites:
    @staticmethod
    def urls(text: str) -> list[str]:
        return values(text, "URL")

    def test_detects_a_full_url_with_a_scheme(self) -> None:
        assert self.urls("Подробности на https://example.com/terms/v2") == ["https://example.com/terms/v2"]

    def test_detects_a_www_prefixed_host(self) -> None:
        assert "www.example.co.uk" in self.urls("Сайт www.example.co.uk работает круглосуточно")

    def test_detects_a_labelled_bare_domain(self) -> None:
        assert self.urls("Сайт: example.com") == ["example.com"]

    def test_detects_a_bare_lowercase_domain(self) -> None:
        assert self.urls("Реквизиты опубликованы на acme-legal.ru") == ["acme-legal.ru"]

    def test_strips_trailing_sentence_punctuation(self) -> None:
        assert self.urls("Смотрите example.com.") == ["example.com"]

    def test_does_not_take_a_url_out_of_a_markdown_link_target(self) -> None:
        assert self.urls("[Сайт](https://example.com)") == ["https://example.com"]

    # Регистр — единственное, что отличает голый домен от сокращения с точкой.
    def test_does_not_treat_a_capitalised_abbreviation_as_a_domain(self) -> None:
        assert self.urls("Acme Ltd.Co подписало договор") == []

    def test_does_not_treat_a_filename_as_a_domain(self) -> None:
        assert self.urls("См. приложение contract.doc и akt.pdf") == []

    def test_does_not_carve_a_domain_out_of_an_email_address(self) -> None:
        assert self.urls("Контакт john.doe@example.com") == []

    def test_reports_exact_offsets(self) -> None:
        text = "Оферта размещена на https://example.com/offer и действует бессрочно"
        m = next((x for x in detect_structural(text) if x.type == "URL"), None)
        assert m is not None
        assert text[m.start:m.end] == "https://example.com/offer"


# Из imageSafety.test.ts: структурные детекторы не видят содержимое картинок.
def test_masks_image_contents_at_unchanged_offsets_before_structural_scans() -> None:
    source = "![alice@example.com +79161234567](data:image/png;base64," + "x" * 64000 + ")\nContact bob@example.com"
    mask = mask_images_for_detection(source)
    assert len(mask) == len(source)
    assert mask.index("\n") == source.index("\n")
    assert [m for m in detect_structural(source) if m.type in ("EMAIL", "PHONE")] == [
        RawMatch("EMAIL", "bob@example.com", source.index("bob@example.com"), len(source)),
    ]


def test_excludes_the_removal_notice_from_structural_detection() -> None:
    assert detect_structural(f"Before {IMAGE_REMOVED_MARKER} After") == []
