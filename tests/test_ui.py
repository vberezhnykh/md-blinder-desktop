"""Сквозные проверки окна в режиме offscreen (без модели NER и OCR)."""

import pytest
from PySide6.QtCore import Qt

from md_blinder.jobs import SKIP
from md_blinder.storage.history import HistoryStore
from md_blinder.ui.main_window import MainWindow

from fixtures import make_docx, para, table

TEXT = "Договор между ООО «Альфа» и Иван Петров. Телефон +7 916 123-45-67, email ivan@example.com."


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("MD_BLINDER_DATA_DIR", str(tmp_path / "data"))
    store = HistoryStore(tmp_path / "h.sqlite")
    w = MainWindow(store)
    w.settings.clear()
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
    window.show_all_docs()
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
