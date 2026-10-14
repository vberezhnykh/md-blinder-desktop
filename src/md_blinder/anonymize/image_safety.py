"""Поиск картинок в Markdown/HTML (порт src/lib/anonymize/imageSafety.ts).

Исходный текст не меняется: функции возвращают диапазоны, которые при выдаче
заменяются отметкой, а для детекторов маскируются пробелами.
"""

from __future__ import annotations

import re
from typing import Optional

from .types import Position

IMAGE_REMOVED_MARKER = "[Изображение удалено при обезличивании]"

_HTML_RE = re.compile(r"""<(img|picture|svg)(?![A-Za-z0-9_])(?:[^>"']|"[^"]*"|'[^']*')*>""", re.I)
_DEFINITION_RE = re.compile(r"^ {0,3}\[([^\]\r\n]+)\]:([^\r\n]*)", re.M)
_DESTINATION_LINE_RE = re.compile(r"\r?\n[ \t]*\S[^\r\n]*")
_TITLE_LINE_RE = re.compile(r"""\r?\n[ \t]+["'(][^\r\n]*""")
_DATA_RE = re.compile(r"""data:image/[^\s<>"')\]]+""", re.I)


def _closing_bracket(text: str, start: int, open_ch: str, close_ch: str) -> int:
    depth = 1
    quote = ""
    i = start + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        # Заголовки и адреса в угловых скобках могут содержать непарные скобки.
        if open_ch == "(":
            if quote:
                if ch == quote:
                    quote = ""
                i += 1
                continue
            if ch in ('"', "'", "<"):
                quote = ">" if ch == "<" else ch
                i += 1
                continue
        if ch == open_ch:
            depth += 1
        if ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _normalize_reference(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip()).lower()


def _char(text: str, i: int) -> str:
    return text[i] if 0 <= i < len(text) else ""


def find_image_ranges(text: str) -> list[Position]:
    ranges: list[list[int]] = []
    references: set[str] = set()
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "!" or _char(text, i + 1) != "[":
            i += 1
            continue
        alt_end = _closing_bracket(text, i + 1, "[", "]")
        if alt_end < 0:
            i += 1
            continue
        end = alt_end + 1
        nxt = _char(text, end)
        if nxt == "(":
            dest_end = _closing_bracket(text, end, "(", ")")
            if dest_end >= 0:
                end = dest_end + 1
            else:
                # Поврежденная картинка не должна оставить свои данные в выдаче.
                line_end = text.find("\n", end)
                end = n if line_end < 0 else line_end
        elif nxt == "[":
            ref_end = _closing_bracket(text, end, "[", "]")
            if ref_end >= 0:
                references.add(_normalize_reference(text[end + 1:ref_end] or text[i + 2:alt_end]))
                end = ref_end + 1
        else:
            references.add(_normalize_reference(text[i + 2:alt_end]))
        ranges.append([i, end])
        i = end

    pos = 0
    while True:
        match = _HTML_RE.search(text, pos)
        if not match:
            break
        end = match.end()
        tag = match.group(1).lower()
        if tag != "img" and not re.search(r"/\s*>$", match.group(0)):
            close = re.compile(rf"</{tag}\s*>", re.I).search(text, end)
            end = close.end() if close else n
        ranges.append([match.start(), end])
        pos = max(end, match.start() + 1)

    if references:
        pos = 0
        while True:
            match = _DEFINITION_RE.search(text, pos)
            if not match:
                break
            end = match.end()
            if _normalize_reference(match.group(1)) in references:
                if not match.group(2).strip():
                    dest = _DESTINATION_LINE_RE.match(text, end)
                    if dest:
                        end = dest.end()
                title = _TITLE_LINE_RE.match(text, end)
                if title:
                    end = title.end()
                ranges.append([match.start(), end])
            pos = max(end, match.start() + 1)

    for match in _DATA_RE.finditer(text):
        ranges.append([match.start(), match.end()])

    ranges.sort(key=lambda r: (r[0], -r[1]))
    merged: list[list[int]] = []
    for start, end in ranges:
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [Position(s, e) for s, e in merged]


def find_image_exclusion_ranges(text: str) -> list[Position]:
    """Картинки и уже вставленные отметки об удалении: детекторы их не видят."""
    ranges = list(find_image_ranges(text))
    start = text.find(IMAGE_REMOVED_MARKER)
    while start >= 0:
        ranges.append(Position(start, start + len(IMAGE_REMOVED_MARKER)))
        start = text.find(IMAGE_REMOVED_MARKER, start + len(IMAGE_REMOVED_MARKER))
    return sorted(ranges, key=lambda r: (r.start, -r.end))


def mask_images_for_detection(text: str, ranges: Optional[list[Position]] = None) -> str:
    """Та же длина и те же переводы строк, но без содержимого картинок."""
    if ranges is None:
        ranges = find_image_exclusion_ranges(text)
    if not ranges:
        return text
    parts: list[str] = []
    start = 0
    for r in ranges:
        if r.end <= start:
            continue
        begin = max(start, r.start)
        parts.append(text[start:begin])
        parts.append(re.sub(r"[^\r\n]", " ", text[begin:r.end]))
        start = r.end
    parts.append(text[start:])
    return "".join(parts)
