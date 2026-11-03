"""Прогон тестового корпуса: конвертация, поиск ПДн кодом программы и метрики.

    python scripts/eval_corpus.py <папка test-samples> [--cache <папка>] [--set dev|holdout]

Корпус конфиденциальный и в репозиторий не входит (см. README). Скрипт печатает
только сводные числа, ни одного значения из документов: полноту по эталонным
сущностям, строгую полноту, полноту по вхождениям и шум (подтвержденные находки,
которых нет в эталоне). С --details <файл> пишет разбор по документам со
значениями в JSON для локального просмотра; этот файл никуда не выкладывать.

Кеш. Конвертация (с OCR) и ответ модели NER сохраняются в --cache (по умолчанию
<test-samples>/../cache). Ключ конвертации — хеш файла и исходников convert/,
ключ NER — хеш Markdown, ревизия модели и исходники ner/ и предобработки текста.
Правка фильтра находок, склейки, правил или поиска повторов модель не
перезапускает: повторный прогон корпуса занимает секунды. Без скачанной модели
NER (MD_BLINDER_DATA_DIR) работает только с готовым кешем или с --no-ner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus_scoring import norm, score_detections, score_occurrence_coverage  # noqa: E402

from md_blinder.anonymize.batch import reconcile_batch_placeholders, seed_from_batch  # noqa: E402
from md_blinder.anonymize.pipeline import anonymize_markdown  # noqa: E402
from md_blinder.anonymize.related import find_related_occurrences  # noqa: E402
from md_blinder.anonymize.types import RawMatch  # noqa: E402
from md_blinder.components import model_asset  # noqa: E402

SRC = ROOT / "src" / "md_blinder"
CONVERT_SOURCES = sorted((SRC / "convert").glob("*.py"))
NER_SOURCES = sorted((SRC / "ner").glob("*.py")) + [SRC / "anonymize" / "strip_formatting.py"]


def _sources_hash(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for path in paths:
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


class Cache:
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)

    def get(self, kind: str, key: str) -> Optional[Any]:
        path = self.dir / kind / f"{key}.json"
        return json.loads(path.read_text("utf-8")) if path.exists() else None

    def put(self, kind: str, key: str, value: Any) -> None:
        path = self.dir / kind / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), "utf-8")
        tmp.replace(path)


def convert(path: Path, fmt: str) -> str:
    """Как ProcessingJob._run: docx/xlsx напрямую, PDF с OCR страниц без текстового слоя."""
    if fmt == "docx" or path.suffix.lower() == ".docx":
        from md_blinder.convert.docx import docx_to_markdown

        md = docx_to_markdown(path)
    elif path.suffix.lower() == ".xlsx":
        from md_blinder.convert.xlsx import xlsx_to_markdown

        md = xlsx_to_markdown(path)
    else:
        from md_blinder.components import ocr_models
        from md_blinder.convert.ocr import ocr_pdf_pages
        from md_blinder.convert.pdf import analyze_pdf, assemble_markdown

        analysis = analyze_pdf(path)
        recognized: dict[int, str] = {}
        if analysis.ocr_page_numbers:
            if not ocr_models.is_ocr_present():
                raise SystemExit("Нужны модели OCR: python scripts/fetch_ocr_models.py")
            recognized = ocr_pdf_pages(path, analysis.ocr_page_numbers, ocr_models.ocr_models_dir())
        md = assemble_markdown(analysis, recognized)
    return md.replace("\r\n", "\n").replace("\r", "\n")


class Ner:
    def __init__(self, cache: Cache, enabled: bool) -> None:
        self.cache = cache
        self.enabled = enabled
        self.model = None
        self.key_suffix = f"{model_asset.MODEL_REVISION[:12]}-{_sources_hash(NER_SOURCES)}"
        self.seconds = 0.0
        self.runs = 0

    def detect(self, md: str) -> list[RawMatch]:
        key = f"{_sha(md.encode())}-{self.key_suffix}"
        cached = self.cache.get("ner", key)
        if cached is not None:
            return [RawMatch(**m) for m in cached]
        if not self.enabled:
            return []
        if self.model is None:
            if not model_asset.is_model_present():
                raise SystemExit("Модель NER не скачана (MD_BLINDER_DATA_DIR). Запустите с --no-ner или скачайте модель.")
            from md_blinder.ner.engine import load_model

            self.model = load_model(model_asset.model_dir(), devices=["cpu"])
        from md_blinder.ner.engine import detect_entities_ner

        started = time.perf_counter()
        matches = detect_entities_ner(md, self.model)
        self.seconds += time.perf_counter() - started
        self.runs += 1
        self.cache.put("ner", key, [asdict(m) for m in matches])
        return matches


def detect(md: str, ner: Ner) -> list[dict[str, Any]]:
    """Как ProcessingJob._detect для одиночного документа (без пакета)."""
    result = anonymize_markdown(md, ner.detect)
    if result.ner_failed:
        raise SystemExit(f"Сбой NER: {result.ner_error}")
    seeded = seed_from_batch(md, result.entities, [])
    related = find_related_occurrences(md, seeded)
    entities = reconcile_batch_placeholders(seeded + related, md, [])
    return [e.to_json() for e in entities]


def _totals() -> dict[str, float]:
    keys = ("HIT", "PARTIAL", "TYPE_MISMATCH", "MISS", "extras", "noise", "candidates", "tokens",
            "occ_total", "occ_full", "occ_partial", "occ_missed", "unanchored", "documents")
    return dict.fromkeys(keys, 0)


def _summary(t: dict[str, float]) -> dict[str, Any]:
    gold = t["HIT"] + t["PARTIAL"] + t["TYPE_MISMATCH"] + t["MISS"]
    return {
        "documents": t["documents"],
        "gold": gold,
        "recall": round(100 * (t["HIT"] + 0.5 * t["PARTIAL"]) / gold, 1) if gold else None,
        "strict": round(100 * t["HIT"] / gold, 1) if gold else None,
        "occurrence_recall": round(100 * t["occ_full"] / t["occ_total"], 1) if t["occ_total"] else None,
        "hit": t["HIT"], "partial": t["PARTIAL"], "type_mismatch": t["TYPE_MISMATCH"], "miss": t["MISS"],
        "occurrences": {"total": t["occ_total"], "full": t["occ_full"], "partial": t["occ_partial"],
                        "missed": t["occ_missed"], "unanchored_gold": t["unanchored"]},
        "noise": t["noise"],
        "over_capture": t["extras"] - t["noise"],
        "noise_per_1k_tokens": round(t["noise"] / (t["tokens"] / 1000), 2) if t["tokens"] else None,
        "candidates": t["candidates"],
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("corpus", type=Path, help="папка test-samples (manifest.json, gold/, dev/, holdout/)")
    parser.add_argument("--cache", type=Path, help="папка кеша (по умолчанию <corpus>/../cache)")
    parser.add_argument("--set", choices=("dev", "holdout"), help="только один набор")
    parser.add_argument("--no-ner", action="store_true", help="не запускать модель; брать NER только из кеша")
    parser.add_argument("--json", type=Path, help="записать сводку в файл (без значений из документов)")
    parser.add_argument("--details", type=Path, help="разбор по документам СО ЗНАЧЕНИЯМИ, только локально")
    args = parser.parse_args(argv)

    corpus: Path = args.corpus
    cache = Cache(args.cache or corpus.parent / "cache")
    manifest = json.loads((corpus / "manifest.json").read_text("utf-8"))
    file_to_gold: dict[str, dict] = {}
    for gold_path in sorted((corpus / "gold").glob("contract-*.json")):
        gold = json.loads(gold_path.read_text("utf-8"))
        for name in gold.get("files", []):
            file_to_gold[name] = gold
    try:
        triage = json.loads((corpus / "extras-triage.json").read_text("utf-8"))
    except FileNotFoundError:
        triage = {}

    ner = Ner(cache, enabled=not args.no_ner)
    convert_suffix = _sources_hash(CONVERT_SOURCES)
    totals = {"dev": _totals(), "holdout": _totals(), "combined": _totals()}
    details: dict[str, Any] = {}
    started = time.perf_counter()
    for doc in manifest["documents"]:
        if args.set and doc["set"] != args.set:
            continue
        path = corpus / doc["path"]
        gold = file_to_gold.get(path.name)
        if gold is None or not path.exists():
            print(f"[пропуск] документ {doc['set']} без эталона или файла", file=sys.stderr)
            continue
        key = f"{_sha(path.read_bytes())}-{convert_suffix}"
        md = cache.get("md", key)
        if md is None:
            md = convert(path, doc["format"])
            cache.put("md", key, md)
        entities = detect(md, ner)
        confirmed = [e for e in entities if not e.get("candidate")]
        scored = score_detections(gold["entities"], confirmed, md)
        coverage = score_occurrence_coverage(gold["entities"], entities, md)
        for bucket in (totals[doc["set"]], totals["combined"]):
            bucket["documents"] += 1
            bucket["tokens"] += len(md.split())
            for r in scored.gold_results:
                bucket[r.status] += 1
            bucket["extras"] += len(scored.extras)
            bucket["noise"] += sum(
                1 for e in scored.extras if triage.get(f"{path.name}|{e['type']}|{norm(e['value'])}") != "over-capture"
            )
            bucket["candidates"] += sum(1 for e in entities if e.get("candidate") and not e.get("dismissed"))
            bucket["occ_total"] += coverage.total
            bucket["occ_full"] += coverage.full
            bucket["occ_partial"] += coverage.partial
            bucket["occ_missed"] += coverage.missed
            bucket["unanchored"] += coverage.unanchored
        if args.details:
            details[doc["path"]] = {
                "results": [{"status": r.status, "type": r.gold["type"], "gold": r.gold["value"],
                             "matched": r.matched and r.matched["value"], "kind": r.match_kind}
                            for r in scored.gold_results],
                "extras": [{"type": e["type"], "value": e["value"]} for e in scored.extras],
                "missed_occurrences": coverage.missed_values,
            }

    summary = {name: _summary(t) for name, t in totals.items() if t["documents"]}
    summary["seconds"] = round(time.perf_counter() - started, 1)
    summary["ner_runs"] = ner.runs
    summary["ner_seconds"] = round(ner.seconds, 1)
    for name in ("dev", "holdout", "combined"):
        s = summary.get(name)
        if not s:
            continue
        print(f"{name:9} документов {s['documents']:2}  полнота {s['recall']}%  строгая {s['strict']}%  "
              f"по вхождениям {s['occurrence_recall']}%  шум {s['noise']}  кандидатов {s['candidates']}")
    print(f"время {summary['seconds']} с, модель запускалась {ner.runs} раз ({summary['ner_seconds']} с)")
    if args.json:
        args.json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    if args.details:
        args.details.write_text(json.dumps(details, ensure_ascii=False, indent=2), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
