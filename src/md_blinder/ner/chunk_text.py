"""Нарезка текста на фрагменты для NER (порт src/workers/chunkText.ts).

Длины и смещения — в кодовых точках Python (в TS — UTF-16); на тексте без
символов вне BMP результат совпадает.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import regex

from .gliner_span import WORD_RE

OVERLAP = 200
DEFAULT_MAX_LEN = 1500

# Фрагмент без единой буквы или цифры (строки-разделители таблиц «|---|»,
# подчеркивания под подпись, чистая пунктуация) не может содержать сущность —
# PERSON/COMPANY/ADDRESS требуют букв, REG_NUMBER/ACCOUNT — цифр. Выбрасываем его
# до NER, экономя вызов ONNX. Полнота не страдает.
HAS_ALNUM = regex.compile(r"[\p{L}\p{N}]")

# Стоимость GLiNER — O(слов) спанов, трансформер ~O(слов²), обучалась модель на
# max_len=384. Лимит 1500 символов держит прозу далеко ниже, но фрагмент с густой
# пунктуацией (таблицы: каждый «|»/«.»/«<br>» — отдельное слово) может превысить
# 384 слова → качество вне диапазона обучения и медленный фрагмент. Поэтому
# ограничиваем и число СЛОВ, пересекая под-фрагменты на WORD_OVERLAP слов.
MAX_WORDS = 350
WORD_OVERLAP = 24

_PARAGRAPH_SPLIT = re.compile(r"\n\n+")


@dataclass(frozen=True)
class Chunk:
    text: str
    base: int  # смещение начала фрагмента в исходном тексте


def _cap_words(chunk: Chunk) -> list[Chunk]:
    words = [(m.start(), m.end()) for m in WORD_RE.finditer(chunk.text)]
    if len(words) <= MAX_WORDS:
        return [chunk]
    out: list[Chunk] = []
    i = 0
    while i < len(words):
        end_idx = min(i + MAX_WORDS, len(words))
        char_start = words[i][0]
        char_end = words[end_idx - 1][1]
        out.append(Chunk(chunk.text[char_start:char_end], chunk.base + char_start))
        if end_idx >= len(words):
            break
        i = max(end_idx - WORD_OVERLAP, i + 1)
    return out


def chunk_text(text: str, max_len: int = DEFAULT_MAX_LEN) -> list[Chunk]:
    """Режет на фрагменты ≤max_len по границам абзацев; слишком длинный абзац режется
    жестко с перекрытием OVERLAP. base вычисляется по исходной строке, а не сборкой
    из частей, поэтому смещения не «плывут» из-за разной длины разделителей."""
    paragraphs = _PARAGRAPH_SPLIT.split(text)
    chunks: list[Chunk] = []
    search_from = 0
    current = ""
    current_base = 0

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(Chunk(current, current_base))
            current = ""

    for para in paragraphs:
        idx = text.find(para, search_from)
        para_start = search_from if idx == -1 else idx
        search_from = para_start + len(para)

        if not para:
            continue

        if len(para) > max_len:
            flush()
            offset = 0
            while offset < len(para):
                window_end = min(offset + max_len, len(para))
                end = _find_split_point(para, offset, window_end)
                chunks.append(Chunk(para[offset:end], para_start + offset))
                if end >= len(para):
                    break
                offset = max(end - OVERLAP, offset + 1)
            continue

        if not current:
            current = para
            current_base = para_start
        elif para_start + len(para) - current_base <= max_len:
            current = text[current_base : para_start + len(para)]
        else:
            flush()
            current = para
            current_base = para_start
    flush()
    return [part for c in chunks if HAS_ALNUM.search(c.text) for part in _cap_words(c)]


def _find_split_point(para: str, offset: int, window_end: int) -> int:
    """Последняя граница предложения ('. ' или перевод строки), иначе последний
    пробел — если они дальше середины окна; иначе конец окна."""
    if window_end >= len(para):
        return window_end

    halfway = offset + (window_end - offset) // 2
    window = para[offset:window_end]

    best_sentence = -1
    search_idx = 0
    while True:
        dot = window.find(". ", search_idx)
        nl = window.find("\n", search_idx)
        found = -1
        length = 0
        if dot != -1:
            found = dot
            length = 2
        if nl != -1 and (found == -1 or nl < found):
            found = nl
            length = 1
        if found == -1:
            break
        best_sentence = found + length
        search_idx = found + 1
    if best_sentence != -1 and offset + best_sentence > halfway:
        return offset + best_sentence

    last_space = window.rfind(" ")
    if last_space != -1 and offset + last_space + 1 > halfway:
        return offset + last_space + 1

    return window_end
