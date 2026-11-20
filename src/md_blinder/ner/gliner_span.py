"""Подготовка входов GLiNER и декодирование спанов (порт src/workers/glinerSpan.ts).

Адаптировано из span processor/decoder GLiNER.js 0.0.19 (Ingvarstep/GLiNER.js),
лицензия Apache-2.0 (src/workers/gliner.LICENSE). Входы модели и жадное
декодирование сохраняют прежнюю семантику ради совместимости.

Смещения — индексы кодовых точек Python-строки (в TS — единицы UTF-16).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

import numpy as np
import regex

# Тот же словоделитель, что у (пропатченного) gliner: буквенно-цифровые слова с
# внутренними «-»/«_», остальное — по одному непробельному символу.
WORD_RE = regex.compile(r"[\p{L}\p{N}_]+(?:[-_][\p{L}\p{N}_]+)*|\S")
MAX_WIDTH = 12
ENT_TOKEN = "<<ENT>>"
SEP_TOKEN = "<<SEP>>"


class SpanTokenizer(Protocol):
    cls_id: int
    sep_id: int

    def encode_word(self, word: str) -> Sequence[int]: ...


@dataclass
class SpanBatch:
    input_ids: np.ndarray  # int64 [B, T]
    attention_mask: np.ndarray  # int64 [B, T]
    words_mask: np.ndarray  # int64 [B, T]
    text_lengths: np.ndarray  # int64 [B, 1]
    span_idx: np.ndarray  # int64 [B, W*maxWidth, 2]
    span_mask: np.ndarray  # bool [B, W*maxWidth]
    batch_size: int
    token_count: int
    word_count: int
    max_width: int
    starts: list[list[int]]
    ends: list[list[int]]


@dataclass(frozen=True)
class Span:
    span_text: str
    start: int
    end: int
    label: str
    score: float


def split_words(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(), m.start(), m.end()) for m in WORD_RE.finditer(text)]


def prepare_span_batch(
    texts: Sequence[str], labels: Sequence[str], tokenizer: SpanTokenizer, max_width: int = MAX_WIDTH
) -> SpanBatch:
    if not texts or not labels:
        raise ValueError("NER requires text and entity labels")
    words = [split_words(text) for text in texts]
    starts = [[start for _, start, _ in row] for row in words]
    ends = [[end for _, _, end in row] for row in words]
    prompt: list[str] = []
    for label in labels:
        prompt += [ENT_TOKEN, label]
    prompt.append(SEP_TOKEN)
    # Кеш токенов на батч: промпт и повторяющиеся слова токенизируются один раз.
    cache: dict[str, Sequence[int]] = {}
    encoded: list[tuple[list[int], list[int]]] = []
    for row in words:
        ids = [tokenizer.cls_id]
        mask = [0]
        word_number = 1
        for index, word in enumerate([*prompt, *(w for w, _, _ in row)]):
            tokens = cache.get(word)
            if tokens is None:
                tokens = tokenizer.encode_word(word)
                cache[word] = tokens
            for token_index, token in enumerate(tokens):
                ids.append(token)
                # Номер слова ставится только на первый сабтокен слова текста.
                if index >= len(prompt) and token_index == 0:
                    mask.append(word_number)
                    word_number += 1
                else:
                    mask.append(0)
        ids.append(tokenizer.sep_id)
        mask.append(0)
        encoded.append((ids, mask))

    batch_size = len(texts)
    token_count = max(len(ids) for ids, _ in encoded)
    word_count = max(len(row) for row in words)
    input_ids = np.zeros((batch_size, token_count), dtype=np.int64)
    attention_mask = np.zeros((batch_size, token_count), dtype=np.int64)
    words_mask = np.zeros((batch_size, token_count), dtype=np.int64)
    text_lengths = np.zeros((batch_size, 1), dtype=np.int64)
    span_idx = np.zeros((batch_size, word_count, max_width, 2), dtype=np.int64)
    span_mask = np.zeros((batch_size, word_count, max_width), dtype=np.bool_)
    width = np.arange(max_width, dtype=np.int64)
    for batch, (ids, mask) in enumerate(encoded):
        input_ids[batch, : len(ids)] = ids
        attention_mask[batch, : len(ids)] = 1
        words_mask[batch, : len(ids)] = mask
        n = len(words[batch])
        text_lengths[batch, 0] = n
        if n == 0:
            continue
        start = np.arange(n, dtype=np.int64)[:, None]
        span_idx[batch, :n, :, 0] = start
        # Сохраняем исходную конвенцию входа модели: конец спана обрезается по
        # числу слов; декодер отбрасывает ширины за пределами текста.
        span_idx[batch, :n, :, 1] = np.minimum(start + width[None, :], n - 1)
        span_mask[batch, :n, :] = True
    return SpanBatch(
        input_ids=input_ids,
        attention_mask=attention_mask,
        words_mask=words_mask,
        text_lengths=text_lengths,
        span_idx=span_idx.reshape(batch_size, word_count * max_width, 2),
        span_mask=span_mask.reshape(batch_size, word_count * max_width),
        batch_size=batch_size,
        token_count=token_count,
        word_count=word_count,
        max_width=max_width,
        starts=starts,
        ends=ends,
    )


def _overlaps(a: Span, b: Span) -> bool:
    # Правило перекрытия GLiNER.js, включая его границы: касание (a.end == b.start)
    # тоже считается перекрытием.
    return not (a.start > b.end or b.start > a.end)


def decode_spans(
    logits: Sequence[float] | np.ndarray,
    batch: SpanBatch,
    texts: Sequence[str],
    labels: Sequence[str],
    threshold: float,
) -> list[list[Span]]:
    flat = np.asarray(logits, dtype=np.float64).reshape(-1)
    n_labels = len(labels)
    expected_length = batch.batch_size * batch.word_count * batch.max_width * n_labels
    if flat.size != expected_length:
        raise ValueError("Модель NER вернула неполный результат")
    if not np.isfinite(flat).all():
        raise ValueError("Модель NER вернула нечисловые оценки")
    with np.errstate(over="ignore"):
        scores = 1.0 / (1.0 + np.exp(-flat))
    rows: list[list[Span]] = [[] for _ in range(batch.batch_size)]
    row_stride = batch.word_count * batch.max_width * n_labels
    # Индексы в порядке возрастания — как в цикле TS, чтобы при равных оценках
    # стабильная сортировка давала тот же порядок.
    for index in np.flatnonzero(scores >= threshold).tolist():
        row = index // row_stride
        start_word = (index // (batch.max_width * n_labels)) % batch.word_count
        end_word = start_word + (index // n_labels) % batch.max_width
        if start_word >= len(batch.starts[row]) or end_word >= len(batch.ends[row]):
            continue
        start = batch.starts[row][start_word]
        end = batch.ends[row][end_word]
        rows[row].append(Span(texts[row][start:end], start, end, labels[index % n_labels], float(scores[index])))
    result: list[list[Span]] = []
    for candidates in rows:
        selected: list[Span] = []
        for candidate in sorted(candidates, key=lambda span: -span.score):
            if not any(_overlaps(candidate, other) for other in selected):
                selected.append(candidate)
        result.append(sorted(selected, key=lambda span: span.start))
    return result


__all__ = ["MAX_WIDTH", "WORD_RE", "Span", "SpanBatch", "SpanTokenizer", "decode_spans", "prepare_span_batch", "split_words"]
