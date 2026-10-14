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


from md_blinder.editing import active_span_at, drag_span, hovered_entity_at, replace_position  # noqa: E402


def two_entities():
    text = "Директор Иван Петров и бухгалтер Анна Смирнова."
    a = text.index("Иван Петров")
    b = text.index("Анна Смирнова")
    e1 = Entity(id="a", type="PERSON", value="", placeholder="[PERSON_1]", positions=[Position(a, a + 4)])
    e2 = Entity(id="b", type="PERSON", value="", placeholder="[PERSON_2]", positions=[Position(b, b + 13)])
    return text, e1, e2


def test_drag_right_snaps_to_word_end():
    text, e1, e2 = two_entities()
    span = e1.positions[0]
    # Ручку отпускают ближе к концу «Петров»: край привязывается к ближайшей границе слова.
    new = drag_span([e1, e2], "a", span, span, "right", text.index("Петров") + 4, text)
    assert text[new.start:new.end] == "Иван Петров"


def test_drag_left_keeps_opposite_edge():
    text, e1, e2 = two_entities()
    span = e2.positions[0]
    new = drag_span([e1, e2], "b", span, span, "left", text.index("бухгалтер") + 3, text)
    assert new.end == span.end
    assert text[new.start:new.end] == "бухгалтер Анна Смирнова"


def test_drag_does_not_cross_neighbour():
    text, e1, e2 = two_entities()
    span = e1.positions[0]
    new = drag_span([e1, e2], "a", span, span, "right", len(text), text)
    assert new.end <= e2.positions[0].start


def test_drag_cannot_collapse():
    text, e1, e2 = two_entities()
    span = e1.positions[0]
    new = drag_span([e1, e2], "a", span, span, "right", 0, text)
    assert new is not None and new.end > new.start


def test_replace_position_and_lookup():
    text, e1, e2 = two_entities()
    old = e1.positions[0]
    moved = replace_position([e1, e2], "a", old, Position(old.start, old.end + 7))
    assert moved[0].positions == [Position(old.start, old.end + 7)]
    assert moved[1] is e2
    assert active_span_at([e1, e2], old.start + 1) == ("a", old)
    assert active_span_at([e1, e2], 0) is None
    assert hovered_entity_at([e1, e2], e2.positions[0].start + 2) == "b"
