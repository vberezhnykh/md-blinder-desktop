"""Фоновая обработка файла: конвертация, OCR, NER, похожие упоминания.

Задача идет в отдельном потоке. Когда нужен выбор пользователя (скачать языки
OCR или модель NER), поток отправляет сигнал ask и ждет ответа из главного
потока. Отмена через CancelToken прерывает и ожидание ответа.

Загруженная модель живет в ModelHolder и переиспользуется следующими задачами:
разворачивание графа занимает десятки секунд.
"""

from __future__ import annotations

import queue
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from PySide6.QtCore import QObject, QThread, Signal

from .anonymize.batch import BatchMember, reconcile_batch_placeholders, seed_from_batch
from .anonymize.pipeline import anonymize_markdown
from .anonymize.related import find_related_occurrences
from .anonymize.types import Entity
from .cancel import CancelToken, Cancelled, check
from .components import model_asset, tessdata
from .convert.docx import docx_to_markdown
from .convert.file_type import detect_file_kind
from .convert.ocr import OcrProgress, ocr_pdf_pages
from .convert.pdf import analyze_pdf, assemble_markdown
from .convert.xlsx import xlsx_to_markdown
from .paths import tessdata_dir
from .ui.progress_label import UiProgress

# Ответы на вопросы задачи.
DOWNLOAD = "download"
SKIP = "skip"


@dataclass
class ProcessingResult:
    path: Path
    format: str  # docx | xlsx | pdf
    markdown: str
    entities: list[Entity]
    ner_failed: bool = False
    ner_error: Optional[str] = None
    ner_skipped: bool = False
    ocr_skipped_pages: list[int] = field(default_factory=list)
    force_ocr: bool = False


class UserFacingError(Exception):
    """Ошибка с готовым текстом для пользователя."""


class ModelHolder:
    """Одна загруженная модель на все задачи."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model: Any = None

    def get(self, on_progress=None) -> Any:
        with self._lock:
            if self._model is None:
                from .ner.engine import load_model

                self._model = load_model(model_asset.model_dir(), on_progress)
            return self._model

    @property
    def loaded(self) -> bool:
        return self._model is not None


MODEL = ModelHolder()


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


class ProcessingJob(QObject):
    progress = Signal(object)  # UiProgress
    ask = Signal(str, object)  # вид вопроса, данные
    finished = Signal(object)  # ProcessingResult
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self, path: Path, force_ocr: bool = False, batch_members: Sequence[BatchMember] = (),
        skipped: Optional[set[str]] = None,
    ) -> None:
        super().__init__()
        self.path = Path(path)
        self.force_ocr = force_ocr
        # Остальные документы пакета: по ним размечаются уже подтвержденные
        # значения и согласуются метки.
        self.batch_members = list(batch_members)
        # Вопросы, на которые пользователь уже ответил «без скачивания» для
        # предыдущего файла пакета: второй раз они не задаются.
        self.skipped = skipped if skipped is not None else set()
        self.cancel_token = CancelToken()
        self._answers: "queue.Queue[str]" = queue.Queue()

    # --- вызывается из главного потока ---

    def answer(self, value: str) -> None:
        self._answers.put(value)

    def cancel(self) -> None:
        self.cancel_token.cancel()
        self._answers.put(SKIP)

    # --- поток задачи ---

    def _ask(self, kind: str, payload: Any = None) -> str:
        if kind in self.skipped:
            return SKIP
        self.ask.emit(kind, payload)
        while True:
            check(self.cancel_token)
            try:
                value = self._answers.get(timeout=0.2)
            except queue.Empty:
                continue
            check(self.cancel_token)
            if value == SKIP:
                self.skipped.add(kind)
            return value

    def _emit(self, **kwargs: Any) -> None:
        if not self.cancel_token.cancelled:
            self.progress.emit(UiProgress(**kwargs))

    def run(self) -> None:
        try:
            result = self._run()
        except Cancelled:
            self.cancelled.emit()
        except UserFacingError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Не удалось сконвертировать файл: {exc}")
        else:
            if self.cancel_token.cancelled:
                self.cancelled.emit()
            else:
                self.finished.emit(result)

    def _run(self) -> ProcessingResult:
        cancel = self.cancel_token
        name = self.path.name
        kind = detect_file_kind(self.path)
        if kind == "legacy-doc":
            raise UserFacingError(
                f'Формат .doc не поддерживается. Откройте файл "{name}" в Word и сохраните как .docx, '
                "затем загрузите заново."
            )
        if kind == "legacy-xls":
            raise UserFacingError(
                f'Формат .xls не поддерживается. Откройте файл "{name}" в Excel и сохраните как .xlsx, '
                "затем загрузите заново."
            )
        if kind == "unknown":
            raise UserFacingError(
                f'Файл "{name}" не поддерживается. Поддерживаются .docx, .xlsx и .pdf (включая сканы).'
            )

        ocr_skipped: list[int] = []
        if kind == "docx":
            md = docx_to_markdown(self.path)
        elif kind == "xlsx":
            try:
                md = xlsx_to_markdown(self.path)
            except zipfile.BadZipFile:
                raise UserFacingError(
                    f'Файл "{name}" не открылся как книга Excel. Если он защищен паролем, '
                    "снимите защиту в Excel и загрузите заново."
                ) from None
            if not md.strip():
                raise UserFacingError(f'В файле "{name}" нет заполненных ячеек.')
        else:
            analysis = analyze_pdf(
                self.path, force_ocr=self.force_ocr,
                on_progress=lambda page, total: self._emit(phase="pdf", page=page, total=total), cancel=cancel,
            )
            recognized: dict[int, str] = {}
            pages = analysis.ocr_page_numbers
            if pages:
                if not tessdata.is_tessdata_present() and self._ask("ocr-download", len(pages)) == DOWNLOAD:
                    tessdata.download_tessdata(
                        on_progress=lambda got, total: self._emit(phase="ocr-download", pct=int(got * 100 / max(1, total))),
                        cancel=cancel,
                    )
                if tessdata.is_tessdata_present():
                    recognized = ocr_pdf_pages(
                        self.path, pages, tessdata_dir(),
                        on_progress=lambda p: self._emit(phase="ocr", page=p.page, total=p.total), cancel=cancel,
                    )
                else:
                    ocr_skipped = pages
            md = assemble_markdown(analysis, recognized)
        check(cancel)
        md = _normalize_newlines(md)

        ner_detect = None
        ner_skipped = False
        if MODEL.loaded or model_asset.is_model_present():
            ner_detect = self._ner_detect
        else:
            choice = self._ask("ner-model")
            if choice == DOWNLOAD:
                ner_detect = self._download_then_detect
            elif choice.startswith("file:"):
                model_asset.import_model_file(choice[5:], cancel=cancel)
                if not model_asset.is_model_present():
                    # Остальные файлы модели небольшие, докачиваем их.
                    ner_detect = self._download_then_detect
                else:
                    ner_detect = self._ner_detect
            else:
                ner_skipped = True

        self._emit(phase="regex")
        result = anonymize_markdown(md, ner_detect, cancel)
        check(cancel)
        self._emit(phase="related")
        seeded = seed_from_batch(md, result.entities, self.batch_members)
        related = find_related_occurrences(md, seeded)
        entities = reconcile_batch_placeholders(seeded + related, md, self.batch_members)
        return ProcessingResult(
            path=self.path, format=kind, markdown=md, entities=entities,
            ner_failed=result.ner_failed, ner_error=result.ner_error, ner_skipped=ner_skipped,
            ocr_skipped_pages=ocr_skipped, force_ocr=self.force_ocr,
        )

    def _download_then_detect(self, md: str):
        model_asset.download_model(
            on_progress=lambda got, total: self._emit(phase="ner-download", pct=int(got * 100 / max(1, total))),
            cancel=self.cancel_token,
        )
        return self._ner_detect(md)

    def _ner_detect(self, md: str):
        from .ner.engine import detect_entities_ner

        model = MODEL.get(self._on_ner_progress)
        check(self.cancel_token)
        return detect_entities_ner(md, model, on_progress=self._on_ner_progress, cancel=self.cancel_token)

    def _on_ner_progress(self, p: Any) -> None:
        stage = getattr(p, "stage", None) or getattr(p, "phase", None) or "detect"
        phase = stage if str(stage).startswith("ner-") else f"ner-{stage}"
        if phase not in ("ner-download", "ner-init", "ner-warmup", "ner-detect"):
            phase = "ner-detect"
        self._emit(
            phase=phase,
            chunk=getattr(p, "chunk", None), chunks=getattr(p, "chunks", None),
            batch=getattr(p, "batch", None), batches=getattr(p, "batches", None),
            pct=getattr(p, "pct", None), ep=getattr(p, "ep", None),
        )


def start_job(job: ProcessingJob) -> QThread:
    # Сборщик мусора в рабочем потоке выключен (ui/gc_guard.py): иначе он мог бы
    # удалить здесь Qt-объекты интерфейса, а это падение на Windows.
    thread = QThread()
    job.moveToThread(thread)
    thread.started.connect(job.run)
    for signal in (job.finished, job.failed, job.cancelled):
        signal.connect(thread.quit)
    thread.start()
    return thread
