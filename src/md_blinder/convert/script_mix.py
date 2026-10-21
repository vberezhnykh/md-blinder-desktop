"""Латинские двойники в кириллических словах после OCR (и наоборот).

Indra иногда читает часть букв короткого заглавного слова не той письменностью:
«ИHH», «ОГPH», «KПП», «ФІЗИЧНA», а в английском тексте «Раyment». Глазом такое
слово не отличить, но шаблоны реквизитов (regex_detectors.py) ищут метки ИНН,
КПП, ОГРН кириллицей и такие слова пропускают.

Буква-якорь есть только в одной письменности (И, Ж, Ц; l, n, t). Если в слове есть
якоря одной письменности и нет якорей другой, двойники переводятся в нее. Слово
только из двойников («ОOO», «M.П.») решают соседние части того же слова, затем
преобладающая письменность строки. Целиком латинские слова не трогаются, кроме
нескольких сокращений (ООО, ТОВ, р/с) и коротких слов между кириллическими
(«K Договору», «Bce иные»); «000» перед кавычками там же становится «ООО». Замена буква в букву, длина текста не меняется.
"""

from __future__ import annotations

import regex

_LAT = "ABCEHIKMOPTXYacekiopxy"
_CYR = "АВСЕНІКМОРТХУасекіорху"
TO_CYR = str.maketrans(_LAT, _CYR)
TO_LAT = str.maketrans(_CYR + "ЈЅјѕ", _LAT + "JSjs")
_LAT_TWINS = set(_LAT)
_CYR_TWINS = set(_CYR) | set("ЈЅјѕ")

# Сокращения из одних двойников, которые в кириллической строке почти всегда кириллица.
_CYR_ABBREVIATIONS = {"ООО", "ОАО", "АО", "АТ", "ВАТ", "ТОВ", "р/с", "к/с"}

_TOKEN_RE = regex.compile(r"\S+")
# «000 «Альфа»»: нули вместо О в ООО перед названием в кавычках.
_ZERO_OOO_RE = regex.compile(r"(?<![\p{L}\d])(?<!\d[\s\u00a0])[0OО]*0[0OО]*(?=\s*[«\"“„])")
_RUN_RE = regex.compile(r"\p{L}+")


def _is_cyr(ch: str) -> bool:
    return "Ѐ" <= ch <= "ӿ"


def _is_lat(ch: str) -> bool:
    return ch.isascii() and ch.isalpha()


def _anchors(run: str) -> tuple[int, int]:
    cyr = sum(1 for ch in run if _is_cyr(ch) and ch not in _CYR_TWINS)
    lat = sum(1 for ch in run if _is_lat(ch) and ch not in _LAT_TWINS)
    return cyr, lat


def _line_script(line: str) -> int:
    """+1 — строка в основном кириллическая, -1 — латинская, 0 — не понять. Считаются якоря."""
    cyr, lat = _anchors(line)
    if cyr > lat * 1.5:
        return 1
    if lat > cyr * 1.5:
        return -1
    return 0


def _fix_token(token: str, line_script: int) -> str:
    has_cyr = any(_is_cyr(ch) for ch in token)
    has_lat = any(_is_lat(ch) for ch in token)
    if not has_cyr:
        if line_script > 0:
            core = token.strip(".,;:()«»\"'")
            if core.isascii() and core.translate(TO_CYR) in _CYR_ABBREVIATIONS:
                return token.replace(core, core.translate(TO_CYR), 1)
        return token
    if not has_lat:
        return token
    runs = list(_RUN_RE.finditer(token))
    token_cyr = sum(_anchors(m.group())[0] for m in runs)
    token_lat = sum(_anchors(m.group())[1] for m in runs)
    out = list(token)
    for m in runs:
        run = m.group()
        mixed = any(_is_cyr(ch) for ch in run) and any(_is_lat(ch) for ch in run)
        # Часть из одной письменности («IT» в «IT-компания») остается как есть;
        # исключение — одиночная буква: инициалы «M.П.», «к/c».
        if not mixed and len(run) > 1:
            continue
        cyr, lat = _anchors(run)
        if cyr and not lat:
            target = 1
        elif lat and not cyr:
            target = -1
        elif cyr or lat:
            continue  # якоря обеих письменностей: не угадываем
        elif token_cyr and not token_lat:
            target = 1
        elif token_lat and not token_cyr:
            target = -1
        else:
            target = line_script
        if target:
            out[m.start():m.end()] = run.translate(TO_CYR if target > 0 else TO_LAT)
    return "".join(out)


def _is_cyr_word(token: str) -> bool:
    return any(_is_cyr(ch) for ch in token) and not any(_is_lat(ch) for ch in token)


def _fix_between_cyrillic(line: str) -> str:
    """«K Договору o нижеследующем», «Bce иные»: латинское слово из одних двойников
    между кириллическими словами. Заглавные сокращения длиннее буквы (IT, HR) не трогаются."""
    tokens = list(_TOKEN_RE.finditer(line))
    out = list(line)
    for i, m in enumerate(tokens):
        core = m.group().strip(".,;:()«»\"'")
        if not core or not core.isascii() or not core.isalpha() or not set(core) <= _LAT_TWINS:
            continue
        if len(core) > 1 and core.isupper():
            continue
        neighbours = [tokens[j].group() for j in (i - 1, i + 1) if 0 <= j < len(tokens)]
        if neighbours and all(_is_cyr_word(n) for n in neighbours):
            start = m.start() + m.group().index(core)
            out[start:start + len(core)] = core.translate(TO_CYR)
    return "".join(out)


def fix_mixed_scripts(text: str) -> str:
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if not line:
            continue
        script = _line_script(line)
        line = _TOKEN_RE.sub(lambda m: _fix_token(m.group(), script), line)
        if script > 0:
            line = _ZERO_OOO_RE.sub(lambda m: "ООО" if len(m.group()) == 3 else m.group(), line)
            line = _fix_between_cyrillic(line)
        lines[index] = line
    return "\n".join(lines)
