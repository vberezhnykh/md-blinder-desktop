"""Оценка находок по эталону корпуса (порт md-blinder/scripts/scoring-core.mjs).

Две метрики, как в веб-версии:

- score_detections — сопоставление эталонных сущностей с подтвержденными находками
  один к одному. Сначала пары правильного типа, внутри — по силе совпадения
  (anchor > exact > company-core/person-initials > partial); пары неправильного
  типа берутся только для оставшихся эталонных сущностей.
- score_occurrence_coverage — строгая полнота по вхождениям: каждое вхождение
  эталонного значения (value, variants, aliases) в исходнике должно быть целиком
  закрыто активными позициями (anonymize и не dismissed).

Смещения — в кодовых точках Python, как у находок десктопа (в JS — UTF-16).
Модуль не читает файлы и ничего не печатает: это делает eval_corpus.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import regex

_NON_WORD = regex.compile(r"[^\p{L}\p{N}]+")
_SPACES = regex.compile(r"\s+")
_HAS_WORD = regex.compile(r"[\p{L}\p{N}]")
_STARTS_WORD = regex.compile(r"^[\p{L}\p{N}]")
_ENDS_WORD = regex.compile(r"[\p{L}\p{N}]$")

ORG_PREFIXES = frozenset({
    "ооо", "оао", "зао", "пао", "ао", "ип", "нко", "фгуп", "гуп",
    "llc", "ltd", "inc", "plc", "gmbh", "corp", "co",
})
TIER_RANK = {"anchor": 4, "exact": 3, "company-core": 2, "person-initials": 2, "partial": 1}


def norm(s: Optional[str]) -> str:
    return _SPACES.sub(" ", _NON_WORD.sub(" ", (s or "").lower()).strip())


def _tokens(s: str) -> set[str]:
    return {t for t in norm(s).split(" ") if t}


def jaccard(a: str, b: str) -> float:
    left, right = _tokens(a), _tokens(b)
    if not left or not right:
        return 0.0
    inter = len(left & right)
    return inter / (len(left) + len(right) - inter)


def _value_match(gold: str, det: str) -> Optional[str]:
    g, d = norm(gold), norm(det)
    if not g or not d:
        return None
    if g == d:
        return "full"
    if g in d or d in g or jaccard(g, d) >= 0.5:
        return "partial"
    return None


def _company_core(value: str) -> str:
    words = [w for w in norm(value).split(" ") if w]
    while words and words[0] in ORG_PREFIXES:
        words.pop(0)
    return " ".join(words)


def _person_initials_match(full_value: str, initials_value: str) -> bool:
    full = [w for w in norm(full_value).split(" ") if w]
    other = [w for w in norm(initials_value).split(" ") if w]
    if len(full) < 2 or len(other) < 2:
        return False
    initials = [t for t in other if len(t) == 1]
    surnames = [t for t in other if len(t) > 1]
    if not initials or len(surnames) != 1:
        return False
    surname = surnames[0]
    if surname not in full:
        return False
    index = full.index(surname)
    remaining = [w for i, w in enumerate(full) if i != index]
    if len(remaining) < len(initials):
        return False
    ri = 0
    for init in initials:
        while ri < len(remaining) and remaining[ri][0] != init:
            ri += 1
        if ri >= len(remaining):
            return False
        ri += 1
    return True


def _spaced_pattern(raw: str) -> str:
    return r"\s+".join(regex.escape(part) for part in raw.split())


def _find_anchor_span(source_text: str, candidates: list[str]) -> Optional[tuple[int, int]]:
    if not source_text:
        return None
    for cand in candidates:
        raw = (cand or "").strip()
        if not raw:
            continue
        m = regex.search(_spaced_pattern(raw), source_text, regex.IGNORECASE)
        if m:
            return (m.start(), m.end())
    return None


def _positions(det: dict[str, Any]) -> list[tuple[int, int]]:
    out = []
    for p in det.get("positions") or []:
        if isinstance(p, dict) and isinstance(p.get("start"), int) and isinstance(p.get("end"), int):
            out.append((p["start"], p["end"]))
    return out


def _best_tier(gold: dict[str, Any], det: dict[str, Any], anchor: Optional[tuple[int, int]]) -> Optional[tuple[str, str]]:
    candidates = [gold["value"], *(gold.get("variants") or [])]
    if anchor:
        for start, end in _positions(det):
            if start < anchor[1] and anchor[0] < end:
                return ("anchor", "full")
    det_norm = norm(det.get("value"))
    for cand in candidates:
        if norm(cand) and norm(cand) == det_norm:
            return ("exact", "full")
    if gold["type"] == "COMPANY" and det.get("type") == "COMPANY":
        det_core = _company_core(det.get("value") or "")
        if det_core and any(_company_core(c) == det_core for c in candidates):
            return ("company-core", "full")
    if gold["type"] == "PERSON" and det.get("type") == "PERSON":
        value = det.get("value") or ""
        if any(_person_initials_match(c, value) or _person_initials_match(value, c) for c in candidates):
            return ("person-initials", "full")
    if any(_value_match(c, det.get("value") or "") == "partial" for c in candidates):
        return ("partial", "partial")
    return None


@dataclass
class GoldResult:
    gold: dict[str, Any]
    status: str  # HIT | PARTIAL | TYPE_MISMATCH | MISS
    matched: Optional[dict[str, Any]] = None
    match_kind: Optional[str] = None


@dataclass
class DetectionScore:
    gold_results: list[GoldResult]
    extras: list[dict[str, Any]]


def score_detections(gold_entities: list[dict[str, Any]], detected: list[dict[str, Any]], source_text: str) -> DetectionScore:
    anchors = [_find_anchor_span(source_text, [g["value"], *(g.get("variants") or [])]) for g in gold_entities]
    pairs = []
    for gi, gold in enumerate(gold_entities):
        for di, det in enumerate(detected):
            tier = _best_tier(gold, det, anchors[gi])
            if tier:
                pairs.append((gi, di, tier[0], tier[1], det.get("type") == gold["type"]))
    # Стабильная сортировка, как Array.prototype.sort в современных движках JS.
    pairs.sort(key=lambda p: (not p[4], -TIER_RANK[p[2]]))
    gold_taken = [False] * len(gold_entities)
    det_taken = [False] * len(detected)
    # Одна находка закрывает несколько эталонных значений, если ее вхождения лежат на
    # каждом из них: имя латиницей и кириллицей в двуязычном договоре — одна сущность.
    det_anchors: list[list[tuple[int, int]]] = [[] for _ in detected]
    det_exclusive = [False] * len(detected)
    assignment: list[Optional[tuple]] = [None] * len(gold_entities)
    for type_ok_pass in (True, False):
        for p in pairs:
            if p[4] != type_ok_pass or gold_taken[p[0]]:
                continue
            anchor = anchors[p[0]]
            if p[2] == "anchor" and anchor and not det_exclusive[p[1]]:
                if any(a[0] < anchor[1] and anchor[0] < a[1] for a in det_anchors[p[1]]):
                    continue
                det_anchors[p[1]].append(anchor)
            elif det_taken[p[1]]:
                continue
            else:
                det_exclusive[p[1]] = True
            gold_taken[p[0]] = det_taken[p[1]] = True
            assignment[p[0]] = p
    results = []
    for gi, gold in enumerate(gold_entities):
        p = assignment[gi]
        if p is None:
            results.append(GoldResult(gold, "MISS"))
            continue
        status = "TYPE_MISMATCH" if not p[4] else ("HIT" if p[3] == "full" else "PARTIAL")
        results.append(GoldResult(gold, status, detected[p[1]], p[2]))
    extras = [det for di, det in enumerate(detected) if not det_taken[di]]
    return DetectionScore(results, extras)


def _gold_spellings(gold: dict[str, Any]) -> list[str]:
    values = [gold.get("value"), *(gold.get("variants") or []), *(gold.get("aliases") or [])]
    return [v for v in values if isinstance(v, str) and _HAS_WORD.search(v)]


def _all_anchors(source_text: str, spellings: list[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for spelling in dict.fromkeys(spellings):
        raw = spelling.strip()
        left = r"(?<![\p{L}\p{N}])" if _STARTS_WORD.search(raw) else ""
        right = r"(?![\p{L}\p{N}])" if _ENDS_WORD.search(raw) else ""
        pattern = regex.compile(left + _spaced_pattern(raw) + right, regex.IGNORECASE)
        spans.extend((m.start(), m.end()) for m in pattern.finditer(source_text))
    spans.sort(key=lambda s: (s[0], -s[1]))
    unique = []
    longest_end = -1
    for start, end in spans:
        # Вложенный короткий вариант — то же вхождение, что длинное имя.
        if end <= longest_end:
            continue
        unique.append((start, end))
        longest_end = end
    return unique


@dataclass
class OccurrenceSummary:
    total: int = 0
    full: int = 0
    partial: int = 0
    missed: int = 0
    unanchored: int = 0
    invalid_positions: int = 0
    missed_values: list[str] = field(default_factory=list)


def score_occurrence_coverage(gold_entities: list[dict[str, Any]], detected: list[dict[str, Any]], source_text: str) -> OccurrenceSummary:
    occurrences: dict[tuple[int, int], None] = {}
    summary = OccurrenceSummary()
    for gold in gold_entities:
        spans = _all_anchors(source_text, _gold_spellings(gold))
        if not spans:
            summary.unanchored += 1
        for span in spans:
            occurrences.setdefault(span, None)
    active = []
    for det in detected:
        if det.get("anonymize") is not True or det.get("dismissed"):
            continue
        for p in det.get("positions") or []:
            start, end = p.get("start"), p.get("end")
            if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or end > len(source_text):
                summary.invalid_positions += 1
                continue
            active.append([start, end])
    active.sort(key=lambda a: (a[0], -a[1]))
    covered: list[list[int]] = []
    for start, end in active:
        if covered and start <= covered[-1][1]:
            covered[-1][1] = max(covered[-1][1], end)
        else:
            covered.append([start, end])
    first = 0
    for start, end in sorted(occurrences, key=lambda s: (s[0], -s[1])):
        while first < len(covered) and covered[first][1] <= start:
            first += 1
        chars = 0
        i = first
        while i < len(covered) and covered[i][0] < end:
            chars += max(0, min(end, covered[i][1]) - max(start, covered[i][0]))
            i += 1
        summary.total += 1
        if chars == end - start:
            summary.full += 1
        elif chars > 0:
            summary.partial += 1
        else:
            summary.missed += 1
            summary.missed_values.append(source_text[start:end])
    return summary
