"""Модель данных обезличивания. Порт src/lib/anonymize/types.ts.

Смещения (start, end) считаются в кодовых точках Python-строки. Браузерная версия
хранит их в единицах UTF-16; пересчет выполняется при импорте и экспорте истории
(storage/history.py).

Сущности считаются неизменяемыми: функции из apply.py возвращают новые объекты
через dataclasses.replace и не меняют переданные.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

EntityType = Literal[
    "PERSON", "COMPANY", "ADDRESS", "REG_NUMBER", "ID_DOC",
    "IBAN", "SWIFT", "ACCOUNT", "CARD", "EMAIL", "PHONE", "URL", "OTHER",
]

# Единственный источник правды о списке типов: интерфейс и экспорт читают его.
ALL_ENTITY_TYPES: tuple[EntityType, ...] = (
    "PERSON", "COMPANY", "ADDRESS", "REG_NUMBER", "ID_DOC",
    "IBAN", "SWIFT", "ACCOUNT", "CARD", "EMAIL", "PHONE", "URL", "OTHER",
)


@dataclass(frozen=True)
class Position:
    start: int
    end: int  # не включая


@dataclass(frozen=True)
class RawMatch:
    type: EntityType
    value: str
    start: int
    end: int
    score: Optional[float] = None  # уверенность NER; у регулярных выражений None


@dataclass
class Entity:
    id: str
    type: EntityType
    value: str  # значение первого вхождения на момент создания; после правок устаревает
    placeholder: str  # например [COMPANY_1]
    positions: list[Position]
    anonymize: bool = True
    manual: bool = False
    score: Optional[float] = None
    candidate: bool = False  # непроверенный кандидат; не заменяется, пока не принят
    candidate_note: Optional[str] = None
    related_to: Optional[str] = None  # id сущности, на которую похож кандидат
    dismissed: bool = False  # кандидат отклонен вручную

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "value": self.value,
            "placeholder": self.placeholder,
            "positions": [{"start": p.start, "end": p.end} for p in self.positions],
            "anonymize": self.anonymize,
            "manual": self.manual,
        }
        if self.score is not None:
            data["score"] = self.score
        if self.candidate:
            data["candidate"] = True
        if self.candidate_note is not None:
            data["candidateNote"] = self.candidate_note
        if self.related_to is not None:
            data["relatedTo"] = self.related_to
        if self.dismissed:
            data["dismissed"] = True
        return data

    @staticmethod
    def from_json(data: dict[str, Any]) -> "Entity":
        return Entity(
            id=str(data["id"]),
            type=data["type"],
            value=str(data.get("value", "")),
            placeholder=str(data.get("placeholder", "")),
            positions=[Position(int(p["start"]), int(p["end"])) for p in data.get("positions", [])],
            anonymize=bool(data.get("anonymize", True)),
            manual=bool(data.get("manual", False)),
            score=data.get("score"),
            candidate=bool(data.get("candidate", False)),
            candidate_note=data.get("candidateNote"),
            related_to=data.get("relatedTo"),
            dismissed=bool(data.get("dismissed", False)),
        )


@dataclass
class AnonymizationResult:
    markdown: str
    entities: list[Entity] = field(default_factory=list)
    ner_failed: bool = False
    ner_error: Optional[str] = None
