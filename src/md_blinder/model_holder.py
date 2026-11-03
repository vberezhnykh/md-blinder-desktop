"""Одна модель NER на всю программу и ее состояние для индикатора.

Модель скачивается и разворачивается в фоновых потоках: по кнопке индикатора на
стартовом экране, заранее после запуска программы или в задаче обработки, если
модели еще нет. Все пути идут через ModelHolder, поэтому файл не качается дважды
одновременно, а сессия создается один раз. Второй вызывающий ждет первого и
получает его прогресс.

Слушатели состояния вызываются из того потока, где оно изменилось: интерфейс
пересылает их в главный поток сигналом Qt.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from .cancel import CancelToken, Cancelled, check
from .components import model_asset

log = logging.getLogger(__name__)

ModelState = Literal["missing", "downloading", "idle", "loading", "ready", "error"]
# missing — файлов нет; idle — файлы скачаны, сессия не создана.

_POLL_SEC = 0.2


@dataclass(frozen=True)
class ModelStatus:
    state: ModelState
    pct: Optional[int] = None  # downloading: 0..100
    importing: bool = False  # downloading: копируется файл, указанный пользователем
    device: Optional[str] = None  # loading/ready: "gpu" или "cpu"
    error: Optional[str] = None  # error, а у missing — почему не удалось скачать


Listener = Callable[[ModelStatus], None]


def _acquire(lock: threading.Lock, cancel: Optional[CancelToken]) -> None:
    """Ждет замок, пока задачу не отменили: отмена не должна ждать чужую загрузку."""
    while not lock.acquire(timeout=_POLL_SEC):
        check(cancel)


class ModelHolder:
    def __init__(self, directory: Optional[Path] = None) -> None:
        self._directory = directory
        self._guard = threading.Lock()  # поля ниже
        self._load_lock = threading.Lock()
        self._download_lock = threading.Lock()
        self._model: Any = None
        self._state: ModelState = "idle"
        self._status = ModelStatus("idle")
        self._download_error: Optional[str] = None
        self._listeners: list[Listener] = []
        # Прогресс текущей загрузки или скачивания раздается всем, кто его ждет.
        self._load_progress: list[Callable[[Any], None]] = []
        self._last_load_progress: Any = None
        self._download_progress: list[Callable[[int, int], None]] = []
        self._preloading = False

    def directory(self) -> Path:
        return self._directory or model_asset.model_dir()

    # --- состояние ---

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def downloading(self) -> bool:
        return self._download_lock.locked()

    @property
    def model(self) -> Any:
        return self._model

    def status(self) -> ModelStatus:
        with self._guard:
            status = self._status
        if status.state in ("idle", "missing"):
            # Файлы могли появиться или пропасть без нас (удалены вручную, скачаны задачей).
            if model_asset.is_model_present(self.directory()):
                return ModelStatus("idle")
            return ModelStatus("missing", error=self._download_error)
        return status

    def subscribe(self, listener: Listener) -> None:
        with self._guard:
            self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        with self._guard:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _set(self, status: ModelStatus) -> None:
        with self._guard:
            self._status = status
            listeners = list(self._listeners)
        current = self.status()
        for listener in listeners:
            try:
                listener(current)
            except Exception:  # noqa: BLE001 — слушатель из закрытого окна не должен ронять загрузку
                log.debug("[ner] слушатель состояния модели упал", exc_info=True)

    # --- скачивание ---

    def download(
        self,
        on_progress: Optional[Callable[[int, int], None]] = None,
        cancel: Optional[CancelToken] = None,
        source: Optional[str | Path] = None,
    ) -> None:
        """Докачивает файлы модели; source — файл или папка, указанные пользователем:
        их файлы копируются с проверкой хеша, недостающие докачиваются."""
        if on_progress:
            with self._guard:
                self._download_progress.append(on_progress)
        try:
            _acquire(self._download_lock, cancel)
            try:
                self._download_locked(cancel, source)
            finally:
                self._download_lock.release()
        finally:
            if on_progress:
                with self._guard:
                    self._download_progress.remove(on_progress)

    def _download_locked(self, cancel: Optional[CancelToken], source: Optional[str | Path]) -> None:
        directory = self.directory()
        if source is None and model_asset.is_model_present(directory):
            return
        last = [-1]

        def report(got: int, total: int, importing: bool = False) -> None:
            pct = min(99, int(got * 100 / max(1, total)))
            with self._guard:
                listeners = list(self._download_progress)
            for listener in listeners:
                listener(got, total)
            if pct != last[0]:
                last[0] = pct
                self._set(ModelStatus("downloading", pct=pct, importing=importing))

        self._download_error = None
        self._set(ModelStatus("downloading", pct=0, importing=source is not None))
        try:
            if source is not None:
                model_asset.import_model_file(
                    source, lambda got, total: report(got, total, True), cancel, directory,
                )
            if not model_asset.is_model_present(directory):
                model_asset.download_model(report, cancel, directory)
        except Cancelled:
            self._set(ModelStatus("idle"))
            raise
        except Exception as error:
            self._download_error = str(error)
            self._set(ModelStatus("idle"))
            raise
        self._set(ModelStatus("idle"))

    # --- загрузка ---

    def get(self, on_progress: Optional[Callable[[Any], None]] = None, cancel: Optional[CancelToken] = None) -> Any:
        """Загруженная модель; первый вызов создает сессию и проходит калибровку."""
        model = self._model
        if model is not None:
            return model
        if on_progress:
            with self._guard:
                self._load_progress.append(on_progress)
                last = self._last_load_progress
            if last is not None:
                on_progress(last)
        try:
            _acquire(self._load_lock, cancel)
            try:
                if self._model is None:
                    self._load_locked()
                return self._model
            finally:
                self._load_lock.release()
        finally:
            if on_progress:
                with self._guard:
                    self._load_progress.remove(on_progress)

    def _load_locked(self) -> None:
        from .ner.engine import load_model

        def fan_out(progress: Any) -> None:
            with self._guard:
                self._last_load_progress = progress
                listeners = list(self._load_progress)
            ep = getattr(progress, "ep", None)
            if ep and ep != self._status.device:
                self._set(ModelStatus("loading", device=ep))
            for listener in listeners:
                listener(progress)

        self._set(ModelStatus("loading"))
        try:
            model = load_model(self.directory(), fan_out)
        except BaseException as error:
            self._set(ModelStatus("error", error=str(error) or type(error).__name__))
            raise
        finally:
            with self._guard:
                self._last_load_progress = None
        self._model = model
        self._set(ModelStatus("ready", device=getattr(model, "device", None)))

    def preload(self) -> bool:
        """Загружает скачанную модель в фоне, чтобы первый документ не ждал.
        Возвращает False, если загружать нечего или загрузка уже идет."""
        with self._guard:
            if self._model is not None or self._preloading or self._status.state == "loading":
                return False
            if not model_asset.is_model_present(self.directory()):
                return False
            self._preloading = True

        def run() -> None:
            try:
                self.get()
            except Exception:  # noqa: BLE001 — причина уже в состоянии error
                log.warning("[ner] предварительная загрузка модели не удалась", exc_info=True)
            finally:
                with self._guard:
                    self._preloading = False

        threading.Thread(target=run, name="ner-preload", daemon=True).start()
        return True

    def download_then_preload(self, cancel: Optional[CancelToken] = None, source: Optional[str | Path] = None) -> None:
        """Для кнопки индикатора: скачивает (или копирует) модель в фоне и загружает ее."""

        def run() -> None:
            try:
                self.download(cancel=cancel, source=source)
            except Cancelled:
                return
            except Exception:  # noqa: BLE001 — причина уже в состоянии missing
                log.warning("[ner] модель не скачана", exc_info=True)
                return
            self.preload()

        threading.Thread(target=run, name="ner-download", daemon=True).start()


__all__ = ["ModelHolder", "ModelState", "ModelStatus"]
