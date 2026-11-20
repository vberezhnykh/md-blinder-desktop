"""Порт src/workers/glinerSpan.test.ts и проверки формы выхода из glinerRuntime.ts."""

from __future__ import annotations

import numpy as np
import pytest

from md_blinder.ner.gliner_span import decode_spans, prepare_span_batch
from md_blinder.ner.runtime import GlinerModel, NerRuntimeError

# Идентификаторы токенов записаны из токенизатора Deberta на закрепленной ревизии
# модели. Синтетические имена проверяют кириллицу и слова из нескольких сабтокенов.
IDS: dict[str, list[int]] = {
    "<<ENT>>": [250103], "<<SEP>>": [250104], "person": [2986],
    "John": [4041], "Smith": [16786], "Иван": [30869], "Петров": [67960],
    "Олена": [984, 20380], "Коваленко": [126188, 166581],
}


class FakeTokenizer:
    cls_id = 1
    sep_id = 2

    def __init__(self) -> None:
        self.calls = 0

    def encode_word(self, word: str) -> list[int]:
        self.calls += 1
        return IDS.get(word, [3])


def test_preserves_legacy_prompt_word_mask_padding_and_offsets() -> None:
    batch = prepare_span_batch(["John Smith", "Олена Коваленко", "Иван Петров"], ["person"], FakeTokenizer())
    assert batch.input_ids.reshape(-1).tolist() == [
        1, 250103, 2986, 250104, 4041, 16786, 2, 0, 0,
        1, 250103, 2986, 250104, 984, 20380, 126188, 166581, 2,
        1, 250103, 2986, 250104, 30869, 67960, 2, 0, 0,
    ]
    assert batch.words_mask.reshape(-1).tolist() == [
        0, 0, 0, 0, 1, 2, 0, 0, 0,
        0, 0, 0, 0, 1, 0, 2, 0, 0,
        0, 0, 0, 0, 1, 2, 0, 0, 0,
    ]
    assert batch.attention_mask.reshape(-1).tolist() == [
        1, 1, 1, 1, 1, 1, 1, 0, 0,
        1, 1, 1, 1, 1, 1, 1, 1, 1,
        1, 1, 1, 1, 1, 1, 1, 0, 0,
    ]
    assert batch.starts == [[0, 5], [0, 6], [0, 5]]
    assert batch.ends == [[4, 10], [5, 15], [4, 11]]
    for array in (batch.input_ids, batch.attention_mask, batch.words_mask, batch.text_lengths, batch.span_idx):
        assert array.dtype == np.int64
    assert batch.span_mask.dtype == np.bool_


def test_span_tensors_clamp_end_positions_and_mask_padding() -> None:
    batch = prepare_span_batch(["John", "Иван Петров"], ["person"], FakeTokenizer())
    assert batch.text_lengths.tolist() == [[1], [2]]
    assert batch.span_idx.shape == (2, 2 * 12, 2)
    assert batch.span_mask.shape == (2, 2 * 12)
    # Строка 0: одно слово — все 12 ширин указывают на [0, 0], паддинг-слово замаскировано.
    assert batch.span_idx[0, :12].tolist() == [[0, 0]] * 12
    assert batch.span_mask[0].tolist() == [True] * 12 + [False] * 12
    assert batch.span_idx[0, 12:].tolist() == [[0, 0]] * 12
    # Строка 1: конец спана обрезан по последнему слову.
    assert batch.span_idx[1, :3].tolist() == [[0, 0], [0, 1], [0, 1]]
    assert batch.span_idx[1, 12:14].tolist() == [[1, 1], [1, 1]]
    assert batch.span_mask[1].all()


def test_tokenizes_repeated_prompt_words_only_once_per_batch() -> None:
    tokenizer = FakeTokenizer()
    prepare_span_batch(["John Smith", "John Smith"], ["person"], tokenizer)
    assert tokenizer.calls == 5


def test_requires_texts_and_labels() -> None:
    with pytest.raises(ValueError):
        prepare_span_batch([], ["person"], FakeTokenizer())
    with pytest.raises(ValueError):
        prepare_span_batch(["John"], [], FakeTokenizer())


def test_does_not_decode_padded_spans_or_widths_past_source_text() -> None:
    texts = ["John", "Иван Петров"]
    batch = prepare_span_batch(texts, ["person"], FakeTokenizer())
    logits = np.full(2 * 2 * 12, -20, dtype=np.float32)
    logits[0] = 5
    logits[1] = 9  # ширина за пределами единственного слова первой строки
    logits[12] = 9  # паддинг-слово первой строки
    logits[25] = 5  # полное русское имя во второй строке
    decoded = decode_spans(logits, batch, texts, ["person"], 0.5)
    assert [[(s.span_text, s.start, s.end) for s in row] for row in decoded] == [
        [("John", 0, 4)],
        [("Иван Петров", 0, 11)],
    ]


def test_selects_highest_confidence_overlap_and_offsets_after_astral_characters() -> None:
    text = "💻 Иван Петров"
    batch = prepare_span_batch([text], ["person"], FakeTokenizer())
    logits = np.full(3 * 12, -20, dtype=np.float32)
    logits[12] = 3
    logits[13] = 5
    matches = decode_spans(logits, batch, [text], ["person"], 0.5)[0]
    assert len(matches) == 1
    # В TS ожидается 3..14 (UTF-16: эмодзи — суррогатная пара). В Python смещения —
    # кодовые точки, эмодзи занимает одну позицию, поэтому 2..13.
    assert (matches[0].span_text, matches[0].start, matches[0].end, matches[0].label) == ("Иван Петров", 2, 13, "person")
    assert text[2:13] == "Иван Петров"


def test_touching_spans_count_as_overlapping() -> None:
    # Правило GLiNER.js включительное: спан, начинающийся там, где кончился
    # выбранный (start == other.end), отбрасывается. Слова «ab», «,», «c» идут
    # вплотную друг к другу.
    text = "ab,c"
    batch = prepare_span_batch([text], ["person"], FakeTokenizer())
    assert batch.starts == [[0, 2, 3]] and batch.ends == [[2, 3, 4]]
    logits = np.full(3 * 12, -20, dtype=np.float32)
    logits[0] = 5  # «ab» 0..2
    logits[12] = 4  # «,» 2..3 — касается «ab»
    logits[24] = 3  # «c» 3..4 — касается «,», но не «ab»
    rows = decode_spans(logits, batch, [text], ["person"], 0.5)[0]
    assert [(s.start, s.end) for s in rows] == [(0, 2), (3, 4)]


def test_label_index_is_innermost_dimension() -> None:
    text = "John Smith"
    batch = prepare_span_batch([text], ["person", "company"], FakeTokenizer())
    logits = np.full((1, 2, 12, 2), -20, dtype=np.float32)
    logits[0, 0, 1, 1] = 2  # «John Smith» как company
    rows = decode_spans(logits, batch, [text], ["person", "company"], 0.5)[0]
    assert [(s.span_text, s.label) for s in rows] == [("John Smith", "company")]
    assert rows[0].score == pytest.approx(1 / (1 + np.exp(-2)))


@pytest.mark.parametrize("invalid", [np.full(24, np.nan), np.full(24, np.inf), np.array([5.0])])
def test_rejects_non_finite_or_truncated_outputs(invalid: np.ndarray) -> None:
    batch = prepare_span_batch(["John Smith"], ["person"], FakeTokenizer())
    with pytest.raises(ValueError):
        decode_spans(invalid, batch, ["John Smith"], ["person"], 0.5)


class FakeSession:
    def __init__(self, output: object) -> None:
        self.output = output
        self.feeds: dict | None = None

    def run(self, names: list[str], feeds: dict) -> list[object]:
        self.feeds = feeds
        return [self.output]


def make_model(output: object) -> tuple[GlinerModel, FakeSession]:
    model = GlinerModel.__new__(GlinerModel)
    session = FakeSession(output)
    model._session = session  # type: ignore[assignment]
    model.tokenizer = FakeTokenizer()  # type: ignore[assignment]
    return model, session


def test_runtime_feeds_and_decodes() -> None:
    logits = np.full((1, 2, 12, 1), -20, dtype=np.float32)
    logits[0, 0, 1, 0] = 4
    model, session = make_model(logits)
    rows = model.infer(["John Smith"], ["person"], 0.5)
    assert [(s.span_text, s.start, s.end) for s in rows[0]] == [("John Smith", 0, 10)]
    assert session.feeds is not None
    assert set(session.feeds) == {"input_ids", "attention_mask", "words_mask", "text_lengths", "span_idx", "span_mask"}
    assert session.feeds["text_lengths"].shape == (1, 1)
    assert session.feeds["span_idx"].shape == (1, 24, 2)
    assert session.feeds["span_mask"].dtype == np.bool_
    assert model.infer([], ["person"], 0.5) == []


@pytest.mark.parametrize("output", [np.zeros((1, 2, 12, 2), np.float32), np.zeros((2, 12, 1), np.float32), np.zeros((1, 2, 12, 1), np.int64)])
def test_runtime_rejects_wrong_shape_or_type(output: np.ndarray) -> None:
    model, _ = make_model(output)
    with pytest.raises(NerRuntimeError):
        model.infer(["John Smith"], ["person"], 0.5)
