"""Порт логики src/lib/entityEditorState.test.ts для сдвига разметки при правке."""

import re
from dataclasses import replace

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


from md_blinder.editing import resize_occurrence  # noqa: E402

BVI = (
    "registered office at Quijano Chambers, P.O. Box 3159, Road Town, Tortola; "
    "physical: Quijano Chambers, P.O. Box 3159, Road Town, Tortola."
)


def address_entity(text, value, *, dismissed_company=None):
    starts = [m.start() for m in re.finditer(re.escape(value), text)]
    addr = Entity(id="addr", type="ADDRESS", value=value, placeholder="[ADDRESS_5]",
                  positions=[Position(s, s + len(value)) for s in starts])
    entities = [addr]
    if dismissed_company is not None:
        s = text.index(dismissed_company)
        entities.append(Entity(id="co", type="COMPANY", value=dismissed_company, placeholder="[COMPANY_9]",
                               positions=[Position(s, s + len(dismissed_company))],
                               anonymize=False, dismissed=True))
    return entities


def test_drag_ignores_dismissed_finding():
    # Отклоненная находка «Quijano Chambers» в тексте не видна и не держит ручку.
    entities = address_entity(BVI, "P.O. Box 3159, Road Town", dismissed_company="Quijano Chambers")
    span = entities[0].positions[0]
    new = drag_span(entities, "addr", span, span, "left", BVI.index("Quijano") + 1, BVI)
    assert BVI[new.start:new.end] == "Quijano Chambers, P.O. Box 3159, Road Town"


def test_drag_still_stops_at_visible_finding():
    entities = address_entity(BVI, "P.O. Box 3159, Road Town", dismissed_company="Quijano Chambers")
    entities[1] = replace(entities[1], dismissed=False, anonymize=True)
    span = entities[0].positions[0]
    new = drag_span(entities, "addr", span, span, "left", BVI.index("Quijano") + 1, BVI)
    assert new.start == entities[1].positions[0].end


def test_drag_left_does_not_take_space_before_word():
    entities = address_entity(BVI, "P.O. Box 3159, Road Town")
    span = entities[0].positions[1]
    new = drag_span(entities, "addr", span, span, "left", BVI.index(" Quijano", span.start - 30), BVI)
    assert BVI[new.start:new.end] == "Quijano Chambers, P.O. Box 3159, Road Town"


def test_resize_extends_every_occurrence_with_same_context():
    entities = address_entity(BVI, "P.O. Box 3159, Road Town")
    old = entities[0].positions[1]
    new = Position(BVI.index("Quijano", old.start - 30), old.end)
    result = resize_occurrence(entities, BVI, "addr", old, new)
    values = [BVI[p.start:p.end] for p in result[0].positions]
    assert values == ["Quijano Chambers, P.O. Box 3159, Road Town"] * 2


def test_resize_matches_occurrence_with_commas_on_edges():
    # Вхождение, найденное вместе с запятыми, тоже расширяется (и теряет их).
    entities = address_entity(BVI, "P.O. Box 3159, Road Town")
    first = entities[0].positions[0]
    entities[0] = replace(entities[0], positions=[Position(first.start - 2, first.end + 1), entities[0].positions[1]])
    old = entities[0].positions[1]
    new = Position(BVI.index("Quijano", old.start - 30), old.end)
    result = resize_occurrence(entities, BVI, "addr", old, new)
    values = [BVI[p.start:p.end] for p in result[0].positions]
    assert values == ["Quijano Chambers, P.O. Box 3159, Road Town"] * 2


def test_resize_removes_dismissed_under_new_bounds():
    entities = address_entity(BVI, "P.O. Box 3159, Road Town", dismissed_company="Quijano Chambers")
    old = entities[0].positions[0]
    new = Position(BVI.index("Quijano"), old.end)
    result = resize_occurrence(entities, BVI, "addr", old, new)
    assert [e.id for e in result] == ["addr"]
    assert [BVI[p.start:p.end] for p in result[0].positions] == ["Quijano Chambers, P.O. Box 3159, Road Town"] * 2


def test_resize_leaves_occurrence_with_other_context_or_blocked():
    text = "at Quijano Chambers, P.O. Box 1; at Other House, P.O. Box 1; Quijano Chambers, P.O. Box 1."
    entities = address_entity(text, "P.O. Box 1")
    third = text.rindex("Quijano")
    entities.append(Entity(id="co", type="COMPANY", value="Quijano", placeholder="[COMPANY_1]",
                           positions=[Position(third, third + 7)]))
    old = entities[0].positions[0]
    new = Position(text.index("Quijano"), old.end)
    result = resize_occurrence(entities, text, "addr", old, new)
    values = [text[p.start:p.end] for p in result[0].positions]
    # Второе вхождение стоит в другом контексте, третье заехало бы на видимую находку.
    assert values == ["Quijano Chambers, P.O. Box 1", "P.O. Box 1", "P.O. Box 1"]
    assert result[1] is entities[1]


def test_resize_shrink_applies_everywhere():
    entities = address_entity(BVI, "Quijano Chambers, P.O. Box 3159, Road Town, Tortola")
    old = entities[0].positions[0]
    new = Position(old.start, BVI.index(", Tortola", old.start))
    result = resize_occurrence(entities, BVI, "addr", old, new)
    assert [BVI[p.start:p.end] for p in result[0].positions] == ["Quijano Chambers, P.O. Box 3159, Road Town"] * 2
