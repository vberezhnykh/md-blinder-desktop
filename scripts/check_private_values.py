"""Проверка, что в репозиторий не попадают значения из настоящих документов.

Список значений хранится вне репозитория: эталон тестового корпуса (gold/*.json, extras-triage.json), тексты
документов корпуса (holdout_text/*.txt, кеш конвертации cache/md/*.json) и файлы *.txt с отдельными значениями
(по одному на строку, «#» — комментарий). В репозитории только сам скрипт, без значений.

    python scripts/check_private_values.py                       # файлы репозитория и сообщения неотправленных коммитов
    python scripts/check_private_values.py --text pr-body.md     # текст PR или релиза
    echo "текст" | python scripts/check_private_values.py --text -

Источники по умолчанию — папка проекта /mnt/project-files (корпус и private-values/), или переменная
MD_BLINDER_PRIVATE_DIR. В CI список приходит файлом из секрета (--values-file). Найденные значения не печатаются,
только место и вид совпадения: лог CI открыт всем. --show печатает значения (только для локального разбора).
Код выхода 1, если что-то найдено; 0, если чисто; 2, если не найден ни один источник значений.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path("/mnt/project-files")
MIN_LENGTH = 5
MIN_DIGITS = 9  # номер ищется и без разделителей: «12.345.678/0001-90» и «12345678000190»
SHINGLE_WORDS = 12  # столько слов подряд из текста документа считаются выдержкой
# Общие слова и роли, которые бывают в эталоне, но ничего не раскрывают.
GENERIC = {
    "hong kong", "new york", "united kingdom", "united states", "russian federation", "ukraine", "brazil",
    "call center", "plan b", "the company", "the client", "the customer", "the contractor", "the parties", "director",
}
# Лицензии и шрифты повторяют типовые юридические фразы, выдержками они не считаются.
SKIP_FILES = re.compile(r"(LICENSE|OFL\.txt|\.lock|\.ttf|\.png|\.ico)$", re.IGNORECASE)
WORD_RE = re.compile(r"\w+", re.UNICODE)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _digits(text: str) -> str:
    # Разделители внутри числа убираются, сами числа разделяются пробелом.
    return re.sub(r"(?<=\d)[\s.\-/()]+(?=\d)", "", text)


def _gold_values(data: object, out: set[str]) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if key in ("value", "aliases", "variants") and isinstance(value, str):
                out.add(value)
            elif key in ("aliases", "variants") and isinstance(value, list):
                out.update(v for v in value if isinstance(v, str))
            else:
                _gold_values(value, out)
    elif isinstance(data, list):
        for item in data:
            _gold_values(item, out)


def _words(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def _shingles(text: str) -> set[str]:
    words = _words(text)
    return {" ".join(words[i:i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)}


def load_sources(root: Path, values_files: list[Path]) -> tuple[set[str], set[str]]:
    """Значения (строки) и выдержки (по SHINGLE_WORDS слов) из всех найденных источников."""
    values: set[str] = set()
    shingles: set[str] = set()
    corpus = root / "corpus" / "test-samples"
    for path in sorted((corpus / "gold").glob("contract-*.json")):
        _gold_values(json.loads(path.read_text(encoding="utf-8")), values)
    triage = corpus / "extras-triage.json"
    if triage.is_file():
        values.update(key.split("|")[-1] for key in json.loads(triage.read_text(encoding="utf-8")))
    files = sorted((root / "private-values").glob("*.txt")) + list(values_files)
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                values.add(line.strip())
    for path in sorted((corpus / "holdout_text").glob("*.txt")):
        shingles |= _shingles(path.read_text(encoding="utf-8", errors="ignore"))
    for path in sorted((root / "corpus" / "cache" / "md").glob("*.json")):
        text = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(text, str):
            shingles |= _shingles(text)
    values = {_norm(v) for v in values}
    values = {v for v in values if len(v) >= MIN_LENGTH and v not in GENERIC}
    return values, shingles


class Checker:
    def __init__(self, values: set[str], shingles: set[str]) -> None:
        self.values = sorted(values, key=len, reverse=True)
        self.numbers = sorted({d for v in values if len(d := re.sub(r"\D", "", v)) >= MIN_DIGITS})
        self.shingles = shingles

    def check(self, text: str) -> list[tuple[int, str, str]]:
        """Совпадения в тексте: (номер строки, вид, значение)."""
        hits: list[tuple[int, str, str]] = []
        for number, line in enumerate(text.splitlines(), 1):
            low = _norm(line)
            found = [v for v in self.values if v in low]
            hits.extend((number, "значение из документа", v) for v in found)
            digits = _digits(low)
            hits.extend(
                (number, "номер из документа", d) for d in self.numbers
                if d in digits and not any(re.sub(r"\D", "", v) == d for v in found)
            )
        words = _words(text)
        if self.shingles and len(words) >= SHINGLE_WORDS:
            for i in range(len(words) - SHINGLE_WORDS + 1):
                piece = " ".join(words[i:i + SHINGLE_WORDS])
                if piece in self.shingles:
                    hits.append((0, "выдержка из текста документа", piece))
                    break
        return hits


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8").stdout


def repo_texts() -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []
    for name in _git("ls-files", "--cached", "--others", "--exclude-standard").splitlines():
        path = Path(name)
        if SKIP_FILES.search(name) or not path.is_file():
            continue
        try:
            texts.append((name, path.read_text(encoding="utf-8")))
        except (UnicodeDecodeError, OSError):
            continue
    upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}").strip() or "origin/main"
    for commit in _git("rev-list", f"{upstream}..HEAD").split():
        texts.append((f"сообщение коммита {commit[:9]}", _git("log", "-1", "--format=%B", commit)))
    texts.append(("имя ветки", _git("branch", "--show-current")))
    return texts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("MD_BLINDER_PRIVATE_DIR", DEFAULT_ROOT)))
    parser.add_argument("--values-file", type=Path, action="append", default=[])
    parser.add_argument("--text", action="append", default=[], help="файл с текстом PR/релиза или «-» для stdin")
    parser.add_argument("--show", action="store_true", help="печатать найденные значения (не в CI)")
    args = parser.parse_args(argv)

    values, shingles = load_sources(args.root, args.values_file)
    if not values and not shingles:
        print("Не найден список значений: ни корпуса, ни файлов со значениями.", file=sys.stderr)
        return 2
    checker = Checker(values, shingles)
    if args.text:
        texts = [(name, sys.stdin.read() if name == "-" else Path(name).read_text(encoding="utf-8"))
                 for name in args.text]
    else:
        texts = repo_texts()

    total = 0
    for name, text in texts:
        for line, kind, value in checker.check(text):
            total += 1
            where = f"{name}:{line}" if line else name
            detail = f": {value}" if args.show else f" ({len(value)} знаков)"
            print(f"{where}: {kind}{detail}")
    if total:
        print(f"Найдено совпадений с настоящими документами: {total}. Замените их выдуманными значениями.",
              file=sys.stderr)
        return 1
    print(f"Чисто: {len(texts)} текстов, {len(values)} значений и {len(shingles)} выдержек в списке.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
