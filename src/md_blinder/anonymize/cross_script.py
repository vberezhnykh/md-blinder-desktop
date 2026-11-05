"""Одно значение латиницей и кириллицей в двуязычном договоре.

В договоре на двух языках сторона записана дважды: «PE HNATIUK TARAS OLEHOVYCH»
и «ФОП ГНАТЮК ТАРАС ОЛЕГОВИЧ», «Lviv region, Zolochivskyi district» и «Львівська
обл., Золочівський район». Модель находит их отдельно, и без связи одна сторона
получала бы две метки, а перевод, который модель пропустила, оставался бы открытым.

Слова сравниваются по транслитерации: кириллица переводится в латиницу (украинская и
русская системы сразу), затем обе стороны приводятся к грубой форме, в которой
совпадают разные системы транслитерации («Yuliia», «Yuliya» и «Yulia»; «Shcherbak» и
«Scherbak»; «Olha» и «Olga»). Слово длиннее четырех букв может отличаться на одну правку
(«Vyshneve» и «Вишнєве», «Odesa» и «Одеси»).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import regex

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie", "ё": "e",
    "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh",
    "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ь": "", "ъ": "", "ы": "y", "э": "e", "ю": "iu",
    "я": "ia", "ў": "u",
}
# Порядок важен: длинные сочетания раньше их частей.
_FOLD_STEPS = (
    ("shch", "s"), ("sch", "s"), ("sh", "s"), ("ch", "c"), ("zh", "z"), ("kh", "h"), ("ts", "c"),
    ("tz", "c"), ("ph", "f"), ("h", "g"), ("w", "v"), ("x", "ks"), ("q", "k"), ("j", "i"), ("y", "i"),
)
_LETTERS_RE = regex.compile(r"[^\p{L}\p{N}]+")
_DOUBLE_RE = regex.compile(r"(.)\1+")
_TOKEN_RE = regex.compile(r"[\p{L}\p{N}][\p{L}\p{N}'’ʼ]*")
_NUMBER_RE = regex.compile(r"\d+")
# Перевод ищется в пределах абзаца: точки в «вул.», «обл.» его не прерывают.
_PARAGRAPH_END_RE = regex.compile(r"\n")

# Слова перед именем, которые есть только в одном языке: «ФОП», «PE», «ИП».
_PERSON_PREFIXES = frozenset({
    "фоп", "fop", "pe", "ie", "ип", "ip", "спд", "spd", "mr", "mrs", "ms", "dr", "пан", "пані", "г-н", "гн",
})
MIN_FUZZY_LENGTH = 5


def _is_cyrillic(ch: str) -> bool:
    return "Ѐ" <= ch <= "ӿ"


def script_of(text: str) -> int:
    """1 — кириллица, -1 — латиница, 0 — букв поровну или нет."""
    cyr = sum(1 for ch in text if _is_cyrillic(ch))
    lat = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if cyr > 2 * lat:
        return 1
    if lat > 2 * cyr:
        return -1
    return 0


def fold_word(word: str) -> str:
    """Грубая латинская форма слова, одинаковая для разных систем транслитерации."""
    text = "".join(_TRANSLIT.get(ch, ch) for ch in word.lower())
    text = _LETTERS_RE.sub("", text)
    for old, new in _FOLD_STEPS:
        text = text.replace(old, new)
    return _DOUBLE_RE.sub(r"\1", text)


def _edits_at_most_one(a: str, b: str) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:] if len(a) < len(b) else a[i + 1:] == b[i + 1:]


def same_word(a: str, b: str) -> bool:
    """Два свернутых слова — одно слово в разных письменностях."""
    if a == b:
        return True
    return min(len(a), len(b)) >= MIN_FUZZY_LENGTH and _edits_at_most_one(a, b)


@dataclass(frozen=True)
class _Words:
    words: tuple[str, ...]  # свернутые слова без цифр
    numbers: tuple[str, ...]  # числа по порядку


def _words(value: str, type_: str) -> _Words:
    words: list[str] = []
    numbers: list[str] = []
    for token in _TOKEN_RE.findall(value):
        numbers.extend(_NUMBER_RE.findall(token))
        letters = "".join(ch for ch in token if ch.isalpha())
        if not letters:
            continue
        if type_ == "PERSON" and letters.lower() in _PERSON_PREFIXES:
            continue
        folded = fold_word(letters)
        if folded:
            words.append(folded)
    return _Words(tuple(words), tuple(numbers))


def _matched(short: Sequence[str], long: Sequence[str]) -> int:
    """Сколько слов short нашлось в long (каждое слово long — один раз)."""
    free = list(long)
    count = 0
    for word in short:
        hit = next((i for i, other in enumerate(free) if same_word(word, other)), None)
        if hit is not None:
            free.pop(hit)
            count += 1
    return count


def same_value(a: str, b: str, type_: str) -> bool:
    """a и b — одно имя или адрес в разных письменностях (одно латиницей, другое кириллицей)."""
    if {script_of(a), script_of(b)} != {-1, 1}:
        return False
    wa, wb = _words(a, type_), _words(b, type_)
    if type_ == "PERSON":
        short, long = sorted((wa.words, wb.words), key=len)
        # Одно слово («Іван», «Петров») не связывается: так зовут многих.
        if len(short) < 2:
            return False
        return _matched(short, long) == len(short)
    if type_ == "ADDRESS":
        short, long = sorted((wa, wb), key=lambda w: (len(w.numbers), len(w.words)))
        if not set(short.numbers) <= set(long.numbers):
            return False
        hits = _matched(short.words, long.words)
        if not short.numbers:
            # Город без улицы: «Львів» и «Lviv, Ukraine» — да, «Одеса» и адрес офиса в Одесі — нет.
            if len(short.words) == 1 and (long.numbers or len(short.words[0]) < 3):
                return False
            return bool(short.words) and hits == len(short.words)
        # Родовые слова («region», «обл.») переводятся, а не транслитерируются; совпадают
        # собственные названия.
        return hits >= 2
    return False


@dataclass(frozen=True)
class _Token:
    start: int
    end: int
    word: str  # свернутое слово, "" — без букв или приставка имени
    numbers: tuple[str, ...]
    script: int
    prefix: bool  # «ФОП», «PE»: в имени не учитывается


class TextIndex:
    """Слова текста, свернутые один раз для поиска переводов всех значений."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens: list[_Token] = []
        for m in _TOKEN_RE.finditer(text):
            token = m.group()
            letters = "".join(ch for ch in token if ch.isalpha())
            self.tokens.append(_Token(
                m.start(), m.end(), fold_word(letters) if letters else "", tuple(_NUMBER_RE.findall(token)),
                script_of(token), letters.lower() in _PERSON_PREFIXES,
            ))


def find_counterpart(index: TextIndex, value: str, type_: str,
                     taken: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Вхождения value в другой письменности, которые модель не нашла: «№ 12-14,
    вул. Садова, Одеса» для «Office No. 12-14, Sadova street, Odesa». Отрезок от первого до последнего совпавшего
    слова; между совпадениями не больше двух чужих слов, отрезок не пересекает конец
    абзаца и уже найденные значения.

    Имя ищется целиком (все слова, не меньше двух), адрес — со всеми числами и хотя бы
    двумя словами."""
    source_script = script_of(value)
    if source_script == 0 or type_ not in ("PERSON", "ADDRESS"):
        return []
    target = _words(value, type_)
    if type_ == "PERSON" and len(target.words) < 2:
        return []
    if type_ == "ADDRESS" and (not target.numbers or len(target.words) < 2):
        return []
    result: list[tuple[int, int]] = []
    i = 0
    while i < len(index.tokens):
        found = _cluster_from(index, i, target, source_script, type_)
        if found is None:
            i += 1
            continue
        start, end, next_index = found
        if not any(start < t_end and t_start < end for t_start, t_end in taken):
            result.append((start, end))
        i = next_index
    return result


def _hits(token: _Token, target: _Words, type_: str) -> tuple[list[str], Optional[str]]:
    numbers = [n for n in token.numbers if n in target.numbers]
    word = None
    if token.word and not (type_ == "PERSON" and token.prefix) and any(same_word(token.word, w) for w in target.words):
        word = token.word
    return numbers, word


def _cluster_from(index: TextIndex, i: int, target: _Words, source_script: int, type_: str):
    tokens = index.tokens
    numbers, word = _hits(tokens[i], target, type_)
    if not numbers and word is None:
        return None
    if word is not None and tokens[i].script != -source_script:
        return None  # слово в той же письменности — это само значение, а не перевод
    found_numbers = list(numbers)
    found_words = [word] if word else []
    last = i
    j = i + 1
    while j < len(tokens) and j - last <= 3:
        between = index.text[tokens[last].end:tokens[j].start]
        if _PARAGRAPH_END_RE.search(between):
            break
        numbers, word = _hits(tokens[j], target, type_)
        if word is not None and tokens[j].script != -source_script:
            break
        if numbers or word is not None:
            found_numbers.extend(numbers)
            if word is not None:
                found_words.append(word)
            last = j
        j += 1
    if not found_words:
        return None
    if type_ == "PERSON":
        ok = _matched(target.words, found_words) == len(target.words)
    else:
        ok = set(target.numbers) <= set(found_numbers) and _matched(found_words, target.words) >= 2
    return (tokens[i].start, tokens[last].end, last + 1) if ok else None
