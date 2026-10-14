"""Локальная история документов в SQLite (порт src/lib/history.ts).

Две таблицы, как два хранилища IndexedDB: documents с полным текстом и
document_meta для списка истории, чтобы не читать все тексты при каждом
обновлении списка. Обе пишутся в одной транзакции.

Текст и значения сущностей хранятся без шифрования, как и в браузерной версии.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

from ..anonymize.types import ALL_ENTITY_TYPES, Entity, Position
from .offsets import OffsetMap

EXPORT_VERSION = 1
INVALID_EXPORT_ERROR = "Файл не похож на экспорт истории MD Blinder"

DocFormat = Literal["docx", "pdf"]


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class HistoryDoc:
    id: str
    name: str
    format: DocFormat
    created_at: int
    updated_at: int
    source_text: str
    entities: list[Entity] = field(default_factory=list)


@dataclass
class HistoryDocMeta:
    id: str
    name: str
    format: DocFormat
    created_at: int
    updated_at: int
    entity_count: int


def sanitize_entities(raw: Any, source_length: Optional[int] = None) -> list[Entity]:
    """Никогда не бросает: битая сущность отбрасывается, неизвестный тип становится OTHER."""
    if not isinstance(raw, list):
        return []
    result: list[Entity] = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        if not all(isinstance(e.get(k), str) for k in ("id", "value", "placeholder", "type")):
            continue
        type_ = e["type"] if e["type"] in ALL_ENTITY_TYPES else "OTHER"
        if not isinstance(e.get("positions"), list):
            continue
        positions: list[Position] = []
        for p in e["positions"]:
            if not isinstance(p, dict):
                continue
            start, end = p.get("start"), p.get("end")
            if isinstance(start, bool) or isinstance(end, bool):
                continue
            if isinstance(start, float) and start.is_integer():
                start = int(start)
            if isinstance(end, float) and end.is_integer():
                end = int(end)
            if not isinstance(start, int) or not isinstance(end, int):
                continue
            if start < 0 or start >= end or (source_length is not None and end > source_length):
                continue
            positions.append(Position(start, end))
        if not positions:
            continue
        score = e.get("score")
        result.append(Entity(
            id=e["id"],
            type=type_,
            value=e["value"],
            placeholder=e["placeholder"],
            positions=positions,
            anonymize=e["anonymize"] if isinstance(e.get("anonymize"), bool) else True,
            manual=bool(e.get("manual")),
            score=float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
            candidate=e.get("candidate") is True,
            candidate_note=e["candidateNote"] if isinstance(e.get("candidateNote"), str) else None,
            related_to=e["relatedTo"] if isinstance(e.get("relatedTo"), str) else None,
            dismissed=e.get("dismissed") is True,
        ))
    return result


def _entities_to_json(entities: list[Entity]) -> list[dict[str, Any]]:
    return [e.to_json() for e in entities]


# --- Формат экспорта: совместим с браузерной версией, смещения в UTF-16 ---

def _doc_to_export(doc: HistoryDoc) -> dict[str, Any]:
    offsets = OffsetMap(doc.source_text)
    entities = []
    for e in doc.entities:
        data = e.to_json()
        if not offsets.trivial:
            data["positions"] = [
                {"start": offsets.to_utf16(p.start), "end": offsets.to_utf16(p.end)} for p in e.positions
            ]
        entities.append(data)
    return {
        "id": doc.id,
        "name": doc.name,
        "format": doc.format,
        "createdAt": doc.created_at,
        "updatedAt": doc.updated_at,
        "sourceText": doc.source_text,
        "entities": entities,
    }


def serialize_history(docs: list[HistoryDoc]) -> str:
    return json.dumps(
        {
            "app": "md-blinder",
            "kind": "history",
            "version": EXPORT_VERSION,
            "exportedAt": now_ms(),
            "documents": [_doc_to_export(d) for d in docs],
        },
        ensure_ascii=False,
        indent=2,
    )


def parse_history(text: str) -> list[HistoryDoc]:
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ValueError(INVALID_EXPORT_ERROR) from exc
    if not isinstance(parsed, dict):
        raise ValueError(INVALID_EXPORT_ERROR)
    if parsed.get("app") != "md-blinder" or parsed.get("kind") != "history" or not isinstance(parsed.get("documents"), list):
        raise ValueError(INVALID_EXPORT_ERROR)
    if "version" in parsed and parsed["version"] != EXPORT_VERSION:
        raise ValueError("Версия экспорта истории не поддерживается. Обновите приложение.")
    docs: list[HistoryDoc] = []
    for d in parsed["documents"]:
        if not isinstance(d, dict):
            raise ValueError(INVALID_EXPORT_ERROR)
        if not (isinstance(d.get("id"), str) and isinstance(d.get("name"), str)
                and isinstance(d.get("sourceText"), str) and isinstance(d.get("entities"), list)):
            raise ValueError(INVALID_EXPORT_ERROR)
        source = d["sourceText"]
        offsets = OffsetMap(source)
        raw_entities = d["entities"]
        if not offsets.trivial:
            raw_entities = [_entity_from_utf16(e, offsets) for e in raw_entities]
        now = now_ms()
        created = d.get("createdAt")
        updated = d.get("updatedAt")
        docs.append(HistoryDoc(
            id=d["id"],
            name=d["name"],
            format="pdf" if d.get("format") == "pdf" else "docx",
            created_at=int(created) if isinstance(created, (int, float)) and not isinstance(created, bool) else now,
            updated_at=int(updated) if isinstance(updated, (int, float)) and not isinstance(updated, bool) else now,
            source_text=source,
            entities=sanitize_entities(raw_entities, len(source)),
        ))
    return docs


def _entity_from_utf16(e: Any, offsets: OffsetMap) -> Any:
    if not isinstance(e, dict) or not isinstance(e.get("positions"), list):
        return e
    positions = []
    for p in e["positions"]:
        if isinstance(p, dict) and isinstance(p.get("start"), int) and isinstance(p.get("end"), int):
            positions.append({"start": offsets.from_utf16(p["start"]), "end": offsets.from_utf16(p["end"])})
        else:
            positions.append(p)
    return {**e, "positions": positions}


def merge_docs(existing: list[HistoryDoc], incoming: list[HistoryDoc]) -> list[HistoryDoc]:
    """Для одинакового id побеждает более новый updated_at."""
    by_id = {d.id: d for d in existing}
    for doc in incoming:
        current = by_id.get(doc.id)
        if current is None or doc.updated_at > current.updated_at:
            by_id[doc.id] = doc
    return list(by_id.values())


class HistoryStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, name TEXT NOT NULL, format TEXT NOT NULL,"
            " created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, source_text TEXT NOT NULL,"
            " entities TEXT NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS document_meta (id TEXT PRIMARY KEY, name TEXT NOT NULL, format TEXT NOT NULL,"
            " created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, entity_count INTEGER NOT NULL)"
        )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def put_doc(self, doc: HistoryDoc) -> None:
        entities = json.dumps(_entities_to_json(doc.entities), ensure_ascii=False)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)",
                (doc.id, doc.name, doc.format, doc.created_at, doc.updated_at, doc.source_text, entities),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO document_meta VALUES (?, ?, ?, ?, ?, ?)",
                (doc.id, doc.name, doc.format, doc.created_at, doc.updated_at, len(doc.entities)),
            )

    def delete_doc(self, doc_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            self._conn.execute("DELETE FROM document_meta WHERE id = ?", (doc_id,))

    def _row_to_doc(self, row: tuple) -> HistoryDoc:
        doc_id, name, fmt, created, updated, source, entities = row
        try:
            raw = json.loads(entities)
        except ValueError:
            raw = []
        return HistoryDoc(doc_id, name, fmt, created, updated, source, sanitize_entities(raw, len(source)))

    def load_doc(self, doc_id: str) -> Optional[HistoryDoc]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return self._row_to_doc(row) if row else None

    def load_all(self) -> list[HistoryDoc]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM documents ORDER BY updated_at DESC").fetchall()
        return [self._row_to_doc(r) for r in rows]

    def load_metadata(self) -> list[HistoryDocMeta]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM document_meta ORDER BY updated_at DESC").fetchall()
        return [HistoryDocMeta(*r) for r in rows]
