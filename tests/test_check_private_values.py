"""Проверка значений из настоящих документов (scripts/check_private_values.py). Все значения выдуманы."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_private_values.py"
spec = importlib.util.spec_from_file_location("check_private_values", SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def make_root(tmp_path: Path) -> Path:
    gold = tmp_path / "corpus" / "test-samples" / "gold"
    gold.mkdir(parents=True)
    entities = [{"type": "PERSON", "value": "Арсений Вымыслов"}, {"type": "REG_NUMBER", "value": "98.765.432/0001-10"},
                {"type": "ADDRESS", "value": "Hong Kong"}]
    (gold / "contract-1.json").write_text(json.dumps({"entities": entities}), encoding="utf-8")
    private = tmp_path / "private-values"
    private.mkdir()
    (private / "values.txt").write_text("# комментарий\nVymyslov Trading\n", encoding="utf-8")
    holdout = tmp_path / "corpus" / "test-samples" / "holdout_text"
    holdout.mkdir()
    (holdout / "contract-9.txt").write_text(
        "The Supplier shall deliver every purple widget to the warehouse within nine calendar days of the order.",
        encoding="utf-8")
    return tmp_path


def test_finds_values_numbers_and_excerpts(tmp_path):
    checker = check.Checker(*check.load_sources(make_root(tmp_path), []))
    text = ("Директор: арсений  вымыслов\nCNPJ 98765432000110\nVYMYSLOV TRADING LLC\n"
            "shall deliver every purple widget to the warehouse within nine calendar days of the order")
    kinds = [(line, kind) for line, kind, _ in checker.check(text)]
    assert (1, "значение из документа") in kinds
    assert (2, "номер из документа") in kinds
    assert (3, "значение из документа") in kinds
    assert (0, "выдержка из текста документа") in kinds


def test_generic_and_invented_values_pass(tmp_path):
    checker = check.Checker(*check.load_sources(make_root(tmp_path), []))
    assert checker.check("Office in Hong Kong, director Ivan Petrov, CNPJ 12.345.678/0001-90.") == []


def test_values_are_not_printed_by_default(tmp_path, capsys):
    root = make_root(tmp_path)
    text = tmp_path / "pr.md"
    text.write_text("Подписал Арсений Вымыслов", encoding="utf-8")
    assert check.main(["--root", str(root), "--text", str(text)]) == 1
    out = capsys.readouterr().out
    assert "значение из документа" in out and "Вымыслов" not in out


def test_without_sources_reports_missing_list(tmp_path):
    assert check.main(["--root", str(tmp_path / "none"), "--text", "-"]) == 2
