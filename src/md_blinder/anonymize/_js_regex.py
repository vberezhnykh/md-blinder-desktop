"""Регулярные выражения с семантикой JavaScript для портов детекторов.

Исходники шаблонов переносятся из TS дословно, а этот модуль переводит их в
синтаксис модуля regex с теми же правилами, что в JS:

- \\d, \\w, \\b только ASCII (в JS это так даже с флагом u);
- \\s как в JS: WhiteSpace + LineTerminator. В Python \\s включает еще
  \\x1c-\\x1f и \\x85, но не включает \\ufeff;
- $ без флага m означает только конец строки (в Python $ совпадает и перед
  завершающим переводом строки);
- . не совпадает с \\r, \\u2028, \\u2029;
- регистр. regex.IGNORECASE отождествляет «İ» с «i», «ı» с «I», а «ſ» и «K»
  (знак кельвина) с «s» и «k». JS с флагом i без u не отождествляет ни одну из
  этих пар, с флагами iu — только последние две. Перед поиском такие символы
  заменяются буквой без регистра, поэтому смещения сохраняются, а значения
  групп берутся из исходного текста. Кириллица складывается одинаково.

Смещения считаются в кодовых точках, а не в единицах UTF-16. Границы окон и
квантификаторов с символами вне BMP (эмодзи) поэтому могут отличаться от JS.
"""

from __future__ import annotations

from typing import Any, Iterator, Optional, Union

import regex

JS_WHITESPACE = (
    "\t\n\x0b\x0c\r \xa0\u1680"
    + "".join(chr(c) for c in range(0x2000, 0x200B))
    + "\u2028\u2029\u202f\u205f\u3000\ufeff"
)

_WS = r"\t\n\x0b\x0c\r \xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_WORD = "A-Za-z0-9_"
_DIGIT = "0-9"
# Встроенная ASCII-граница regex быстрее lookaround и совпадает с \b из JS, кроме
# «İ», «ı», «ſ», «K» под IGNORECASE. В шаблонах с i их убирает замена (см. ниже);
# с флагами iu JS считает «ſ» и «K» буквами слова, поэтому там нужна эмуляция.
_BOUNDARY = r"(?a:\b)"
_BOUNDARY_IU = f"(?:(?<=[{_WORD}])(?![{_WORD}])|(?<![{_WORD}])(?=[{_WORD}]))"
_CLASSES = {"d": _DIGIT, "w": _WORD, "s": _WS}

# Буква без регистра (Lo): входит в \p{L}, но не совпадает ни с одной буквой шаблонов.
_NEUTRAL = 0x01BB
_FOLD_I = {0x0130: _NEUTRAL, 0x0131: _NEUTRAL, 0x017F: _NEUTRAL, 0x212A: _NEUTRAL}
_FOLD_IU = {0x0130: _NEUTRAL, 0x0131: _NEUTRAL}


def js_trim(s: str) -> str:
    return s.strip(JS_WHITESPACE)


def js_trim_start(s: str) -> str:
    return s.lstrip(JS_WHITESPACE)


def translate(source: str, unicode_ignore_case: bool = False) -> str:
    """Исходник регулярного выражения JS -> шаблон модуля regex."""
    boundary = _BOUNDARY_IU if unicode_ignore_case else _BOUNDARY
    out: list[str] = []
    in_class = False
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == "\\":
            nxt = source[i + 1]
            if nxt in _CLASSES:
                out.append(_CLASSES[nxt] if in_class else f"[{_CLASSES[nxt]}]")
            elif nxt.lower() in _CLASSES:
                if in_class:
                    raise ValueError(f"\\{nxt} inside a class is not supported: {source}")
                out.append(f"[^{_CLASSES[nxt.lower()]}]")
            elif nxt in "bB":
                if in_class or nxt == "B":
                    raise ValueError(f"\\{nxt} is not supported here: {source}")
                out.append(boundary)
            else:
                out.append(ch + nxt)
            i += 2
            continue
        if in_class:
            if ch == "]":
                in_class = False
                out.append(ch)
            elif ch == "[":
                out.append("\\[")
            else:
                out.append(ch)
        elif ch == "[":
            in_class = True
            out.append(ch)
            if source.startswith("^", i + 1):
                out.append("^")
                i += 1
            if source.startswith("]", i + 1):
                raise ValueError(f"empty JS class is not supported: {source}")
        elif ch == "$":
            out.append(r"\Z")
        elif ch == ".":
            out.append(r"[^\n\r\u2028\u2029]")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


class JsMatch:
    """Совпадение, найденное в тексте с заменой символов; значения из исходника."""

    __slots__ = ("_m", "_text")

    def __init__(self, m: regex.Match[str], text: str) -> None:
        self._m = m
        self._text = text

    def start(self, group: int = 0) -> int:
        return self._m.start(group)

    def end(self, group: int = 0) -> int:
        return self._m.end(group)

    def span(self, group: int = 0) -> tuple[int, int]:
        return self._m.span(group)

    # Как у regex.Match: str | Any, для неучаствовавшей группы None.
    def group(self, group: int = 0) -> str | Any:
        start, end = self._m.span(group)
        return None if start < 0 else self._text[start:end]

    def __getitem__(self, group: int) -> str | Any:
        return self.group(group)


Match = Union["regex.Match[str]", JsMatch]


class JsRegex:
    """Скомпилированное выражение JS. Флаги g/y/d задаются циклом вызывающего кода."""

    def __init__(self, source: str, flags: str = "") -> None:
        self.source = source
        ignore_case = "i" in flags
        pattern = translate(source, ignore_case and "u" in flags)
        self._pattern = regex.compile(pattern, regex.V0 | (regex.IGNORECASE if ignore_case else 0))
        self._fold: Optional[dict[int, int]] = None
        if ignore_case:
            self._fold = _FOLD_IU if "u" in flags else _FOLD_I
        self._cache: tuple[Optional[str], str] = (None, "")

    def _prepare(self, text: str) -> str:
        fold = self._fold
        if fold is None:
            return text
        # Кэш на последний текст: циклы search(text, pos) не сканируют его заново.
        cached = self._cache
        if cached[0] is text:
            return cached[1]
        folded = text.translate(fold) if any(chr(c) in text for c in fold) else text
        self._cache = (text, folded)
        return folded

    def _wrap(self, m: Optional[regex.Match[str]], text: str, folded: str) -> Optional[Match]:
        if m is None or folded is text:
            return m
        return JsMatch(m, text)

    def search(self, text: str, pos: int = 0) -> Optional[Match]:
        folded = self._prepare(text)
        return self._wrap(self._pattern.search(folded, pos), text, folded)

    def match(self, text: str, pos: int = 0) -> Optional[Match]:
        """Как sticky-флаг y: совпадение ровно с позиции pos."""
        folded = self._prepare(text)
        return self._wrap(self._pattern.match(folded, pos), text, folded)

    def finditer(self, text: str) -> Iterator[Match]:
        """Цикл while (m = re.exec(text)) без ручной правки lastIndex."""
        folded = self._prepare(text)
        for m in self._pattern.finditer(folded):
            yield m if folded is text else JsMatch(m, text)

    def test(self, text: str) -> bool:
        return self.search(text) is not None

    def split(self, text: str) -> list[str]:
        assert self._fold is None
        return self._pattern.split(text)
