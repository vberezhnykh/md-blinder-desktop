"""Порт placeholderGuard, normalizeValue, stripFormatting и части imageSafety тестов."""

import pytest

from md_blinder.anonymize.image_safety import IMAGE_REMOVED_MARKER, find_image_ranges, mask_images_for_detection
from md_blinder.anonymize.normalize import normalize_value
from md_blinder.anonymize.placeholder_guard import (
    escape_placeholder_literal,
    find_placeholder_ranges,
    has_placeholder_collision,
    unescape_placeholders,
)
from md_blinder.anonymize.strip_formatting import remap_matches, strip_inline_formatting
from md_blinder.anonymize.types import ALL_ENTITY_TYPES, Position, RawMatch


# placeholderGuard

def test_find_placeholder_ranges():
    text = "Contact [PERSON_1] for details"
    assert find_placeholder_ranges(text) == [Position(8, 18)]
    assert find_placeholder_ranges("Ref [REG_NUMBER_12] applies") == [Position(4, 19)]
    assert find_placeholder_ranges("Just a normal sentence.") == []


def test_collision_and_escape():
    assert has_placeholder_collision("See [COMPANY_3] below")
    assert not has_placeholder_collision("No placeholders here")
    escaped = escape_placeholder_literal("[PERSON_1]")
    assert escaped != "[PERSON_1]"
    assert unescape_placeholders(escaped) == "[PERSON_1]"
    assert not has_placeholder_collision(escaped)
    assert unescape_placeholders("Nothing to see here") == "Nothing to see here"
    for t in ALL_ENTITY_TYPES:
        assert has_placeholder_collision(f"текст [{t}_1] текст")


# normalizeValue

def test_normalize_value():
    assert normalize_value("PHONE", "+36 1 234 5678") == normalize_value("PHONE", "+36-1-234-5678")
    assert normalize_value("PHONE", "(202) 555-0123") == normalize_value("PHONE", "202.555.0123")
    assert normalize_value("REG_NUMBER", "hu12345678") == normalize_value("REG_NUMBER", "HU12345678")
    assert normalize_value("SWIFT", "deutdeff") == "DEUTDEFF"
    assert normalize_value("PERSON", "John Doe") == "john doe"
    assert normalize_value("EMAIL", "John@Acme.co") == "john@acme.co"
    assert normalize_value("CARD", "4111 1111 1111 1111") == "4111111111111111"
    assert normalize_value("IBAN", "gb82 west1234 5698765432") == "GB82WEST12345698765432"
    assert normalize_value("ACCOUNT", "1234-5678") == "12345678"
    a = normalize_value("ID_DOC", "4506 123456")
    assert normalize_value("ID_DOC", "45 06 123456") == a == normalize_value("ID_DOC", "4506123456")
    assert normalize_value("ID_DOC", "ab 1234567") == "AB1234567"
    u = normalize_value("URL", "https://Example.com/")
    assert normalize_value("URL", "www.example.com") == u == normalize_value("URL", "example.com")
    assert normalize_value("URL", "example.com/a") != normalize_value("URL", "example.com/b")


# stripFormatting

def test_strip_emphasis_map():
    md = "**_AKKOL LTD_**"
    s = strip_inline_formatting(md)
    assert s.plain == "AKKOL LTD"
    assert len(s.map) == len(s.plain)
    assert all(md[s.map[i]] == s.plain[i] for i in range(len(s.plain)))


def test_strip_drops_image():
    b64 = "A" * 500 + "+/=="
    md = f"Заказчик: Иван Петров ![схема](data:image/jpeg;base64,{b64}) ИНН 7736324582"
    s = strip_inline_formatting(md)
    assert b64 not in s.plain and "base64" not in s.plain and "схема" not in s.plain
    assert "Иван Петров" in s.plain and "ИНН 7736324582" in s.plain
    assert all(md[s.map[i]] == s.plain[i] for i in range(len(s.plain)))
    assert all(s.map[i] > s.map[i - 1] for i in range(1, len(s.map)))


def test_strip_html_image_and_marker():
    md = f'Alice <img src="data:image/png;base64,{"A" * 64000}" alt="Private Smith"> {IMAGE_REMOVED_MARKER} Bob'
    s = strip_inline_formatting(md)
    assert s.plain == "Alice   Bob"


def test_remap_after_data_uri():
    md = f"![x](data:image/png;base64,{'Z' * 1000}) Иван Петров подписал"
    s = strip_inline_formatting(md)
    start = s.plain.index("Иван Петров")
    [m] = remap_matches([RawMatch("PERSON", "", start, start + 11)], s.plain, s.map)
    assert md[m.start:m.end] == "Иван Петров"


def test_strip_noop():
    md = "ООО «Когнитек», ИНН 7736324582"
    assert strip_inline_formatting(md).plain == md


def test_remap_rejects_bridging_span():
    md = "Alice ![signature](data:image/png;base64,QQ==) Smith"
    s = strip_inline_formatting(md)
    bridging = RawMatch("PERSON", s.plain, 0, len(s.plain))
    smith = RawMatch("PERSON", "Smith", s.plain.index("Smith"), len(s.plain))
    assert remap_matches([bridging, smith], s.plain, s.map, s.excluded) == [
        RawMatch("PERSON", "Smith", md.index("Smith"), len(md))
    ]


def test_remap_full_and_mid_word():
    md = "**_AKKOL LTD_**"
    s = strip_inline_formatting(md)
    [m] = remap_matches([RawMatch("COMPANY", "", 0, 9)], s.plain, s.map)
    assert md[m.start:m.end] == "AKKOL LTD" and m.value == "AKKOL LTD"
    md = "**Acme** Ltd"
    s = strip_inline_formatting(md)
    assert s.plain == "Acme Ltd"
    [m] = remap_matches([RawMatch("COMPANY", "", 0, len(s.plain))], s.plain, s.map)
    assert m.value == "Acme Ltd"


# imageSafety (части без apply/detectStructural)

@pytest.mark.parametrize("image", [
    "![Alice signature](data:image/png;base64,QWxpY2U=)",
    '![Alice](https://example.com/signature(1).png "signed by Alice")',
    "![Alice [signature]](<https://example.com/image(1).png>)",
    '<IMG src="data:image/png;base64,QWxpY2U=" alt="Alice > Smith">',
    '<picture><source srcset="https://example.com/private.png"><img src="fallback.png"></picture>',
    '<svg><image href="data:image/png;base64,QWxpY2U=" /></svg>',
])
def test_find_complete_image(image):
    source = f"Before {image} After alice@example.com"
    ranges = find_image_ranges(source)
    assert ranges == [Position(7, 7 + len(image))]


def test_mask_keeps_offsets():
    source = "![alice@example.com +79161234567](data:image/png;base64," + "x" * 64000 + ")\nContact bob@example.com"
    mask = mask_images_for_detection(source)
    assert len(mask) == len(source)
    assert mask.index("\n") == source.index("\n")
    assert len(find_image_ranges(source)) == 1
    marker_source = f"Before {IMAGE_REMOVED_MARKER} After"
    projection = mask_images_for_detection(marker_source)
    assert "Изображение" not in projection and len(projection) == len(marker_source)


def test_reference_definitions():
    for image in ("![Alice][signature]", "![signature][]", "![signature]"):
        source = f'{image}\n\n[signature]: data:image/png;base64,QWxpY2U= "Alice signature"\n[other]: https://example.com/info'
        ranges = find_image_ranges(source)
        assert len(ranges) == 2
        assert source[ranges[1].start:ranges[1].end].startswith("[signature]:")
        assert source[ranges[1].end:] == "\n[other]: https://example.com/info"
    source = '![signature][sig]\n\n[sig]:\n  data:image/png;base64,QWxpY2U=\n  "Alice Smith"\n\nAfter'
    ranges = find_image_ranges(source)
    assert source[ranges[-1].end:] == "\n\nAfter"
