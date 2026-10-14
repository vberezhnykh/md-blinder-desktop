"""Порт src/workers/chunkText.test.ts."""

from __future__ import annotations

import regex

from md_blinder.ner.chunk_text import MAX_WORDS, chunk_text

MARKER = "ZZUNIQUEENTITYZZ"
WORD_RE = regex.compile(r"[\p{L}\p{N}_]+(?:[-_][\p{L}\p{N}_]+)*|\S")


def make_filler(length: int) -> str:
    words: list[str] = []
    total = 0
    i = 0
    while total < length:
        word = f"word{i}"
        words.append(word)
        total += len(word) + 1  # + пробел
        i += 1
    return " ".join(words)


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text))


def test_keeps_marker_straddling_hard_split_boundary_intact() -> None:
    # Ни одной пустой строки: один гигантский абзац, как сырой вывод OCR.
    text = f"{make_filler(1490)} {MARKER} {make_filler(2000)}"
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert any(MARKER in c.text for c in chunks)


def test_reports_correct_base_offsets() -> None:
    text = f"{make_filler(1490)} {MARKER} {make_filler(2000)}"
    chunks = chunk_text(text)
    assert chunks
    for chunk in chunks:
        assert text[chunk.base : chunk.base + len(chunk.text)] == chunk.text


def test_overlaps_consecutive_hard_split_chunks() -> None:
    chunks = chunk_text(make_filler(5000))
    assert len(chunks) > 1
    for a, b in zip(chunks, chunks[1:]):
        assert b.base < a.base + len(a.text)


def test_still_chunks_short_text_on_paragraph_boundaries() -> None:
    text = "First paragraph.\n\nSecond paragraph."
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].base == 0


def test_drops_chunks_with_no_letters_or_digits() -> None:
    noise = "| --- | --- |\n\n___________________\n\n| :-: | :-: |"
    para = "Иван Петров подписал договор."
    text = f"{noise}\n\n{para}\n\n{noise}"
    chunks = chunk_text(text)
    # Шумовые фрагменты исчезли, настоящий абзац остался.
    assert any("Иван Петров" in c.text for c in chunks)
    for c in chunks:
        assert regex.search(r"[\p{L}\p{N}]", c.text)
        assert text[c.base : c.base + len(c.text)] == c.text


def test_caps_word_dense_chunk_with_overlap_and_offsets() -> None:
    # 400 коротких «слов» в ~800 символах: один фрагмент (<1500), но >350 слов.
    text = " ".join(f"w{i}" for i in range(400))
    chunks = chunk_text(text)
    assert len(chunks) > 1
    for c in chunks:
        assert count_words(c.text) <= MAX_WORDS
        assert text[c.base : c.base + len(c.text)] == c.text
    for a, b in zip(chunks, chunks[1:]):
        assert b.base < a.base + len(a.text)
    assert any(c.text.startswith("w0 ") for c in chunks)
    assert any(c.text.endswith("w399") for c in chunks)


def test_leaves_under_limit_chunk_untouched_by_word_cap() -> None:
    text = " ".join(f"слово{i}" for i in range(100))
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].base == 0


def test_splits_when_paragraphs_together_exceed_max_len() -> None:
    para_a = make_filler(1000)
    para_b = make_filler(1000)
    chunks = chunk_text(f"{para_a}\n\n{para_b}", 1500)
    assert len(chunks) == 2
    assert chunks[0].text == para_a
    assert chunks[1].text == para_b
    assert chunks[1].base == len(para_a) + 2


def test_prefers_sentence_boundary_for_hard_split() -> None:
    sentence = "Первое предложение договора о поставке. "
    text = sentence * 60  # ~2400 символов одним абзацем
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert chunks[0].text.endswith(". ")
    for c in chunks:
        assert text[c.base : c.base + len(c.text)] == c.text
