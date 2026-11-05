"""Порт src/lib/anonymize/nerFilter.test.ts."""

from __future__ import annotations

import pytest

from md_blinder.anonymize.ner_filter import filter_generic_ner_matches
from md_blinder.anonymize.types import EntityType, RawMatch


def match(type_: EntityType, value: str) -> RawMatch:
    return RawMatch(type=type_, value=value, start=0, end=len(value))


# filterGenericNerMatches

def test_drops_generic_role_noun_person_matches():
    dropped = [
        "Director",
        "Client",
        "End-User",
        "Service Provider",
        "such party",
        "recipient Party",
        "disclosing Party",
        "the violating Party",
    ]
    result = filter_generic_ner_matches([match("PERSON", v) for v in dropped])
    for v in dropped:
        assert not any(m.value == v for m in result)


# C2: ролевые/определённые термины, которые старый словарь пропускал как PERSON/COMPANY.
def test_drops_c2_role_defined_term_person_and_company_matches():
    dropped: list[tuple[EntityType, str]] = [
        ("PERSON", "Consultant"),
        ("PERSON", "CEO"),
        ("COMPANY", "Notifying Party"),
        ("PERSON", "General Counsel"),
        ("COMPANY", "Agency sales force"),
        ("PERSON", "the Addressee"),
        ("PERSON", "Vice President"),
        ("PERSON", "Sponsor"),
    ]
    result = filter_generic_ner_matches([match(t, v) for t, v in dropped])
    for _, v in dropped:
        assert not any(m.value == v for m in result)


def test_keeps_real_names_companies_that_merely_contain_a_role_word():
    kept = [
        match("PERSON", "John Smith"),
        match("COMPANY", "General Motors"),
        match("COMPANY", "Creative Agency"),
        match("COMPANY", "Acme Consulting"),
        match("PERSON", "Sales Director Anna Lee"),
    ]
    result = filter_generic_ner_matches(kept)
    for m in kept:
        last = m.value.split(" ")[-1]
        assert any(last in r.value for r in result)


def test_drops_generic_address_matches():
    dropped = ["registered office address", "principal place of business"]
    result = filter_generic_ner_matches([match("ADDRESS", v) for v in dropped])
    for v in dropped:
        assert not any(m.value == v for m in result)


def test_keeps_real_person_names():
    result = filter_generic_ner_matches([match("PERSON", "John Smith")])
    assert len(result) == 1


def test_keeps_real_company_names():
    kept = ["Acme Ltd", "Baker & Partners"]
    result = filter_generic_ner_matches([match("COMPANY", v) for v in kept])
    assert len(result) == 2


def test_keeps_address_matches_containing_digits():
    result = filter_generic_ner_matches([match("ADDRESS", "12 Baker Street")])
    assert len(result) == 1


def test_keeps_real_address_names_without_generic_words():
    result = filter_generic_ner_matches([match("ADDRESS", "Avenida Paulista")])
    assert len(result) == 1


def test_passes_structural_types_through_unchanged():
    email = match("EMAIL", "a@b.com")
    result = filter_generic_ner_matches([email])
    assert len(result) == 1
    assert result[0] == email


def test_drops_generic_reg_number_phrases_without_digits():
    dropped = [
        "Company registration number",
        "registration number",
        "VAT number",
        "tax id",
    ]
    result = filter_generic_ner_matches([match("REG_NUMBER", v) for v in dropped])
    for v in dropped:
        assert not any(m.value == v for m in result)


def test_keeps_reg_number_matches_containing_digits():
    kept = ["12345678", "53.200.745/0001-60"]
    result = filter_generic_ner_matches([match("REG_NUMBER", v) for v in kept])
    assert len(result) == 2


def test_drops_a_uk_postcode_mislabeled_as_reg_number():
    dropped = ["AB1 2CD", "SW1A 1AA", "EC1A 1BB"]
    result = filter_generic_ner_matches([match("REG_NUMBER", v) for v in dropped])
    assert len(result) == 0


def test_drops_a_person_span_that_carries_a_company_suffix():
    dropped = ["ACME LIMITED GLOBEX LLC", "Acme Ltd", "Foo GmbH"]
    result = filter_generic_ner_matches([match("PERSON", v) for v in dropped])
    assert len(result) == 0


def test_keeps_a_company_span_with_a_company_suffix():
    # Правило суффикса — только для PERSON.
    result = filter_generic_ner_matches([match("COMPANY", "Acme Ltd")])
    assert len(result) == 1


def test_trims_a_leading_title_from_a_person_and_keeps_the_name_with_adjusted_offsets():
    result = filter_generic_ner_matches([match("PERSON", "Director John Smith")])
    assert len(result) == 1
    assert result[0].value == "John Smith"
    assert result[0].start == len("Director ")
    assert result[0].end == len("Director John Smith")


def test_leaves_a_title_less_person_name_untouched():
    result = filter_generic_ner_matches([match("PERSON", "John Smith")])
    assert len(result) == 1
    assert result[0].value == "John Smith"
    assert result[0].start == 0


@pytest.mark.parametrize(
    ("value", "number"),
    [
        ("registration number 193394577", "193394577"),
        ("Company registration no. 193394577", "193394577"),
        ("VAT No. GB123456789", "GB123456789"),
    ],
)
def test_trims_a_leading_generic_wrapper_from_a_reg_number(value: str, number: str):
    result = filter_generic_ner_matches([match("REG_NUMBER", value)])
    assert len(result) == 1
    assert result[0].value == number
    # start сдвинут на длину обёртки, end остался прежним → спан = сам номер.
    assert result[0].start == len(value) - len(number)
    assert result[0].end == len(value)


def test_leaves_a_wrapper_less_reg_number_untouched():
    result = filter_generic_ner_matches([match("REG_NUMBER", "HU12345678")])
    assert len(result) == 1
    assert result[0].value == "HU12345678"
    assert result[0].start == 0


def test_still_drops_a_pure_generic_reg_number_wrapper_with_no_number():
    dropped = ["registration number", "company number"]
    result = filter_generic_ner_matches([match("REG_NUMBER", v) for v in dropped])
    assert len(result) == 0


def test_drops_generic_person_phrases_built_from_participial_adjective_modifiers():
    dropped = ["permitted subcontractors", "Impacted Party", "natural person"]
    result = filter_generic_ner_matches([match("PERSON", v) for v in dropped])
    for v in dropped:
        assert not any(m.value == v for m in result)


# Ролевые термины договора ("Contributor", "the Contributor", "undersigned
# contributor") GLiNER тегает как PERSON — это определения ролей, не PII.
def test_drops_contributor_role_defined_terms_tagged_as_person():
    dropped = ["Contributor", "the Contributor", "undersigned contributor"]
    result = filter_generic_ner_matches([match("PERSON", v) for v in dropped])
    for v in dropped:
        assert not any(m.value == v for m in result)


# В системе нет типа DATE, значит дата не может быть валидной сущностью
# ни для одного из проверяемых типов.
def test_drops_a_date_mislabeled_as_reg_number_or_person():
    dropped: list[tuple[EntityType, str]] = [
        ("REG_NUMBER", "05.06.2026"),
        ("REG_NUMBER", "2026-06-05"),
        ("PERSON", "5/6/26"),
        ("REG_NUMBER", "31.12.1999"),
        ("REG_NUMBER", "12/25/2024"),  # американский порядок m/d/y
    ]
    result = filter_generic_ner_matches([match(t, v) for t, v in dropped])
    assert len(result) == 0


# Регрессия recall: GENERIC_DATE не должна отбрасывать регномера, которые
# просто выглядят как дата по разделителям, но не проходят по диапазону
# дня/месяца (день/месяц > 31/12 соответственно).
def test_keeps_date_shaped_values_that_are_not_plausible_dates():
    kept: list[tuple[EntityType, str]] = [
        ("REG_NUMBER", "12.34.5678"),
        ("REG_NUMBER", "99.99.9999"),
        ("REG_NUMBER", "45.13.2020"),
        ("REG_NUMBER", "2026-13-45"),
    ]
    result = filter_generic_ner_matches([match(t, v) for t, v in kept])
    assert len(result) == len(kept)


# Регрессия recall: значения, не покрытые другими тестами файла, которые
# фильтр не должен отбрасывать.
def test_keeps_real_reg_numbers_companies_not_covered_elsewhere():
    kept = [match("REG_NUMBER", "HU12345678"), match("COMPANY", "Consultant Software")]
    result = filter_generic_ner_matches(kept)
    assert len(result) == 2


# Этап 3: русский словарь ролевых/родовых терминов договора. Мультиязычная
# NER-модель тегает русские ролевые термины ("Заказчик", "Сторона", "третьи
# лица", "порядковый номер", "адрес электронной почты") так же уверенно
# (0.8-0.95), как английские "Client"/"Party" — это не PII.

def test_ru_drops_role_noun_person_matches():
    dropped = [
        "Заказчик",
        "Заказчика",
        "Заказчику",
        "Заказчиком",
        "Исполнитель",
        "Исполнителя",
        "Сторона",
        "Стороны",
        "Сторонами",
        "третьи лица",
        "третьих лиц",
        "уполномоченного представителя Стороны",
        "Виновная Сторона",
        "должностным лицам",
        "аффилированных лиц",
        "ФИО",
        "работники",
        "представителей",
        "Арендатор",
        "Субарендатор",
    ]
    result = filter_generic_ner_matches([match("PERSON", v) for v in dropped])
    for v in dropped:
        assert not any(m.value == v for m in result)


def test_ru_drops_role_noun_company_matches():
    dropped = ["Заказчик", "Сторонами", "Общество с ограниченной ответственностью"]
    result = filter_generic_ner_matches([match("COMPANY", v) for v in dropped])
    for v in dropped:
        assert not any(m.value == v for m in result)


def test_ru_drops_generic_reg_number_phrases_without_digits():
    dropped = ["порядковый номер", "регистрационный номер"]
    result = filter_generic_ner_matches([match("REG_NUMBER", v) for v in dropped])
    for v in dropped:
        assert not any(m.value == v for m in result)


def test_ru_drops_generic_address_phrases_without_digits():
    dropped = ["адрес электронной почты", "юридический адрес", "адрес местонахождения"]
    result = filter_generic_ner_matches([match("ADDRESS", v) for v in dropped])
    for v in dropped:
        assert not any(m.value == v for m in result)


# Регрессия recall: настоящие ФИО не должны отбрасываться, даже если
# содержат слоги, похожие на родовые корни.
def test_ru_keeps_real_person_names():
    kept = [
        "Окишев Алексей Александрович",
        "Столяров Валентин Михайлович",
        "Галенко Сергей Николаевич",
        "Цой Алексей",
        "Сергей Сущевский",
        "Иван Петров",
    ]
    result = filter_generic_ner_matches([match("PERSON", v) for v in kept])
    assert len(result) == len(kept)


# Регрессия recall: настоящие названия компаний (в т.ч. смешанные RU/EN)
# не должны отбрасываться.
def test_ru_keeps_real_ru_en_company_names():
    kept = [
        "ООО «Когнитек»",
        "ООО «Бифорком Текнолоджис»",
        "ООО «Коммуникации технологии менеджмент»",
        "Ling Trade Limited",
        "Acme Ltd",
    ]
    result = filter_generic_ner_matches([match("COMPANY", v) for v in kept])
    assert len(result) == len(kept)


# Регрессия против утечки PII: ролевые ОСНОВЫ матчились по префиксу, и на
# однословном спане allGeneric+hasRoleNoun становились тривиально истинны для
# реальных фамилий/брендов/городов, начинающихся с того же корня. Фикс —
# однословный спан дропаем только по точной форме. Эти кейсы ДОЛЖНЫ выживать.
def test_ru_keeps_single_word_person_matches_that_merely_share_a_prefix_with_a_role_stem():
    kept = ["Работников", "Директоров", "Сторонин", "Гарантов"]
    result = filter_generic_ner_matches([match("PERSON", v) for v in kept])
    assert len(result) == len(kept)


def test_ru_keeps_single_word_company_matches_that_merely_share_a_prefix_with_a_role_stem():
    kept = ["Гарант", "Гарантия"]
    result = filter_generic_ner_matches([match("COMPANY", v) for v in kept])
    assert len(result) == len(kept)


def test_ru_keeps_single_word_address_matches_that_merely_share_a_prefix_with_the_el_stem():
    kept = ["Электросталь", "Элиста", "Эльбрус"]
    result = filter_generic_ner_matches([match("ADDRESS", v) for v in kept])
    assert len(result) == len(kept)


# Регрессия: настоящие однословные дропы родовых терминов по-прежнему
# должны отбрасываться (фикс не должен урезать recall фильтра).
def test_ru_still_drops_real_single_word_generic_person_terms():
    dropped = ["Заказчик", "Заказчика", "Стороны", "Исполнитель", "Арендатор", "работники", "представителей"]
    result = filter_generic_ner_matches([match("PERSON", v) for v in dropped])
    for v in dropped:
        assert not any(m.value == v for m in result)


# Регрессия: многословные дропы родовых фраз по-прежнему должны отбрасываться.
def test_ru_still_drops_multi_word_generic_phrases():
    dropped_person = ["третьи лица", "уполномоченного представителя Стороны"]
    result_person = filter_generic_ner_matches([match("PERSON", v) for v in dropped_person])
    for v in dropped_person:
        assert not any(m.value == v for m in result_person)

    result_address = filter_generic_ner_matches([match("ADDRESS", "адрес электронной почты")])
    assert not any(m.value == "адрес электронной почты" for m in result_address)

    result_regnum = filter_generic_ner_matches([match("REG_NUMBER", "порядковый номер")])
    assert not any(m.value == "порядковый номер" for m in result_regnum)


# Украинский словарь ролевых/родовых терминов договора — по образцу русского.
# Реальные ложняки NER на украинских документах: "Замовника" (PERSON),
# "фізичної особи" (PERSON).

def test_ua_drops_role_noun_person_matches():
    dropped = [
        "Замовник", "Замовника", "Виконавець", "Підрядник", "Сторони", "Орендар",
        "фізична особа", "юридична особа", "фізичної особи",
    ]
    result = filter_generic_ner_matches([match("PERSON", v) for v in dropped])
    for v in dropped:
        assert not any(m.value == v for m in result)


def test_ua_drops_role_noun_company_matches():
    result = filter_generic_ner_matches([match("COMPANY", "Замовник")])
    assert not any(m.value == "Замовник" for m in result)


# Регрессия recall: настоящие украинские (и латинские) ФИО/компании не
# должны отбрасываться — критично, иначе утечка PII.
def test_ua_keeps_real_ua_en_person_and_company_names():
    persons = ["Петренко", "Вікторія Петренко", "Дерріл Ідс", "Ідс", "Вікторія", "Петренко Вікторія"]
    person_result = filter_generic_ner_matches([match("PERSON", v) for v in persons])
    assert len(person_result) == len(persons)

    company_result = filter_generic_ner_matches([match("COMPANY", "BJSS Limited")])
    assert len(company_result) == 1


# Ложные находки из договора пользователя (2026-11): роли, общие слова, «адреса» без адреса.
def test_drops_roles_and_common_words_from_user_report():
    dropped: list[tuple[EntityType, str]] = [
        ("PERSON", "professional advisers"),
        ("PERSON", "lawyers"),
        ("PERSON", "auditors"),
        ("PERSON", "bankers"),
        ("PERSON", "Upstream Providers"),
        ("PERSON", "indemnified Party"),
        ("PERSON", "sender"),
        ("PERSON", "successor"),
        ("PERSON", "Senior executives of the Parties"),
        ("PERSON", "legal and financial advisers"),
        ("PERSON", "insurers"),
        ("PERSON", "minors"),
        ("PERSON", "Party"),
        ("PERSON", "Parties"),
        ("PERSON", "a professional"),
        ("PERSON", "CLIENT LEGAL NAME"),
        ("PERSON", "Chief Executive Officer"),
        ("PERSON", "The Customer / Замовник"),
        ("PERSON", "посетителя"),
        ("PERSON", "контактное лицо"),
        ("PERSON", "Получившая Информацию Сторона"),
        ("PERSON", "второй Стороне"),
        ("COMPANY", "it"),
        ("COMPANY", "limited liability company"),
        ("COMPANY", "affiliates or subsidiaries"),
        ("COMPANY", "a Nevada corporation"),
        ("COMPANY", "Delaware limited liability company"),
        ("ADDRESS", "wallet address"),
        ("ADDRESS", "Notice addresses"),
        ("ADDRESS", "addresses listed in the Agreement"),
        ("ADDRESS", "territory"),
        ("ADDRESS", "адресов"),
        ("ADDRESS", "адрес нахождения Помещений"),
        ("ADDRESS", "ИНН"),
        ("REG_NUMBER", "number"),
        ("REG_NUMBER", "part number"),
        ("REG_NUMBER", "Telecopy No."),
        ("REG_NUMBER", "Licensee"),
    ]
    result = filter_generic_ner_matches([match(t, v) for t, v in dropped])
    assert [m.value for m in result] == []


def test_keeps_real_data_next_to_the_new_rules():
    kept = [
        match("PERSON", "John Smith"),
        match("PERSON", "Jim Minor"),
        match("PERSON", "Timothy S Orr"),
        match("PERSON", "Иван Петров"),
        match("COMPANY", "Delaware Trust Company"),
        match("COMPANY", "Nevada Power Company"),
        match("COMPANY", "Creative Agency"),
        RawMatch("COMPANY", "plan b", 0, 6, 0.97),
        match("ADDRESS", "Nicosia, Cyprus"),
        match("ADDRESS", "Dallas, Texas"),
        match("ADDRESS", "County of San Diego, California"),
        match("REG_NUMBER", "MM12/2023"),
    ]
    result = filter_generic_ner_matches(kept)
    assert [m.value for m in result] == [m.value for m in kept]


def test_unsure_lowercase_company_is_dropped_but_sure_one_is_kept():
    unsure = RawMatch("COMPANY", "courier service", 0, 15, 0.28)
    sure = RawMatch("COMPANY", "acme widgets", 0, 12, 0.9)
    assert filter_generic_ner_matches([unsure, sure]) == [sure]


def test_drops_market_roles_and_descriptions_from_second_report():
    dropped: list[tuple[EntityType, str]] = [
        ("COMPANY", "carriers"),
        ("COMPANY", "aggregators"),
        ("COMPANY", "cloud/platform operators"),
        ("COMPANY", "Network Operator"),
        ("COMPANY", "Network Operators"),
        ("COMPANY", "mobile operator"),
        ("PERSON", "claiming Party"),
        ("PERSON", "Affiliates’ employees"),
        ("PERSON", "SMS recipient"),
        ("PERSON", "designated Commercial Coordinator"),
        ("PERSON", "Section 15"),
        ("PERSON", "operators of the Call Center"),
        ("COMPANY", "Clause 3.2"),
        ("ADDRESS", "пункт 5.1"),
    ]
    unsure = [
        RawMatch("COMPANY", "Client-provided", 0, 15, 0.2),
        RawMatch("COMPANY", "Digital Asset network", 0, 21, 0.2),
    ]
    result = filter_generic_ner_matches([match(t, v) for t, v in dropped] + unsure)
    assert [m.value for m in result] == []


def test_lowercase_edge_words_are_trimmed_from_a_real_name():
    text = "John Smith, director"
    result = filter_generic_ner_matches([match("PERSON", text)])
    assert [(m.value, m.start, m.end) for m in result] == [("John Smith", 0, 10)]
    kept = filter_generic_ner_matches([match("PERSON", "Ludwig van Beethoven"), match("PERSON", "Fernando da Silva")])
    assert [m.value for m in kept] == ["Ludwig van Beethoven", "Fernando da Silva"]
    # Уверенная компания строчными остается: бывают такие названия.
    sure = RawMatch("COMPANY", "plan b", 0, 6, 0.97)
    assert filter_generic_ner_matches([sure]) == [sure]


def test_trims_punctuation_on_span_edges():
    text = "registered office at Quijano Chambers, P.O. Box 3159, Road Town, Tortola"
    start = text.index(", P.O.")
    end = text.index("Town,") + 5
    [m] = filter_generic_ner_matches([RawMatch("ADDRESS", text[start:end], start, end)])
    assert m.value == "P.O. Box 3159, Road Town"
    assert text[m.start:m.end] == m.value


def test_keeps_inner_and_dot_punctuation():
    [m] = filter_generic_ner_matches([match("COMPANY", "Acme Holdings Inc.")])
    assert m.value == "Acme Holdings Inc."
    assert filter_generic_ner_matches([match("ADDRESS", " , ;")]) == []


def test_drops_defined_party_terms_in_ukrainian_and_russian():
    for type_, value in [
        ("PERSON", "Клієнт"), ("PERSON", "Клієнтом"), ("PERSON", "Клиента"),
        ("COMPANY", "Провайдер Послуг"), ("COMPANY", "Провайдеру Послуг"), ("COMPANY", "Поставщик услуг"),
    ]:
        assert filter_generic_ner_matches([match(type_, value)]) == [], value
    assert filter_generic_ner_matches([match("PERSON", "Клієнтенко Іван")]) != []
