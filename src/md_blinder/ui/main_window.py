"""Главное окно (порт App.tsx)."""

from __future__ import annotations

import datetime as dt
import os
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings, QStandardPaths, Qt, QThread, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..anonymize.apply import (
    accept_all_related,
    accept_related_candidate,
    add_entity_from_selection,
    add_occurrence_to_entity,
    build_anonymized_text_with_map,
    change_entity_type,
    compact_placeholder_numbers,
    dedupe_entity_ids,
    merge_entities,
    pending_candidates,
    restore_candidate,
)
from ..anonymize.csv_export import csv_bytes
from ..anonymize.image_safety import find_image_ranges
from ..anonymize.placeholder_guard import has_placeholder_collision
from ..jobs import DOWNLOAD, SKIP, ProcessingJob, ProcessingResult, start_job
from .. import shell
from ..paths import history_db_path
from ..storage.history import HistoryDoc, HistoryStore, merge_docs, now_ms, parse_history, serialize_history
from ..editing import replace_position
from ..text_utils import plural
from . import theme as t
from .autosave import Autosave
from .icons import icon, pixmap
from .progress_label import UiProgress, status_hint, status_label
from .result_preview import ResultPane
from .session import DocumentSession
from .side_panel import SidePanel
from .source_editor import EditorPane
from .start_screen import StartScreen, dropped_file
from .theme import add_shadow
from .update_ui import UpdateBar, UpdateController, version_footer
from .widgets import Notice, PopupMenu, StatusStrip, Toast

SETTING_EXPLORER_MENU = "explorerMenu"

WIDE_WINDOW = 1280
DELETE_UNDO_MS = 8000


def base_name(name: str) -> str:
    stem = Path(name).stem
    return stem or "document"


def shorten(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


class TopBar(QFrame):
    """Верхняя панель экрана документа (TopBar.tsx)."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("topbar")
        self.setAcceptDrops(True)
        add_shadow(self)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 10, 8)
        row.setSpacing(14)
        brand = QHBoxLayout()
        brand.setSpacing(8)
        mark = QLabel()
        mark.setPixmap(pixmap("mark", t.INK, 20))
        brand.addWidget(mark)
        name = QLabel("MD Blinder")
        name.setStyleSheet("font-size: 15px; font-weight: 600; background: transparent;")
        brand.addWidget(name)
        row.addLayout(brand)
        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedSize(1, 22)
        row.addWidget(divider)
        self.file_label = QLabel()
        self.file_label.setStyleSheet("font-size: 15px; font-weight: 500; background: transparent;")
        self.file_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        row.addWidget(self.file_label)
        self.save_icon = QLabel()
        self.save_icon.setFixedSize(14, 14)
        self.save_label = QLabel()
        save = QHBoxLayout()
        save.setSpacing(5)
        save.addWidget(self.save_icon)
        save.addWidget(self.save_label)
        row.addLayout(save)
        row.addStretch(1)
        self.home_button = QPushButton("На главную")
        self.home_button.setIcon(icon("home", t.INK, 16, t.ACCENT))
        self.home_button.setToolTip("Стартовый экран со всеми документами")
        self.new_button = QPushButton("Новый файл")
        self.new_button.setIcon(icon("upload", t.INK, 16, t.ACCENT))
        self.new_button.setToolTip("Или перетащите файл на эту панель")
        self.history_button = QPushButton()
        hrow = QHBoxLayout(self.history_button)
        hrow.setContentsMargins(13, 0, 11, 0)
        hrow.setSpacing(7)
        for widget in (QLabel(), QLabel("История"), QLabel()):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            widget.setStyleSheet("background: transparent; color: inherit; font-size: 14px; font-weight: 500;")
            hrow.addWidget(widget)
        hrow.itemAt(0).widget().setPixmap(pixmap("clock", t.INK, 16))
        hrow.itemAt(2).widget().setPixmap(pixmap("chevron-down", t.INK_3, 14))
        self.history_button.setMinimumWidth(130)
        self.history_button.setMinimumHeight(self.new_button.sizeHint().height())
        row.addWidget(self.home_button)
        row.addWidget(self.new_button)
        row.addWidget(self.history_button)
        self.history_menu = PopupMenu(self, 340)
        self.set_save_state("saved")

    def set_save_state(self, state: str) -> None:
        if state == "error":
            self.save_icon.setPixmap(pixmap("alert", t.DANGER, 14))
            self.save_icon.show()
            text, color = "Ошибка сохранения", t.DANGER
        elif state == "saving":
            self.save_icon.hide()
            text, color = "Сохранение…", t.INK_3
        else:
            self.save_icon.setPixmap(pixmap("check", t.GOOD, 14))
            self.save_icon.show()
            text, color = "Сохранено на компьютере", t.GOOD
        self.save_label.setText(text)
        self.save_label.setStyleSheet(f"font-size: 12.5px; color: {color}; background: transparent;")


class DocumentPage(QWidget):
    """Экран документа: верхняя панель, статус, уведомления, исходник, результат и боковая панель."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(0)
        self.topbar = TopBar()
        self.home_button = self.topbar.home_button
        self.new_button = self.topbar.new_button
        self.history_button = self.topbar.history_button
        self.history_menu = self.topbar.history_menu
        self.file_label = self.topbar.file_label
        layout.addWidget(self.topbar)
        layout.addSpacing(16)

        self.save_error = Notice(error=True)
        self.retry_save = self.save_error.add_button("Повторить сохранение", lambda: None)
        self.save_error.hide()
        layout.addWidget(self.save_error)

        self.status = StatusStrip()
        layout.addWidget(self.status)

        self.notices = QVBoxLayout()
        self.notices.setContentsMargins(0, 0, 0, 0)
        self.notices.setSpacing(0)
        layout.addLayout(self.notices)
        self.ocr_retry = Notice(plain=True)
        self.ocr_retry.label.setText("Текст выглядит битым или с ошибками?")
        self.ocr_retry.layout().setStretch(0, 0)
        self.ocr_retry_button = self.ocr_retry.add_button("Распознать заново через OCR", lambda: None, link=True)
        retry_hint = QLabel("Все страницы PDF будут распознаны как изображения. Прежняя версия останется в истории.")
        retry_hint.setObjectName("muted")
        self.ocr_retry.layout().addWidget(retry_hint, 1)
        self.ner_notice = Notice()
        self.ocr_notice = Notice()
        self.image_notice = Notice()
        self.collision_notice = Notice(
            "В документе уже есть текст вида [PERSON_1], похожий на метки замены. В итоговом тексте "
            "такие фрагменты экранируются, чтобы их нельзя было спутать."
        )
        for notice in (self.ocr_retry, self.ner_notice, self.ocr_notice, self.image_notice, self.collision_notice):
            slot = QWidget()
            wrap = QVBoxLayout(slot)
            wrap.setContentsMargins(0, 12, 0, 0)
            wrap.addWidget(notice)
            notice.slot = slot
            notice.hide()
            self.notices.addWidget(slot)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 16, 0, 6)
        toolbar.setSpacing(12)
        self.view_box = QFrame()
        self.view_box.setObjectName("viewToggleBox")
        view_row = QHBoxLayout(self.view_box)
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(0)
        self.source_button = QPushButton("Исходник")
        self.result_button = QPushButton("Результат")
        for b in (self.source_button, self.result_button):
            b.setObjectName("viewToggle")
            b.setCheckable(True)
            view_row.addWidget(b)
        toolbar.addWidget(self.view_box)
        self.side_hint = QLabel("Правки в исходнике сразу видны в результате справа")
        self.side_hint.setObjectName("muted")
        toolbar.addWidget(self.side_hint)
        toolbar.addStretch(1)
        self.side_by_side_box = QCheckBox("Результат рядом")
        self.side_by_side_box.setStyleSheet("font-size: 13.5px;")
        toolbar.addWidget(self.side_by_side_box)
        layout.addLayout(toolbar)

        work = QHBoxLayout()
        work.setContentsMargins(0, 6, 0, 0)
        work.setSpacing(16)
        self.editor_pane = EditorPane()
        self.result_pane = ResultPane()
        self.editor = self.editor_pane.editor
        self.preview = self.result_pane.preview
        work.addWidget(self.editor_pane, 1)
        work.addWidget(self.result_pane, 1)
        self.side = SidePanel()
        self.side.setFixedWidth(440)
        work.addWidget(self.side)
        layout.addLayout(work, 1)


class MainWindow(QMainWindow):
    def __init__(self, store: Optional[HistoryStore] = None) -> None:
        super().__init__()
        self.setWindowTitle("MD Blinder")
        self.resize(1360, 860)
        self.setAcceptDrops(True)
        self.settings = QSettings("md-blinder", "desktop")
        self.store = store or HistoryStore(history_db_path())
        self.autosave = Autosave(self.store, self)
        self.session = DocumentSession(on_dirty=self.autosave.queue)

        self.job: Optional[ProcessingJob] = None
        self.thread: Optional[QThread] = None
        self._threads: list[QThread] = []  # QThread нельзя удалять, пока поток не завершился
        self.job_started = 0.0
        self.progress: Optional[UiProgress] = None
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self._update_loading)

        self.last_pdf: Optional[tuple[Path, str, bool]] = None  # путь, id документа, OCR принудительно
        self.view = "source"
        self.pending_delete: Optional[str] = None
        self.delete_timer = QTimer(self)
        self.delete_timer.setSingleShot(True)
        self.delete_timer.timeout.connect(self._commit_delete)
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(150)
        self.render_timer.timeout.connect(self._render_derived)
        self.anonymized_text = ""
        self._rendered_text: Optional[str] = None

        self.stack = QStackedWidget()
        self.start = StartScreen()
        self.page = DocumentPage()
        self.stack.addWidget(self.start)
        self.stack.addWidget(self.page)
        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.update_bar = UpdateBar()
        column.addWidget(self.update_bar)
        column.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.toast = Toast(self.stack)
        self.updates = UpdateController(self.update_bar, self.settings, self)
        self.updates.toast.connect(lambda text, tone: self.toast.show_message(text, 5000, tone=tone))
        self.updates.restart_requested.connect(self._restart_for_update)
        footer = version_footer(self.updates)
        if shell.available():
            footer.layout().insertWidget(footer.layout().count() - 1, self._explorer_menu_box())
            self._sync_explorer_menu(self.settings.value(SETTING_EXPLORER_MENU, True, type=bool))
        self.start.add_footer(footer)

        self._wire()
        self._refresh_history()

    # ------------------------------------------------------------------ связи

    def _wire(self) -> None:
        s, p = self.start, self.page
        s.pick_file.connect(self.pick_file)
        s.file_dropped.connect(lambda path: self.process_file(Path(path)))
        s.loading.cancel.connect(self.cancel_job)
        s.history.open_doc.connect(self.open_doc)
        s.history.delete_doc.connect(self.request_delete)
        s.history.export_history.connect(self.export_history)
        s.history.import_history.connect(self.import_history)

        p.home_button.clicked.connect(self.show_all_docs)
        p.new_button.clicked.connect(self.pick_file)
        p.retry_save.clicked.connect(self.autosave.flush)
        p.ocr_retry_button.clicked.connect(self.retry_with_ocr)
        p.source_button.clicked.connect(lambda: self.set_view("source"))
        p.result_button.clicked.connect(lambda: self.set_view("result"))
        p.side_by_side_box.setChecked(self.settings.value("sideBySide", True, type=bool))
        p.side_by_side_box.toggled.connect(self._side_by_side_changed)
        p.history_button.clicked.connect(self._open_history_menu)

        p.status.show_candidates.connect(self.show_candidates)
        p.status.export_md.connect(self.export_md)
        p.status.copy_text.connect(self.copy_text)
        p.status.export_csv.connect(self.export_csv)

        p.editor.edited.connect(self.session.editor_changed)
        p.editor.undo_requested.connect(self.session.undo_stack.undo)
        p.editor.redo_requested.connect(self.session.undo_stack.redo)
        p.editor.add_requested.connect(self._add_entity)
        p.editor.attach_requested.connect(self._attach_entity)
        p.editor.entity_clicked.connect(self._entity_clicked)
        p.editor.merge_requested.connect(
            lambda a, b: self._update(lambda es: compact_placeholder_numbers(merge_entities(es, a, b)), "Объединить")
        )
        p.editor.span_changed.connect(self._span_changed)
        p.editor.hovered.connect(p.side.found.set_highlighted)
        p.side.found.row_hovered.connect(p.editor.set_outlined)

        review, found = p.side.review, p.side.found
        review.activate.connect(self.activate_candidate)
        review.accept.connect(lambda i: self._update(lambda es: compact_placeholder_numbers(accept_related_candidate(es, i)), "Принять кандидата"))
        review.reject.connect(self._reject)
        review.accept_all_related.connect(lambda i: self._update(lambda es: compact_placeholder_numbers(accept_all_related(es, i)), "Принять похожие"))
        review.restore.connect(lambda i: self._update(lambda es: restore_candidate(es, i), "Вернуть кандидата"))
        review.change_type.connect(self._change_type)
        review.export.connect(self.export_md)
        found.set_anonymize.connect(self._set_anonymize)
        found.select.connect(self.select_entity)
        found.change_type.connect(self._change_type)
        found.merge.connect(lambda a, b: self._update(lambda es: compact_placeholder_numbers(merge_entities(es, a, b)), "Объединить"))
        found.delete.connect(lambda i: self._update(lambda es: compact_placeholder_numbers([e for e in es if e.id != i]), "Удалить замену"))

        self.session.doc_replaced.connect(self._doc_replaced)
        self.session.text_changed.connect(p.editor.apply_change)
        self.session.entities_changed.connect(self._entities_changed)
        self.autosave.state_changed.connect(self._save_state_changed)
        self.autosave.saved.connect(self._refresh_history)

        undo = QAction("Отменить", self)
        undo.setShortcut(QKeySequence.StandardKey.Undo)
        undo.triggered.connect(self.session.undo_stack.undo)
        redo = QAction("Повторить", self)
        redo.setShortcut(QKeySequence.StandardKey.Redo)
        redo.triggered.connect(self.session.undo_stack.redo)
        open_action = QAction("Открыть файл", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.pick_file)
        for action in (undo, redo, open_action):
            self.addAction(action)

    # ------------------------------------------------------------------ обработка файла

    def pick_file(self) -> None:
        if self.job is not None:
            return
        start_dir = self.settings.value("lastDir", QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation))
        path, _ = QFileDialog.getOpenFileName(self, "Выберите документ", start_dir, "Документы (*.docx *.pdf);;Все файлы (*)")
        if path:
            self.settings.setValue("lastDir", str(Path(path).parent))
            self.process_file(Path(path))

    def process_file(self, path: Path, force_ocr: bool = False) -> None:
        if self.job is not None:
            return
        if not self.autosave.flush():
            self._show_save_error()
        self.session.replace_doc(None)
        self.stack.setCurrentWidget(self.start)
        self.start.set_error("")
        self.start.set_file_name(path.name)
        self.start.dropzone.set_busy(True)
        self.progress = None
        self.job = ProcessingJob(path, force_ocr)
        self.job.progress.connect(self._job_progress)
        self.job.ask.connect(self._job_ask)
        self.job.finished.connect(self._job_finished)
        self.job.failed.connect(self._job_failed)
        self.job.cancelled.connect(self._job_cancelled)
        self.job_started = time.monotonic()
        self.start.loading.show()
        self._update_loading()
        self.elapsed_timer.start()
        self.thread = start_job(self.job)
        thread = self.thread
        self._threads.append(thread)
        thread.finished.connect(lambda: self._threads.remove(thread) if thread in self._threads else None)

    def cancel_job(self) -> None:
        if self.job is not None:
            self.job.cancel()

    def _job_done(self) -> None:
        self.elapsed_timer.stop()
        self.start.loading.hide()
        self.start.dropzone.set_busy(False)
        self.job = None

    def _job_progress(self, progress: UiProgress) -> None:
        self.progress = progress
        self._update_loading()

    def _update_loading(self) -> None:
        elapsed = time.monotonic() - self.job_started
        self.start.loading.set_status(status_label(self.progress, elapsed), status_hint(self.progress))

    def _job_ask(self, kind: str, payload) -> None:
        job = self.job
        if job is None:
            return
        if kind == "ocr-download":
            pages = int(payload or 0)
            box = QMessageBox(self)
            box.setWindowTitle("Распознавание сканов")
            box.setText(
                f"В документе {plural(pages, ('страница-скан', 'страницы-скана', 'страниц-сканов'))}. "
                "Для распознавания нужно один раз скачать языковые файлы OCR (русский, украинский, английский), "
                "около 8 МБ."
            )
            box.setInformativeText("Это публичные файлы; текст документа никуда не отправляется.")
            download = box.addButton("Скачать", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Продолжить без OCR", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            job.answer(DOWNLOAD if box.clickedButton() is download else SKIP)
        elif kind == "ner-model":
            box = QMessageBox(self)
            box.setWindowTitle("Модель распознавания имен")
            box.setText(
                "Для поиска имен, компаний и адресов нужна модель распознавания (около 580 МБ). "
                "Она скачивается один раз с Hugging Face и хранится на этом компьютере."
            )
            box.setInformativeText(
                "Если сайт закрыт в вашей сети, укажите заранее скачанный файл model_fp16.onnx. "
                "Без модели будут найдены только данные по шаблонам: телефоны, email, реквизиты, номера документов."
            )
            download = box.addButton("Скачать", QMessageBox.ButtonRole.AcceptRole)
            choose = box.addButton("Указать файл…", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Продолжить без модели", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is download:
                job.answer(DOWNLOAD)
            elif clicked is choose:
                path, _ = QFileDialog.getOpenFileName(self, "Файл модели", "", "ONNX (*.onnx);;Все файлы (*)")
                job.answer(f"file:{path}" if path else SKIP)
            else:
                job.answer(SKIP)

    def _job_failed(self, message: str) -> None:
        self._job_done()
        self.start.set_error(message)

    def _job_cancelled(self) -> None:
        self._job_done()
        self.start.set_file_name("")

    def _job_finished(self, result: ProcessingResult) -> None:
        self._job_done()
        now = now_ms()
        doc = HistoryDoc(
            id=str(uuid.uuid4()), name=result.path.name, format=result.format, created_at=now, updated_at=now,
            source_text=result.markdown, entities=result.entities,
        )
        self.last_pdf = (result.path, doc.id, result.force_ocr) if result.format == "pdf" else None
        self.page.side.user_tab = None
        self.page.side.review.active_id = None
        self.session.replace_doc(doc, dirty=True)
        self._set_ner_notice(result)
        if result.ocr_skipped_pages:
            pages = result.ocr_skipped_pages
            self.page.ocr_notice.label.setText(
                f"Страницы-сканы не распознаны ({', '.join(map(str, pages[:10]))}{'…' if len(pages) > 10 else ''}): "
                "языковые файлы OCR не скачаны. Текст этих страниц в результат не попал."
            )
            self.page.ocr_notice.show()
        else:
            self.page.ocr_notice.hide()
        self.set_view("source")
        self.stack.setCurrentWidget(self.page)

    def _set_ner_notice(self, result: Optional[ProcessingResult]) -> None:
        notice = self.page.ner_notice
        if result is not None and result.ner_failed:
            notice.label.setText(
                "Модель распознавания имен, компаний и адресов не загрузилась. Найдены только данные, которые "
                "определяются по шаблону: телефоны, email, реквизиты, номера документов."
                + (f"<br><span style='color:#5b6474'>Причина: {result.ner_error}</span>" if result.ner_error else "")
            )
            notice.show()
        elif result is not None and result.ner_skipped:
            notice.label.setText(
                "Поиск имен, компаний и адресов не выполнялся: модель распознавания не скачана. Найдены только "
                "данные по шаблонам. Отметьте имена вручную или откройте файл заново, чтобы скачать модель."
            )
            notice.show()
        else:
            notice.hide()

    def retry_with_ocr(self) -> None:
        if self.last_pdf is not None:
            self.process_file(self.last_pdf[0], force_ocr=True)

    # ------------------------------------------------------------------ документ

    def _doc_replaced(self) -> None:
        doc = self.session.doc
        p = self.page
        if doc is None:
            p.editor.set_content("", [])
            return
        p.file_label.setText(doc.name)
        p.editor.set_content(doc.source_text, doc.entities)
        self._render_derived()

    def _entities_changed(self) -> None:
        self.page.editor.set_entities(self.session.entities, self.page.side.review.current_id())
        # Действия с разметкой показываем сразу; при наборе текста пересчет откладывается.
        if self.session.text == self._rendered_text:
            self.render_timer.stop()
            self._render_derived()
        else:
            self.render_timer.start()

    def _render_derived(self) -> None:
        doc = self.session.doc
        if doc is None:
            return
        p = self.page
        text, entities = doc.source_text, doc.entities
        self._rendered_text = text
        anonymized = build_anonymized_text_with_map(text, entities)
        self.anonymized_text = anonymized.text
        p.preview.render(anonymized.text, text, anonymized.segments, entities)
        p.side.render(entities, text)
        p.editor.set_current_candidate(p.side.review.current_id() if p.side.currentIndex() == 0 else None)
        unreviewed = len(pending_candidates(entities))
        active = [e for e in entities if e.anonymize]
        p.status.update_state(unreviewed, len(active), sum(len(e.positions) for e in active), f"{base_name(doc.name)}.md")
        images = len(find_image_ranges(text))
        p.image_notice.label.setText(
            f"В документе есть изображения ({images}). При копировании и экспорте текста они заменяются отметкой "
            "об удалении: данные внутри изображений автоматически не обезличиваются."
        )
        p.image_notice.setVisible(images > 0)
        p.collision_notice.setVisible(has_placeholder_collision(text))
        p.ocr_retry.setVisible(
            self.last_pdf is not None and self.last_pdf[1] == doc.id and not self.last_pdf[2] and self.job is None
        )
        self._apply_layout()

    def _update(self, fn, label: str) -> None:
        self.session.update_entities(fn, label)

    def _reject(self, entity_id: str) -> None:
        self._update(
            lambda es: [replace(e, dismissed=True, candidate=False, anonymize=False) if e.id == entity_id else e for e in es],
            "Отклонить кандидата",
        )

    def _change_type(self, entity_id: str, type_: str) -> None:
        self._update(lambda es: change_entity_type(es, entity_id, type_), "Сменить тип")

    def _set_anonymize(self, ids: list, value: bool) -> None:
        selected = set(ids)
        self._update(lambda es: [replace(e, anonymize=value) if e.id in selected else e for e in es], "Переключить замену")

    def _add_entity(self, start: int, end: int, type_: str) -> None:
        text = self.session.text
        self.page.editor.selected_type = type_
        self._update(lambda es: compact_placeholder_numbers(add_entity_from_selection(es, text, start, end, type_)), "Добавить замену")

    def _attach_entity(self, start: int, end: int, entity_id: str) -> None:
        text = self.session.text
        self._update(lambda es: add_occurrence_to_entity(es, text, start, end, entity_id), "Добавить вхождение")

    def _span_changed(self, entity_id: str, old, new) -> None:
        self._update(lambda es: replace_position(es, entity_id, old, new), "Изменить границы")

    def _entity_clicked(self, entity_id: str) -> None:
        entity = next((e for e in self.session.entities if e.id == entity_id), None)
        if entity is None:
            return
        if entity.candidate:
            self.activate_candidate(entity_id)
        else:
            self.page.side.user_tab = 1
            self.page.side.setCurrentIndex(1)
            self.page.side.found.select_entity(entity_id)
            if entity.positions:
                self.page.preview.focus_source_pos(entity.positions[0].start)

    def select_entity(self, entity_id: str) -> None:
        entity = next((e for e in self.session.entities if e.id == entity_id), None)
        if entity is None or not entity.positions:
            return
        first = entity.positions[0]
        self.page.editor.reveal(first.start, first.end)
        self.page.preview.focus_source_pos(first.start)

    def activate_candidate(self, entity_id) -> None:
        side = self.page.side
        side.review.active_id = entity_id
        side.user_tab = 0
        side.setCurrentIndex(0)
        self._render_derived()
        entity = next((e for e in self.session.entities if e.id == entity_id), None)
        if entity is not None and entity.positions:
            first = entity.positions[0]
            self.page.editor.reveal(first.start, first.end)
            self.page.preview.focus_source_pos(first.start)
        side.review.focus_card()

    def show_candidates(self) -> None:
        self.set_view("source")
        self.page.side.user_tab = 0
        self.page.side.setCurrentIndex(0)
        self.page.side.review.focus_card()

    # ------------------------------------------------------------------ вид

    def set_view(self, view: str) -> None:
        self.view = view
        self._apply_layout()

    def _side_by_side_changed(self, value: bool) -> None:
        self.settings.setValue("sideBySide", value)
        self._apply_layout()

    def _apply_layout(self) -> None:
        p = self.page
        wide = self.width() >= WIDE_WINDOW
        side_by_side = wide and p.side_by_side_box.isChecked()
        p.side_by_side_box.setVisible(wide)
        p.side_hint.setVisible(side_by_side)
        p.view_box.setVisible(not side_by_side)
        p.source_button.setChecked(self.view == "source")
        p.result_button.setChecked(self.view == "result")
        p.editor_pane.setVisible(side_by_side or self.view == "source")
        p.result_pane.setVisible(side_by_side or self.view == "result")
        # В одной колонке вид уже назван переключателем, заголовок колонки не нужен.
        p.editor_pane.title.setVisible(side_by_side)
        p.result_pane.title.setVisible(side_by_side)
        p.side.setFixedWidth(360 if side_by_side else 440)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_layout()
        if self.toast.isVisible():
            self.toast._place()

    # ------------------------------------------------------------------ выдача

    def _notify_done(self, done: str) -> None:
        unreviewed = len(pending_candidates(self.session.entities))
        if unreviewed == 0:
            self.toast.show_message(done, 3000)
            return
        self.toast.show_message(
            f"{done} Не проверено: {plural(unreviewed, ('кандидат', 'кандидата', 'кандидатов'))} — "
            "в тексте они остались без замены.",
            8000, action=("Показать", self.show_candidates), tone="warn",
        )

    def _save_path(self, default_name: str, filter_: str) -> Optional[Path]:
        start_dir = self.settings.value("lastDir", QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation))
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить", str(Path(start_dir) / default_name), filter_)
        if not path:
            return None
        self.settings.setValue("lastDir", str(Path(path).parent))
        return Path(path)

    def export_md(self) -> None:
        doc = self.session.doc
        if doc is None:
            return
        name = f"{base_name(doc.name)}.md"
        path = self._save_path(name, "Markdown (*.md)")
        if path is None:
            return
        try:
            path.write_text(self.anonymized_text, encoding="utf-8", newline="\n")
        except OSError as exc:
            self.toast.show_message(f"Не удалось сохранить файл: {exc}", 6000, tone="error")
            return
        self._notify_done(f"Файл «{shorten(path.name, 40)}» сохранен.")

    def copy_text(self) -> None:
        QApplication.clipboard().setText(self.anonymized_text)
        self._notify_done("Текст скопирован.")

    def export_csv(self) -> None:
        doc = self.session.doc
        if doc is None:
            return
        path = self._save_path(f"{base_name(doc.name)}-replacements.csv", "CSV (*.csv)")
        if path is None:
            return
        try:
            path.write_bytes(csv_bytes(doc.source_text, doc.entities))
        except OSError as exc:
            self.toast.show_message(f"Не удалось сохранить файл: {exc}", 6000, tone="error")
            return
        self.toast.show_message(
            "Таблица замен сохранена. Она содержит исходные данные: не передавайте ее вместе с обезличенным текстом.",
            6000, tone="warn",
        )

    # ------------------------------------------------------------------ история

    def _refresh_history(self) -> None:
        try:
            docs = [d for d in self.store.load_metadata() if d.id != self.pending_delete]
        except Exception as exc:  # noqa: BLE001
            self.start.history.set_error(f"История недоступна: {exc}")
            return
        current = self.session.doc.id if self.session.doc else None
        self.start.history.render(docs, current)
        self.history_meta = docs

    def _open_history_menu(self) -> None:
        menu = self.page.history_menu
        menu.clear()
        current = self.session.doc.id if self.session.doc else None
        recent = getattr(self, "history_meta", [])[:5]
        if not recent:
            menu.add_note("История пуста.")
        for meta in recent:
            is_current = meta.id == current
            when = dt.datetime.fromtimestamp(meta.updated_at / 1000).strftime("%d.%m.%Y, %H:%M")
            hint = "Открыт сейчас" if is_current else (
                f"{when} · {plural(meta.entity_count, ('находка', 'находки', 'находок'))}"
            )
            menu.add_item(shorten(meta.name, 44), lambda i=meta.id: self.open_doc(i), hint, current=is_current)
        menu.add_separator()
        menu.add_note("История хранится на этом компьютере вместе с исходным текстом, без шифрования.", warn=True)
        menu.add_row([
            ("Все документы", self.show_all_docs, True),
            ("Экспорт", self.export_history, bool(recent)),
            ("Импорт", self.import_history, True),
        ])
        menu.show_under(self.page.history_button)

    def show_all_docs(self) -> None:
        if not self.autosave.flush():
            self._show_save_error()
            return
        self._refresh_history()
        self.stack.setCurrentWidget(self.start)

    def open_doc(self, doc_id: str) -> None:
        if self.job is not None:
            return
        if not self.autosave.flush():
            self._show_save_error()
            return
        doc = self.store.load_doc(doc_id)
        if doc is None:
            self.start.history.set_error("Документ не найден в истории.")
            return
        doc.entities = dedupe_entity_ids(doc.entities)
        self.page.side.user_tab = None
        self.page.side.review.active_id = None
        self.session.replace_doc(doc)
        self._set_ner_notice(None)
        self.page.ocr_notice.hide()
        self.set_view("source")
        self.stack.setCurrentWidget(self.page)
        self._refresh_history()

    def request_delete(self, doc_id: str) -> None:
        if self.pending_delete:
            self._commit_delete()
        self.pending_delete = doc_id
        self._refresh_history()
        self.toast.show_message("Документ удален из истории.", DELETE_UNDO_MS, action=("Отменить", self._undo_delete))
        self.delete_timer.start(DELETE_UNDO_MS)

    def _undo_delete(self) -> None:
        self.delete_timer.stop()
        self.pending_delete = None
        self._refresh_history()

    def _commit_delete(self) -> None:
        doc_id = self.pending_delete
        if not doc_id:
            return
        self.pending_delete = None
        self.delete_timer.stop()
        self.autosave.discard(doc_id)
        if self.session.doc and self.session.doc.id == doc_id:
            self.session.replace_doc(None)
        try:
            self.store.delete_doc(doc_id)
        except Exception as exc:  # noqa: BLE001
            self.start.history.set_error(f"Не удалось удалить документ: {exc}")
        self._refresh_history()

    def export_history(self) -> None:
        if not self.autosave.flush():
            self._show_save_error()
            return
        path = self._save_path("md-blinder-history.json", "JSON (*.json)")
        if path is None:
            return
        try:
            path.write_text(serialize_history(self.store.load_all()), encoding="utf-8")
        except OSError as exc:
            self.start.history.set_error(f"Не удалось экспортировать историю: {exc}")
            return
        self.toast.show_message("История экспортирована. Файл содержит исходный текст документов.", 5000, tone="warn")

    def import_history(self) -> None:
        if not self.autosave.flush():
            self._show_save_error()
            return
        path, _ = QFileDialog.getOpenFileName(self, "Импорт истории", "", "JSON (*.json);;Все файлы (*)")
        if not path:
            return
        try:
            incoming = parse_history(Path(path).read_text(encoding="utf-8"))
            existing = self.store.load_all()
            merged = merge_docs(existing, incoming)
            before = {d.id: d.updated_at for d in existing}
            for doc in merged:
                if before.get(doc.id) != doc.updated_at:
                    doc.entities = dedupe_entity_ids(doc.entities)
                    self.store.put_doc(doc)
        except Exception as exc:  # noqa: BLE001
            self.start.history.set_error(str(exc) or "Не удалось импортировать историю")
            return
        self.start.history.set_error("")
        current = self.session.doc
        if current is not None:
            replacement = next((d for d in merged if d.id == current.id), None)
            if replacement is not None and replacement.updated_at > current.updated_at:
                self.session.replace_doc(replacement)
        self._refresh_history()
        self.toast.show_message(f"Импортировано документов: {len(incoming)}.", 4000)

    # ------------------------------------------------------------------ сохранение и закрытие

    def _save_state_changed(self) -> None:
        p = self.page
        if self.autosave.error:
            p.topbar.set_save_state("error")
            self._show_save_error()
        else:
            p.save_error.hide()
            p.topbar.set_save_state("saving" if self.autosave.saving else "saved")

    def _show_save_error(self) -> None:
        p = self.page
        p.save_error.label.setText(self.autosave.error or "Не удалось сохранить изменения.")
        p.save_error.show()
        self.start.history.set_error(self.autosave.error)

    def _restart_for_update(self, staged) -> None:
        from .. import updater

        if self.job is not None:
            self.toast.show_message("Дождитесь окончания обработки файла или отмените ее.", 5000, tone="warn")
            return
        if not self.autosave.flush():
            self._show_save_error()
            return
        try:
            script = updater.write_script(Path(staged), updater.install_dir(), os.getpid())
            updater.launch_script(script)
        except OSError as exc:
            self.toast.show_message(f"Не удалось запустить обновление: {exc}", 8000, tone="error")
            return
        self.close()

    def _explorer_menu_box(self) -> QCheckBox:
        box = QCheckBox("Пункт в меню проводника")
        box.setChecked(self.settings.value(SETTING_EXPLORER_MENU, True, type=bool))
        box.setToolTip(
            f"«{shell.LABEL}» в меню файлов .docx и .pdf по правой кнопке мыши. "
            "В Windows 11 — в разделе «Показать дополнительные параметры»."
        )
        box.toggled.connect(self._explorer_menu_toggled)
        return box

    def _explorer_menu_toggled(self, enabled: bool) -> None:
        self.settings.setValue(SETTING_EXPLORER_MENU, enabled)
        self._sync_explorer_menu(enabled)

    def _sync_explorer_menu(self, enabled: bool) -> None:
        error = shell.sync(enabled)
        if error:
            self.toast.show_message(error, 8000, tone="error")

    def open_external(self, path: str) -> None:
        """Файл из меню проводника или из повторного запуска программы."""
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        if not path:
            return
        if self.job is not None:
            self.toast.show_message(
                f"Сейчас обрабатывается другой файл. Откройте «{Path(path).name}» после окончания обработки.",
                8000, tone="warn",
            )
            return
        self.process_file(Path(path))

    def show_startup_notice(self, argv: list[str]) -> None:
        from .. import __version__

        if "--updated" in argv:
            self.toast.show_message(f"Программа обновлена до версии {__version__}.", 5000)
        elif "--update-failed" in argv:
            self.toast.show_message(
                "Не удалось заменить файлы программы, осталась прежняя версия. Попробуйте обновить еще раз.",
                10000, tone="error",
            )

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self.job is None and dropped_file(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        path = dropped_file(event)
        if self.job is None and path:
            self.process_file(Path(path))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.job is not None:
            self.job.cancel()
        self._commit_delete()
        if not self.autosave.flush():
            answer = QMessageBox.question(
                self, "Изменения не сохранены",
                f"{self.autosave.error}\n\nЗакрыть программу и потерять несохраненные изменения?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        for thread in list(self._threads):
            thread.quit()
            thread.wait(5000)
        self.store.close()
        event.accept()
