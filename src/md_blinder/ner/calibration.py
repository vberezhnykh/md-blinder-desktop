"""Калибровка NER-бэкенда (порт src/workers/nerCalibration.ts).

Синтетическая многоязычная «канарейка»: бэкенд может отработать без ошибок, но
потерять различающую способность fp16. Принимаем его, только если найдены
известные сущности.
"""

from __future__ import annotations

import math
from typing import Sequence

from .gliner_span import Span

CALIBRATION_TEXT = (
    "Иван Петров подписал договор с ООО «Альфа». Олена Коваленко працює у ТОВ «Весна». "
    "John Smith signed for Acme Ltd."
)
CALIBRATION_LABELS = ["person", "company", "address", "registration number"]
CALIBRATION_THRESHOLD = 0.5
EXPECTED: tuple[tuple[str, str], ...] = (
    ("Иван Петров", "person"), ("ООО «Альфа»", "company"),
    ("Олена Коваленко", "person"), ("ТОВ «Весна»", "company"),
    ("John Smith", "person"), ("Acme Ltd", "company"),
)


class CalibrationError(RuntimeError):
    pass


def calibration_passes(matches: Sequence[Span]) -> bool:
    def found(value: str, label: str) -> bool:
        start = CALIBRATION_TEXT.find(value)
        return any(
            m.label == label and m.start == start and m.end == start + len(value)
            and math.isfinite(m.score) and m.score >= 0.5
            for m in matches
        )

    return all(found(value, label) for value, label in EXPECTED)


def assert_calibration(matches: Sequence[Span]) -> None:
    if not calibration_passes(matches):
        raise CalibrationError("NER backend не прошёл проверку качества RU/UA/EN")
