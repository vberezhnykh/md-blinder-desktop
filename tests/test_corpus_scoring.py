"""Оценка корпуса (scripts/corpus_scoring.py) на синтетических данных."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("corpus_scoring", ROOT / "scripts" / "corpus_scoring.py")
scoring = importlib.util.module_from_spec(_spec)
sys.modules["corpus_scoring"] = scoring  # dataclass ищет свой модуль в sys.modules
_spec.loader.exec_module(scoring)


def det(type_, value, positions=(), anonymize=True, **extra):
    return {"type": type_, "value": value, "positions": [{"start": s, "end": e} for s, e in positions],
            "anonymize": anonymize, **extra}


TEXT = "Договор между ООО «Альфа» и Ивановым Петром Сергеевичем. Подписал П. С. Иванов, ООО «Альфа»."


def test_anchor_hit_survives_morphology():
    gold = [{"type": "PERSON", "value": "Иванов Петр Сергеевич", "variants": ["Ивановым Петром Сергеевичем"]}]
    start = TEXT.index("Ивановым")
    detected = [det("PERSON", "Ивановым Петром Сергеевичем", [(start, start + 27)])]
    result = scoring.score_detections(gold, detected, TEXT)
    assert [r.status for r in result.gold_results] == ["HIT"]
    assert result.gold_results[0].match_kind == "anchor"
    assert result.extras == []


def test_company_core_and_initials_are_full_hits():
    gold = [{"type": "COMPANY", "value": "ООО «Альфа»"}, {"type": "PERSON", "value": "Иванов Петр Сергеевич"}]
    detected = [det("COMPANY", "Альфа"), det("PERSON", "П. С. Иванов")]
    statuses = [(r.status, r.match_kind) for r in scoring.score_detections(gold, detected, "").gold_results]
    assert statuses == [("HIT", "company-core"), ("HIT", "person-initials")]


def test_one_detection_counts_once_and_right_type_wins():
    gold = [{"type": "COMPANY", "value": "Альфа"}, {"type": "COMPANY", "value": "Альфа Групп"}]
    detected = [det("PERSON", "Альфа Групп"), det("COMPANY", "Альфа")]
    result = scoring.score_detections(gold, detected, "")
    assert [r.status for r in result.gold_results] == ["HIT", "TYPE_MISMATCH"]
    assert result.extras == []


def test_partial_and_extra():
    gold = [{"type": "ADDRESS", "value": "г. Москва, ул. Тверская, д. 7"}]
    detected = [det("ADDRESS", "ул. Тверская"), det("PERSON", "Директор")]
    result = scoring.score_detections(gold, detected, "")
    assert [r.status for r in result.gold_results] == ["PARTIAL"]
    assert [e["value"] for e in result.extras] == ["Директор"]


def test_occurrence_coverage_needs_every_character_and_active_positions():
    gold = [{"type": "COMPANY", "value": "ООО «Альфа»"}]
    first = TEXT.index("ООО «Альфа»")
    second = TEXT.rindex("ООО «Альфа»")
    detected = [
        det("COMPANY", "ООО «Альфа»", [(first, first + 11)]),
        det("COMPANY", "Альфа", [(second + 5, second + 10)]),
        det("COMPANY", "ООО", [(second, second + 3)], anonymize=False),
    ]
    summary = scoring.score_occurrence_coverage(gold, detected, TEXT)
    assert (summary.total, summary.full, summary.partial, summary.missed) == (2, 1, 1, 0)


def test_unanchored_gold_and_invalid_positions_are_reported():
    gold = [{"type": "PERSON", "value": "Сидоров"}]
    summary = scoring.score_occurrence_coverage(gold, [det("PERSON", "x", [(5, 2)])], TEXT)
    assert summary.unanchored == 1 and summary.total == 0 and summary.invalid_positions == 1


def test_contained_alias_is_one_occurrence():
    gold = [{"type": "COMPANY", "value": "ООО «Альфа»", "aliases": ["Альфа"]}]
    summary = scoring.score_occurrence_coverage(gold, [], TEXT)
    assert summary.total == 2 and summary.missed == 2
