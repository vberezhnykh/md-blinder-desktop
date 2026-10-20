"""Порт src/lib/anonymize/ner.test.ts и логики ner.worker.ts с поддельной моделью.

В браузере тесты проверяли жизненный цикл Web Worker'а (очередь, отмена, падение
сессии). Здесь воркера нет: проверяются те же свойства на синхронном API —
отмена между батчами, проброс ошибки модели, повторное использование модели,
прогресс по фазам и калибровка при загрузке.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pytest

from md_blinder.anonymize.config import NER_GRAY_ZONE_LOW
from md_blinder.anonymize.types import RawMatch
from md_blinder.cancel import CancelToken, Cancelled
from md_blinder.ner import engine
from md_blinder.ner.batching import NER_BATCH_SIZE, NER_BATCH_SIZE_GPU
from md_blinder.ner.calibration import CALIBRATION_TEXT, EXPECTED
from md_blinder.ner.engine import NER_LABELS, NerModelError, NerProgress, detect_entities_ner, load_model, map_label
from md_blinder.ner.gliner_span import Span
from md_blinder.ner.runtime import GRAPH_OPTIMIZATION_FALLBACKS, NerRuntimeError


class FakeModel:
    """Находит заданные подстроки с заданной меткой в каждом тексте батча."""

    def __init__(self, entities: dict[str, str] | None = None, score: float = 0.9) -> None:
        self.entities = entities or {}
        self.score = score
        self.calls: list[tuple[list[str], list[str], float]] = []
        self.on_call = None

    def infer(self, texts: Sequence[str], labels: Sequence[str], threshold: float) -> list[list[Span]]:
        self.calls.append((list(texts), list(labels), threshold))
        if self.on_call:
            self.on_call(len(self.calls))
        rows: list[list[Span]] = []
        for text in texts:
            row = []
            for value, label in self.entities.items():
                start = text.find(value)
                while start != -1:
                    row.append(Span(value, start, start + len(value), label, self.score))
                    start = text.find(value, start + 1)
            rows.append(sorted(row, key=lambda s: s.start))
        return rows

    def release(self) -> None:
        pass


def test_map_label() -> None:
    assert [map_label(x) for x in ("person", "Company", "ADDRESS", "registration number", "date")] == [
        "PERSON", "COMPANY", "ADDRESS", "REG_NUMBER", None,
    ]


def test_detects_and_remaps_offsets_through_inline_formatting() -> None:
    md = "Договор: **Иван Петров** и _ООО «Альфа»_."
    model = FakeModel({"Иван Петров": "person", "ООО «Альфа»": "company", "Москва": "address"})
    matches = detect_entities_ner(md, model)  # type: ignore[arg-type]
    assert [(m.type, m.value, md[m.start:m.end], m.score) for m in matches] == [
        ("PERSON", "Иван Петров", "Иван Петров", 0.9),
        ("COMPANY", "ООО «Альфа»", "ООО «Альфа»", 0.9),
    ]
    # Модель получает текст без разметки, метки и порог серой зоны.
    texts, labels, threshold = model.calls[0]
    assert texts == ["Договор: Иван Петров и ООО «Альфа»."]
    assert labels == list(NER_LABELS)
    assert threshold == NER_GRAY_ZONE_LOW


def test_empty_or_whitespace_text_skips_model() -> None:
    model = FakeModel()
    assert detect_entities_ner("", model) == []  # type: ignore[arg-type]
    assert detect_entities_ner("  \n\n **  ** \n", model) == []  # type: ignore[arg-type]
    assert model.calls == []


def test_repeated_paragraphs_are_inferred_once_with_per_occurrence_offsets() -> None:
    para = "Подпись: Иван Петров"
    md = "\n\n".join([para] + ["x" * 1490] + [para])
    model = FakeModel({"Иван Петров": "person"})
    matches = detect_entities_ner(md, model)  # type: ignore[arg-type]
    inferred = [t for texts, _, _ in model.calls for t in texts]
    assert inferred.count(para) == 1
    assert [md[m.start:m.end] for m in matches] == ["Иван Петров", "Иван Петров"]
    assert matches[0].start != matches[1].start


def many_chunks(n: int) -> str:
    # Абзацы по ~1000 символов не сливаются (лимит 1500) — n фрагментов.
    return "\n\n".join(f"Абзац {i} " + "слово " * 170 for i in range(n))


def test_progress_reports_detect_batches_before_and_after_inference() -> None:
    events: list[NerProgress] = []
    model = FakeModel()
    detect_entities_ner(many_chunks(5), model, on_progress=events.append)  # type: ignore[arg-type]
    assert NER_BATCH_SIZE == 2
    assert len(model.calls) == 3  # 2 + 2 + 1
    assert [(e.stage, e.batch, e.batches, e.chunk, e.chunks, e.batch_chunks) for e in events] == [
        ("detect", 1, 3, 0, 5, 2),
        ("detect", 1, 3, 2, 5, 2),
        ("detect", 2, 3, 2, 5, 2),
        ("detect", 2, 3, 4, 5, 2),
        ("detect", 3, 3, 4, 5, 1),
        ("detect", 3, 3, 5, 5, 1),
    ]
    assert all(e.ep == "cpu" and e.elapsed_ms is not None for e in events)


def test_cancel_between_batches() -> None:
    token = CancelToken()
    model = FakeModel()
    model.on_call = lambda n: token.cancel()
    with pytest.raises(Cancelled):
        detect_entities_ner(many_chunks(10), model, cancel=token)  # type: ignore[arg-type]
    assert len(model.calls) == 1


def test_cancelled_before_start_makes_no_inference() -> None:
    token = CancelToken()
    token.cancel()
    model = FakeModel()
    with pytest.raises(Cancelled):
        detect_entities_ner("Иван Петров", model, cancel=token)  # type: ignore[arg-type]
    assert model.calls == []


def test_model_failure_propagates_and_model_is_reusable() -> None:
    model = FakeModel({"Alice": "person"})

    def fail(n: int) -> None:
        if n == 1:
            raise NerRuntimeError("Модель NER вернула неверную форму результата")

    model.on_call = fail
    with pytest.raises(NerRuntimeError):
        detect_entities_ner("Alice", model)  # type: ignore[arg-type]
    assert detect_entities_ner("Bob and Alice", model) == [RawMatch("PERSON", "Alice", 8, 13, 0.9)]  # type: ignore[arg-type]


class FakeGliner(FakeModel):
    created: list["FakeGliner"] = []
    passing: set[str] = set()
    failing_init: set[str] = set()

    def __init__(self, model_dir: Path | str, *, optimization: object, device: str = "cpu") -> None:
        key = str(optimization) if device == "cpu" else f"{device} {optimization}"
        if key in self.failing_init:
            raise NerRuntimeError("boom")
        ok = key in self.passing
        super().__init__({value: label for value, label in EXPECTED} if ok else {"Иван Петров": "person"})
        self.optimization = optimization
        self.device = device
        self.released = False
        FakeGliner.created.append(self)

    def release(self) -> None:
        self.released = True


@pytest.fixture
def fake_gliner(monkeypatch: pytest.MonkeyPatch) -> type[FakeGliner]:
    FakeGliner.created = []
    FakeGliner.passing = set()
    FakeGliner.failing_init = set()
    monkeypatch.setattr(engine, "GlinerModel", FakeGliner)
    # На Windows установлен onnxruntime-directml: без этого тесты зависели бы от ОС.
    monkeypatch.setattr(engine, "gpu_available", lambda: False)
    return FakeGliner


def test_load_model_runs_calibration_and_reports_phases(fake_gliner: type[FakeGliner], tmp_path: Path) -> None:
    first = str(GRAPH_OPTIMIZATION_FALLBACKS[0])
    fake_gliner.passing = {first}
    events: list[NerProgress] = []
    model = load_model(tmp_path, events.append)
    assert [e.stage for e in events] == ["init", "warmup"]
    assert len(fake_gliner.created) == 1 and model is fake_gliner.created[0]
    assert model.calls == [([CALIBRATION_TEXT], ["person", "company", "address", "registration number"], 0.5)]


def test_load_model_falls_back_when_calibration_fails(fake_gliner: type[FakeGliner], tmp_path: Path) -> None:
    fake_gliner.failing_init = {str(GRAPH_OPTIMIZATION_FALLBACKS[0])}
    fake_gliner.passing = {str(GRAPH_OPTIMIZATION_FALLBACKS[2])}
    model = load_model(tmp_path)
    assert [str(m.optimization) for m in fake_gliner.created] == [str(c) for c in GRAPH_OPTIMIZATION_FALLBACKS[1:3]]
    assert fake_gliner.created[0].released
    assert model is fake_gliner.created[1] and not model.released


def test_load_model_prefers_gpu_and_falls_back_to_cpu(
    fake_gliner: type[FakeGliner], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(engine, "gpu_available", lambda: True)
    first = str(GRAPH_OPTIMIZATION_FALLBACKS[0])
    fake_gliner.passing = {f"gpu {first}"}
    events: list[NerProgress] = []
    model = load_model(tmp_path, events.append)
    assert model.device == "gpu" and len(fake_gliner.created) == 1
    assert [(e.stage, e.ep) for e in events] == [("init", "gpu"), ("warmup", "gpu")]

    # Видеокарта не проходит калибровку ни на одном из GPU_CONFIGS: дальше CPU.
    fake_gliner.created = []
    fake_gliner.passing = {first}
    model = load_model(tmp_path)
    assert [(m.device, str(m.optimization)) for m in fake_gliner.created] == [
        *(("gpu", str(c)) for c in engine.GPU_CONFIGS), ("cpu", first),
    ]
    assert model.device == "cpu" and all(m.released for m in fake_gliner.created[:-1])


def test_device_env_disables_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "gpu_available", lambda: True)
    monkeypatch.setenv(engine.DEVICE_ENV, "cpu")
    assert engine.available_devices() == ["cpu"]
    monkeypatch.delenv(engine.DEVICE_ENV)
    assert engine.available_devices() == ["gpu", "cpu"]


def test_gpu_model_uses_larger_batches() -> None:
    model = FakeModel()
    model.device = "gpu"  # type: ignore[attr-defined]
    events: list[NerProgress] = []
    detect_entities_ner(many_chunks(10), model, on_progress=events.append)  # type: ignore[arg-type]
    assert [len(texts) for texts, _, _ in model.calls] == [NER_BATCH_SIZE_GPU, 10 - NER_BATCH_SIZE_GPU]
    assert all(e.ep == "gpu" for e in events)


def test_load_model_raises_when_no_backend_passes(fake_gliner: type[FakeGliner], tmp_path: Path) -> None:
    with pytest.raises(NerModelError, match="проверку качества"):
        load_model(tmp_path)
    assert len(fake_gliner.created) == len(GRAPH_OPTIMIZATION_FALLBACKS)
    assert all(m.released for m in fake_gliner.created)


def test_ensure_model_reports_cached_download(
    fake_gliner: type[FakeGliner], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_gliner.passing = {str(GRAPH_OPTIMIZATION_FALLBACKS[0])}
    monkeypatch.setattr(engine.model_asset, "is_model_present", lambda directory=None: True)
    events: list[NerProgress] = []
    engine.ensure_model(events.append, directory=tmp_path)
    assert [(e.stage, e.pct, e.cached) for e in events][0] == ("download", 100, True)
    assert [e.stage for e in events] == ["download", "init", "warmup"]


def test_ensure_model_downloads_missing_files(
    fake_gliner: type[FakeGliner], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_gliner.passing = {str(GRAPH_OPTIMIZATION_FALLBACKS[0])}
    monkeypatch.setattr(engine.model_asset, "is_model_present", lambda directory=None: False)

    def fake_download(on_progress, cancel, directory):  # type: ignore[no-untyped-def]
        for received in (0, 50, 100):
            on_progress(received, 100)
        return directory

    monkeypatch.setattr(engine.model_asset, "download_model", fake_download)
    events: list[NerProgress] = []
    engine.ensure_model(events.append, directory=tmp_path)
    downloads = [(e.pct, e.cached) for e in events if e.stage == "download"]
    assert downloads == [(0, False), (50, False), (99, False), (100, False)]
