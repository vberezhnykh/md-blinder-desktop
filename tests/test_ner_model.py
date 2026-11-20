"""Проверки на настоящей модели GLiNER (нужен скачанный model_fp16.onnx).

Калибровка и эталонный бенчмарк из scripts/benchmark-ner.mjs: 6 синтетических
текстов, 18 спанов, записанных на прежнем движке (1.19.2 fp16/WASM). Это проверка
совместимости, а не утверждение, что все спаны верны (O'Connor спорен).
"""

from __future__ import annotations

import time
from typing import Iterator

import pytest

from md_blinder.components.model_asset import is_model_present, model_dir
from md_blinder.ner.calibration import CALIBRATION_LABELS, CALIBRATION_TEXT, calibration_passes
from md_blinder.ner.engine import detect_entities_ner, load_model
from md_blinder.ner.runtime import GRAPH_OPTIMIZATION_FALLBACKS, GlinerModel, load_tokenizer

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(not is_model_present(), reason="модель GLiNER не скачана"),
]

TEXTS = [
    "John Smith signed for Acme Ltd in London at 12 Baker Street.",
    "Иван Петров подписал договор с ООО «Альфа» в Москве, улица Лесная, дом 15.",
    "Олена Коваленко уклала договір із ТОВ «Весна» у Києві, вулиця Хрещатик, 20.",
    "Élodie Müller signed with Alpha-Beta and O'Connor. 👩🏽‍💻 № 12345.",
    "Анна-Мария Иванова / Anna-Maria Ivanova: ACME_2026, 50 000 EUR.",
    "ТОВ «Зоря»; пошта sample@example.test; ім’я Марія, Іванівна.",
]
LABELS = ["person", "company", "address", "registration number"]
LEGACY = [
    [(0, 10, "person"), (22, 30, "company"), (44, 59, "address")],
    [(0, 11, "person"), (31, 42, "company"), (53, 65, "address"), (67, 73, "address")],
    [(0, 15, "person"), (34, 45, "company"), (55, 74, "address")],
    # В TS «№ 12345» — 59..66 в UTF-16: эмодзи 👩🏽‍💻 — 4 кодовые точки, из них 3 вне
    # BMP (суррогатные пары). В кодовых точках Python это 56..63.
    [(0, 13, "person"), (26, 36, "company"), (41, 49, "company"), (56, 63, "registration number")],
    [(0, 18, "person"), (21, 39, "person")],
    [(0, 10, "company"), (44, 59, "person")],
]


def test_legacy_offsets_converted_from_utf16() -> None:
    text = TEXTS[3]
    assert text[56:63] == "№ 12345"
    assert len(text[:56].encode("utf-16-le")) // 2 == 59


@pytest.fixture(scope="module")
def model() -> Iterator[GlinerModel]:
    t0 = time.perf_counter()
    loaded = load_model(model_dir())
    print(f"\n[ner] модель загружена и откалибрована ({loaded.optimization}) за {time.perf_counter() - t0:.2f}s")
    yield loaded
    loaded.release()


def test_tokenizer_matches_recorded_ids() -> None:
    # Те же id, что записаны в src/workers/glinerSpan.test.ts.
    tokenizer = load_tokenizer(model_dir())
    expected = {
        "<<ENT>>": [250103], "<<SEP>>": [250104], "person": [2986],
        "John": [4041], "Smith": [16786], "Иван": [30869], "Петров": [67960],
        "Олена": [984, 20380], "Коваленко": [126188, 166581],
    }
    assert {word: tokenizer.encode_word(word) for word in expected} == expected


def test_chosen_backend_passes_calibration(model: GlinerModel) -> None:
    assert model.optimization == GRAPH_OPTIMIZATION_FALLBACKS[0]
    spans = model.infer([CALIBRATION_TEXT], CALIBRATION_LABELS, 0.5)[0]
    assert calibration_passes(spans)


def test_golden_benchmark(model: GlinerModel) -> None:
    model.infer(TEXTS, LABELS, 0.5)  # прогрев формы батча
    timings = []
    for _ in range(3):
        t0 = time.perf_counter()
        rows = model.infer(TEXTS, LABELS, 0.5)
        timings.append(time.perf_counter() - t0)
    print(f"\n[ner] 6 текстов: {', '.join(f'{t * 1000:.0f}ms' for t in timings)}")
    assert [[(s.start, s.end, s.label) for s in row] for row in rows] == LEGACY
    assert sum(len(row) for row in rows) == 18
    assert all(0.5 <= s.score <= 1 for row in rows for s in row)


def test_detect_entities_ner_on_markdown(model: GlinerModel) -> None:
    md = "# Договор\n\n**Иван Петров** подписал договор с ООО «Альфа».\n\n![подпись](data:image/png;base64,AAAA)"
    matches = detect_entities_ner(md, model)
    found = {(m.type, md[m.start:m.end]) for m in matches if m.score is not None and m.score >= 0.5}
    assert ("PERSON", "Иван Петров") in found
    assert ("COMPANY", "ООО «Альфа»") in found


@pytest.mark.parametrize("config", GRAPH_OPTIMIZATION_FALLBACKS, ids=str)
def test_every_fallback_passes_calibration_and_benchmark(config) -> None:  # type: ignore[no-untyped-def]
    t0 = time.perf_counter()
    candidate = GlinerModel(model_dir(), optimization=config)
    init_s = time.perf_counter() - t0
    try:
        assert calibration_passes(candidate.infer([CALIBRATION_TEXT], CALIBRATION_LABELS, 0.5)[0])
        t1 = time.perf_counter()
        rows = candidate.infer(TEXTS, LABELS, 0.5)
        print(f"\n[ner] {config}: сессия {init_s:.2f}s, 6 текстов {(time.perf_counter() - t1) * 1000:.0f}ms")
        assert [[(s.start, s.end, s.label) for s in row] for row in rows] == LEGACY
    finally:
        candidate.release()
