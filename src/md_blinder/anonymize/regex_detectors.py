"""Структурные детекторы на регулярных выражениях (порт src/lib/anonymize/regexDetectors.ts).

Шаблоны перенесены из TS дословно и компилируются через _js_regex с семантикой
JS (ASCII \\d/\\w/\\b, JS-набор \\s, $ только в конце строки). Порядок правил,
константы и проверки совпадают с TS. Смещения — в кодовых точках Python.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ._js_regex import JsRegex, Match, js_trim, js_trim_start
from .image_safety import find_image_exclusion_ranges, mask_images_for_detection
from .presidio import detect_presidio_ids, is_za_id_number
from .types import RawMatch

ISO_COUNTRY_CODES: frozenset[str] = frozenset({
    "AD", "AE", "AF", "AG", "AI", "AL", "AM", "AO", "AQ", "AR", "AS", "AT", "AU", "AW", "AX", "AZ",
    "BA", "BB", "BD", "BE", "BF", "BG", "BH", "BI", "BJ", "BL", "BM", "BN", "BO", "BQ", "BR", "BS",
    "BT", "BV", "BW", "BY", "BZ", "CA", "CC", "CD", "CF", "CG", "CH", "CI", "CK", "CL", "CM", "CN",
    "CO", "CR", "CU", "CV", "CW", "CX", "CY", "CZ", "DE", "DJ", "DK", "DM", "DO", "DZ", "EC", "EE",
    "EG", "EH", "ER", "ES", "ET", "FI", "FJ", "FK", "FM", "FO", "FR", "GA", "GB", "GD", "GE", "GF",
    "GG", "GH", "GI", "GL", "GM", "GN", "GP", "GQ", "GR", "GS", "GT", "GU", "GW", "GY", "HK", "HM",
    "HN", "HR", "HT", "HU", "ID", "IE", "IL", "IM", "IN", "IO", "IQ", "IR", "IS", "IT", "JE", "JM",
    "JO", "JP", "KE", "KG", "KH", "KI", "KM", "KN", "KP", "KR", "KW", "KY", "KZ", "LA", "LB", "LC",
    "LI", "LK", "LR", "LS", "LT", "LU", "LV", "LY", "MA", "MC", "MD", "ME", "MF", "MG", "MH", "MK",
    "ML", "MM", "MN", "MO", "MP", "MQ", "MR", "MS", "MT", "MU", "MV", "MW", "MX", "MY", "MZ", "NA",
    "NC", "NE", "NF", "NG", "NI", "NL", "NO", "NP", "NR", "NU", "NZ", "OM", "PA", "PE", "PF", "PG",
    "PH", "PK", "PL", "PM", "PN", "PR", "PS", "PT", "PW", "PY", "QA", "RE", "RO", "RS", "RU", "RW",
    "SA", "SB", "SC", "SD", "SE", "SG", "SH", "SI", "SJ", "SK", "SL", "SM", "SN", "SO", "SR", "SS",
    "ST", "SV", "SX", "SY", "SZ", "TC", "TD", "TF", "TG", "TH", "TJ", "TK", "TL", "TM", "TN", "TO",
    "TR", "TT", "TV", "TW", "TZ", "UA", "UG", "UM", "US", "UY", "UZ", "VA", "VC", "VE", "VG", "VI",
    "VN", "VU", "WF", "WS", "YE", "YT", "ZA", "ZM", "ZW",
})

_ASCII_DIGITS = frozenset("0123456789")
_ASCII_ALNUM = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")


def _overlaps_any(ranges: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < e and s < end for s, e in ranges)


def _mod97(num_str: str) -> int:
    remainder = 0
    for ch in num_str:
        remainder = (remainder * 10 + int(ch)) % 97
    return remainder


def is_valid_iban(candidate: str) -> bool:
    """Страна из ISO 3166 и контрольная сумма mod-97. Ожидает A-Z и 0-9 без пробелов."""
    if candidate[:2] not in ISO_COUNTRY_CODES:
        return False
    rearranged = candidate[4:] + candidate[:4]
    parts: list[str] = []
    for ch in rearranged:
        if "A" <= ch <= "Z":
            parts.append(str(ord(ch) - 55))
        elif ch in _ASCII_DIGITS:
            parts.append(ch)
        else:
            return False  # в JS Number() дал бы NaN, и проверка тоже не прошла бы
    return _mod97("".join(parts)) == 1


def luhn_check(digits: str) -> bool:
    total = 0
    alt = False
    for ch in reversed(digits):
        n = int(ch)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


_EMAIL_LOCAL_CHARS = _ASCII_ALNUM | frozenset("._%+'-")
_EMAIL_DOMAIN_CHARS = _ASCII_ALNUM | frozenset(".-")


def detect_email(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    n = len(text)
    # Сканируем вокруг @, а не перебираем неограниченный префикс с каждого символа:
    # каждый прогон просматривается не больше двух раз, даже при множестве @.
    at = text.find("@")
    while at >= 0:
        start = at
        while start > 0 and text[start - 1] in _EMAIL_LOCAL_CHARS:
            start -= 1
        if start != at:
            end = at + 1
            while end < n and text[end] in _EMAIL_DOMAIN_CHARS:
                end += 1
            while text[end - 1] == ".":
                end -= 1
            domain = text[at + 1:end]
            dot = domain.rfind(".")
            tld = domain[dot + 1:]
            if dot > 0 and len(tld) >= 2 and tld.isascii() and tld.isalpha():
                matches.append(RawMatch("EMAIL", text[start:end], start, end))
        at = text.find("@", at + 1)
    return matches


# У распространенных юрисдикций длина фиксирована. Для прочих остается общая
# проверка по контрольной сумме, без обрезки внутри токена.
IBAN_LENGTHS: dict[str, int] = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22, "BH": 22,
    "BR": 29, "BY": 28, "CH": 21, "CR": 22, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "DO": 28,
    "EE": 20, "EG": 29, "ES": 24, "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23,
    "GL": 18, "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IS": 26, "IT": 27,
    "JO": 30, "KZ": 20, "KW": 30, "LB": 28, "LC": 32, "LI": 21, "LT": 20, "LU": 20, "LV": 21,
    "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15,
    "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "SA": 24, "SC": 31,
    "SE": 24, "SI": 19, "SK": 24, "SM": 27, "ST": 25, "SV": 28, "TL": 23, "TN": 24, "TR": 26,
    "UA": 29, "VA": 22, "VG": 24, "XK": 20,
}

_IBAN_START_RE = JsRegex(r"(?<![\p{L}\p{N}])[A-Z]{2}\d{2}", "giu")
_IBAN_GAP = " \t  "
_LETTER_OR_NUMBER_RE = JsRegex(r"[\p{L}\p{N}]", "u")
_GAP_THEN_DIGIT_RE = JsRegex(r"^[ \t  ]+\d")


def _detect_iban(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    n = len(text)
    pos = 0
    while True:
        m = _IBAN_START_RE.search(text, pos)
        if m is None:
            break
        pos = m.end()
        head = m.group()
        country = head[:2].upper()
        if country not in ISO_COUNTRY_CODES:
            continue
        length = IBAN_LENGTHS.get(country)
        normalized = head.upper()
        accepted_end = -1
        cursor = m.end()
        while cursor < n and len(normalized) < (length or 34):
            ch = text[cursor]
            if ch in _IBAN_GAP:
                cursor += 1
                continue
            if ch not in _ASCII_ALNUM:
                break
            normalized += ch.upper()
            cursor += 1
            if len(normalized) < 15 or (length and len(normalized) != length):
                continue
            # Не принимаем префикс с верной суммой у более длинного номера.
            if cursor < n and _LETTER_OR_NUMBER_RE.test(text[cursor]):
                continue
            if _GAP_THEN_DIGIT_RE.test(text[cursor:cursor + 16]):
                continue
            if is_valid_iban(normalized):
                accepted_end = cursor
        if accepted_end >= 0:
            matches.append(RawMatch("IBAN", text[m.start():accepted_end], m.start(), accepted_end))
            pos = accepted_end
    return matches


_CARD_RE = JsRegex(r"\b(?:\d[ -]?){13,19}\b")


def _detect_card(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    for m in _CARD_RE.finditer(text):
        raw = m.group()
        digits = raw.replace(" ", "").replace("-", "")
        if len(digits) < 13 or len(digits) > 19:
            continue
        if not luhn_check(digits):
            continue
        # ID-номер ЮАР тоже 13 цифр с Luhn; его забирает detect_presidio_ids как ID_DOC.
        if is_za_id_number(digits):
            continue
        trimmed = raw.rstrip(" -")
        start = m.start()
        matches.append(RawMatch("CARD", trimmed, start, start + len(trimmed)))
    return matches


_SWIFT_RE = JsRegex(r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b")
_SWIFT_CONTEXT_RE = JsRegex(r"(?:SWIFT|BIC)\b[^A-Za-z0-9]{0,10}$", "i")
_ALL_UPPER_RE = JsRegex(r"^[A-Z]+$")


def _detect_swift(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    for m in _SWIFT_RE.finditer(text):
        code = m.group()
        if code[4:6] not in ISO_COUNTRY_CODES:
            continue
        # Чисто буквенный кандидат, похожий на английское слово, почти всегда
        # заголовок, а не BIC («APPOINTMENT», «TERMINATION»: символы 5–6 «IN» —
        # валидный код страны). У настоящих BIC есть цифры либо рядом метка
        # SWIFT/BIC, поэтому для любого буквенного кода (8 и 11 символов) требуем метку.
        if _ALL_UPPER_RE.test(code):
            # В TS проверяется весь префикс text.slice(0, index); совпадение
            # занимает не больше 15 символов у его конца, поэтому окна достаточно.
            if not _SWIFT_CONTEXT_RE.test(text[max(0, m.start() - 16):m.start()]):
                continue
        matches.append(RawMatch("SWIFT", code, m.start(), m.end()))
    return matches


# дд.мм.гггг / дд/мм/гг / дд-мм-гггг (одинаковый разделитель), месяц 1-2 цифры
_DATE_DMY_RE = JsRegex(r"^\d{1,2}([./-])\d{1,2}\1\d{2,4}$")
# гггг-мм-дд / гггг.мм.дд
_DATE_YMD_RE = JsRegex(r"^\d{4}([./-])\d{1,2}\1\d{1,2}$")
_CEP_RE = JsRegex(r"^\d{2}\.\d{3}-\d{3}$")
_SNILS_SHAPE_RE = JsRegex(r"^\d{3}-\d{3}-\d{3}[\s-]\d{2}$")


def _is_date_like(s: str) -> bool:
    t = js_trim(s)
    return _DATE_DMY_RE.test(t) or _DATE_YMD_RE.test(t)


def _is_cep_like(s: str) -> bool:
    return _CEP_RE.test(js_trim(s))


# СНИЛС «123-456-789 01» по форме неотличим от телефона, а detect_snils отдает
# ровно тот же спан, и при равных границах исход решал бы порядок в списке.
# Гасим здесь явно, как даты и CEP.
def _is_snils_like(s: str) -> bool:
    return _SNILS_SHAPE_RE.test(js_trim(s))


PHONE_LABEL_SRC = "telephone|phone|mobile|cell|fax|телефон|телефону|мобільний|мобильный|моб|факс|тел|tel"
PHONE_LABEL = rf"(?<![\p{{L}}\p{{N}}])(?:{PHONE_LABEL_SRC})(?![\p{{L}}\p{{N}}])"
_PHONE_LABEL_BEFORE_RE = JsRegex(rf"{PHONE_LABEL}[\s.:№#]*$", "iu")


# Метка телефона непосредственно перед кандидатом (в пределах ~25 символов слева):
# защищает группировку по 3 цифры («Tel: 123 456 789») и разрешает номер без
# разделителей после контактной метки («Tel: 0612345678»).
def _has_phone_label_before(text: str, index: int) -> bool:
    return _PHONE_LABEL_BEFORE_RE.test(text[max(0, index - 25):index])


_PARENS_RE = JsRegex(r"[()]")
_CENTS_RE = JsRegex(r"\.\d{2}$")
_MONEY_GROUP_SPLIT_RE = JsRegex(r"[\s.-]+")
_CURRENCY_BEFORE_RE = JsRegex(r"(?:[$€£]|USD|EUR|GBP|UAH|RUB|HUF)\s?$", "i")


# Денежная сумма без метки телефона: копейки/центы на конце, ИЛИ группировка по
# тысячам (все группы после первой ровно по 3 цифры), ИЛИ денежный маркер перед ней.
def _looks_like_money_sum(raw: str, text: str, index: int) -> bool:
    if "+" in raw or _PARENS_RE.test(raw):
        return False
    if _has_phone_label_before(text, index):
        return False
    if _CENTS_RE.test(raw):
        return True
    groups = [g for g in _MONEY_GROUP_SPLIT_RE.split(raw) if g]
    if len(groups) >= 3 and all(len(g) == 3 for g in groups[1:]):
        return True
    if _CURRENCY_BEFORE_RE.test(text[max(0, index - 6):index]):
        return True
    return False


# Проход 1: номера с разделителями и необязательным кодом страны. Первая группа
# обычно 2-5 цифр, но после кода страны (+36 …) разрешена 1-значная зона
# (Будапешт «1» в «+36 1 234 5678»); без кода страны 1 цифра не начало номера.
_PHONE_SEP_RE = JsRegex(
    r"(?:\+\d{1,3}[\s.-]?(?:\(\d{1,4}\)[\s.-]?)?\d{1,5}|(?:\(\d{1,4}\)[\s.-]?)?\d{2,5})(?:[\s.-]\d{2,5}){1,4}"
)
# Проход 2: метка + цифры без разделителей («Tel: 0612345678», «Phone #: 12025550123»).
_PHONE_LABELLED_RE = JsRegex(rf"{PHONE_LABEL}[\s.:№#]*(\+?\d{{7,15}})(?![\p{{L}}\p{{N}}])", "giu")
# Явный международный + достаточен и без метки.
_PHONE_INTERNATIONAL_RE = JsRegex(r"(?<![\p{L}\p{N}+])\+[1-9]\d{6,14}(?![\p{L}\p{N}])", "gu")


def _count_digits(s: str) -> int:
    return sum(1 for ch in s if ch in _ASCII_DIGITS)


def _detect_phone(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    sep_matches: list[RawMatch] = []
    for m in _PHONE_SEP_RE.finditer(text):
        raw = m.group()
        if _is_date_like(raw) or _is_cep_like(raw) or _is_snils_like(raw):
            continue
        digits = _count_digits(raw)
        if digits < 7 or digits > 15:
            continue
        if _looks_like_money_sum(raw, text, m.start()):
            continue
        sep_matches.append(RawMatch("PHONE", raw, m.start(), m.end()))
    matches.extend(sep_matches)

    # Без метки голое число телефоном не считается.
    for m in _PHONE_LABELLED_RE.finditer(text):
        captured = m.group(1)
        group_start = m.start() + len(m.group()) - len(captured)
        group_end = group_start + len(captured)
        digits = _count_digits(captured)
        if digits < 7 or digits > 15:
            continue
        # Пересечение с проходом 1: тот приоритетнее.
        if any(group_start < s.end and s.start < group_end for s in sep_matches):
            continue
        matches.append(RawMatch("PHONE", captured, group_start, group_end))

    for m in _PHONE_INTERNATIONAL_RE.finditer(text):
        start, end = m.start(), m.end()
        if not any(start < p.end and p.start < end for p in matches):
            matches.append(RawMatch("PHONE", m.group(), start, end))
    return matches


_ACCOUNT_LABEL_RE = JsRegex(r"\b(?:account|acct|a\/c|iban)\s*(?:no\.?|number|#)?\s*[:.-]?\s*", "gi")
_ACCOUNT_TOKEN_RE = JsRegex(r"[A-Z0-9]+", "giy")
_ACCOUNT_GAP = " \t  -"


def _detect_account(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    n = len(text)
    pos = 0
    while True:
        m = _ACCOUNT_LABEL_RE.search(text, pos)
        if m is None:
            break
        pos = m.end()
        group_start = m.end()
        first = _ACCOUNT_TOKEN_RE.match(text, group_start)
        if first is None or _count_digits(first.group()) == 0:
            continue
        end = first.end()
        count = len(first.group())
        while True:
            nxt = end
            while nxt < n and text[nxt] in _ACCOUNT_GAP:
                nxt += 1
            if nxt == end:
                break
            token = _ACCOUNT_TOKEN_RE.match(text, nxt)
            if token is None or _count_digits(token.group()) == 0:
                break
            end = token.end()
            count += len(token.group())
        # Длина — значимые символы без пробелов; превышение отбрасывает кандидата
        # целиком, а не маскирует только правдоподобный префикс.
        if count < 7 or count > 34:
            continue
        matches.append(RawMatch("ACCOUNT", text[group_start:end], group_start, end))
        pos = end
    return matches


@dataclass(frozen=True)
class _AddressLabelSpec:
    label_re: JsRegex  # метка адреса; значение — все, что идет следом
    double_label_re: JsRegex  # повторная метка в начале значения («Address: Address: …»)
    field_re: JsRegex  # ключевое слово следующего поля после запятой — по нему обрезаем
    contact_field_re: JsRegex  # помеченное контактное поле, обрезается и без запятой
    skip_before: Optional[JsRegex] = None  # не-адресная метка по тексту перед ней («email address:»)


# Граница ячейки таблицы: docx-таблицы склеивают ряд через <br> и |, и без
# обрезки адрес проглатывал бы весь ряд (структурный ADDRESS перебивает PHONE и др.).
_ADDRESS_CELL_BOUNDARY_RE = JsRegex(r"<br\s*\/?>|\||(?:\\?_){3,}", "i")
_DIGIT_OR_COMMA_RE = JsRegex(r"[\d,]")


# Общий разбор «метка -> значение до конца строки -> обрезка по следующему полю»
# для английских и русско-украинских меток.
def _collect_labelled_addresses(text: str, spec: _AddressLabelSpec) -> list[RawMatch]:
    matches: list[RawMatch] = []
    for m in spec.label_re.finditer(text):
        if spec.skip_before is not None and spec.skip_before.test(text[max(0, m.start() - 14):m.start()]):
            continue
        value_start = m.end()
        line_end = text.find("\n", value_start)
        if line_end == -1:
            line_end = len(text)
        raw = text[value_start:line_end]

        # Двойная метка: внешнюю пропускаем, внутреннюю поймает следующая итерация,
        # и значение начнется с самого адреса, а не со слова «Address».
        if spec.double_label_re.test(raw):
            continue

        cuts: list[int] = []
        for cut_re in (spec.field_re, spec.contact_field_re, _ADDRESS_CELL_BOUNDARY_RE):
            cm = cut_re.search(raw)
            if cm is not None:
                cuts.append(cm.start())
        if cuts:
            raw = raw[:min(cuts)]

        # Хвостовая запятая/точка с запятой — разделитель реквизитов, не часть адреса.
        value = js_trim(raw).rstrip(",;")
        lead_ws = len(raw) - len(js_trim_start(raw))
        # Похоже на адрес: минимальная длина, есть цифра или запятая, не e-mail.
        if len(value) < 5 or "@" in value or not _DIGIT_OR_COMMA_RE.test(value):
            continue

        start = value_start + lead_ws
        matches.append(RawMatch("ADDRESS", value, start, start + len(value)))
    return matches


# Адрес по метке «business address:», «registered office address:», «Address:».
# Двоеточие обязательно: отсекает прозу «registered office address of the other
# Party». GLiNER не тегирует адреса, начинающиеся со страны, а структурные
# совпадения перебивают NER в resolveOverlaps.
_EN_ADDRESS_SPEC = _AddressLabelSpec(
    label_re=JsRegex(
        r"\b(?:registered\s+office\s+|business\s+|legal\s+|postal\s+|residential\s+|correspondence\s+|registered\s+)?address\s*:",
        "gi",
    ),
    # Не-адресные метки: «email address:», «IP address:», «web address:».
    skip_before=JsRegex(r"(?:e-?mail|ip|web|url)\s$", "i"),
    double_label_re=JsRegex(
        r"^\s*(?:registered\s+office\s+|business\s+|legal\s+|postal\s+|residential\s+|correspondence\s+)?address\s*:",
        "i",
    ),
    field_re=JsRegex(
        r",\s*(?:company\s+(?:registration|reg)\b|registration\s+(?:no|number)\b|reg\.?\s*(?:no|number)\b|CRN\b|VAT\b"
        r"|represented\s+by\b|acting\b|director\b|tel\b|phone\b|fax\b|e-?mail\b|hereinafter\b)",
        "i",
    ),
    # Контактное поле (Phone #:, Tel:, Fax:, Email:) обрезаем и без запятой: адрес
    # и контакты могут оказаться в одном абзаце.
    contact_field_re=JsRegex(r"\s(?:telephone|tel|phone|fax|e-?mail|mobile|cell)\b[\s.]*[:#]", "i"),
)


def _detect_address(text: str) -> list[RawMatch]:
    return _collect_labelled_addresses(text, _EN_ADDRESS_SPEC)


# Русские и украинские метки адреса. Двоеточие обязательно у именных меток (как
# в английской ветке); исключение — фразовые «по адресу» / «за адресою», после
# которых адрес идет всегда. Границы кириллических меток — lookaround по \p{L},
# потому что \b в JS только ASCII.
RU_ADDRESS_NOUN_SRC = (
    r"(?:(?:юридическ|фактическ|почтов|домашн|юридичн|фактичн|поштов)\p{L}*\s+)?"
    r"адрес(?:а|у)?(?:\s+(?:регистрации|реєстрації|проживания|проживання|"
    r"места\s+жительства|місця\s+проживання|доставки|для\s+корреспонденции))?\s*:"
)

RU_ADDRESS_LABEL_SRC = (
    "(?:"
    r"(?:по\s+адресу|за\s+адресою)\s*:?"
    f"|{RU_ADDRESS_NOUN_SRC}"
    r"|(?:мест[оа]\s+(?:нахождения|жительства)|местонахождение|місцезнаходження)\s*:"
    ")"
)

_RU_ADDRESS_SPEC = _AddressLabelSpec(
    label_re=JsRegex(rf"(?<!\p{{L}}){RU_ADDRESS_LABEL_SRC}", "giu"),
    # «электронный адрес:», «эл. адрес:», «интернет-адрес:» — не почтовый адрес.
    # «адрес электронной почты:» отсекается самой меткой.
    skip_before=JsRegex(r"(?:электронн|эл\.?|интернет-?|сетев|поштов[а-яіїєґ]*\s+скринь)\p{L}*\s*$", "iu"),
    double_label_re=JsRegex(rf"^\s*{RU_ADDRESS_LABEL_SRC}", "iu"),
    # Идентификатор часто вводится словом «код» («, код за ЄДРПОУ 12345678»): без
    # этого префикса адрес проглатывал номер вместе с его типом.
    field_re=JsRegex(
        r",\s*(?:код\s+(?:за\s+)?)?"
        r"(?:тел(?:ефон)?|факс|e-?mail|эл\.?\s*почт\p{L}*|ИНН|КПП|ОГРНИП|ОГРН|БИК|СНИЛС|"
        r"ЄДРПОУ|ІПН|РНОКПП|р\/с|к\/с|паспорт|именуем\p{L}*|іменован\p{L}*|в\s+лице|"
        r"в\s+особі|действующ\p{L}*|діюч\p{L}*)(?!\p{L})",
        "iu",
    ),
    contact_field_re=JsRegex(r"\s(?:тел(?:ефон)?|факс|e-?mail|эл\.?\s*почт\p{L}*)\s*[.:#]", "iu"),
)


def _detect_russian_address(text: str) -> list[RawMatch]:
    return _collect_labelled_addresses(text, _RU_ADDRESS_SPEC)


# C2: адреса США из блоков уведомлений без метки «Address:». Якорь — хвост
# «, <штат> <ZIP5>» (код USPS или полное название) и номер дома в начале.
US_STATE_NAMES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois",
    "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland",
    "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana",
    "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
    "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah",
    "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming",
    "District of Columbia",
)
US_STATE_ABBR = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
)
# Полные названия первыми, чтобы длинное название побеждало 2-буквенный префикс.
US_STATE_ALT = "|".join((*US_STATE_NAMES, *US_STATE_ABBR))
_US_ADDRESS_RE = JsRegex(
    r"\b\d+\s+[^,\n]+(?:,\s*[^,\n]+){0,7}?,\s*(?:" + US_STATE_ALT + r")\s+\d{5}(?:-\d{4})?\b"
)

# Многострочный вариант: извлечение из PDF/docx разносит адрес по строкам
# («11 Hurley Street\n\n----------- Cambridge, MA 02141»). Строка улицы и строка
# «City, <штат> <ZIP>» склеиваются через 1–3 перевода строки и табличный шум;
# в строке улицы обязателен тип улицы, чтобы не глотать «500 units\n…».
US_STREET_SUFFIX = (
    r"(?:street|st|avenue|ave|road|rd|circle|cir|boulevard|blvd|lane|ln|drive|dr|court|ct|place|pl|parkway|pkwy"
    r"|highway|hwy|square|sq|expressway|row|plaza|terrace|ter|trail|building|bldg|suite|ste|way)"
)
_US_ADDRESS_MULTILINE_RE = JsRegex(
    r"\b\d+[ \t]+[^\n]{0,50}?\b" + US_STREET_SUFFIX
    + r"\b[^\n]{0,30}?(?:[ \t\-|_*]*\n){1,3}[ \t\-|_*]*[A-Za-z][^,\n]{1,40},[ \t]*(?:"
    + US_STATE_ALT + r")[ \t]+\d{5}(?:-\d{4})?\b",
    "gi",
)


def _detect_us_address(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    for pattern in (_US_ADDRESS_RE, _US_ADDRESS_MULTILINE_RE):
        for m in pattern.finditer(text):
            value = js_trim(m.group())
            matches.append(RawMatch("ADDRESS", value, m.start(), m.start() + len(value)))
    return matches


_REG_CONTEXT_RES = (
    JsRegex(
        r"\b(?:company\s+(?:registration\s+)?(?:no|number)|reg(?:istration)?\.?\s*no|CRN|registered\s+number)\b"
        r"[\s:#.]*([A-Z0-9][A-Z0-9./-]{4,18}[A-Z0-9])",
        "gi",
    ),
    JsRegex(r"\bVAT(?:\s*(?:reg(?:istration)?|no|number|id))?\b[\s:#.]*([A-Z]{0,2}[\d][\d ]{6,14}\d)", "gi"),
)
_EIN_RE = JsRegex(r"\b\d{2}-\d{7}\b")
# CNPJ (бразильский рег. номер юрлица) ##.###.###/####-##. С июля 2026 первые 12
# позиций могут быть буквами или цифрами, последние 2 — всегда контрольные цифры.
# Формат очень специфичный, поэтому контрольную сумму не проверяем: так номера с
# опечатками и ошибками OCR не теряются.
_CNPJ_RE = JsRegex(r"\b[0-9A-Z]{2}\.[0-9A-Z]{3}\.[0-9A-Z]{3}\/[0-9A-Z]{4}-\d{2}\b")


def _labelled_group(m: Match, group: int = 1) -> tuple[str, int]:
    """Значение группы и ее начало, как m.index + m[0].indexOf(group) в TS."""
    value = m.group(group)
    return value, m.start() + m.group().find(value)


def _detect_reg_number(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    for pattern in _REG_CONTEXT_RES:
        for m in pattern.finditer(text):
            group, start = _labelled_group(m)
            matches.append(RawMatch("REG_NUMBER", group, start, start + len(group)))
    for pattern in (_EIN_RE, _CNPJ_RE):
        for m in pattern.finditer(text):
            matches.append(RawMatch("REG_NUMBER", m.group(), m.start(), m.end()))
    return matches


# Российские структурные идентификаторы: ИНН/ОГРН(ИП)/КПП. \b в JS только ASCII,
# поэтому границы кириллических меток задаем lookaround по буквам.
NOT_LETTER_BEFORE = "(?<![А-Яа-яЁёA-Za-z])"
NOT_LETTER_AFTER = "(?![А-Яа-яЁёA-Za-z])"

# Совмещенная форма «ИНН / КПП 7736321479 / 773601001» (иногда с табуляцией).
_RU_INN_KPP_RE = JsRegex(
    NOT_LETTER_BEFORE + r"ИНН\s*\/\s*КПП" + NOT_LETTER_AFTER + r"[\s:№.]*(\d{12}|\d{10})\s*\/\s*(\d{9})(?!\d)",
    "gid",
)
# ИНН: 10 цифр (юрлицо) или 12 (физлицо/ИП); 12 раньше 10, иначе обрежется.
_RU_INN_RE = JsRegex(NOT_LETTER_BEFORE + "ИНН" + NOT_LETTER_AFTER + r"[\s:№.\/-]*(\d{12}|\d{10})(?!\d)", "gi")
# ОГРНИП (15) раньше ОГРН (13, изредка 15), иначе метка «ОГРН» съест префикс «ОГРНИП».
_RU_OGRNIP_RE = JsRegex(NOT_LETTER_BEFORE + "ОГРНИП" + NOT_LETTER_AFTER + r"[\s:№.\/-]*(\d{15})(?!\d)", "gi")
_RU_OGRN_RE = JsRegex(NOT_LETTER_BEFORE + "ОГРН" + NOT_LETTER_AFTER + r"[\s:№.\/-]*(\d{15}|\d{13})(?!\d)", "gi")
# КПП: 9 цифр.
_RU_KPP_RE = JsRegex(NOT_LETTER_BEFORE + "КПП" + NOT_LETTER_AFTER + r"[\s:№.\/-]*(\d{9})(?!\d)", "gi")


def _detect_russian_ids(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    # Диапазоны совмещенной формы: простые шаблоны их пропускают, чтобы не задвоить КПП.
    consumed: list[tuple[int, int]] = []

    for m in _RU_INN_KPP_RE.finditer(text):
        inn_start, inn_end = m.span(1)
        kpp_start, kpp_end = m.span(2)
        matches.append(RawMatch("REG_NUMBER", m.group(1), inn_start, inn_end))
        matches.append(RawMatch("REG_NUMBER", m.group(2), kpp_start, kpp_end))
        consumed.extend(((inn_start, inn_end), (kpp_start, kpp_end)))

    for pattern, consumes in ((_RU_INN_RE, False), (_RU_OGRNIP_RE, True), (_RU_OGRN_RE, False), (_RU_KPP_RE, False)):
        for m in pattern.finditer(text):
            group, start = _labelled_group(m)
            end = start + len(group)
            if _overlaps_any(consumed, start, end):
                continue
            matches.append(RawMatch("REG_NUMBER", group, start, end))
            if consumes:
                consumed.append((start, end))
    return matches


# Украинские идентификаторы: ЄДРПОУ/ІПН/РНОКПП. В метках есть і/ї/є/ґ вне
# диапазона А-Яа-яЁё, поэтому границы — по \p{L}.
UA_NOT_LETTER_BEFORE = r"(?<!\p{L})"
UA_NOT_LETTER_AFTER = r"(?!\p{L})"

_UA_ID_RES = (
    # ЄДРПОУ: 8 цифр; префикс «код»/«код за» необязателен.
    JsRegex(UA_NOT_LETTER_BEFORE + "ЄДРПОУ" + UA_NOT_LETTER_AFTER + r"[\s:№#.-]*(\d{8})(?!\d)", "giu"),
    # ІПН: 10 цифр (физлицо) или 12 (ІПН/ПДВ юрлица); 12 раньше 10.
    JsRegex(UA_NOT_LETTER_BEFORE + "ІПН" + UA_NOT_LETTER_AFTER + r"[\s:№#.-]*(\d{12}|\d{10})(?!\d)", "giu"),
    # РНОКПП — 10 цифр, аналог ІПН физлица.
    JsRegex(UA_NOT_LETTER_BEFORE + "РНОКПП" + UA_NOT_LETTER_AFTER + r"[\s:№#.-]*(\d{10})(?!\d)", "giu"),
)


def _detect_ukrainian_ids(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    for pattern in _UA_ID_RES:
        for m in pattern.finditer(text):
            group, start = _labelled_group(m)
            matches.append(RawMatch("REG_NUMBER", group, start, start + len(group)))
    return matches


# Российские банковские счета (р/с, к/с, расчетный/корр. счет) — ровно 20 цифр.
_RU_ACCOUNT_RE = JsRegex(
    r"(?<![А-Яа-яЁёA-Za-z])(?:р\/с|к\/с|р\/сч|расч[ёе]тн[а-яё]*\s+сч[ёе]т[а-яё]*|корр[а-яё.]*\s*сч[ёе]т[а-яё]*)"
    r"(?![А-Яа-яЁёA-Za-z])[\s:№./-]*(\d{20})(?!\d)",
    "gi",
)


def _detect_russian_accounts(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    for m in _RU_ACCOUNT_RE.finditer(text):
        group, start = _labelled_group(m)
        matches.append(RawMatch("ACCOUNT", group, start, start + len(group)))
    return matches


# СНИЛС — 11 цифр, канонично «123-456-789 01». По метке принимаем и слитную
# запись, без метки — только дефисный формат. Контрольную сумму не проверяем
# (как у CNPJ): ложное срабатывание дешевле пропуска. Тип REG_NUMBER — в одном
# ряду с ИНН физлица, ІПН и РНОКПП.
_SNILS_LABELLED_RE = JsRegex(
    NOT_LETTER_BEFORE + "СНИЛС" + NOT_LETTER_AFTER
    + r"[\s:№#.-]*(\d{3}\s?-\s?\d{3}\s?-\s?\d{3}[\s-]?\d{2}|\d{11})(?!\d)",
    "gi",
)
# Слитные 11 цифр без метки не берем: они неотличимы от телефона.
_SNILS_BARE_RE = JsRegex(r"(?<!\d)\d{3}-\d{3}-\d{3}[\s-]\d{2}(?!\d)")


def _detect_snils(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    consumed: list[tuple[int, int]] = []
    for m in _SNILS_LABELLED_RE.finditer(text):
        group, start = _labelled_group(m)
        end = start + len(group)
        matches.append(RawMatch("REG_NUMBER", group, start, end))
        consumed.append((start, end))
    for m in _SNILS_BARE_RE.finditer(text):
        start, end = m.start(), m.end()
        if _overlaps_any(consumed, start, end):
            continue
        matches.append(RawMatch("REG_NUMBER", m.group(), start, end))
    return matches


# Метки документов, удостоверяющих личность. Многословные метки требуют
# определения намеренно: «права», «удостоверение», «свидетельство» сами по себе
# стоят в каждом договоре.
ID_DOC_LABEL_SRC = (
    "(?:"
    r"(?:закордонн\p{L}*\s+|заграничн\p{L}*\s+|загран)?паспорт\p{L}*"
    "|passport"
    r"|водительск\p{L}*\s+(?:удостоверени\p{L}*|прав\p{L}*)"
    r"|посв[іи]дченн\p{L}*\s+вод[іи]я"
    r"|driv(?:er['’]?s?|ing)\s+(?:licen[cs]e|permit)"
    r"|вид\p{L}*\s+на\s+жительство"
    "|ВНЖ|РВП"
    r"|посв[іи]дк\p{L}*\s+на\s+(?:пост[іи]йне\s+|тимчасове\s+)?проживання"
    r"|residence\s+(?:permit|card)"
    r"|удостоверени\p{L}*\s+личности"
    r"|посв[іи]дченн\p{L}*\s+особи"
    r"|ID[\s-]?(?:card|картк\p{L}*|карт\p{L}*)"
    r"|identity\s+card"
    r"|военн\p{L}*\s+билет\p{L}*"
    ")"
)

# Слова, допустимые между меткой и номером. Все прочее обрывает разбор, поэтому
# «паспорт сделки», «паспорт объекта» не становятся персональными данными.
ID_DOC_FILLER_WORDS = frozenset({
    "серия", "серии", "серию", "сер", "серія", "серії", "номер", "номера", "нр",
    "no", "nr", "number", "series", "id", "док", "документ", "бланк", "бланка",
    "гражданина", "гражданки", "громадянина", "громадянки",
    "рф", "рб", "россии", "российской", "федерации", "украины", "україни",
    "казахстана", "республики", "беларусь", "узбекистана", "таджикистана",
})

# Метки соседних полей реквизитов: на них скан останавливается. Они короткие и
# заглавные, то есть неотличимы от буквенной серии («…, ІПН 123456789012»).
ID_DOC_STOP_LABELS = frozenset({
    "инн", "кпп", "огрн", "огрнип", "снилс", "бик", "ипн", "іпн", "єдрпоу",
    "едрпоу", "рнокпп", "iban", "swift", "bic", "vat", "ein", "cnpj", "crn",
    "внж", "рвп", "тел", "тел.", "tel", "fax", "email",
})

# Определения, после которых «паспорт» — документ на вещь, а не на человека.
_ID_DOC_NOT_PERSONAL_BEFORE_RE = JsRegex(
    r"(?:техническ|кадастров|санитарн|ветеринарн|энергетическ|качеств|безопасност|заводск|электронн)\p{L}*\s+$",
    "iu",
)
_ID_DOC_LABEL_RE = JsRegex(rf"(?<!\p{{L}}){ID_DOC_LABEL_SRC}(?!\p{{L}})", "giu")
# Токены: буквенно-цифровой прогон, разделитель, все остальное.
_ID_DOC_TOKEN_RE = JsRegex(r"([\p{L}\d]+)|([:№#.,/\\-]+)|(\S)", "gu")
_HAS_LETTER_RE = JsRegex(r"\p{L}", "u")
_ID_DOC_SERIES_RE = JsRegex(r"^[A-ZА-ЯЁІЇЄҐ]{1,3}$", "u")
_ID_DOC_MIXED_RE = JsRegex(r"^[A-Z0-9А-ЯЁІЇЄҐ]{5,20}$", "u")
_ID_DOC_JOINER_RE = JsRegex(r"^[\s-]*$")
_SUBDIVISION_RE = JsRegex(r"(?<!\p{L})код\s+подразделения(?!\p{L})[\s:№#.-]*(\d{3}\s?-?\s?\d{3})(?!\d)", "giu")


# Документ, удостоверяющий личность. Единственный надежный якорь — метка; значение
# берется сканом токенов после нее. «паспорт 4506 123456» — один спан на обе
# группы; «паспорт серия 4506 номер 123456» — два спана, слово «номер» остается
# в тексте. Дата и орган выдачи не попадают: «выдан» не служебное слово.
def _detect_id_document(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []

    for lm in _ID_DOC_LABEL_RE.finditer(text):
        if _ID_DOC_NOT_PERSONAL_BEFORE_RE.test(text[max(0, lm.start() - 24):lm.start()]):
            continue

        window_start = lm.end()
        window = text[window_start:window_start + 70]

        runs: list[list[int]] = []
        digit_count = 0
        pending_prefix: Optional[tuple[int, int]] = None
        for t in _ID_DOC_TOKEN_RE.finditer(window):
            raw = t.group()
            if t.group(2):
                continue
            if not t.group(1):
                break

            has_digit = _count_digits(raw) > 0
            has_letter = _HAS_LETTER_RE.test(raw)

            # Чистые буквы: короткая заглавная серия перед цифрами («АВ 123456»),
            # служебное слово либо конец разбора.
            if not has_digit:
                if raw.lower() in ID_DOC_STOP_LABELS:
                    break
                # Серия бывает только до номера, иначе метка следующего поля
                # подхватывалась бы как серия второго номера.
                if not runs and _ID_DOC_SERIES_RE.test(raw):
                    pending_prefix = (t.start(), t.start() + len(raw))
                    continue
                if raw.lower() in ID_DOC_FILLER_WORDS:
                    pending_prefix = None
                    continue
                break

            # Смешанный прогон — сам номер («SMITH751125AB9CD», «AB1234567»), только
            # в верхнем регистре: обычные слова с цифрами («приложение1») так не пишут.
            if has_letter and not _ID_DOC_MIXED_RE.test(raw):
                break

            start = pending_prefix[0] if pending_prefix else t.start()
            end = t.start() + len(raw)
            digit_count += _count_digits(raw)
            # Соседние группы через пробел или дефис («4506 123456») — одна запись.
            last = runs[-1] if runs else None
            if last is not None and pending_prefix is None and _ID_DOC_JOINER_RE.test(window[last[1]:t.start()]):
                last[1] = end
            else:
                runs.append([start, end])
            pending_prefix = None
            if len(runs) >= 3:
                break

        # Слишком короткое число после метки («паспорт № 5») — не номер документа.
        if digit_count < 6:
            continue
        for run_start, run_end in runs:
            matches.append(RawMatch(
                "ID_DOC", window[run_start:run_end], window_start + run_start, window_start + run_end,
            ))

    # Код подразделения — часть паспортных данных, метка однозначная.
    for m in _SUBDIVISION_RE.finditer(text):
        group, start = _labelled_group(m)
        matches.append(RawMatch("ID_DOC", group, start, start + len(group)))

    return matches


# Домены верхнего уровня для голых адресов без схемы и www. Список, а не «любые
# 2-24 буквы»: иначе сайтом становятся имена файлов («акт.pdf») и сокращения.
URL_TLDS = (
    "com|net|org|ru|su|ua|by|kz|kg|uz|am|az|ge|md|lv|lt|ee|uk|de|fr|es|it|nl|be|at|ch|pl|cz|sk|hu|ro|bg|rs|si|hr|gr|pt"
    "|se|no|fi|dk|ie|tr|il|ae|sa|cn|jp|kr|in|sg|hk|us|ca|mx|br|au|nz|za|eu|io|co|me|tv|cc|ws|info|biz|pro|name|online"
    "|site|store|shop|tech|app|dev|cloud|group|agency|legal|law|digital|media|team|world|space|website|link|xyz"
)
URL_DOMAIN_SRC = rf"[a-z0-9](?:[a-z0-9-]{{0,61}}[a-z0-9])?(?:\.[a-z0-9-]{{1,63}})*\.(?:{URL_TLDS})"
URL_PATH_SRC = r"""(?:/[^\s<>()\[\]"'«»]*)?"""

_URL_SCHEME_RE = JsRegex(r"""(?<![\w@])https?:\/\/[^\s<>()[\]"'«»]+""", "gi")
_URL_WWW_RE = JsRegex(rf"(?<![\w@./-])www\.[a-z0-9-]+(?:\.[a-z0-9-]+)+{URL_PATH_SRC}", "gi")
# По метке регистр значения неважен: метка сама достаточный сигнал.
_URL_LABELLED_RE = JsRegex(
    r"(?<!\p{L})(?:веб-?сайт|сайт|website|web-?site|url|домен)(?!\p{L})[\s:№#-]*"
    rf"((?:https?://)?{URL_DOMAIN_SRC}{URL_PATH_SRC})(?![\w-])",
    "giu",
)
_URL_BARE_RE = JsRegex(rf"(?<![\w@./-]){URL_DOMAIN_SRC}{URL_PATH_SRC}(?![\w-])", "gi")
_URL_TRAILING_PUNCT = ".,;:!?)»\"']"


# Сайты, от самого надежного к самому рискованному: схема, префикс www., голый
# домен по списку TLD. Голый домен принимается только строчными буквами: так
# отсекаются «Ltd.Co» и «АО.Ру» (цена — пропуск «Example.com» в начале фразы).
# Почта не задевается: lookbehind отсекает позицию после «@», а EMAIL начинается
# раньше и выигрывает в resolveOverlaps.
def _detect_website(text: str) -> list[RawMatch]:
    matches: list[RawMatch] = []
    consumed: list[tuple[int, int]] = []

    def push(raw: str, start: int) -> None:
        # Хвостовая пунктуация — часть предложения, а не адреса.
        value = raw.rstrip(_URL_TRAILING_PUNCT)
        if not value:
            return
        end = start + len(value)
        if _overlaps_any(consumed, start, end):
            return
        matches.append(RawMatch("URL", value, start, end))
        consumed.append((start, end))

    for m in _URL_SCHEME_RE.finditer(text):
        push(m.group(), m.start())
    for m in _URL_WWW_RE.finditer(text):
        push(m.group(), m.start())
    for m in _URL_LABELLED_RE.finditer(text):
        group, start = _labelled_group(m)
        push(group, start)
    for m in _URL_BARE_RE.finditer(text):
        value = m.group()
        if value != value.lower():
            continue
        push(value, m.start())
    return matches


def detect_structural(text: str) -> list[RawMatch]:
    images = find_image_exclusion_ranges(text)
    text = mask_images_for_detection(text, images)
    ibans = _detect_iban(text)
    matches = [
        *detect_email(text),
        *ibans,
        *_detect_russian_ids(text),
        *_detect_russian_accounts(text),
        *_detect_ukrainian_ids(text),
        *_detect_snils(text),
        *_detect_id_document(text),
        *_detect_website(text),
        # До карт и телефонов: при одинаковом спане побеждает первый по порядку,
        # иначе SSN 3-2-4 и UUID распознавались бы как телефоны.
        *detect_presidio_ids(text),
        *_detect_card(text),
        *_detect_swift(text),
        *_detect_phone(text),
        *_detect_account(text),
        *_detect_reg_number(text),
        *_detect_address(text),
        *_detect_russian_address(text),
        *_detect_us_address(text),
    ]
    return [
        m for m in matches
        if not any(m.start < image.end and image.start < m.end for image in images)
        and (m.type == "IBAN" or not any(m.start < iban.end and iban.start < m.end for iban in ibans))
    ]
