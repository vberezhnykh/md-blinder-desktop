"""Общий стек отмены текста и разметки, автосохранение."""

from dataclasses import replace

from md_blinder.anonymize.types import Entity, Position
from md_blinder.storage.history import HistoryDoc, HistoryStore
from md_blinder.ui.autosave import Autosave
from md_blinder.ui.session import DocumentSession


def make_doc():
    text = "Договор с Иван Петров подписан."
    s = text.index("Иван Петров")
    e = Entity(id="p", type="PERSON", value="Иван Петров", placeholder="[PERSON_1]",
               positions=[Position(s, s + 11)])
    return HistoryDoc("d", "a.docx", "docx", 1, 1, text, [e])


def test_undo_restores_text_and_markup(qtbot):
    session = DocumentSession()
    session.replace_doc(make_doc())
    changes = []
    session.text_changed.connect(changes.append)
    original = session.text
    # Пользователь удалил имя целиком: вхождение пропадает вместе с сущностью.
    session.editor_changed("Договор с  подписан.")
    assert session.entities == []
    session.undo_stack.undo()
    assert session.text == original
    assert session.entities[0].positions == [Position(10, 21)]
    assert len(changes) == 1
    session.undo_stack.redo()
    assert session.entities == []


def test_typing_merges_into_one_command(qtbot):
    session = DocumentSession()
    session.replace_doc(make_doc())
    base = session.text
    session.editor_changed(base[:8] + "X" + base[8:])
    session.editor_changed(base[:8] + "XY" + base[8:])
    session.editor_changed(base[:8] + "XYZ" + base[8:])
    assert session.undo_stack.count() == 1
    session.undo_stack.undo()
    assert session.text == base


def test_entity_commands_undoable(qtbot):
    session = DocumentSession()
    session.replace_doc(make_doc())
    session.update_entities(lambda es: [replace(e, anonymize=False) for e in es])
    assert not session.entities[0].anonymize
    session.undo_stack.undo()
    assert session.entities[0].anonymize


def test_autosave_debounce_and_flush(qtbot, tmp_path):
    store = HistoryStore(tmp_path / "h.sqlite")
    autosave = Autosave(store)
    session = DocumentSession(on_dirty=autosave.queue)
    session.replace_doc(make_doc(), dirty=True)
    assert autosave.saving
    qtbot.waitUntil(lambda: not autosave.saving, timeout=3000)
    assert store.load_doc("d").source_text == make_doc().source_text
    session.editor_changed(session.text + "!")
    assert autosave.flush()
    assert store.load_doc("d").source_text.endswith("!")
