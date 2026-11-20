"""Состояние модели NER (model_holder.py) и индикатор на стартовом экране."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QLabel

from md_blinder import model_holder
from md_blinder.cancel import CancelToken, Cancelled
from md_blinder.components import model_asset
from md_blinder.model_holder import ModelHolder, ModelStatus
from md_blinder.ner import engine
from md_blinder.ui.model_status import ModelStatusBar, ner_text


class FakeFiles:
    """Файлы модели без диска: «скачаны» после fake_download."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, present: bool = False) -> None:
        self.present = present
        self.downloads = 0
        self.fail: str | None = None
        self.gate: threading.Event | None = None
        monkeypatch.setattr(model_asset, "is_model_present", lambda directory=None: self.present)
        monkeypatch.setattr(model_asset, "download_model", self.download)

    def download(self, on_progress=None, cancel=None, directory=None):
        self.downloads += 1
        if on_progress:
            on_progress(50, 100)
        if self.gate is not None:
            while not self.gate.wait(0.05):
                if cancel is not None:
                    cancel.check()
        if self.fail:
            raise RuntimeError(self.fail)
        self.present = True
        if on_progress:
            on_progress(100, 100)
        return directory


def fake_model(device: str = "gpu"):
    return SimpleNamespace(device=device, optimization="all", release=lambda: None)


def test_status_follows_files_download_and_load(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    files = FakeFiles(monkeypatch)
    holder = ModelHolder(tmp_path)
    seen: list[ModelStatus] = []
    holder.subscribe(seen.append)
    assert holder.status().state == "missing"

    progress: list[tuple[int, int]] = []
    holder.download(lambda got, total: progress.append((got, total)))
    assert files.downloads == 1
    assert progress[0] == (50, 100)
    assert "downloading" in [s.state for s in seen]
    assert any(s.state == "downloading" and s.pct == 50 for s in seen)
    assert holder.status().state == "idle"

    def load(directory, on_progress=None, cancel=None):
        on_progress(engine.NerProgress(stage="init", ep="gpu"))
        return fake_model("gpu")

    monkeypatch.setattr(engine, "load_model", load)
    model = holder.get()
    assert model.device == "gpu"
    assert holder.loaded
    assert any(s.state == "loading" and s.device == "gpu" for s in seen)
    assert holder.status() == ModelStatus("ready", device="gpu")
    # Повторный вызов не создает сессию заново.
    assert holder.get() is model


def test_download_error_shows_reason_and_keeps_missing(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    files = FakeFiles(monkeypatch)
    files.fail = "сеть недоступна"
    holder = ModelHolder(tmp_path)
    with pytest.raises(RuntimeError):
        holder.download()
    status = holder.status()
    assert status.state == "missing"
    assert status.error == "сеть недоступна"
    assert "не удалось скачать: сеть недоступна" in ner_text(status)


def test_load_error_and_retry(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    FakeFiles(monkeypatch, present=True)
    holder = ModelHolder(tmp_path)
    calls = []

    def load(directory, on_progress=None, cancel=None):
        calls.append(directory)
        if len(calls) == 1:
            raise engine.NerModelError("калибровка не пройдена")
        return fake_model("cpu")

    monkeypatch.setattr(engine, "load_model", load)
    with pytest.raises(engine.NerModelError):
        holder.get()
    assert holder.status() == ModelStatus("error", error="калибровка не пройдена")
    assert holder.get().device == "cpu"
    assert holder.status().state == "ready"


def test_second_download_waits_for_first_and_cancel_does_not_wait(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    files = FakeFiles(monkeypatch)
    files.gate = threading.Event()
    holder = ModelHolder(tmp_path)
    first = threading.Thread(target=holder.download)
    first.start()
    while not holder.downloading:
        pass
    # Задача, которую отменили, не ждет чужое скачивание.
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        holder.download(cancel=token)
    # Вторая задача дожидается первой и не качает файл заново.
    second = threading.Thread(target=holder.download)
    second.start()
    files.gate.set()
    first.join(5)
    second.join(5)
    assert files.downloads == 1
    assert holder.status().state == "idle"


def test_preload_runs_once_in_background(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    files = FakeFiles(monkeypatch, present=False)
    holder = ModelHolder(tmp_path)
    assert not holder.preload()  # нечего загружать
    files.present = True
    gate = threading.Event()

    def load(directory, on_progress=None, cancel=None):
        gate.wait(5)
        return fake_model("cpu")

    monkeypatch.setattr(engine, "load_model", load)
    assert holder.preload()
    assert not holder.preload()  # уже идет
    gate.set()
    for _ in range(100):
        if holder.loaded:
            break
        threading.Event().wait(0.05)
    assert holder.status() == ModelStatus("ready", device="cpu")


def test_texts_name_model_and_device() -> None:
    ready = ner_text(ModelStatus("ready", device="gpu"))
    assert "Модель NER готова" in ready and model_asset.MODEL_TITLE in ready and "на видеокарте" in ready
    assert "на процессоре" in ner_text(ModelStatus("loading", device="cpu"))
    assert "37%" in ner_text(ModelStatus("downloading", pct=37))
    assert "Копирование" in ner_text(ModelStatus("downloading", pct=5, importing=True))
    assert "загрузится при первом документе" in ner_text(ModelStatus("idle"))
    assert "не нужна" in ner_text(ModelStatus("idle"), convert_only=True)
    assert "около 580 МБ" in ner_text(ModelStatus("missing"))


def test_bar_buttons_follow_state(qtbot) -> None:
    bar = ModelStatusBar()
    qtbot.addWidget(bar)
    bar.show()
    bar.set_status(ModelStatus("missing"), ocr_present=False)
    assert bar.download_button.isVisible() and bar.file_button.isVisible()
    assert "скачается при первом скане" in bar.ocr_label.text()
    bar.set_status(ModelStatus("downloading", pct=10), ocr_present=True)
    assert bar.cancel_button.isVisible() and not bar.download_button.isVisible()
    assert "скачана" in bar.ocr_label.text()
    bar.set_status(ModelStatus("idle"), ocr_present=True)
    assert bar.load_button.isVisible()
    bar.set_status(ModelStatus("idle"), ocr_present=True, convert_only=True)
    assert not bar.load_button.isVisible()
    bar.set_status(ModelStatus("error", error="x"), ocr_present=True)
    assert bar.retry_button.isVisible()
    bar.set_status(ModelStatus("ready", device="cpu"), ocr_present=True)
    assert not any(b.isVisible() for b in (
        bar.download_button, bar.file_button, bar.cancel_button, bar.load_button, bar.retry_button,
    ))


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    from md_blinder import jobs
    from md_blinder.storage.history import HistoryStore
    from md_blinder.ui.main_window import MainWindow

    monkeypatch.setenv("MD_BLINDER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(jobs, "MODEL", ModelHolder())
    w = MainWindow(HistoryStore(tmp_path / "h.sqlite"))
    w.settings.clear()
    w.start.convert_only_box.setChecked(False)
    qtbot.addWidget(w)
    w.show()
    yield w
    w.close()


def test_start_screen_shows_model_status_instead_of_notes(qtbot, window) -> None:
    texts = " ".join(label.text() for label in window.start.findChildren(QLabel))
    assert "Документы не покидают компьютер" not in texts
    assert "Первый запуск дольше" not in texts
    bar = window.start.model_status
    assert bar.isVisible()
    assert "Модель NER не скачана" in bar.ner_label.text()
    assert bar.download_button.isVisible()


def test_download_button_downloads_and_loads_model(qtbot, window, monkeypatch) -> None:
    files = FakeFiles(monkeypatch)
    monkeypatch.setattr(engine, "load_model", lambda directory, on_progress=None, cancel=None: fake_model("gpu"))
    bar = window.start.model_status
    bar.download_button.click()
    qtbot.waitUntil(lambda: "Модель NER готова" in bar.ner_label.text(), timeout=5000)
    assert files.downloads == 1
    assert "на видеокарте" in bar.ner_label.text()
    assert not bar.download_button.isVisible()


def test_cancel_download_returns_to_missing(qtbot, window, monkeypatch) -> None:
    files = FakeFiles(monkeypatch)
    files.gate = threading.Event()
    bar = window.start.model_status
    bar.download_button.click()
    qtbot.waitUntil(lambda: bar.cancel_button.isVisible(), timeout=5000)
    bar.cancel_button.click()
    qtbot.waitUntil(lambda: bar.download_button.isVisible(), timeout=5000)
    assert "Модель NER не скачана" in bar.ner_label.text()


def test_preload_skipped_in_convert_only(qtbot, window, monkeypatch) -> None:
    FakeFiles(monkeypatch, present=True)
    calls = []
    monkeypatch.setattr(model_holder.ModelHolder, "preload", lambda self: calls.append(1))
    window.start.convert_only_box.setChecked(True)
    window.preload_model()
    assert calls == []
    window.start.convert_only_box.setChecked(False)  # выключение режима загружает модель
    assert calls == [1]
