"""Порт src/lib/candidateContext.test.ts, wordSnap.test.ts и plural.test.ts."""

from __future__ import annotations

import re

import pytest

from md_blinder.text_utils import CandidateContext, candidate_context, plural, snap_to_word_boundary, snap_to_word_edge


# candidateContext

def test_context_returns_whole_short_text():
    text = "Подписал Иванов П. С. лично."
    start = text.index("Иванов")
    assert candidate_context(text, start, start + 12) == CandidateContext(
        before="Подписал ", match="Иванов П. С.", after=" лично.", cut_before=False, cut_after=False,
    )


def test_context_cuts_long_text_on_spaces():
    text = "слово " * 30 + "Петров " + "дальше " * 30
    start = text.index("Петров")
    ctx = candidate_context(text, start, start + 6, 20)
    assert ctx.cut_before is True
    assert ctx.cut_after is True
    assert re.fullmatch(r"слово( слово)* ", ctx.before)
    assert re.fullmatch(r" дальше( дальше)*", ctx.after)


def test_context_squashes_newlines():
    text = "Сторона:\n\n| ИП Смирнова |\nИНН"
    start = text.index("Смирнова")
    ctx = candidate_context(text, start, start + 8)
    assert ctx.before == "Сторона: | ИП "
    assert ctx.after == " | ИНН"


# snapToWordBoundary

def test_snap_mid_word_to_nearest_edge():
    # "hello world" -> границы 0, 5, 6, 11
    text = "hello world"
    assert snap_to_word_boundary(text, 2) == 0
    assert snap_to_word_boundary(text, 4) == 5


def test_snap_keeps_boundary_position():
    text = "hello world"
    assert snap_to_word_boundary(text, 5) == 5
    assert snap_to_word_boundary(text, 6) == 6


def test_snap_start_of_string():
    assert snap_to_word_boundary("hello world", 0) == 0


def test_snap_end_of_string():
    text = "hello world"
    assert snap_to_word_boundary(text, 11) == 11
    assert snap_to_word_boundary(text, 999) == 11  # за концом документа


def test_snap_inside_space_run():
    text = "foo    bar"  # пробелы 3..6, границы 0,3,7,10
    assert snap_to_word_boundary(text, 4) == 3
    assert snap_to_word_boundary(text, 6) == 7


def test_snap_ties_resolve_to_lower_index():
    # "foo  bar": границы 0,3,5,8; позиция 4 равноудалена от 3 и 5.
    assert snap_to_word_boundary("foo  bar", 4) == 3


def test_snap_empty_string():
    assert snap_to_word_boundary("", 0) == 0
    assert snap_to_word_boundary("", 5) == 0
    assert snap_to_word_boundary("", -5) == 0


def test_snap_clamps_negative_position():
    assert snap_to_word_boundary("hello world", -10) == 0


# plural

FORMS = ("находка", "находки", "находок")


@pytest.mark.parametrize("n, expected", [
    (0, "0 находок"), (1, "1 находка"), (2, "2 находки"), (4, "4 находки"), (5, "5 находок"),
    (11, "11 находок"), (12, "12 находок"), (21, "21 находка"), (22, "22 находки"), (101, "101 находка"),
])
def test_plural(n, expected):
    assert plural(n, FORMS) == expected


# Дополнительно (нет в TS): дробные — категория other, целые float печатаются как в JS.

def test_plural_fraction_and_integral_float():
    assert plural(1.5, FORMS) == "1.5 находок"
    assert plural(2.0, FORMS) == "2 находки"
    assert plural(-1, FORMS) == "-1 находка"


# snap_to_word_edge: левая ручка встает на начало слова, правая на конец

def test_snap_edge_left_skips_space_before_word():
    text = "physical: Quijano Chambers"
    # Указатель на пробеле после двоеточия: обычная привязка дала бы границу перед
    # пробелом, левая ручка встает на начало «Quijano».
    assert snap_to_word_edge(text, text.index(" Quijano"), "left") == text.index("Quijano")
    assert snap_to_word_edge(text, text.index("Quijano") + 2, "left") == text.index("Quijano")


def test_snap_edge_right_stops_at_word_end():
    text = "Road Town, Tortola"
    assert snap_to_word_edge(text, text.index(" Tortola") + 1, "right") == text.index(" Tortola")
    assert snap_to_word_edge(text, len(text), "right") == len(text)


def test_snap_edge_without_words():
    assert snap_to_word_edge("   ", 2, "left") == snap_to_word_boundary("   ", 2)
    assert snap_to_word_edge("", 0, "right") == 0


def test_snap_edge_stops_at_markup_inside_table_cell():
    # Между «Augli» и «Director» нет пробела, только <br>: правая ручка встает
    # после «Augli», а не в конце ячейки.
    text = "| \\_\\_\\_/Xodillayev Saydullokhon Nurillo Augli<br>Director | x |"
    augli_end = text.index("<br>")
    assert snap_to_word_edge(text, augli_end + 1, "right") == augli_end
    assert snap_to_word_edge(text, text.index("Director") + 2, "left") == text.index("Director")
    # Левая ручка встает на начало фамилии, а не на подчеркивания строки подписи.
    assert snap_to_word_edge(text, text.index("/X"), "left") == text.index("Xodillayev")
    # Точка остается частью слова: «Inc.» целиком.
    assert snap_to_word_edge("Acme Inc. signs", 9, "right") == 9
