"""Сквозные проверки окна в режиме offscreen (без модели NER и OCR)."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from md_blinder.jobs import SKIP
from md_blinder.storage.history import HistoryStore
from md_blinder.ui.main_window import MainWindow

from fixtures import make_docx, make_xlsx, para, table

TEXT = "Договор между ООО «Альфа» и Иван Петров. Телефон +7 916 123-45-67, email ivan@example.com."


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("MD_BLINDER_DATA_DIR", str(tmp_path / "data"))
    # Модель, загруженная другим тестом, живет в общем ModelHolder; здесь окно работает без NER.
    import md_blinder.jobs as jobs

    monkeypatch.setattr(jobs, "MODEL", jobs.ModelHolder())
    store = HistoryStore(tmp_path / "h.sqlite")
    w = MainWindow(store)
    w.settings.clear()
    w.start.convert_only_box.setChecked(False)
    # Диалоги в тестах не показываем: на все вопросы отвечаем «без скачивания».
    monkeypatch.setattr(w, "_job_ask", lambda kind, payload: w.job.answer(SKIP))
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    yield w
    w.close()


def open_docx(qtbot, window, tmp_path, body):
    path = make_docx(tmp_path / "contract.docx", body)
    window.process_file(path)
    qtbot.waitUntil(lambda: window.job is None, timeout=20000)
    return path


def test_open_docx_detects_structural_data(qtbot, window, tmp_path):
    open_docx(qtbot, window, tmp_path, para(TEXT) + table([["Сторона", "Телефон"], ["Альфа", "+7 916 765-43-21"]]))
    assert window.stack.currentWidget() is window.page
    types = {e.type for e in window.session.entities}
    assert {"PHONE", "EMAIL"} <= types
    assert "ivan@example.com" not in window.anonymized_text
    assert "[EMAIL_1]" in window.anonymized_text
    assert window.page.ner_notice.isVisible()  # модель не скачана — пользователь об этом знает
    # Документ сохранился в историю.
    qtbot.waitUntil(lambda: not window.autosave.saving, timeout=3000)
    assert len(window.store.load_metadata()) == 1


def test_convert_only_skips_anonymization(qtbot, window, tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(window, "_job_ask", lambda kind, payload: (asked.append(kind), window.job.answer(SKIP)))
    window.start.convert_only_box.setChecked(True)
    assert window.settings.value("convertOnly", type=bool)
    open_docx(qtbot, window, tmp_path, para(TEXT))
    assert window.stack.currentWidget() is window.page
    assert window.session.entities == []
    assert "ivan@example.com" in window.anonymized_text
    assert "+7 916 123-45-67" in window.anonymized_text
    assert asked == []  # модель NER не предлагается скачать
    assert window.page.convert_notice.isVisible()
    assert not window.page.ner_notice.isVisible()
    # Вручную отметить данные можно, но в режиме конвертации текст выгружается как есть.
    start = window.session.text.index("Иван Петров")
    window._add_entity(start, start + len("Иван Петров"), "PERSON")
    assert "Иван Петров" in window.anonymized_text

    # Флажок на экране документа тот же: снятый, он возвращает замены.
    assert window.page.convert_only_box.isChecked()
    window.page.convert_only_box.setChecked(False)
    assert not window.start.convert_only_box.isChecked()
    assert not window.page.convert_notice.isVisible()
    assert "Иван Петров" not in window.anonymized_text
    assert "ivan@example.com" in window.anonymized_text  # поиск еще не запускался

    window.page.detect_button.click()
    qtbot.waitUntil(lambda: window.job is None, timeout=20000)
    assert window.stack.currentWidget() is window.page
    types = {e.type for e in window.session.entities}
    assert {"PHONE", "EMAIL", "PERSON"} <= types
    assert "ivan@example.com" not in window.anonymized_text
    assert "Иван Петров" not in window.anonymized_text  # ручная отметка сохранилась
    assert not window.page.detect_notice.isVisible()
    window.session.undo_stack.undo()
    assert {e.type for e in window.session.entities} == {"PERSON"}


def test_convert_only_switched_on_during_processing(qtbot, window, tmp_path, monkeypatch):
    import threading
    import time

    import md_blinder.jobs as jobs
    from md_blinder.cancel import check

    started = threading.Event()

    def slow_search(md, ner_detect, cancel):
        started.set()
        while True:  # долгий поиск, как NER на большом документе
            check(cancel)
            time.sleep(0.01)

    monkeypatch.setattr(jobs, "anonymize_markdown", slow_search)
    window.process_file(make_docx(tmp_path / "contract.docx", para(TEXT)))
    qtbot.waitUntil(started.is_set, timeout=20000)
    window.start.convert_only_box.setChecked(True)
    qtbot.waitUntil(lambda: window.job is None, timeout=20000)
    assert window.stack.currentWidget() is window.page
    assert window.session.entities == []
    assert "ivan@example.com" in window.anonymized_text
    assert window.page.convert_notice.isVisible()
    assert not window.page.side.isVisible()  # проверка находок не нужна
    assert not window.page.status.steps.isVisible()
    window.page.convert_only_box.setChecked(False)
    assert window.page.side.isVisible()


def test_convert_only_toggle_on_open_document(qtbot, window, tmp_path):
    open_docx(qtbot, window, tmp_path, para(TEXT))
    assert "ivan@example.com" not in window.anonymized_text
    found = list(window.session.entities)
    window.page.convert_only_box.setChecked(True)
    assert window.start.convert_only_box.isChecked()
    assert window.page.convert_notice.isVisible()
    assert "ivan@example.com" in window.anonymized_text
    assert window.session.entities == found  # разметка не теряется
    window.page.convert_only_box.setChecked(False)
    assert "ivan@example.com" not in window.anonymized_text
    assert not window.page.convert_notice.isVisible()
    assert not window.page.detect_notice.isVisible()


def test_manual_entity_and_undo(qtbot, window, tmp_path):
    open_docx(qtbot, window, tmp_path, para(TEXT))
    text = window.session.text
    start = text.index("Иван Петров")
    window._add_entity(start, start + len("Иван Петров"), "PERSON")
    person = next(e for e in window.session.entities if e.type == "PERSON")
    assert person.placeholder == "[PERSON_1]"
    assert "Иван Петров" not in window.anonymized_text
    window.session.undo_stack.undo()
    assert not any(e.type == "PERSON" for e in window.session.entities)


def test_editing_keeps_markup_and_undo_restores(qtbot, window, tmp_path):
    open_docx(qtbot, window, tmp_path, para(TEXT))
    editor = window.page.editor
    email = next(e for e in window.session.entities if e.type == "EMAIL")
    cursor = editor.textCursor()
    cursor.setPosition(0)
    editor.setTextCursor(cursor)
    qtbot.keyClicks(editor, "ABC ")
    moved = next(e for e in window.session.entities if e.id == email.id)
    assert moved.positions[0].start == email.positions[0].start + 4
    assert window.session.text.startswith("ABC ")
    window.session.undo_stack.undo()
    assert window.session.text == editor.document().toRawText().replace(" ", "\n")
    assert not window.session.text.startswith("ABC")


def test_candidate_review_keyboard(qtbot, window, tmp_path):
    open_docx(qtbot, window, tmp_path, para("Иван Петров подписал. Позже Петров уехал."))
    text = window.session.text
    s = text.index("Иван Петров")
    window._add_entity(s, s + len("Иван Петров"), "PERSON")
    # Похожие упоминания ищутся при обработке файла; здесь вызываем поиск вручную.
    from md_blinder.anonymize.related import find_related_occurrences
    related = find_related_occurrences(text, window.session.entities)
    assert related
    window.session.update_entities(lambda es: es + related)
    window._render_derived()
    review = window.page.side.review
    window.show_candidates()
    assert review._card is not None
    qtbot.keyClick(review._card, Qt.Key.Key_Return)
    assert not any(e.candidate for e in window.session.entities)
    assert "Петров" not in window.anonymized_text


def test_unknown_and_legacy_files(qtbot, window, tmp_path):
    legacy = tmp_path / "old.doc"
    legacy.write_bytes(b"\xd0\xcf\x11\xe0")
    window.process_file(legacy)
    qtbot.waitUntil(lambda: window.job is None, timeout=5000)
    assert "Формат .doc не поддерживается" in window.start.error.text()


def test_history_reopen(qtbot, window, tmp_path):
    open_docx(qtbot, window, tmp_path, para(TEXT))
    doc_id = window.session.doc.id
    qtbot.mouseClick(window.page.home_button, Qt.MouseButton.LeftButton)
    assert window.stack.currentWidget() is window.start
    window.open_doc(doc_id)
    assert window.session.doc.id == doc_id
    assert window.stack.currentWidget() is window.page


def test_full_pipeline_with_real_model(qtbot, tmp_path, monkeypatch):
    """Сквозной прогон с настоящей моделью: имена и компании находит NER."""
    from md_blinder.components import model_asset

    if not model_asset.is_model_present():
        pytest.skip("модель NER не скачана")
    store = HistoryStore(tmp_path / "h.sqlite")
    w = MainWindow(store)
    qtbot.addWidget(w)
    monkeypatch.setattr(w, "_job_ask", lambda kind, payload: w.job.answer(SKIP))
    path = make_docx(tmp_path / "c.docx", para(
        "Иван Петров подписал договор с ООО «Альфа». Олена Коваленко працює у ТОВ «Весна». "
        "Позже Петров И. направил письмо на ivan@example.com."
    ))
    w.process_file(path)
    qtbot.waitUntil(lambda: w.job is None, timeout=180000)
    assert not w.page.ner_notice.isVisible()
    types = {e.type for e in w.session.entities if e.anonymize}
    assert {"PERSON", "COMPANY", "EMAIL"} <= types
    for name in ("Иван Петров", "Олена Коваленко"):
        assert name not in w.anonymized_text
    w.close()


def test_fonts_registered():
    from PySide6.QtGui import QFontDatabase

    families = QFontDatabase.families()
    for family in ("IBM Plex Sans", "IBM Plex Serif", "IBM Plex Mono"):
        assert family in families


def _two_people(qtbot, window, tmp_path):
    open_docx(qtbot, window, tmp_path, para("Иван Петров подписал, Анна Смирнова согласовала."))
    text = window.session.text
    for name in ("Иван Петров", "Анна Смирнова"):
        s = text.index(name)
        window._add_entity(s, s + len(name), "PERSON")
    return text


def test_hover_sync_both_ways(qtbot, window, tmp_path):
    text = _two_people(qtbot, window, tmp_path)
    editor = window.page.editor
    found = window.page.side.found
    window.page.side.setCurrentIndex(1)
    person = next(e for e in window.session.entities if e.type == "PERSON")
    # Указатель над сущностью в редакторе подсвечивает ее строку в списке.
    rect = editor.range_rects(person.positions[0].start, person.positions[0].end)[0]
    qtbot.mouseMove(editor.viewport(), rect.center().toPoint())
    qtbot.waitUntil(lambda: found._highlighted == person.id, timeout=2000)
    assert found._rows[person.id].property("highlighted") == "true"
    # Наведение на строку списка обводит вхождения в редакторе.
    found.row_hovered.emit(person.id)
    assert editor._outlined == person.id
    del text


def test_merge_from_editor(qtbot, window, tmp_path):
    _two_people(qtbot, window, tmp_path)
    people = [e for e in window.session.entities if e.type == "PERSON"]
    assert len(people) == 2
    window.page.editor.merge_requested.emit(people[1].id, people[0].id)
    merged = [e for e in window.session.entities if e.type == "PERSON"]
    assert len(merged) == 1 and len(merged[0].positions) == 2
    assert window.anonymized_text.count("[PERSON_1]") == 2
    window.session.undo_stack.undo()
    assert len([e for e in window.session.entities if e.type == "PERSON"]) == 2


def test_drag_handle_changes_span_as_one_command(qtbot, window, tmp_path):
    from md_blinder.anonymize.types import Position

    open_docx(qtbot, window, tmp_path, para("Директор Иван Петров подписал договор."))
    text = window.session.text
    s = text.index("Иван")
    window._add_entity(s, s + 4, "PERSON")
    editor = window.page.editor
    entity = next(e for e in window.session.entities if e.type == "PERSON")
    span = entity.positions[0]
    # Щелчок внутри вхождения показывает ручки по краям.
    rect = editor.range_rects(span.start, span.end)[0]
    qtbot.mouseClick(editor.viewport(), Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    assert editor._active == (entity.id, span)
    handles = dict(editor._handle_points())
    target = editor.range_rects(text.index("Петров") + 5, text.index("Петров") + 6)[0]
    before = window.session.undo_stack.count()
    qtbot.mousePress(editor.viewport(), Qt.MouseButton.LeftButton, pos=handles["right"].toPoint())
    qtbot.mouseMove(editor.viewport(), target.center().toPoint())
    qtbot.mouseRelease(editor.viewport(), Qt.MouseButton.LeftButton, pos=target.center().toPoint())
    moved = next(e for e in window.session.entities if e.id == entity.id)
    assert text[moved.positions[0].start:moved.positions[0].end] == "Иван Петров"
    assert window.session.undo_stack.count() == before + 1
    window.session.undo_stack.undo()
    restored = next(e for e in window.session.entities if e.id == entity.id)
    assert restored.positions == [Position(span.start, span.end)]


def test_open_external_processes_file_or_waits_for_running_job(qtbot, window, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(window, "process_file", lambda path, force_ocr=False: opened.append(path))
    window.open_external(str(tmp_path / "a.pdf"))
    assert opened == [tmp_path / "a.pdf"]
    window.job = object()
    window.open_external(str(tmp_path / "b.pdf"))
    assert opened == [tmp_path / "a.pdf"]
    window.job = None
    window.open_external("")
    assert opened == [tmp_path / "a.pdf"]

def test_select_shows_every_entity_type_without_scrolling(qtbot):
    from md_blinder.anonymize.types import ALL_ENTITY_TYPES
    from md_blinder.ui.widgets import Select

    box = Select()
    qtbot.addWidget(box)
    box.addItems(list(ALL_ENTITY_TYPES))
    assert box.maxVisibleItems() >= len(ALL_ENTITY_TYPES)


@pytest.mark.parametrize("width", [440, 360])  # обычный режим и «Результат рядом»
def test_side_panel_fits_its_width_with_long_values(qtbot, width):
    from md_blinder.anonymize.types import Entity, Position
    from md_blinder.ui.side_panel import SidePanel

    values = [
        "International Business Machines Corporation of New York and Delaware",
        "https://www.example.com/very/long/path/without/any/spaces/at/all/here",
        "john.very.long.email.address@subdomain.example-company.com",
    ]
    text = "Intro. " + " ... ".join(values) + " end."

    def entity(eid, value, **kw):
        start = text.index(value)
        return Entity(eid, "COMPANY", value, f"[COMPANY_{eid}]", [Position(start, start + len(value))], **kw)

    confirmed = [entity(f"e{i}", v) for i, v in enumerate(values)]
    candidates = [entity(f"c{i}", v, candidate=True, related_to="e1") for i, v in enumerate(values)]
    panel = SidePanel()
    qtbot.addWidget(panel)
    panel.setFixedWidth(width)
    panel.resize(width, 800)
    panel.show()
    for active in ("c0", "c1", "c2"):
        panel.review.active_id = active
        panel.found._selected, panel.found._merging = "e1", "e1"
        panel.render(confirmed + candidates, text)
        for index, part in ((0, panel.review), (1, panel.found)):
            panel.setCurrentIndex(index)
            qtbot.wait(1)
            assert part.area.widget().width() <= part.area.viewport().width()



def process_all(qtbot, window, paths):
    window.process_files(paths)
    qtbot.waitUntil(lambda: window.job is None and window.run is None, timeout=40000)


def test_files_opened_together_share_labels_across_the_package(qtbot, window, tmp_path, monkeypatch):
    first = make_docx(tmp_path / "first.docx", para("Пишите на alice@example.com или bob@example.com."))
    second = make_docx(tmp_path / "second.docx", para("Копия bob@example.com и carol@example.com."))
    process_all(qtbot, window, [first, second])
    assert window.stack.currentWidget() is window.page
    assert window.session.doc.name == "first.docx"
    assert "Пишите на [EMAIL_1] или [EMAIL_2]." in window.anonymized_text
    assert window.page.topbar.batch_label.text() == "Пакет · 2"
    window._open_batch_menu()
    menu = window.page.batch_menu
    titles = [label.text() for label in menu.findChildren(QLabel) if label.objectName() == "menuTitle"]
    assert titles[:3] == ["1. first.docx", "2. second.docx", "Добавить документы в пакет"]
    menu.hide()
    qtbot.waitUntil(lambda: not window.autosave.saving, timeout=3000)
    other = next(m for m in window.store.load_metadata() if m.name == "second.docx")
    window.open_doc(other.id)
    assert "Копия [EMAIL_2] и [EMAIL_3]." in window.anonymized_text

    out = tmp_path / "package.md"
    monkeypatch.setattr(window, "_save_path", lambda name, filter_: out)
    window.export_batch_md()
    text = out.read_text(encoding="utf-8")
    assert "# Документ 1\n\nПишите на [EMAIL_1] или [EMAIL_2]." in text
    assert "# Документ 2\n\nКопия [EMAIL_2] и [EMAIL_3]." in text
    assert "example.com" not in text and ".docx" not in text


def test_package_labels_stay_aligned_after_deleting_a_value(qtbot, window, tmp_path, monkeypatch):
    first = make_docx(tmp_path / "first.docx", para("Пишите на alice@example.com."))
    second = make_docx(tmp_path / "second.docx", para("Копия alice@example.com и carol@example.com."))
    process_all(qtbot, window, [first, second])
    qtbot.waitUntil(lambda: not window.autosave.saving, timeout=3000)
    other = next(m for m in window.store.load_metadata() if m.name == "second.docx")
    window.open_doc(other.id)
    alice = next(e for e in window.session.entities if e.placeholder == "[EMAIL_1]")
    # В одиночном документе carol стала бы [EMAIL_1], но эта метка в пакете у alice.
    window.page.side.found.delete.emit(alice.id)
    assert window.anonymized_text.startswith("Копия alice@example.com и [EMAIL_2].")


def test_add_files_to_open_document_makes_a_package(qtbot, window, tmp_path):
    open_docx(qtbot, window, tmp_path, para("Пишите на alice@example.com."))
    assert window.session.doc.batch_id is None
    assert window.page.topbar.batch_label.text() == "Пакет"
    added = make_docx(tmp_path / "added.docx", para("Ответ от alice@example.com и dan@example.com."))
    window.add_to_batch([added])
    qtbot.waitUntil(lambda: window.job is None and window.run is None, timeout=20000)
    assert window.session.doc.name == "added.docx"
    assert "Ответ от [EMAIL_1] и [EMAIL_2]." in window.anonymized_text
    qtbot.waitUntil(lambda: not window.autosave.saving, timeout=3000)
    batch_ids = {m.batch_id for m in window.store.load_metadata()}
    assert len(batch_ids) == 1 and None not in batch_ids


def test_xlsx_opens_in_a_package_with_docx(qtbot, window, tmp_path):
    contract = make_docx(tmp_path / "contract.docx", para("Пишите на alice@example.com."))
    book = make_xlsx(tmp_path / "contacts.xlsx", {"Контакты": {"A1": "Email", "A2": "alice@example.com", "A3": "dan@example.com"}})
    process_all(qtbot, window, [contract, book])
    qtbot.waitUntil(lambda: not window.autosave.saving, timeout=3000)
    meta = next(m for m in window.store.load_metadata() if m.name == "contacts.xlsx")
    assert meta.format == "xlsx"
    window.open_doc(meta.id)
    assert "## Контакты" in window.anonymized_text
    assert "| [EMAIL_1] |" in window.anonymized_text and "| [EMAIL_2] |" in window.anonymized_text
