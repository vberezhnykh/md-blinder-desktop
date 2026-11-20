"""Текст ошибки ONNX Runtime в кодировке Windows и переход с видеокарты на CPU."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from md_blinder.anonymize.pipeline import anonymize_markdown
from md_blinder.cancel import CancelToken, Cancelled
from md_blinder.components import model_asset
from md_blinder import error_text as error_text_module
from md_blinder.error_text import error_text
from md_blinder.model_holder import ModelHolder, ModelStatus
from md_blinder.ner import engine
from md_blinder.ner.runtime import GlinerModel, NerRuntimeError

# Так сообщение DirectML о нехватке памяти выглядит на русской Windows: путь и код
# по-английски, текст системы в cp1251. ORT отдает эти байты Python как UTF-8.
WINDOWS_MESSAGE = (
    "D:\\a\\_work\\1\\s\\onnxruntime\\core\\providers\\dml\\DmlExecutionProvider\\src\\"
    "ExecutionProvider.cpp(1157)\\onnxruntime_pybind11_state.pyd!00007FFB1E2C3A4B: "
    "(caller: 00007FFB1E2A1234) Exception(3) tid(4f2c) 8007000E "
    "Недостаточно памяти для завершения операции."
)


@pytest.fixture(autouse=True)
def russian_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    # Кодовая страница машины CI — cp1252, а у пользователя — cp1251, как на русской Windows.
    monkeypatch.setattr(error_text_module, "system_encodings", lambda: ("cp1251",))


def ort_decode_error() -> UnicodeDecodeError:
    raw = WINDOWS_MESSAGE.encode("cp1251")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as error:
        return error
    raise AssertionError("сообщение должно быть не в UTF-8")


def test_windows_message_is_decoded_from_system_code_page() -> None:
    error = ort_decode_error()
    assert "can't decode byte 0xcd" in str(error)
    text = error_text(error)
    assert text == WINDOWS_MESSAGE
    assert "Недостаточно памяти" in text


def test_message_is_never_left_as_codec_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(error_text_module, "system_encodings", lambda: ("cp1252",))
    text = error_text(ort_decode_error())
    assert "codec" not in text
    assert text.startswith("D:\\a\\_work") and "8007000E" in text


def test_other_errors_keep_their_text() -> None:
    assert error_text(RuntimeError("сеть недоступна")) == "сеть недоступна"
    assert error_text(ValueError()) == "ValueError"
    # Длинные байты — это данные, а не сообщение: их содержимое не показывается.
    big = b"\xcd" * 10_000
    try:
        big.decode("utf-8")
    except UnicodeDecodeError as error:
        assert "\xcd" not in error_text(error) and "Н" not in error_text(error)


class _Session:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def run(self, names, feeds):  # noqa: ANN001, ANN201
        raise self.error


def _model_with_session(session: object, device: str = "gpu") -> GlinerModel:
    model = GlinerModel.__new__(GlinerModel)
    model.device = device  # type: ignore[assignment]
    model.tokenizer = SimpleNamespace(cls_id=1, sep_id=2, encode_word=lambda word: [5])  # type: ignore[assignment]
    model._session = session  # type: ignore[assignment]
    return model


def test_inference_failure_becomes_readable_runtime_error() -> None:
    model = _model_with_session(_Session(ort_decode_error()))
    with pytest.raises(NerRuntimeError) as caught:
        model.infer(["Иван Петров"], ["person"])
    assert "Недостаточно памяти для завершения операции" in str(caught.value)
    assert "(gpu)" in str(caught.value)


def test_pipeline_keeps_regex_results_and_shows_readable_reason() -> None:
    def ner_detect(md: str):
        raise ort_decode_error()

    result = anonymize_markdown("Телефон +7 916 123-45-67", ner_detect)
    assert result.ner_failed
    assert "Недостаточно памяти" in (result.ner_error or "")
    assert any(e.type == "PHONE" for e in result.entities)


class FakeModel:
    def __init__(self, device: str) -> None:
        self.device = device
        self.released = False

    def release(self) -> None:
        self.released = True


@pytest.fixture
def holder(monkeypatch: pytest.MonkeyPatch, tmp_path) -> tuple[ModelHolder, list]:
    monkeypatch.setattr(model_asset, "is_model_present", lambda directory=None: True)
    loads: list = []

    def load(directory, on_progress=None, cancel=None, devices=None):
        loads.append(devices)
        model = FakeModel("cpu" if devices == ["cpu"] else "gpu")
        if on_progress:
            on_progress(engine.NerProgress(stage="init", ep=model.device))
        return model

    monkeypatch.setattr(engine, "load_model", load)
    return ModelHolder(tmp_path), loads


def test_gpu_failure_during_detection_retries_on_cpu(holder) -> None:
    holder, loads = holder
    used: list[FakeModel] = []

    def work(model: FakeModel) -> str:
        used.append(model)
        if model.device == "gpu":
            raise NerRuntimeError("Сбой модели NER (gpu): Недостаточно памяти")
        return "найдено"

    assert holder.run(work) == "найдено"
    assert [m.device for m in used] == ["gpu", "cpu"]
    assert used[0].released
    assert loads == [None, ["cpu"]]
    assert holder.status() == ModelStatus("ready", device="cpu")
    # Дальше программа работает на CPU и сессию заново не создает.
    assert holder.run(lambda model: model.device) == "cpu"
    assert loads == [None, ["cpu"]]


def test_gpu_failure_with_raw_decode_error_also_falls_back(holder) -> None:
    holder, _loads = holder

    def work(model: FakeModel) -> str:
        if model.device == "gpu":
            raise ort_decode_error()
        return model.device

    assert holder.run(work) == "cpu"


def test_cpu_failure_and_cancel_are_not_retried(holder, monkeypatch: pytest.MonkeyPatch) -> None:
    holder, loads = holder
    monkeypatch.setattr(engine, "load_model", lambda directory, on_progress=None, cancel=None, devices=None: (
        loads.append(devices), FakeModel("cpu"))[1])
    calls = []

    def fail(model: FakeModel) -> None:
        calls.append(model)
        raise NerRuntimeError("сбой")

    with pytest.raises(NerRuntimeError):
        holder.run(fail)
    assert len(calls) == 1

    cancel = CancelToken()

    def cancelled(model: FakeModel) -> None:
        cancel.cancel()
        raise Cancelled()

    with pytest.raises(Cancelled):
        holder.run(cancelled, cancel=cancel)


def test_real_logits_check_still_applies_after_wrapping() -> None:
    class Session:
        def run(self, names, feeds):  # noqa: ANN001, ANN201
            return [np.zeros((1, 1, 1, 1), dtype=np.int64)]

    model = _model_with_session(Session(), device="cpu")
    with pytest.raises(NerRuntimeError, match="неподдерживаемый результат"):
        model.infer(["Иван"], ["person"])
