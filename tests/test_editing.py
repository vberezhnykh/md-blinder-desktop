"""Порт логики src/lib/entityEditorState.test.ts для сдвига разметки при правке."""

from md_blinder.anonymize.types import Entity, Position
from md_blinder.editing import TextChange, apply_change, diff_texts, map_entities, map_pos


def ent(start, end):
    return Entity(id="e", type="PERSON", value="x", placeholder="[PERSON_1]", positions=[Position(start, end)])


def test_diff_texts():
    assert diff_texts("abc", "abc") is None
    assert diff_texts("hello world", "hello brave world") == TextChange(6, "", "brave ")
    assert diff_texts("aaa", "aa") == TextChange(2, "a", "")
    c = diff_texts("Иван Петров", "Иван Сидоров")
    assert apply_change("Иван Петров", c) == "Иван Сидоров"


def test_insert_at_edges_does_not_extend():
    e = ent(5, 10)
    assert map_entities([e], TextChange(5, "", "XX"))[0].positions == [Position(7, 12)]
    assert map_entities([e], TextChange(10, "", "XX"))[0].positions == [Position(5, 10)]
    assert map_entities([e], TextChange(7, "", "XX"))[0].positions == [Position(5, 12)]


def test_delete_whole_occurrence_drops_entity():
    assert map_entities([ent(5, 10)], TextChange(4, "x" * 7, "")) == []


def test_partial_delete_shrinks():
    assert map_entities([ent(5, 10)], TextChange(8, "xxxx", ""))[0].positions == [Position(5, 8)]
    assert map_entities([ent(5, 10)], TextChange(3, "xxxx", ""))[0].positions == [Position(3, 6)]


def test_replace_inside():
    # Замена внутри сущности: начало остается, конец сдвигается на разницу длин.
    assert map_entities([ent(5, 10)], TextChange(6, "ab", "abcd"))[0].positions == [Position(5, 12)]


def test_map_pos_before_and_after():
    c = TextChange(10, "abc", "z")
    assert map_pos(3, c, 1) == 3
    assert map_pos(20, c, -1) == 18
