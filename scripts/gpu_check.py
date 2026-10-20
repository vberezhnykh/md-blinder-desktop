"""Проверка NER на видеокарте (DirectML) на машине пользователя.

Собирается в отдельный exe workflow'ом ner-bench.yml (onnxruntime-directml вместо
onnxruntime). Берет уже скачанную приложением модель, считает те же договор и
таблицу, что bench_ner.py, сначала на CPU (как в программе), потом на каждой
видеокарте, и сравнивает найденное. Отчет пишется в ner-gpu-report.txt рядом с exe.
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import onnxruntime as ort

import bench_ner as b
from md_blinder.components import model_asset
from md_blinder.ner.batching import NER_BATCH_SIZE

LEVELS = ("all", "layout", "extended", "basic", "disabled")
MAX_DEVICES = 3


def gpu_names() -> list[str]:
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"],
            capture_output=True, text=True, timeout=20,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


class Report:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lines: list[str] = []

    def __call__(self, line: str = "") -> None:
        print(line, flush=True)
        self.lines.append(line)
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def measure(session, tokenizer, workloads, v: b.Variant, repeat: int, log: Report) -> dict:
    """Первый проход включает подготовку форм на GPU, лучший из остальных — установившаяся скорость."""
    out = {}
    for w in workloads:
        times = []
        found = None
        for _ in range(repeat):
            timing, found = b.run_workload(session, tokenizer, w, v)
            times.append(timing["total"])
        out[w.name] = dict(first=times[0], best=min(times), found=found)
        log(f"  {w.name}: первый проход {times[0]:.1f} с, лучший {min(times):.1f} с")
    return out


def main() -> int:
    here = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", type=Path, default=model_asset.model_dir())
    ap.add_argument("--out", type=Path, default=here / "ner-gpu-report.txt")
    ap.add_argument("--repeat", type=int, default=2)
    args = ap.parse_args()
    log = Report(args.out)

    log(f"Проверка NER на видеокарте, {time.strftime('%Y-%m-%d %H:%M')}")
    log(f"Система: {platform.platform()}, {platform.processor()}, {os.cpu_count()} логических ядер")
    log(f"Видеокарты: {', '.join(gpu_names()) or 'не удалось определить'}")
    log(f"onnxruntime {ort.__version__}, провайдеры: {', '.join(ort.get_available_providers())}")
    if not model_asset.is_model_present(args.model_dir):
        log(f"Модель не найдена в {args.model_dir}. Откройте документ в MD Blinder, чтобы она скачалась.")
        return 1
    tokenizer = b.load_tokenizer(args.model_dir)
    model = args.model_dir / model_asset.MODEL_FILE
    workloads = [
        b.Workload("договор", b.strip_inline_formatting(b.prose(60)).plain),
        b.Workload("таблица", b.strip_inline_formatting(b.table(250)).plain),
    ]

    log(f"\n== Процессор, как в программе (fp16, батч {NER_BATCH_SIZE})")
    cpu = b.Variant("CPU", model_asset.MODEL_FILE, batch=NER_BATCH_SIZE)
    session = b.make_session(model, cpu)
    base = measure(session, tokenizer, workloads, cpu, 1, log)
    del session

    rows = []
    for device in range(MAX_DEVICES):
        log(f"\n== DirectML, адаптер {device}")
        session = None
        for level in LEVELS:
            v = b.Variant(f"DML {device}", model_asset.MODEL_FILE, provider="dml", device=device, level=level)
            t0 = time.perf_counter()
            try:
                candidate = b.make_session(model, v)
            except Exception as error:  # noqa: BLE001
                log(f"  граф '{level}': сессия не создана ({str(error).splitlines()[0][:160]})")
                if "device" in str(error).lower():
                    break
                continue
            if candidate.get_providers()[0] != "DmlExecutionProvider":
                log("  адаптер недоступен, onnxruntime перешел на процессор")
                break
            spans = b.infer(candidate, tokenizer, [b.CALIBRATION_TEXT], b.CALIBRATION_LABELS, 0.5,
                            dict(prepare=0.0, run=0.0, decode=0.0, tokens=0, real_tokens=0))[0]
            if not b.calibration_passes(spans):
                log(f"  граф '{level}': калибровка не пройдена, найдено {[(s.span_text, s.label) for s in spans]}")
                del candidate
                continue
            log(f"  граф '{level}': сессия за {time.perf_counter() - t0:.1f} с, калибровка пройдена")
            session = candidate
            break
        if session is None:
            continue
        for batch in (NER_BATCH_SIZE, 8):
            v = b.Variant(f"DML {device}", model_asset.MODEL_FILE, provider="dml", device=device, level=level, batch=batch)
            log(f" батч {batch}:")
            res = measure(session, tokenizer, workloads, v, args.repeat, log)
            for w in workloads:
                diff = b.compare(base[w.name]["found"], res[w.name]["found"])["confirmed"]
                speed = base[w.name]["best"] / max(res[w.name]["best"], 1e-9)
                rows.append((f"адаптер {device}, граф {level}, батч {batch}", w.name, res[w.name]["best"], speed, diff))
                for line in b.changed(w.plain, base[w.name]["found"], res[w.name]["found"])[:8]:
                    log(f"    Δ {w.name}: {line}")
        del session

    log("\n== Итог")
    for w in workloads:
        log(f"CPU, {w.name}: {base[w.name]['best']:.1f} с")
    for name, wl, t, speed, diff in rows:
        log(f"{name}, {wl}: {t:.1f} с, в {speed:.1f} раза быстрее CPU; "
            f"сущностей ≥0.3: потеряно {diff['lost']}, новых {diff['new']} из {diff['base']}")
    if not rows:
        log("Ни одна видеокарта не прошла проверку.")
    log(f"\nОтчет сохранен: {args.out}")
    return 0


if __name__ == "__main__":
    code = main()
    if getattr(sys, "frozen", False):
        try:
            input("\nНажмите Enter, чтобы закрыть окно")
        except EOFError:
            pass
    raise SystemExit(code)
