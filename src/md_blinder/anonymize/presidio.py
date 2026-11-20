"""Детекторы, перенесенные из Microsoft Presidio (порт src/lib/anonymize/presidioDetectors.ts).

Источник: predefined_recognizers Presidio, коммит d884790. Взяты форматы и
правила проверки тех идентификаторов, которые встречаются в договорах и не
покрыты остальными детекторами.
License: presidio.LICENSE (MIT, Copyright (c) Presidio Contributors).

В Presidio слабые шаблоны добирают уверенность контекстными словами рядом с
совпадением. У нас нет оценки уверенности для структурных находок, поэтому
шаблон, который без контекста дает ложные срабатывания (налоговый номер SARS,
IPv4), требует метку перед значением, а остальные принимаются только после
полной проверки формата.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ._js_regex import JsRegex
from .types import EntityType, RawMatch


def _is_ascii_digits(s: str) -> bool:
    return s.isascii() and s.isdigit()


def luhn_valid(digits: str) -> bool:
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _push(matches: list[RawMatch], type_: EntityType, text: str, start: int, end: int) -> None:
    matches.append(RawMatch(type_, text[start:end], start, end))


def is_za_id_number(digits: str, today: Optional[datetime] = None) -> bool:
    """ЮАР, ID-номер: YYMMDD SSSS C A Z.

    Дата рождения, порядковый номер, гражданство (0 гражданин, 1 постоянный
    резидент, 2 беженец), устаревшая цифра 8/9 и контрольная цифра Luhn. Дата не
    может быть в будущем. today без часового пояса считается местным временем,
    как Date в JS.
    """
    if today is None:
        today = datetime.now(timezone.utc)
    if len(digits) != 13 or not _is_ascii_digits(digits):
        return False
    if digits[10] not in "012" or digits[11] not in "89":
        return False
    yy = int(digits[0:2])
    month = int(digits[2:4])
    day = int(digits[4:6])
    pivot = today.astimezone().year % 100  # getFullYear(): год по местному времени
    year = (1900 if yy > pivot else 2000) + yy
    try:
        birth = datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return False
    if birth.timestamp() > today.timestamp():
        return False
    return luhn_valid(digits)


# Кроме слитной записи из Presidio принимаем принятую группировку 6-4-3.
_ZA_ID_RE = JsRegex(r"(?<![\d-])(?:\d{13}|\d{6} \d{4} \d{3})(?![\d-])")


def _detect_za_id_number(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    for m in _ZA_ID_RE.finditer(text):
        if is_za_id_number(m.group().replace(" ", "")):
            _push(matches, "ID_DOC", text, m.start(), m.end())
    return matches


# ЮАР, регистрационный номер компании CIPC: YYYY/NNNNNN/NN, у старых форм —
# префикс вида CK1995/012345. Год не позже текущего.
_ZA_COMPANY_RE = JsRegex(r"\b(?:(?:CK|NR|K|T|W|B|M|N)(\d{4})\/\d{6}|(\d{4})\/\d{6}\/\d{2})\b")


def _detect_za_company_registration(text: str, today: Optional[datetime] = None) -> list[RawMatch]:
    matches: list[RawMatch] = []
    this_year = (today or datetime.now(timezone.utc)).astimezone().year
    for m in _ZA_COMPANY_RE.finditer(text):
        year = int(m.group(1) if m.group(1) is not None else m.group(2))
        if 1800 <= year <= this_year:
            _push(matches, "REG_NUMBER", text, m.start(), m.end())
    return matches


# ЮАР, налоговый номер SARS: 10 цифр, первая 0, 1, 2, 3 или 9 (VAT начинается
# с 4 и ловится меткой VAT). Шаблон слишком общий, поэтому только после метки.
_ZA_TAX_RE = JsRegex(
    r"\b(?:income\s+tax|tax\s+ref(?:erence)?|taxpayer(?:\s+ref(?:erence)?)?|tax|SARS|ITR)"
    r"(?:\s+(?:reference|registration|ref|number|no))*\.?[\s:#.]*([01239]\d{9})\b",
    "gi",
)


def _detect_za_tax_number(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    for m in _ZA_TAX_RE.finditer(text):
        group = m.group(1)
        start = m.start() + len(m.group()) - len(group)
        _push(matches, "REG_NUMBER", text, start, start + len(group))
    return matches


# США, SSN: средний по надежности шаблон Presidio (3-2-4 с одним видом
# разделителя) и его правила отсева заведомо недействительных номеров.
_REPEATED_DIGIT_RE = JsRegex(r"^(\d)\1*$")


def _is_valid_us_ssn(raw: str) -> bool:
    delimiters = {ch for ch in raw if not ("0" <= ch <= "9")}
    if len(delimiters) > 1:
        return False
    digits = "".join(ch for ch in raw if "0" <= ch <= "9")
    if _REPEATED_DIGIT_RE.test(digits):
        return False
    if digits[3:5] == "00" or digits[5:] == "0000":
        return False
    if digits.startswith("000") or digits.startswith("666"):
        return False
    return digits not in ("123456789", "987654320", "078051120")


_US_SSN_RE = JsRegex(r"(?<![\d-])\d{3}[- .]\d{2}[- .]\d{4}(?![\d-])")


def _detect_us_ssn(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    for m in _US_SSN_RE.finditer(text):
        if _is_valid_us_ssn(m.group()):
            _push(matches, "REG_NUMBER", text, m.start(), m.end())
    return matches


# Великобритания, National Insurance Number. Только заглавные буквы: в прозе
# строчные сочетания вида «ab 12 34 56 c» дали бы ложные срабатывания.
_UK_NINO_RE = JsRegex(
    r"\b(?!BG|GB|NK|KN|NT|TN|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z] ?\d{2} ?\d{2} ?\d{2} ?[A-D]\b"
)


def _detect_uk_nino(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    for m in _UK_NINO_RE.finditer(text):
        _push(matches, "REG_NUMBER", text, m.start(), m.end())
    return matches


# IP-адреса. IPv4 неотличим от многоуровневой нумерации пунктов («12.1.3.4»),
# поэтому требует метку IP рядом. У IPv6 обязательны «::» или восемь групп,
# поэтому время вида 10:30 под шаблон не попадает.
_IPV4_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IPV4_SRC = rf"{_IPV4_OCTET}(?:\.{_IPV4_OCTET}){{3}}(?:/(?:3[0-2]|[12]?\d))?"
_IPV4_RE = JsRegex(rf"\bIP(?:v4)?(?:\s+address(?:es)?)?\b[^\n\d]{{0,24}}?({_IPV4_SRC})(?![\d.])", "gi")
# В TS это ^(...) на text.slice(cursor); здесь то же через match с позиции.
_IPV4_LIST_RE = JsRegex(rf"(?:\s*(?:,|;|and|or|и|или)\s*)({_IPV4_SRC})(?![\d.])", "i")
_IPV6_RE = JsRegex(
    r"(?<![\w:])(?:(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}|(?:[0-9A-Fa-f]{1,4}:){1,7}:"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,6}:[0-9A-Fa-f]{1,4}|(?:[0-9A-Fa-f]{1,4}:){1,5}(?::[0-9A-Fa-f]{1,4}){1,2}"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,4}(?::[0-9A-Fa-f]{1,4}){1,3}|(?:[0-9A-Fa-f]{1,4}:){1,3}(?::[0-9A-Fa-f]{1,4}){1,4}"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,2}(?::[0-9A-Fa-f]{1,4}){1,5}|[0-9A-Fa-f]{1,4}:(?::[0-9A-Fa-f]{1,4}){1,6}"
    r"|:(?::[0-9A-Fa-f]{1,4}){1,7})(?:\/(?:12[0-8]|1[01]\d|[1-9]?\d))?(?![\w:]|\.\d)"
)
_HEX_PAIR_RE = JsRegex(r"[0-9A-Fa-f]{2}")


def _detect_ip_address(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    pos = 0
    while True:
        m = _IPV4_RE.search(text, pos)
        if m is None:
            break
        group = m.group(1)
        start = m.start() + len(m.group()) - len(group)
        _push(matches, "OTHER", text, start, start + len(group))
        # Перечисление «IP addresses 10.0.0.1, 10.0.0.2 and 10.0.0.3»: адреса после
        # первого в том же списке принадлежат той же метке.
        cursor = start + len(group)
        while True:
            nxt = _IPV4_LIST_RE.match(text, cursor)
            if nxt is None:
                break
            item = nxt.group(1)
            s = cursor + len(nxt.group()) - len(item)
            _push(matches, "OTHER", text, s, s + len(item))
            cursor = s + len(item)
        pos = cursor
    for m in _IPV6_RE.finditer(text):
        value = m.group()
        if value == "::" or not _HEX_PAIR_RE.test(value):
            continue
        _push(matches, "OTHER", text, m.start(), m.end())
    return matches


# UUID версий 1–8 с корректным вариантом. Нулевой UUID — служебное значение.
_UUID_RE = JsRegex(
    r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-([0-9A-Fa-f])[0-9A-Fa-f]{3}-([0-9A-Fa-f])[0-9A-Fa-f]{3}-[0-9A-Fa-f]{12}\b"
)


def _detect_uuid(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    for m in _UUID_RE.finditer(text):
        if m.group(1) not in "12345678" or m.group(2) not in "89abAB":
            continue
        _push(matches, "OTHER", text, m.start(), m.end())
    return matches


def detect_presidio_ids(text: str) -> list[RawMatch]:
    return [
        *_detect_za_id_number(text),
        *_detect_za_company_registration(text),
        *_detect_za_tax_number(text),
        *_detect_us_ssn(text),
        *_detect_uk_nino(text),
        *_detect_ip_address(text),
        *_detect_uuid(text),
    ]
