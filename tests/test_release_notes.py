import importlib.util
from pathlib import Path

import md_blinder

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("release_notes", ROOT / "release_notes.py")
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)

CHANGELOG = "# Что нового\n\n## 0.3.0\n\n- Новое.\n\n## 0.2.6 — пакеты\n\n- Пакеты.\n- Меню.\n\n## 0.2.5\n\n- Старое.\n"


def test_takes_only_the_section_of_the_version():
    notes = release_notes.release_notes("0.2.6", CHANGELOG)
    assert notes.startswith("- Пакеты.\n- Меню.\n\nНастольная версия MD Blinder 0.2.6")
    assert "Старое" not in notes and "Новое" not in notes


def test_without_a_section_keeps_only_the_install_line():
    assert release_notes.release_notes("0.2.60", CHANGELOG).startswith("Настольная версия MD Blinder 0.2.60 ")


def test_changelog_describes_the_current_version():
    """Релиз выходит при подъеме версии; его описание берется из CHANGELOG.md."""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"\n## {md_blinder.__version__}\n" in changelog
