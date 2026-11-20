"""Порт src/workers/nerCalibration.test.ts."""

from __future__ import annotations

import dataclasses
import math

import pytest

from md_blinder.ner.calibration import CALIBRATION_TEXT, CalibrationError, assert_calibration
from md_blinder.ner.gliner_span import Span

MATCHES = [
    Span(text, CALIBRATION_TEXT.index(text), CALIBRATION_TEXT.index(text) + len(text), label, 0.9)
    for text, label in [
        ("Иван Петров", "person"), ("ООО «Альфа»", "company"), ("Олена Коваленко", "person"),
        ("ТОВ «Весна»", "company"), ("John Smith", "person"), ("Acme Ltd", "company"),
    ]
]


def test_accepts_backend_finding_six_entities() -> None:
    assert_calibration(MATCHES)


def test_rejects_backend_losing_names_or_companies() -> None:
    with pytest.raises(CalibrationError, match="проверку качества"):
        assert_calibration([m for m in MATCHES if m.label == "person"])
    with pytest.raises(CalibrationError):
        assert_calibration([dataclasses.replace(m, score=math.nan) for m in MATCHES])


def test_rejects_low_scores_and_shifted_offsets() -> None:
    with pytest.raises(CalibrationError):
        assert_calibration([dataclasses.replace(m, score=0.49) for m in MATCHES])
    with pytest.raises(CalibrationError):
        assert_calibration([dataclasses.replace(m, end=m.end - 1) for m in MATCHES])
