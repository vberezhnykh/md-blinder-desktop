"""Сквозные проверки окна в режиме offscreen (без модели NER и OCR)."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton

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


def test_history_shows_ten_recent_docs_until_expanded(qtbot, window):
    from md_blinder.storage.history import HistoryDocMeta
    from md_blinder.ui.start_screen import HISTORY_VISIBLE

    panel = window.start.history
    docs = [HistoryDocMeta(f"d{i}", f"doc{i}.docx", "docx", i, 100 - i, 0) for i in range(HISTORY_VISIBLE + 5)]

    def names():
        return [
            next(lb for lb in panel.rows.itemAt(i).widget().findChildren(QLabel) if lb.objectName() == "historyName").text()
            for i in range(panel.rows.count())
            if panel.rows.itemAt(i).widget() is not None
        ]

    panel.render(docs, None)
    assert names() == [d.name for d in docs[:HISTORY_VISIBLE]]
    assert not panel.more_button.isHidden()
    assert panel.more_button.text() == "Показать еще 5 документов"
    assert panel.count.text() == str(len(docs))

    qtbot.mouseClick(panel.more_button, Qt.MouseButton.LeftButton)
    assert names() == [d.name for d in docs]
    assert panel.more_button.text() == "Свернуть"
    # Обновление списка (автосохранение, удаление) не сворачивает раскрытую историю.
    panel.render(docs[1:], None)
    assert len(names()) == len(docs) - 1

    qtbot.mouseClick(panel.more_button, Qt.MouseButton.LeftButton)
    assert len(names()) == HISTORY_VISIBLE

    panel.render(docs[:HISTORY_VISIBLE], None)
    assert len(names()) == HISTORY_VISIBLE
    assert panel.more_button.isHidden()


def test_history_menu_all_docs_expands_list(qtbot, window, tmp_path):
    open_docx(qtbot, window, tmp_path, para(TEXT))
    assert not window.start.history.expanded
    window._show_full_history()
    assert window.stack.currentWidget() is window.start
    assert window.start.history.expanded


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


def test_similar_spelling_hint_inside_single_document(qtbot, window, tmp_path):
    open_docx(qtbot, window, tmp_path, para(
        "Makedon Kiriakidis signed. Makedon Kiriakidis paid. Witness Makedon Kirjakidjs and Petr Ivanov."))
    text = window.session.text
    for name in ("Makedon Kiriakidis", "Makedon Kirjakidjs", "Petr Ivanov"):
        s = text.index(name)
        window._add_entity(s, s + len(name), "PERSON")
    people = {text[e.positions[0].start:e.positions[0].end]: e for e in window.session.entities if e.type == "PERSON"}
    assert len(people["Makedon Kiriakidis"].positions) == 2
    found = window.page.side.found
    window.page.side.setCurrentIndex(1)

    def hints():
        qtbot.wait(10)  # старые строки списка удаляются в цикле событий
        return [lb.text() for lb in found.findChildren(QLabel) if lb.objectName() == "hint"]

    # Подсказка одна: у редкого написания, с меткой частого.
    [hint] = hints()
    assert people["Makedon Kiriakidis"].placeholder in hint and "в этом документе" in hint
    button = next(b for b in found.findChildren(QPushButton) if b.text() == "Объединить")
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    merged = [e for e in window.session.entities if e.type == "PERSON" and not e.candidate]
    assert sorted(len(e.positions) for e in merged) == [1, 3]
    assert window.anonymized_text.count("[PERSON_1]") == 3 and "Kirjakidjs" not in window.anonymized_text
    assert not hints()
    window.session.undo_stack.undo()
    assert len(hints()) == 1


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


def test_drag_extends_address_everywhere_past_dismissed_finding(qtbot, window, tmp_path):
    from md_blinder.anonymize.apply import entity_current_value
    from md_blinder.anonymize.types import Entity, Position

    open_docx(qtbot, window, tmp_path, para(
        "Registered office at Quijano Chambers, P.O. Box 3159, Road Town, Tortola. "
        "Physical address: Quijano Chambers, P.O. Box 3159, Road Town, Tortola."
    ))
    text = window.session.text
    value = "P.O. Box 3159, Road Town"
    first = text.index(", P.O.")
    second = text.index(value, first + 10)
    company = text.index("Quijano")
    # Адрес найден в первом месте вместе с запятыми; «Quijano Chambers» там же —
    # отклоненная находка, в тексте ее не видно.
    address = Entity(id="addr", type="ADDRESS", value=value, placeholder="[ADDRESS_5]",
                     positions=[Position(first, first + len(value) + 3), Position(second, second + len(value))])
    dismissed = Entity(id="co", type="COMPANY", value="Quijano Chambers", placeholder="[COMPANY_2]",
                       positions=[Position(company, company + 16)], anonymize=False, dismissed=True)
    window.session.update_entities(lambda es: [address, dismissed], "test")
    editor = window.page.editor
    span = address.positions[0]
    rect = editor.range_rects(span.start + 6, span.start + 7)[0]
    qtbot.mouseClick(editor.viewport(), Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    assert editor._active == ("addr", span)
    handles = dict(editor._handle_points())
    target = editor.range_rects(company + 2, company + 3)[0]
    before = window.session.undo_stack.count()
    qtbot.mousePress(editor.viewport(), Qt.MouseButton.LeftButton, pos=handles["left"].toPoint())
    qtbot.mouseMove(editor.viewport(), target.center().toPoint())
    qtbot.mouseRelease(editor.viewport(), Qt.MouseButton.LeftButton, pos=target.center().toPoint())
    moved = next(e for e in window.session.entities if e.id == "addr")
    extended = "Quijano Chambers, P.O. Box 3159, Road Town"
    assert [text[p.start:p.end].rstrip(",") for p in moved.positions] == [extended, extended]
    # В списке замен значение уже расширенное, отклоненная находка ушла.
    assert entity_current_value(text, moved).rstrip(",") == extended
    assert not any(e.id == "co" for e in window.session.entities)
    assert window.session.undo_stack.count() == before + 1


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


def test_folder_with_nested_folders_is_one_package(qtbot, window, tmp_path, monkeypatch):
    root = tmp_path / "Клиент"
    (root / "2024" / "март").mkdir(parents=True)
    make_docx(root / "договор.docx", para("Пишите на alice@example.com или bob@example.com."))
    make_docx(root / "2024" / "март" / "акт.docx", para("Копия bob@example.com и carol@example.com."))
    make_xlsx(root / "2024" / "контакты.xlsx", {"Лист": {"A1": "alice@example.com"}})
    (root / "старый.doc").write_bytes(b"\xd0\xcf\x11\xe0")
    (root / "заметки.txt").write_text("не документ", encoding="utf-8")
    process_all(qtbot, window, [root])
    assert window.stack.currentWidget() is window.page
    assert window.session.doc.name == "Клиент/договор.docx"
    assert window.page.topbar.batch_label.text() == "Пакет · 3"
    assert "старый" not in window.start.error.text()
    qtbot.waitUntil(lambda: not window.autosave.saving, timeout=3000)
    metas = {m.name: m for m in window.store.load_metadata()}
    assert set(metas) == {"Клиент/договор.docx", "Клиент/2024/контакты.xlsx", "Клиент/2024/март/акт.docx"}
    assert len({m.batch_id for m in metas.values()}) == 1
    window.open_doc(metas["Клиент/2024/март/акт.docx"].id)
    assert "Копия [EMAIL_2] и [EMAIL_3]." in window.anonymized_text

    out = tmp_path / "выгрузка"
    out.mkdir()
    monkeypatch.setattr(window, "_pick_folder", lambda title: out)
    window.export_batch_folder()
    assert (out / "Клиент" / "договор.md").read_text(encoding="utf-8").startswith("Пишите на [EMAIL_1] или [EMAIL_2].")
    assert (out / "Клиент" / "2024" / "март" / "акт.md").read_text(encoding="utf-8").startswith("Копия [EMAIL_2] и [EMAIL_3].")
    assert "[EMAIL_1]" in (out / "Клиент" / "2024" / "контакты.md").read_text(encoding="utf-8")
    assert not any("example.com" in f.read_text(encoding="utf-8") for f in out.rglob("*.md"))

    # Повторная выгрузка в ту же папку спрашивает, заменять ли файлы.
    from md_blinder.ui import dialogs

    asked = []
    monkeypatch.setattr(dialogs, "ask", lambda parent, title, text, *a, **k: (asked.append(text), False)[1])
    (out / "Клиент" / "договор.md").write_text("старое", encoding="utf-8")
    window.export_batch_folder()
    assert asked and "3 файла" in asked[0]
    assert (out / "Клиент" / "договор.md").read_text(encoding="utf-8") == "старое"


def test_folder_without_documents_shows_error(qtbot, window, tmp_path):
    empty = tmp_path / "пусто"
    (empty / "внутри").mkdir(parents=True)
    (empty / "старый.doc").write_bytes(b"\xd0\xcf\x11\xe0")
    window.process_files([empty])
    assert window.run is None and window.job is None
    assert "В папке нет файлов .docx, .xlsx или .pdf." in window.start.error.text()
    assert ".doc и .xls из папки пропущены (1)" in window.start.error.text()


def test_add_folder_to_open_document(qtbot, window, tmp_path, monkeypatch):
    open_docx(qtbot, window, tmp_path, para("Пишите на alice@example.com."))
    folder = tmp_path / "Приложения"
    (folder / "внутри").mkdir(parents=True)
    make_docx(folder / "внутри" / "письмо.docx", para("Ответ от alice@example.com."))
    monkeypatch.setattr(window, "_pick_folder", lambda title: folder)
    window.add_folder_to_batch()
    qtbot.waitUntil(lambda: window.job is None and window.run is None, timeout=20000)
    assert window.session.doc.name == "Приложения/внутри/письмо.docx"
    assert "Ответ от [EMAIL_1]." in window.anonymized_text
    assert window.page.topbar.batch_label.text() == "Пакет · 2"


def _assert_menu_items_readable(menu):
    items = [b for b in menu.findChildren(QPushButton) if b.objectName() == "menuItem"]
    assert items
    for item in items:
        for label in item.findChildren(QLabel):
            need = label.heightForWidth(label.width()) if label.wordWrap() else label.sizeHint().height()
            assert label.height() >= need, label.text()
            assert item.rect().contains(label.geometry()), label.text()


def test_popup_menus_show_item_titles(qtbot, window, tmp_path):
    """Пункты меню не обрезаются по высоте пустой кнопки: видны заголовки и подсказки."""
    window.resize(1200, 800)
    window.show()
    paths = [make_docx(tmp_path / f"doc{i}.docx", para(f"Пишите на a{i}@example.com.")) for i in range(3)]
    process_all(qtbot, window, paths)
    qtbot.waitUntil(lambda: not window.autosave.saving, timeout=3000)
    window._refresh_history()
    assert window.history_meta
    for open_menu, menu in (
        (window._open_batch_menu, window.page.batch_menu),
        (window._open_history_menu, window.page.history_menu),
        (window.page.status._open_menu, window.page.status.menu),
    ):
        for _ in range(2):
            open_menu()
            qtbot.waitUntil(menu.isVisible)
            qtbot.wait(1)
            _assert_menu_items_readable(menu)
            menu.hide()


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


def test_merge_findings_from_different_documents_of_a_package(qtbot, window, tmp_path):
    first = make_docx(tmp_path / "first.docx", para("Agent: Makedon Kiriakidis, alice@example.com."))
    second = make_docx(tmp_path / "second.docx", para("Signed by Makedon Kirjakidjs and Petr Ivanov."))
    process_all(qtbot, window, [first, second])

    def add(name):
        s = window.session.text.index(name)
        window._add_entity(s, s + len(name), "PERSON")

    add("Makedon Kiriakidis")
    qtbot.waitUntil(lambda: not window.autosave.saving, timeout=3000)
    other = next(m for m in window.store.load_metadata() if m.name == "second.docx")
    window.open_doc(other.id)
    add("Makedon Kirjakidjs")
    add("Petr Ivanov")

    def label(name):
        e = next(e for e in window.session.entities if window.session.text[e.positions[0].start:].startswith(name))
        return e.id, e.placeholder

    assert "[PERSON_1]" not in window.anonymized_text
    found = window.page.side.found
    window.page.side.setCurrentIndex(1)
    # Подсказка о похожем написании в другом документе и кнопка объединения в ней.
    def hints():
        qtbot.wait(10)  # старые строки списка удаляются в цикле событий
        return [lb.text() + lb.toolTip() for lb in found.findChildren(QLabel) if lb.objectName() == "hint"]

    assert len(hints()) == 1 and "[PERSON_1]" in hints()[0] and "first.docx" in hints()[0]
    button = next(b for b in found.findChildren(QPushButton) if b.text() == "Объединить")
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert label("Makedon")[1] == "[PERSON_1]" and label("Petr")[1] != "[PERSON_1]"
    assert not hints()
    window.session.undo_stack.undo()
    assert "[PERSON_1]" not in window.anonymized_text
    # Объединение без подсказки: из списка «Объединить…» в редакторе.
    window.page.editor.merge_requested.emit(label("Petr")[0], "batch:[PERSON_1]")
    assert label("Petr")[1] == "[PERSON_1]" and label("Makedon")[1] != "[PERSON_1]"
    window.session.undo_stack.undo()
    found.link_batch.emit(label("Makedon")[0], "[PERSON_1]")
    assert label("Makedon")[1] == "[PERSON_1]"

    # Связь сохраняется: первый документ после повторного открытия тот же, таблица замен пакета общая.
    qtbot.waitUntil(lambda: not window.autosave.saving, timeout=3000)
    first_meta = next(m for m in window.store.load_metadata() if m.name == "first.docx")
    window.open_doc(first_meta.id)
    assert "Agent: [PERSON_1], [EMAIL_1]." in window.anonymized_text
    out = tmp_path / "package.csv"
    window._save_path = lambda name, filter_: out
    window.export_batch_csv()
    table_text = out.read_text(encoding="utf-8-sig")
    assert "Makedon Kiriakidis; Makedon Kirjakidjs" in table_text


def test_package_overview_merges_labels_of_documents(qtbot, window, tmp_path):
    first = make_docx(tmp_path / "first.docx", para("Agent: Makedon Kiriakidis."))
    second = make_docx(tmp_path / "second.docx", para("Signed by Makedon Kirjakidjs."))
    process_all(qtbot, window, [first, second])
    name = "Makedon Kiriakidis"
    s = window.session.text.index(name)
    window._add_entity(s, s + len(name), "PERSON")
    qtbot.waitUntil(lambda: not window.autosave.saving, timeout=3000)
    other = next(m for m in window.store.load_metadata() if m.name == "second.docx")
    window.open_doc(other.id)
    name = "Makedon Kirjakidjs"
    s = window.session.text.index(name)
    window._add_entity(s, s + len(name), "PERSON")
    assert "Signed by [PERSON_2]." in window.anonymized_text
    # Открытый документ — первый: объединение должно поменять сохраненный второй.
    first_meta = next(m for m in window.store.load_metadata() if m.name == "first.docx")
    window.open_doc(first_meta.id)

    window.show_batch_overview()
    overview = window.overview
    assert overview.isVisible()
    assert [(x.placeholder, y.placeholder) for x, y in overview.pairs] == [("[PERSON_1]", "[PERSON_2]")]
    button = next(b for b in overview.findChildren(QPushButton) if b.text() == "Объединить в [PERSON_1]")
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert overview.pairs == []
    assert [t.placeholder for t in overview.targets] == ["[PERSON_1]"]
    stored = window.store.load_doc(other.id)
    assert [e.placeholder for e in stored.entities if e.type == "PERSON"] == ["[PERSON_1]"]
    # Отмена из всплывающего сообщения возвращает обе метки.
    window.toast.action.click()
    assert [e.placeholder for e in window.store.load_doc(other.id).entities if e.type == "PERSON"] == ["[PERSON_2]"]
    assert [x.placeholder for x, _ in overview.pairs] == ["[PERSON_1]"]
    overview.close()


def test_rename_and_swap_labels(qtbot, window, tmp_path):
    from md_blinder.ui.widgets import LabelEditor

    first = make_docx(tmp_path / "first.docx", para("Alpha Ltd and Beta Ltd."))
    second = make_docx(tmp_path / "second.docx", para("Beta Ltd and Gamma Ltd."))
    process_all(qtbot, window, [first, second])
    for name in ("Alpha Ltd", "Beta Ltd"):
        s = window.session.text.index(name)
        window._add_entity(s, s + len(name), "COMPANY")
    qtbot.waitUntil(lambda: not window.autosave.saving, timeout=3000)
    other = next(m for m in window.store.load_metadata() if m.name == "second.docx")
    window.open_doc(other.id)
    for name in ("Beta Ltd", "Gamma Ltd"):
        s = window.session.text.index(name)
        window._add_entity(s, s + len(name), "COMPANY")
    assert window.anonymized_text.startswith("[COMPANY_2] and [COMPANY_3].")

    found = window.page.side.found
    window.page.side.setCurrentIndex(1)
    gamma = next(e for e in window.session.entities if e.placeholder == "[COMPANY_3]")
    found.select_entity(gamma.id)
    qtbot.wait(10)  # старые строки списка удаляются в цикле событий
    [editor] = found.findChildren(LabelEditor)
    # В списке есть и метка первого документа.
    assert [editor.box.itemText(i) for i in range(editor.box.count())] == ["[COMPANY_1]", "[COMPANY_2]", "[COMPANY_3]"]
    editor.box.setCurrentText("[COMPANY_1]")
    editor.apply.click()
    assert window.anonymized_text.startswith("[COMPANY_2] and [COMPANY_1].")
    first_meta = next(m for m in window.store.load_metadata() if m.name == "first.docx")
    stored = window.store.load_doc(first_meta.id)
    assert [e.placeholder for e in stored.entities if e.type == "COMPANY"] == ["[COMPANY_3]", "[COMPANY_2]"]
    # Своя метка вместо номера.
    window._relabel("[COMPANY_2]", "Поставщик")
    assert window.anonymized_text.startswith("[Поставщик] and [COMPANY_1].")
    stored = window.store.load_doc(first_meta.id)
    assert [e.placeholder for e in stored.entities if e.type == "COMPANY"] == ["[COMPANY_2]", "[Поставщик]"]
    window.toast.action.click()  # отмена из всплывающего сообщения
    assert window.anonymized_text.startswith("[COMPANY_2] and [COMPANY_1].")
    assert [e.placeholder for e in window.store.load_doc(first_meta.id).entities] == ["[COMPANY_3]", "[COMPANY_2]"]
    window._relabel("[COMPANY_1]", "[a]b]")
    assert window.anonymized_text.startswith("[COMPANY_2] and [COMPANY_1].")


def test_rename_label_in_single_document(qtbot, window, tmp_path):
    open_docx(qtbot, window, tmp_path, para("Alpha Ltd and Beta Ltd."))
    for name in ("Alpha Ltd", "Beta Ltd"):
        s = window.session.text.index(name)
        window._add_entity(s, s + len(name), "COMPANY")
    window._relabel("[COMPANY_1]", "Заказчик")
    assert window.anonymized_text.startswith("[Заказчик] and [COMPANY_1].")
    window._relabel("[COMPANY_1]", "[Заказчик]")  # занятая метка того же типа: меняются местами
    assert window.anonymized_text.startswith("[COMPANY_1] and [Заказчик].")
    window.session.undo_stack.undo()
    assert window.anonymized_text.startswith("[Заказчик] and [COMPANY_1].")


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


def _menu_titles(menu):
    return [label.text() for label in menu.findChildren(QLabel) if label.objectName() == "menuTitle"]


def test_redacted_pdf_export_from_menu(qtbot, window, tmp_path, monkeypatch):
    import pymupdf

    from md_blinder.ui.theme import FONTS_DIR

    source = tmp_path / "contract.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="plex", fontfile=str(FONTS_DIR / "IBMPlexSans-Regular.ttf"))
    page.insert_text((50, 100), "Пишите на ivan@example.com или звоните.", fontsize=12, fontname="plex")
    doc.save(source)
    window.process_file(source)
    qtbot.waitUntil(lambda: window.job is None, timeout=20000)
    assert any(e.value == "ivan@example.com" for e in window.session.entities)

    window.page.status._open_menu()
    qtbot.waitUntil(window.page.status.menu.isVisible)
    assert "Обезличенный PDF" in _menu_titles(window.page.status.menu)
    window.page.status.menu.hide()

    out = tmp_path / "out.pdf"
    monkeypatch.setattr(window, "_save_path", lambda name, filter_: out)
    window.export_pdf()
    qtbot.waitUntil(lambda: window.pdf_job is None, timeout=20000)
    with pymupdf.open(out) as result:
        text = result[0].get_text()
    assert "ivan@example.com" not in text and "[EMAIL_1]" in text

    # Документ из истории: путь к исходнику запомнен и после перезапуска окна.
    assert window._pdf_sources()[window.session.doc.id]["path"] == str(source.resolve())


def test_redacted_pdf_not_offered_for_docx(qtbot, window, tmp_path):
    open_docx(qtbot, window, tmp_path, para(TEXT))
    window.page.status._open_menu()
    qtbot.waitUntil(window.page.status.menu.isVisible)
    assert "Обезличенный PDF" not in _menu_titles(window.page.status.menu)
    window.page.status.menu.hide()


def test_long_package_list_scrolls_within_screen(qtbot, window, tmp_path):
    """Список пакета из 24 документов не уходит за край экрана: он прокручивается,
    пункты пакета под ним видны, имя файла не обрезается длинным путем."""
    from PySide6.QtGui import QGuiApplication

    root = tmp_path / "Agreements"
    folder = root / "Заказчики-Ресселеры" / "Очень длинное название папки с договорами клиента"
    folder.mkdir(parents=True)
    for i in range(24):
        make_docx(folder / f"договор-{i:02d}.docx", para(f"Пишите на a{i}@example.com."))
    process_all(qtbot, window, [root])
    assert window.page.topbar.batch_label.text() == "Пакет · 24"
    qtbot.waitUntil(lambda: not window.autosave.saving, timeout=3000)
    last = next(m for m in window.store.load_metadata() if m.name.endswith("договор-23.docx"))
    window.open_doc(last.id)

    window._open_batch_menu()
    menu = window.page.batch_menu
    qtbot.waitUntil(menu.isVisible)
    screen = QGuiApplication.screenAt(menu.pos()) or QGuiApplication.primaryScreen()
    assert menu.frameGeometry().bottom() <= screen.availableGeometry().bottom()
    area = menu.list_area
    assert area.verticalScrollBar().maximum() > 0
    actions = [b for b in menu.findChildren(QPushButton) if b.objectName() == "menuItem" and not area.isAncestorOf(b)]
    assert actions and all(b.isVisible() for b in actions)
    # Открытый документ последний в списке, список прокручен к нему.
    qtbot.waitUntil(lambda: area.verticalScrollBar().value() > 0)
    current = next(b for b in menu.visible_list_items() if b.property("current") == "true")
    top_left = current.mapTo(area.viewport(), current.rect().topLeft())
    assert area.viewport().rect().contains(top_left)
    title = next(lbl for lbl in current.findChildren(QLabel) if lbl.objectName() == "menuTitle")
    assert title.text() == "24. договор-23.docx"
    assert current.toolTip().startswith("Agreements/Заказчики-Ресселеры/")
    _assert_menu_items_readable(menu)

    # Поиск по имени: остается один документ, Enter открывает его.
    assert menu.search is not None
    menu.search.setText("договор-07")
    assert len(menu.visible_list_items()) == 1
    menu.search.setText("нет такого")
    assert menu.visible_list_items() == []
    menu.search.setText("ДОГОВОР-07")
    menu.search.returnPressed.emit()
    assert not menu.isVisible()
    assert window.session.doc.name.endswith("договор-07.docx")


def test_history_groups_batch_documents_with_review_state(qtbot, window):
    from md_blinder.storage.history import HistoryDocMeta

    panel = window.start.history
    docs = [
        HistoryDocMeta("a", "Клиент/акт.docx", "docx", 1, 100, 5, "b", 2),
        HistoryDocMeta("x", "скан.pdf", "pdf", 2, 90, 0, None, 0),
        HistoryDocMeta("c", "Клиент/2024/счет.xlsx", "xlsx", 3, 80, 3, "b", 0),
        HistoryDocMeta("old", "старый.docx", "docx", 4, 70, 4),  # запись без счетчика проверки
    ]
    panel.render(docs, None)
    rows = [panel.rows.itemAt(i).widget() for i in range(panel.rows.count())]
    assert [r.objectName() for r in rows] == ["historyGroup", "historyRow", "historyRow", "historyRow", "historyRow"]
    group_texts = [lb.text() for lb in rows[0].findChildren(QLabel)]
    assert "Пакет «Клиент»" in group_texts

    def chips(row):
        return [lb.text() for lb in row.findChildren(QLabel) if lb.objectName() in ("chipPending", "chipReady")]

    names = [next(lb.text() for lb in r.findChildren(QLabel) if lb.objectName() == "historyName") for r in rows[1:]]
    assert names == ["Клиент/акт.docx", "Клиент/2024/счет.xlsx", "скан.pdf", "старый.docx"]
    assert [chips(r) for r in rows[1:]] == [["Не проверено: 2"], ["Проверено"], [], []]
    # Документы пакета на цветной подложке, первый одиночный документ после пакета отделен линией цвета пакета.
    assert [bool(r.property("grouped")) for r in rows[1:]] == [True, True, False, False]
    assert [bool(r.property("afterBatch")) for r in rows[1:]] == [False, False, True, False]

    opened = []
    panel.show_batch.connect(opened.append)
    button = next(b for b in rows[0].findChildren(QPushButton) if b.text() == "Все замены пакета")
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert opened == ["a"]


def test_header_switches_between_start_and_document(qtbot, window, tmp_path):
    header = window.header
    assert header.start_actions.isVisible() and not header.doc_actions.isVisible()
    open_docx(qtbot, window, tmp_path, para(TEXT))
    assert header.doc_actions.isVisible() and header.crumbs.isVisible()
    assert header.file_label.text() == "contract.docx"
    qtbot.mouseClick(header.home_button, Qt.MouseButton.LeftButton)
    assert window.stack.currentWidget() is window.start
    assert header.start_actions.isVisible() and not header.crumbs.isVisible()


def test_long_file_name_does_not_push_side_panel_off_screen(qtbot, window, tmp_path):
    """Развернутое окно не растет под содержимое: если минимум раскладки шире окна,
    правая панель уходит за край. Минимум не должен зависеть от длины имени файла
    и подсказок, а в режиме «Результат рядом» должен помещаться в WIDE_WINDOW,
    иначе окно нельзя сузить до одной колонки."""
    from md_blinder.ui.main_window import WIDE_WINDOW

    name = "Supplementary Agreement 1 to the Consultancy Agreement dd 03.09.2026 " * 3 + "— копия.docx"
    window.process_file(make_docx(tmp_path / name, para(TEXT)))
    qtbot.waitUntil(lambda: window.job is None, timeout=20000)
    assert window.header.file_label.text() == name
    window.page.side_by_side_box.setChecked(True)
    for width, side_by_side in ((WIDE_WINDOW, True), (WIDE_WINDOW - 1, False)):
        window.resize(width, 900)
        qtbot.waitUntil(lambda: window.width() == width)
        assert window.page.result_pane.isVisible() is side_by_side
        assert window.minimumSizeHint().width() <= width
        side = window.page.side
        # Раскладка пересчитывается после события изменения размера.
        qtbot.waitUntil(lambda: side.mapTo(window, side.rect().topRight()).x() < width)


def test_view_switch_stays_inside_pane_head(qtbot, window, tmp_path):
    """Переключатель «Исходник / Результат» не растягивается на весь заголовок колонки,
    иначе его подложка заходит на рамку карточки."""
    open_docx(qtbot, window, tmp_path, para(TEXT))
    window.page.side_by_side_box.setChecked(False)
    box = window.page.view_box
    qtbot.waitUntil(box.isVisible)
    head = box.parentWidget()
    assert head.objectName() == "paneHead"
    geo = box.geometry()
    assert geo.top() >= 4 and geo.bottom() <= head.height() - 5
    assert geo.left() >= 8


def test_overview_label_click_opens_editor(qtbot, window, tmp_path):
    from md_blinder.ui.widgets import LabelEditor

    first = make_docx(tmp_path / "first.docx", para("Alpha Ltd signs."))
    second = make_docx(tmp_path / "second.docx", para("Alpha Ltd pays."))
    process_all(qtbot, window, [first, second])
    s = window.session.text.index("Alpha Ltd")
    window._add_entity(s, s + len("Alpha Ltd"), "COMPANY")
    window.show_batch_overview()
    overview = window.overview
    assert not overview.findChildren(LabelEditor)
    chip = next(b for b in overview.findChildren(QPushButton) if b.objectName() == "labelChip")
    qtbot.mouseClick(chip, Qt.MouseButton.LeftButton)
    qtbot.wait(10)
    [editor] = overview.findChildren(LabelEditor)
    editor.box.setCurrentText("Поставщик")
    editor.apply.click()
    assert window.anonymized_text.startswith("[Поставщик] signs.")
    assert [x.placeholder for x in overview.targets] == ["[Поставщик]"]
    overview.close()


def test_ner_failure_is_remembered_in_history(qtbot, window, tmp_path):
    open_docx(qtbot, window, tmp_path, para(TEXT))
    assert window.session.doc.ner_missing  # модель не скачана: имена не искались
    qtbot.waitUntil(lambda: not window.autosave.saving, timeout=3000)
    window.autosave.flush()
    [meta] = window.store.load_metadata()
    assert meta.ner_missing
    window.show_all_docs()
    window.open_doc(meta.id)
    assert window.page.ner_notice.isVisible()
    assert "не искались" in window.page.ner_notice.label.text()
    assert window.page.ner_retry.isVisible()


def test_old_documents_are_removed_on_start(qtbot, tmp_path, monkeypatch):
    import md_blinder.jobs as jobs
    from md_blinder.storage.history import HistoryDoc, now_ms

    monkeypatch.setenv("MD_BLINDER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(jobs, "MODEL", jobs.ModelHolder())
    store = HistoryStore(tmp_path / "h.sqlite")
    day = 24 * 60 * 60 * 1000
    store.put_doc(HistoryDoc("old", "old.docx", "docx", 1, now_ms() - 8 * day, "text"))
    store.put_doc(HistoryDoc("new", "new.docx", "docx", 1, now_ms() - 6 * day, "text"))
    w = MainWindow(store)
    qtbot.addWidget(w)
    assert [m.id for m in store.load_metadata()] == ["new"]
    assert "7 дней" in w.start.history.warning.text()
    # Без флажка документы не удаляются.
    w.settings.setValue("historyRetention", False)
    store.put_doc(HistoryDoc("old2", "old2.docx", "docx", 1, now_ms() - 30 * day, "text"))
    w._purge_old_history()
    assert {m.id for m in store.load_metadata()} == {"new", "old2"}
    w.settings.clear()
    w.close()


def test_clear_history_removes_everything(qtbot, window, tmp_path, monkeypatch):
    from md_blinder.ui import dialogs

    open_docx(qtbot, window, tmp_path, para(TEXT))
    window.autosave.flush()
    window._refresh_history()
    assert window.start.history.clear_button.isEnabled()
    monkeypatch.setattr(dialogs, "ask", lambda *a, **k: False)
    window.clear_history()
    assert len(window.store.load_metadata()) == 1
    monkeypatch.setattr(dialogs, "ask", lambda *a, **k: True)
    window.clear_history()
    assert window.store.load_metadata() == []
    assert window.session.doc is None
    assert window.stack.currentWidget() is window.start
    assert not window.start.history.clear_button.isEnabled()


def test_search_in_text_replaces_all_matches(qtbot, window, tmp_path):
    open_docx(qtbot, window, tmp_path, para("Подписант Иван Петров. Позже Иван Петров и ИВАН ПЕТРОВ согласились."))
    window.open_search()
    bar = window.page.editor_pane.search
    assert bar.isVisible()
    bar.query.setText("иван петров")
    assert len(bar.matches) == 3 and bar.count.text() == "1 из 3"
    bar.step(1)
    assert bar.count.text() == "2 из 3"
    bar.type_box.setCurrentText("PERSON")
    bar.replace_button.click()
    assert "Иван Петров" not in window.anonymized_text
    assert "ИВАН ПЕТРОВ" not in window.anonymized_text
    window.session.undo_stack.undo()
    assert "Иван Петров" in window.anonymized_text
    bar.close_bar()
    assert not bar.isVisible()


def test_next_batch_document_with_pending_candidates(qtbot, window):
    from dataclasses import replace

    from md_blinder.anonymize.types import Entity, Position
    from md_blinder.storage.history import HistoryDoc

    def doc(doc_id, created, candidate):
        e = Entity(id=f"e-{doc_id}", type="PERSON", value="Иван", placeholder="[PERSON_1]",
                   positions=[Position(0, 4)], anonymize=not candidate, candidate=candidate)
        return HistoryDoc(doc_id, f"Клиент/{doc_id}.docx", "docx", created, 10**13, "Иван подписал.", [e], "b")

    for d in (doc("a", 1, False), doc("b", 2, False), doc("c", 3, True), doc("d", 4, True)):
        window.store.put_doc(d)
    window.open_doc("a")
    review = window.page.side.review
    assert review.next_in_batch == ("Клиент/c.docx", 2, 2)
    buttons = [b for b in review.findChildren(QPushButton) if b.text() == "Следующий документ"]
    assert buttons and buttons[0].isVisibleTo(review)
    buttons[0].click()
    assert window.session.doc.id == "c"
    # В открытом документе еще есть кандидат: кнопка не нужна, пока он не разобран.
    window._update(lambda es: [replace(e, candidate=False, anonymize=True) for e in es], "Принять")
    assert window.page.side.review.next_in_batch == ("Клиент/d.docx", 1, 1)
    window.open_next_pending_doc()
    assert window.session.doc.id == "d"
    window._update(lambda es: [replace(e, candidate=False, anonymize=True) for e in es], "Принять")
    assert window.page.side.review.next_in_batch is None


def test_restore_dialog_returns_real_values(qtbot, window, tmp_path, monkeypatch):
    from md_blinder.ui.restore_dialog import RestoreDialog

    monkeypatch.setattr(RestoreDialog, "exec", lambda self: 0)
    open_docx(qtbot, window, tmp_path, para(TEXT))
    email = next(e for e in window.session.entities if e.type == "EMAIL")
    window.open_restore()
    dialog = window.restore_dialog
    qtbot.addWidget(dialog)
    dialog.source.setPlainText(f"Напишите на {email.placeholder}, а не на [EMAIL_99].")
    assert dialog.output.toPlainText() == "Напишите на ivan@example.com, а не на [EMAIL_99]."
    assert "[EMAIL_99]" in dialog.status.text()
    dialog.copy()
    from PySide6.QtWidgets import QApplication

    assert QApplication.clipboard().text() == dialog.output.toPlainText()
