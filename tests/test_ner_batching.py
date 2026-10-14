"""Порт src/workers/nerBatching.test.ts."""

from __future__ import annotations

from md_blinder.anonymize.types import RawMatch
from md_blinder.ner.batching import count_words, expand_batch_result, plan_batches
from md_blinder.ner.chunk_text import Chunk
from md_blinder.ner.gliner_span import Span


def test_count_words_letter_digit_runs() -> None:
    assert count_words("Іван Петров") == 2


def test_count_words_punctuation_is_own_word() -> None:
    # «а.а.» без пробела: точка не входит в класс букв/цифр и не является
    # разделителем «-»/«_», поэтому «а», «.», «а», «.» — четыре токена, плюс «окишев».
    assert count_words("а.а. окишев") == 5


def test_plan_dedups_identical_chunk_text() -> None:
    chunks = [Chunk("повторяющийся текст", 0), Chunk("повторяющийся текст", 100), Chunk("повторяющийся текст", 500)]
    plan = plan_batches(chunks, 8)
    assert len(plan.occurrences["повторяющийся текст"]) == 3
    flat = [t for batch in plan.batches for t in batch]
    assert flat == ["повторяющийся текст"]


def test_plan_sorts_unique_texts_by_word_count() -> None:
    short = " ".join(f"word{i}" for i in range(5))
    long = " ".join(f"word{i}" for i in range(300))
    mid = " ".join(f"word{i}" for i in range(40))
    # Намеренно не по возрастанию — plan_batches должен пересортировать сам.
    plan = plan_batches([Chunk(long, 0), Chunk(short, 1000), Chunk(mid, 2000)], 8)
    assert plan.batches == [[short, mid, long]]


def test_plan_ties_broken_by_length_then_stable() -> None:
    plan = plan_batches([Chunk("bbbb", 0), Chunk("aa", 5), Chunk("cc", 9)], 8)
    assert plan.batches == [["aa", "cc", "bbbb"]]


def test_plan_slices_into_batches_of_given_size() -> None:
    plan = plan_batches([Chunk(f"chunk-{i}", i * 10) for i in range(20)], 8)
    assert [len(b) for b in plan.batches] == [8, 8, 4]


def test_expand_maps_offsets_per_occurrence() -> None:
    text = "Иван Иванов работает в компании"
    item = Span("Иван Иванов", 0, 11, "person", 0.9)
    occurrences = {text: [Chunk(text, 100), Chunk(text, 500)]}
    matches = expand_batch_result([text], [[item]], occurrences, lambda _label: "PERSON")
    assert matches == [
        RawMatch("PERSON", "Иван Иванов", 100, 111, 0.9),
        RawMatch("PERSON", "Иван Иванов", 500, 511, 0.9),
    ]


def test_expand_skips_items_whose_label_maps_to_none() -> None:
    item = Span("текст", 0, 5, "unknown", 0.5)
    assert expand_batch_result(["текст"], [[item]], {"текст": [Chunk("текст", 0)]}, lambda _label: None) == []


def test_expand_tolerates_missing_rows() -> None:
    assert expand_batch_result(["a", "b"], [[]], {"a": [Chunk("a", 0)], "b": [Chunk("b", 3)]}, lambda _l: "PERSON") == []
