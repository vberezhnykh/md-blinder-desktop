"""Одно имя и адрес латиницей и кириллицей в двуязычном договоре (anonymize/cross_script.py).

Все имена и адреса здесь выдуманы."""

from md_blinder.anonymize.cross_script import TextIndex, find_counterpart, fold_word, same_value
from md_blinder.anonymize.pipeline import anonymize_markdown
from md_blinder.anonymize.types import RawMatch


def test_fold_word_unifies_transliteration_systems():
    assert fold_word("Щербак") == fold_word("SHCHERBAK") == fold_word("Scherbak")
    assert fold_word("Юлія") == fold_word("Yuliia") == fold_word("Yuliya") == fold_word("Yulia")
    assert fold_word("Ольга") == fold_word("Olha") == fold_word("Olga")
    assert fold_word("Олегівна") == fold_word("Olehivna")
    assert fold_word("Алексей") == fold_word("Alexey")
    assert fold_word("Львів") == fold_word("Lviv")


def test_same_person_in_two_scripts():
    assert same_value("PE HNATIUK TARAS OLEHOVYCH", "ФОП ГНАТЮК ТАРАС ОЛЕГОВИЧ", "PERSON")
    assert same_value("Tsoy Alexey", "Цой Алексей", "PERSON")
    # Разные люди и одна письменность не связываются.
    assert not same_value("Ivan Petrov", "Іван Коваленко", "PERSON")
    assert not same_value("Ivan Petrov", "Ivan Petrov", "PERSON")
    # Одно слово не связывается: «Іван» встречается у многих.
    assert not same_value("Ivan Petrov", "Іван", "PERSON")
    assert not same_value("Ivan Petrov", "Петров", "PERSON")


def test_same_address_in_two_scripts():
    assert same_value(
        "79000, Lviv region, Zolochivskyi district, village Vyshneve, Sadova street, house 3B, apartment 21",
        "Україна, 79000, Львівська обл., Золочівський район, село Вишнєве, вул. Садова, будинок 3Б, квартира 21",
        "ADDRESS",
    )
    assert same_value("Lviv, Ukraine", "Львів, Україна", "ADDRESS")
    assert same_value("Lviv, Ukraine", "Львів", "ADDRESS")
    # Другой дом на той же улице и город внутри адреса офиса — разные значения.
    assert not same_value("Sadova street 5, Lviv", "вул. Садова 7, Львів", "ADDRESS")
    assert not same_value("Office 4, Sadova street, Odesa", "Одеса", "ADDRESS")


def test_counterpart_found_where_model_missed_it():
    text = (
        "registered office is at Office No. 12-14, Sadova street, Odesa, Ukraine.\n\n"
        "зареєстрований офіс знаходиться в офісі № 12-14, вул. Садова, Одеса, Україна та "
        "інше. Офіс 12 в Одесі."
    )
    value = "Office No. 12-14, Sadova street, Odesa"
    start = text.index(value)
    spans = find_counterpart(TextIndex(text), value, "ADDRESS", [(start, start + len(value))])
    assert [text[s:e] for s, e in spans] == ["12-14, вул. Садова, Одеса"]


def test_pipeline_gives_one_label_to_both_scripts():
    text = (
        "and PE HNATIUK TARAS OLEHOVYCH (the Service Provider), office: Office No. 12-14, Sadova street, "
        "Odesa, Ukraine.\n\n"
        "та ФОП ГНАТЮК ТАРАС ОЛЕГОВИЧ («Провайдер»), офіс № 12-14, вул. Садова, Одеса, Україна."
    )

    def at(type_, value, score=0.9):
        start = text.index(value)
        return RawMatch(type_, value, start, start + len(value), score)

    matches = [
        at("PERSON", "PE HNATIUK TARAS OLEHOVYCH"),
        at("PERSON", "ФОП ГНАТЮК ТАРАС ОЛЕГОВИЧ"),
        at("ADDRESS", "Office No. 12-14, Sadova street, Odesa"),
        at("ADDRESS", "№ 12-14", 0.2),
    ]
    result = anonymize_markdown(text, lambda _md: matches)
    people = [e for e in result.entities if e.type == "PERSON"]
    addresses = [e for e in result.entities if e.type == "ADDRESS"]
    assert len(people) == 1 and len(people[0].positions) == 2
    assert len(addresses) == 1 and len(addresses[0].positions) == 2 and addresses[0].anonymize
    assert "ГНАТЮК" not in result.markdown and "Садова" not in result.markdown
    assert result.markdown.count("[PERSON_1]") == 2 and result.markdown.count("[ADDRESS_1]") == 2
