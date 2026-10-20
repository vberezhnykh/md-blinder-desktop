"""Порт src/lib/history.test.ts плюс тесты SQLite-хранилища и пересчета UTF-16."""

import json
import time

import pytest

from md_blinder.anonymize.types import Entity, Position
from md_blinder.storage.history import (
    HistoryDoc,
    HistoryStore,
    merge_docs,
    parse_history,
    sanitize_entities,
    serialize_history,
)
from md_blinder.storage.offsets import OffsetMap

INVALID = "Файл не похож на экспорт истории MD Blinder"


def entity(**kw) -> Entity:
    base = dict(id="e1", type="PERSON", value="Иван Иванов", placeholder="[PERSON_1]",
                positions=[Position(0, 10)], anonymize=True, manual=False)
    base.update(kw)
    return Entity(**base)


def entity_json(**kw) -> dict:
    return entity(**kw).to_json()


def doc(**kw) -> HistoryDoc:
    base = dict(id="doc-1", name="contract.docx", format="docx", created_at=1000, updated_at=2000,
                source_text="Hello world", entities=[entity()])
    base.update(kw)
    return HistoryDoc(**base)


def test_round_trip():
    parsed = parse_history(serialize_history([doc(), doc(id="doc-2", name="scan.pdf", format="pdf")]))
    assert len(parsed) == 2
    d1, d2 = parsed
    assert (d1.id, d1.name, d1.source_text, d1.format) == ("doc-1", "contract.docx", "Hello world", "docx")
    assert d1.entities == [entity()]
    assert d2.format == "pdf"


def test_xlsx_format_survives_round_trip():
    [d] = parse_history(serialize_history([doc(name="book.xlsx", format="xlsx")]))
    assert d.format == "xlsx"


def test_invalid_json():
    with pytest.raises(ValueError, match=INVALID):
        parse_history("не json")
    with pytest.raises(ValueError, match=INVALID):
        parse_history(json.dumps({"foo": 1}))


def test_unsupported_version():
    with pytest.raises(ValueError, match="Версия экспорта"):
        parse_history(json.dumps({"app": "md-blinder", "kind": "history", "version": 2, "documents": []}))


def test_defaults_missing_meta():
    raw = {"app": "md-blinder", "kind": "history", "version": 1, "exportedAt": 0,
           "documents": [{"id": "doc-x", "name": "no-meta.txt", "sourceText": "text", "entities": []}]}
    before = int(time.time() * 1000)
    [d] = parse_history(json.dumps(raw))
    after = int(time.time() * 1000)
    assert d.format == "docx"
    assert before <= d.created_at <= after and before <= d.updated_at <= after


def test_sanitize_drops_malformed():
    raw = [
        {},
        {**entity_json(id="e2"), "positions": None},
        {**entity_json(id="e3"), "type": "ALIEN_TYPE"},
        {**entity_json(id="e4"), "positions": [{"start": "x", "end": 5}, {"start": 5, "end": 3}]},
        entity_json(),
    ]
    result = sanitize_entities(raw)
    assert [e.id for e in result] == ["e3", "e1"]
    assert result[0].type == "OTHER" and result[1].type == "PERSON"


def test_sanitize_non_list():
    assert sanitize_entities(None) == []
    assert sanitize_entities("not an array") == []


def test_sanitize_preserves_candidate_fields():
    [e] = sanitize_entities([entity_json(id="c", candidate=True, candidate_note="Низкая уверенность модели",
                                          score=0.2, anonymize=False)])
    assert e.candidate and e.candidate_note == "Низкая уверенность модели" and e.score == 0.2


def test_sanitize_related_to():
    result = sanitize_entities([
        entity_json(id="related", candidate=True, related_to="person-1", anonymize=False),
        {**entity_json(id="broken", candidate=True, anonymize=False), "relatedTo": 42},
    ])
    assert [e.related_to for e in result] == ["person-1", None]


def test_sanitize_keeps_valid_positions():
    raw = [{**entity_json(), "positions": [
        {"start": 0, "end": 10}, {"start": "a", "end": 5}, {"start": 8, "end": 8}, {"start": 20, "end": 15},
    ]}]
    assert sanitize_entities(raw)[0].positions == [Position(0, 10)]


def test_parse_drops_malformed_entities_in_doc():
    raw = {"app": "md-blinder", "kind": "history", "version": 1, "exportedAt": 0, "documents": [{
        "id": "doc-x", "name": "mixed.docx", "sourceText": "Hello world", "entities": [
            {},
            {**entity_json(id="p-null"), "positions": None},
            {**entity_json(id="p-unknown-type"), "type": "NOT_A_TYPE"},
            {**entity_json(id="p-bad-pos"), "positions": [{"start": 5, "end": 2}]},
            entity_json(id="p-good"),
        ]}]}
    [d] = parse_history(json.dumps(raw))
    assert [e.id for e in d.entities] == ["p-unknown-type", "p-good"]
    assert d.entities[0].type == "OTHER"


def test_merge_docs():
    older = doc(id="shared", updated_at=100, name="old.docx")
    newer = doc(id="shared", updated_at=200, name="new.docx")
    assert [d.name for d in merge_docs([older], [newer])] == ["new.docx"]
    assert [d.name for d in merge_docs([newer], [older])] == ["new.docx"]
    assert sorted(d.id for d in merge_docs([doc(id="a")], [doc(id="b")])) == ["a", "b"]


def test_utf16_offsets_in_export():
    # «💻» занимает в UTF-16 две единицы: браузерная версия хранит 3..14, Python — 2..13.
    text = "💻 Иван Петров"
    d = doc(source_text=text, entities=[entity(positions=[Position(2, 13)])])
    exported = json.loads(serialize_history([d]))
    assert exported["documents"][0]["entities"][0]["positions"] == [{"start": 3, "end": 14}]
    [back] = parse_history(json.dumps(exported))
    assert back.entities[0].positions == [Position(2, 13)]
    assert text[2:13] == "Иван Петров"


def test_offset_map():
    m = OffsetMap("a💻b💻c")
    assert [m.to_utf16(i) for i in range(6)] == [0, 1, 3, 4, 6, 7]
    assert [m.from_utf16(u) for u in (0, 1, 3, 4, 6, 7)] == [0, 1, 2, 3, 4, 5]
    assert OffsetMap("abc").trivial


def test_store_put_load_delete(tmp_path):
    store = HistoryStore(tmp_path / "h.sqlite")
    store.put_doc(doc(id="a", updated_at=1))
    store.put_doc(doc(id="b", updated_at=5, entities=[]))
    meta = store.load_metadata()
    assert [m.id for m in meta] == ["b", "a"]
    assert [m.entity_count for m in meta] == [0, 1]
    loaded = store.load_doc("a")
    assert loaded == doc(id="a", updated_at=1)
    store.put_doc(doc(id="a", updated_at=9, name="renamed.docx"))
    assert store.load_metadata()[0].name == "renamed.docx"
    store.delete_doc("a")
    assert store.load_doc("a") is None
    assert [m.id for m in store.load_metadata()] == ["b"]
    store.close()
    reopened = HistoryStore(tmp_path / "h.sqlite")
    assert [d.id for d in reopened.load_all()] == ["b"]


def test_store_empty_document_is_valid(tmp_path):
    store = HistoryStore(tmp_path / "h.sqlite")
    store.put_doc(doc(source_text="", entities=[]))
    assert store.load_doc("doc-1").source_text == ""


def test_store_sanitizes_out_of_range(tmp_path):
    store = HistoryStore(tmp_path / "h.sqlite")
    store.put_doc(doc(source_text="short", entities=[entity(positions=[Position(0, 50)])]))
    assert store.load_doc("doc-1").entities == []


def test_batch_id_round_trip_and_absent_for_single_docs():
    batched, single = parse_history(serialize_history([doc(batch_id="b1"), doc(id="doc-2")]))
    assert batched.batch_id == "b1"
    assert single.batch_id is None
    assert "batchId" not in json.loads(serialize_history([doc()]))["documents"][0]


def test_store_keeps_batch_and_loads_it_in_upload_order(tmp_path):
    store = HistoryStore(tmp_path / "h.sqlite")
    store.put_doc(doc(id="late", created_at=3000, batch_id="b"))
    store.put_doc(doc(id="early", created_at=1000, batch_id="b"))
    store.put_doc(doc(id="other"))
    assert [d.id for d in store.load_batch("b")] == ["early", "late"]
    assert {m.id: m.batch_id for m in store.load_metadata()} == {"late": "b", "early": "b", "other": None}
    store.close()


def test_store_upgrades_database_without_batch_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE documents (id TEXT PRIMARY KEY, name TEXT NOT NULL, format TEXT NOT NULL,"
        " created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, source_text TEXT NOT NULL, entities TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE document_meta (id TEXT PRIMARY KEY, name TEXT NOT NULL, format TEXT NOT NULL,"
        " created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, entity_count INTEGER NOT NULL)"
    )
    conn.execute("INSERT INTO documents VALUES ('old', 'a.docx', 'docx', 1, 2, 'Hello world', '[]')")
    conn.execute("INSERT INTO document_meta VALUES ('old', 'a.docx', 'docx', 1, 2, 0)")
    conn.commit()
    conn.close()
    store = HistoryStore(path)
    assert store.load_doc("old").batch_id is None
    assert store.load_metadata()[0].batch_id is None
    store.put_doc(doc(id="new", batch_id="b"))
    assert [d.id for d in store.load_batch("b")] == ["new"]
    store.close()
