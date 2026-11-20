"""Фоновая сборка обезличенного PDF (anonymize/redacted_pdf.py).

Если в PDF есть сканы, а модель распознавания текста не скачана, задача
сообщает об этом сигналом need_ocr и завершается: интерфейс спрашивает
разрешения и запускает задачу заново с download_ocr=True.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .anonymize.redacted_pdf import RedactedPdfError, RedactedPdfReport, export_redacted_pdf, page_kinds
from .anonymize.types import Entity
from .cancel import CancelToken, Cancelled
from .components import ocr_models


@dataclass
class PdfExportRequest:
    source: Path
    target: Path
    source_text: str
    entities: list[Entity] = field(default_factory=list)
    force_ocr: bool = False
    download_ocr: bool = False


class PdfExportJob(QObject):
    progress = Signal(str)  # текст для окна ожидания
    need_ocr = Signal()
    finished = Signal(object)  # RedactedPdfReport
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, request: PdfExportRequest) -> None:
        super().__init__()
        self.request = request
        self.cancel_token = CancelToken()

    def cancel(self) -> None:
        self.cancel_token.cancel()

    def run(self) -> None:
        try:
            report = self._run()
        except Cancelled:
            self.cancelled.emit()
        except RedactedPdfError as exc:
            self.failed.emit(str(exc))
        except OSError as exc:
            self.failed.emit(f"Не удалось сохранить PDF: {exc}")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Не удалось собрать PDF: {exc}")
        else:
            if report is None:
                self.need_ocr.emit()
            elif self.cancel_token.cancelled:
                self.cancelled.emit()
            else:
                self.finished.emit(report)

    def _run(self) -> RedactedPdfReport | None:
        r = self.request
        cancel = self.cancel_token
        model_dir = None
        self.progress.emit("Чтение PDF")
        kinds = page_kinds(r.source, r.force_ocr, cancel)
        if "ocr" in kinds:
            if not ocr_models.is_ocr_present():
                if not r.download_ocr:
                    return None
                ocr_models.download_ocr_models(
                    on_progress=lambda got, total: self.progress.emit(
                        f"Скачивание модели распознавания текста: {int(got * 100 / max(1, total))}%"
                    ),
                    cancel=cancel,
                )
            model_dir = ocr_models.ocr_models_dir()

        def on_progress(done: int, total: int) -> None:
            pages = max(1, total // 2)
            if done < pages:
                self.progress.emit(f"Распознавание страниц: {done + 1} из {pages}")
            else:
                self.progress.emit(f"Сборка PDF: страница {min(done - pages + 1, pages)} из {pages}")

        return export_redacted_pdf(
            r.source, r.target, r.source_text, r.entities, model_dir,
            force_ocr=r.force_ocr, on_progress=on_progress, cancel=cancel, kinds=kinds,
        )
