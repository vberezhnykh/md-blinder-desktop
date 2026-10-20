"""Замер NER на настоящей модели: на что уходит время и что дают варианты сессии.

Не часть приложения: запускается вручную или workflow ner-bench.yml. Каждый вариант
прогоняется по тем же фрагментам, что и в приложении (strip → chunk_text →
plan_batches), а его спаны сравниваются с текущим вариантом (fp16, CPU).

    python scripts/bench_ner.py --model-dir model --out bench.json
    python scripts/bench_ner.py --model-dir model --only dml --baseline bench.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from md_blinder.anonymize.config import NER_CONFIRM_THRESHOLD, NER_GRAY_ZONE_LOW  # noqa: E402
from md_blinder.anonymize.strip_formatting import strip_inline_formatting  # noqa: E402
from md_blinder.convert.xlsx import xlsx_to_markdown  # noqa: E402
from md_blinder.ner import chunk_text as chunk_mod  # noqa: E402
from md_blinder.ner.batching import count_words, plan_batches  # noqa: E402
from md_blinder.ner.calibration import CALIBRATION_LABELS, CALIBRATION_TEXT, calibration_passes  # noqa: E402
from md_blinder.ner.engine import NER_LABELS  # noqa: E402
from md_blinder.ner.gliner_span import decode_spans, prepare_span_batch  # noqa: E402
from md_blinder.ner.runtime import _LEVELS, SIMPLIFIED_LAYER_NORM_FUSION, load_tokenizer  # noqa: E402

# ---------------------------------------------------------------- рабочие тексты

FIRST = ["Иван", "Петр", "Алексей", "Мария", "Ольга", "Сергей", "Наталья", "Дмитрий", "Елена", "Андрей",
         "Олена", "Тарас", "Оксана", "John", "Anna", "Michael"]
LAST = ["Петров", "Сидоров", "Кузнецова", "Смирнов", "Волкова", "Коваленко", "Шевченко", "Бондаренко",
        "Морозов", "Лебедева", "Smith", "Brown", "Новиков", "Федорова", "Мельник", "Ткаченко"]
PATRON = ["Иванович", "Петровна", "Сергеевич", "Андреевна", "Олегович", "Викторовна"]
COMPANY = ["ООО «Альфа»", "АО «Северный терминал»", "ТОВ «Весна»", "ООО «Ромашка-Трейд»", "ПАО «Восток»",
           "Acme Ltd", "ИП Соколов", "ООО «Гранит Строй»", "ТОВ «Зоря»", "АО «Медиа Плюс»"]
STREET = ["ул. Лесная", "пр-т Мира", "ул. Садовая", "вулиця Хрещатик", "ул. Ленина", "Baker Street",
          "наб. Обводного канала", "ул. Профсоюзная"]
CITY = ["Москва", "Санкт-Петербург", "Киев", "Казань", "Новосибирск", "Львов", "London", "Екатеринбург"]
CLAUSES = [
    "{p} обязуется передать {c} товар в срок не позднее {d} рабочих дней с даты подписания настоящего договора.",
    "Оплата производится на расчетный счет {c} в течение {d} банковских дней после получения счета.",
    "Представитель {c} {p}, действующий на основании доверенности № {n}, подтверждает полномочия.",
    "Место исполнения обязательств: г. {city}, {street}, д. {d}, кв. {d2}.",
    "Стороны договорились, что все уведомления направляются по адресу: {city}, {street}, {d}.",
    "В случае просрочки {c} уплачивает неустойку в размере {d2} % от суммы задолженности за каждый день.",
    "Ответственным за исполнение со стороны заказчика назначен {p}, телефон для связи указан в приложении.",
    "Настоящий договор составлен в двух экземплярах, имеющих равную юридическую силу, по одному для каждой стороны.",
    "Споры разрешаются в Арбитражном суде г. {city} в порядке, установленном законодательством.",
    "Акт приемки подписывают {p} и {p2} не позднее {d} числа месяца, следующего за отчетным.",
]


def _person(rng: random.Random) -> str:
    if rng.random() < 0.5:
        return f"{rng.choice(LAST)} {rng.choice(FIRST)} {rng.choice(PATRON)}"
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def prose(paragraphs: int, seed: int = 1) -> str:
    rng = random.Random(seed)
    parts = []
    for i in range(paragraphs):
        sentences = [
            rng.choice(CLAUSES).format(
                p=_person(rng), p2=_person(rng), c=rng.choice(COMPANY), city=rng.choice(CITY),
                street=rng.choice(STREET), d=rng.randint(1, 30), d2=rng.randint(1, 99), n=rng.randint(100, 9999),
            )
            for _ in range(rng.randint(2, 5))
        ]
        parts.append(f"{i + 1}. " + " ".join(sentences))
    return "# Договор поставки\n\n" + "\n\n".join(parts)


def table(rows: int, seed: int = 2) -> str:
    import openpyxl

    rng = random.Random(seed)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Сотрудники"
    ws.append(["ФИО", "Дата рождения", "Организация", "Адрес", "Телефон", "Сумма"])
    for _ in range(rows):
        ws.append([
            _person(rng), f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.{rng.randint(1960, 2002)}",
            rng.choice(COMPANY), f"г. {rng.choice(CITY)}, {rng.choice(STREET)}, д. {rng.randint(1, 120)}",
            f"+7 9{rng.randint(10, 99)} {rng.randint(100, 999)}-{rng.randint(10, 99)}-{rng.randint(10, 99)}",
            rng.randint(1000, 250000),
        ])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.xlsx"
        wb.save(path)
        return xlsx_to_markdown(path)


# ---------------------------------------------------------------- преобразование fp16 → fp32


def convert_fp16_to_fp32(src: Path, dst: Path) -> float:
    """Те же веса fp16, поднятые до fp32: граф без Cast'ов, которые ORT вставляет
    вокруг узлов без fp16-ядер на CPU. Возвращает время в секундах."""
    import onnx
    from onnx import AttributeProto, TensorProto, numpy_helper

    t0 = time.perf_counter()
    model = onnx.load(str(src))

    def tensor(t: "onnx.TensorProto") -> None:
        if t.data_type == TensorProto.FLOAT16:
            t.CopyFrom(numpy_helper.from_array(numpy_helper.to_array(t).astype(np.float32), t.name))

    def walk(graph: "onnx.GraphProto") -> None:
        for t in graph.initializer:
            tensor(t)
        for vi in [*graph.value_info, *graph.input, *graph.output]:
            if vi.type.tensor_type.elem_type == TensorProto.FLOAT16:
                vi.type.tensor_type.elem_type = TensorProto.FLOAT
        for node in graph.node:
            for a in node.attribute:
                if node.op_type == "Cast" and a.name == "to" and a.i == TensorProto.FLOAT16:
                    a.i = TensorProto.FLOAT
                elif a.type == AttributeProto.TENSOR:
                    tensor(a.t)
                elif a.type == AttributeProto.GRAPH:
                    walk(a.g)
                elif a.type == AttributeProto.GRAPHS:
                    for g in a.graphs:
                        walk(g)

    walk(model.graph)
    onnx.save(model, str(dst))
    return time.perf_counter() - t0


def quantize_int8(src: Path, dst: Path) -> float:
    """Динамическое int8 только для MatMul (эмбеддинги и LayerNorm остаются fp32)."""
    from onnxruntime.quantization import QuantType, quantize_dynamic

    t0 = time.perf_counter()
    quantize_dynamic(str(src), str(dst), op_types_to_quantize=["MatMul"], per_channel=True,
                     weight_type=QuantType.QInt8)
    return time.perf_counter() - t0


# ---------------------------------------------------------------- прогон


@dataclass
class Variant:
    name: str
    model: str  # имя файла в каталоге модели
    threads: int = 0
    batch: int = 8
    provider: str = "cpu"
    level: str = "all"
    max_len: Optional[int] = None
    max_words: Optional[int] = None
    note: str = ""


@dataclass
class Workload:
    name: str
    plain: str
    stats: dict = field(default_factory=dict)


def make_session(path: Path, v: Variant) -> ort.InferenceSession:
    options = ort.SessionOptions()
    options.graph_optimization_level = _LEVELS[v.level]
    options.log_severity_level = 3
    if v.threads:
        options.intra_op_num_threads = v.threads
    kwargs = {"disabled_optimizers": [SIMPLIFIED_LAYER_NORM_FUSION]} if v.level == "all" else {}
    providers = {"cpu": ["CPUExecutionProvider"], "dml": ["DmlExecutionProvider", "CPUExecutionProvider"]}[v.provider]
    if v.provider == "dml":
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(str(path), options, providers=providers, **kwargs)


def infer(session, tokenizer, texts, labels, threshold, timing):
    t0 = time.perf_counter()
    b = prepare_span_batch(texts, labels, tokenizer)
    t1 = time.perf_counter()
    feeds = {"input_ids": b.input_ids, "attention_mask": b.attention_mask, "words_mask": b.words_mask,
             "text_lengths": b.text_lengths, "span_idx": b.span_idx, "span_mask": b.span_mask}
    (logits,) = session.run(["logits"], feeds)
    t2 = time.perf_counter()
    spans = decode_spans(np.asarray(logits, dtype=np.float32), b, texts, labels, threshold)
    t3 = time.perf_counter()
    timing["prepare"] += t1 - t0
    timing["run"] += t2 - t1
    timing["decode"] += t3 - t2
    timing["tokens"] += b.batch_size * b.token_count
    timing["real_tokens"] += int(b.attention_mask.sum())
    return spans


def chunks_for(plain: str, v: Variant):
    saved = chunk_mod.MAX_WORDS
    try:
        if v.max_words:
            chunk_mod.MAX_WORDS = v.max_words
        return chunk_mod.chunk_text(plain, v.max_len or chunk_mod.DEFAULT_MAX_LEN)
    finally:
        chunk_mod.MAX_WORDS = saved


def run_workload(session, tokenizer, w: Workload, v: Variant):
    chunks = chunks_for(w.plain, v)
    plan = plan_batches(chunks, v.batch)
    timing = dict(prepare=0.0, run=0.0, decode=0.0, tokens=0, real_tokens=0)
    t0 = time.perf_counter()
    found = []
    for texts in plan.batches:
        rows = infer(session, tokenizer, texts, list(NER_LABELS), NER_GRAY_ZONE_LOW, timing)
        for text, row in zip(texts, rows):
            for occ in plan.occurrences[text]:
                found += [(occ.base + s.start, occ.base + s.end, s.label, round(s.score, 4)) for s in row]
    timing["total"] = time.perf_counter() - t0
    timing["chunks"] = len(chunks)
    timing["batches"] = len(plan.batches)
    timing["words"] = sum(count_words(c.text) for c in chunks)
    return timing, sorted(found)


def changed(plain: str, base: list, other: list, thr: float = NER_CONFIRM_THRESHOLD) -> list[str]:
    """Спаны, которые пересекли порог подтверждения в ту или другую сторону."""
    a = {(s, e, lab): sc for s, e, lab, sc in base}
    b = {(s, e, lab): sc for s, e, lab, sc in other}
    out = []
    for key in sorted(a.keys() | b.keys()):
        sa, sb = a.get(key), b.get(key)
        if (sa is not None and sa >= thr) != (sb is not None and sb >= thr):
            s, e, lab = key
            out.append(f"{plain[s:e]!r} {lab}: {sa} → {sb}")
    return out


def compare(base: list, other: list) -> dict:
    def keyed(rows, thr):
        return {(s, e, lab): sc for s, e, lab, sc in rows if sc >= thr}

    out = {}
    for name, thr in (("confirmed", NER_CONFIRM_THRESHOLD), ("any", NER_GRAY_ZONE_LOW)):
        a, b = keyed(base, thr), keyed(other, thr)
        common = a.keys() & b.keys()
        out[name] = dict(base=len(a), lost=len(a.keys() - b.keys()), new=len(b.keys() - a.keys()),
                         max_delta=round(max((abs(a[k] - b[k]) for k in common), default=0.0), 4))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("bench.json"))
    ap.add_argument("--only", default="")
    ap.add_argument("--baseline", type=Path)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--full", action="store_true", help="еще потоки, длина фрагментов и int8")
    args = ap.parse_args()

    md = args.model_dir
    tokenizer = load_tokenizer(md)
    info = dict(platform=platform.platform(), processor=platform.processor(), cpus=os.cpu_count(),
                ort=ort.__version__, providers=ort.get_available_providers())
    print(json.dumps(info, ensure_ascii=False))

    workloads = [
        Workload("договор", strip_inline_formatting(prose(60)).plain),
        Workload("таблица", strip_inline_formatting(table(250)).plain),
    ]
    for w in workloads:
        chunks = chunk_mod.chunk_text(w.plain)
        words = [count_words(c.text) for c in chunks]
        pipes = sum(c.text.count("|") for c in chunks)
        w.stats = dict(chars=len(w.plain), chunks=len(chunks), words=sum(words), max_words=max(words),
                       pipe_share=round(pipes / max(sum(words), 1), 3))
        print(w.name, w.stats)

    if args.only == "dml":
        variants = [Variant("DirectML fp16", "model_fp16.onnx", provider="dml"),
                    Variant("DirectML fp16, layout", "model_fp16.onnx", provider="dml", level="layout")]
    else:
        fp32_local = md / "model_fp16_as_fp32.onnx"
        if not fp32_local.exists():
            print(f"fp16→fp32: {convert_fp16_to_fp32(md / 'model_fp16.onnx', fp32_local):.1f}s")
        int8 = md / "model_int8_matmul.onnx"
        if args.full and (md / "model.onnx").exists() and not int8.exists():
            print(f"int8 MatMul: {quantize_int8(md / 'model.onnx', int8):.1f}s")
        variants = [
            Variant("сейчас: fp16, батч 8", "model_fp16.onnx", note="эталон"),
            Variant("fp16, батч 1", "model_fp16.onnx", batch=1),
            Variant("fp16, батч 2", "model_fp16.onnx", batch=2),
            Variant("fp16, батч 4", "model_fp16.onnx", batch=4),
            Variant("fp16→fp32, батч 8", "model_fp16_as_fp32.onnx"),
            Variant("fp16→fp32, батч 1", "model_fp16_as_fp32.onnx", batch=1),
            Variant("fp16→fp32, батч 2", "model_fp16_as_fp32.onnx", batch=2),
            Variant("fp32 оригинал, батч 1", "model.onnx", batch=1),
        ]
        if args.full:
            variants += [
                Variant("fp16, 1 поток", "model_fp16.onnx", threads=1),
                Variant("fp16→fp32, 2 потока", "model_fp16_as_fp32.onnx", threads=2),
                Variant("fp16→fp32, фрагменты 150 слов", "model_fp16_as_fp32.onnx", max_len=700, max_words=150),
                Variant("int8 MatMul (из fp32)", "model_int8_matmul.onnx"),
            ]
        variants = [v for v in variants if (md / v.model).exists()]

    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    base_found = baseline["found"] if baseline else None
    results = []
    for v in variants:
        print(f"\n=== {v.name}", flush=True)
        t0 = time.perf_counter()
        try:
            session = make_session(md / v.model, v)
        except Exception as error:  # noqa: BLE001
            print("  сессия не создана:", error)
            results.append(dict(name=v.name, error=str(error)))
            continue
        init_s = time.perf_counter() - t0
        t1 = time.perf_counter()
        dummy = dict(prepare=0.0, run=0.0, decode=0.0, tokens=0, real_tokens=0)
        calib = infer(session, tokenizer, [CALIBRATION_TEXT], CALIBRATION_LABELS, 0.5, dummy)[0]
        warm_s = time.perf_counter() - t1
        row = dict(name=v.name, model=v.model, threads=v.threads, batch=v.batch, provider=v.provider,
                   init_s=round(init_s, 2), warmup_s=round(warm_s, 2), calibration=calibration_passes(calib),
                   workloads={}, found={})
        print(f"  сессия {init_s:.2f}s, прогрев {warm_s:.2f}s, калибровка {row['calibration']}", flush=True)
        for w in workloads:
            best = None
            for _ in range(max(1, args.repeat if v.provider == "cpu" else 1)):
                timing, found = run_workload(session, tokenizer, w, v)
                if best is None or timing["total"] < best["total"]:
                    best = timing
            row["workloads"][w.name] = {k: (round(x, 3) if isinstance(x, float) else x) for k, x in best.items()}
            row["found"][w.name] = found
            print(f"  {w.name}: {best['total']:.2f}s (run {best['run']:.2f}, prepare {best['prepare']:.2f}, "
                  f"decode {best['decode']:.2f}; паддинг {1 - best['real_tokens'] / max(best['tokens'], 1):.0%})",
                  flush=True)
        del session
        results.append(row)
        if base_found is not None:
            for w in workloads:
                lines = changed(w.plain, base_found[w.name], row["found"][w.name])
                for line in lines[:12]:
                    print(f"  Δ {w.name}: {line}")
        if base_found is None:
            base_found = row["found"]

    print("\n| вариант | калибровка | сессия, с | договор, с | таблица, с | потеряно/новых (≥0.3) из | max Δscore |")
    print("|---|---|---|---|---|---|---|")
    for row in results:
        if "error" in row:
            print(f"| {row['name']} | ошибка: {row['error'][:80]} | | | | | |")
            continue
        diffs = [compare(base_found[w], row["found"][w]) for w in row["found"] if w in base_found]
        lost = sum(d["confirmed"]["lost"] for d in diffs)
        new = sum(d["confirmed"]["new"] for d in diffs)
        delta = max((d["any"]["max_delta"] for d in diffs), default=0)
        row["vs_baseline"] = diffs
        wl = row["workloads"]
        print(f"| {row['name']} | {'да' if row['calibration'] else 'НЕТ'} | {row['init_s']} | "
              f"{wl['договор']['total']} | {wl['таблица']['total']} | {lost}/{new} из {sum(d['confirmed']['base'] for d in diffs)} | {delta} |")

    payload = dict(info=info, workloads={w.name: w.stats for w in workloads}, results=results,
                   found=base_found)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
