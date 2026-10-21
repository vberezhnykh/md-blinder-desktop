"""Латинские двойники в словах после OCR."""

import pytest

from md_blinder.convert.script_mix import fix_mixed_scripts


@pytest.mark.parametrize(
    "ocr, expected",
    [
        ("ИHH 7736321479, ОГPH 1027700132195, KПП 773601001, БИK 044525225",
         "ИНН 7736321479, ОГРН 1027700132195, КПП 773601001, БИК 044525225"),
        ("ФІЗИЧНA OCOБА-ПІДПPИЄMЕЦЬ", "ФІЗИЧНА ОСОБА-ПІДПРИЄМЕЦЬ"),
        ("CONTRACTOR/ВИКОHABEЦЬ", "CONTRACTOR/ВИКОНАВЕЦЬ"),
        # Слово только из двойников решает строка.
        ("ОOO «Альфа», к/c 30101810400000000225", "ООО «Альфа», к/с 30101810400000000225"),
        ("M.П. подпись", "М.П. подпись"),
        # Целиком латинские сокращения — только известные и в кириллической строке.
        ("OOO «Бета», p/c 40702810900000000001", "ООО «Бета», р/с 40702810900000000001"),
        ("TOB «Весна»", "ТОВ «Весна»"),
        ("Покупатель: 000 «Аналит», 0OO \"Бета\"", "Покупатель: ООО «Аналит», ООО \"Бета\""),
        ("соглашение K Договору o нижеследующем. Bce иные", "соглашение К Договору о нижеследующем. Все иные"),
        ("Петро i Марія", "Петро і Марія"),
        ("в ПАО Сбербанк \\kappa/c 30101810400000000225, к/с No 30101810400000000225, к/с Ne 301",
         "в ПАО Сбербанк к/с 30101810400000000225, к/с № 30101810400000000225, к/с № 301"),
        # Английские слова с кириллическими двойниками.
        ("Alexeу Раyment Comраny", "Alexey Payment Company"),
    ],
)
def test_fixes_twins(ocr: str, expected: str) -> None:
    assert fix_mixed_scripts(ocr) == expected


@pytest.mark.parametrize(
    "text",
    [
        "IT-компания и HR-отдел",
        "услуги в IT отделе",
        "John and I signed",
        "Payment to OOO account p/c",
        "Договор поставки № 17/26 от 1 марта",
        "Сумма 1 000 «рублей»",
        "Paid 000 \"units\"",
        "Invoice No. 15 dated",
        "John Smith, ООО «Гамма»",
        "",
    ],
)
def test_leaves_clean_text(text: str) -> None:
    assert fix_mixed_scripts(text) == text


def test_keeps_lines() -> None:
    text = "ИHH 1234567890\n\nAlexeу\n"
    fixed = fix_mixed_scripts(text)
    assert fixed.split("\n") == ["ИНН 1234567890", "", "Alexey", ""]
