import pytest
from utils.structured_output import parse_structured
from utils.srt_parser import get_scene_lookahead_hint


def test_parse_structured_valid():
    text = '{"lines": [{"i": 1, "t": "Hello world"}, {"i": 2, "t": "Test line"}]}'
    result = parse_structured(text)
    assert len(result) == 2
    # src_head is carried for the source-echo guard (empty when not provided).
    assert result[0]["index"] == 1 and result[0]["text"] == "Hello world"
    assert result[0]["src_head"] == ""
    assert result[1]["index"] == 2 and result[1]["text"] == "Test line"


def test_parse_structured_with_markdown_blocks():
    text = """
```json
{
  "lines": [
    {"i": 1, "t": "Hello world"},
    {"i": 2, "t": "Test line"}
  ]
}
```
"""
    result = parse_structured(text)
    assert len(result) == 2
    assert result[0]["index"] == 1
    assert result[0]["text"] == "Hello world"


def test_parse_structured_tolerant_trailing_comma():
    # Trailing comma before closing bracket
    text = '{"lines": [{"i": 1, "t": "Hello world"}, {"i": 2, "t": "Test line"},]}'
    result = parse_structured(text)
    assert len(result) == 2
    assert result[0]["index"] == 1


def test_parse_structured_fallback_to_text():
    # If the JSON is completely broken, fall back to numbered parsing
    text = """
1| Translated one
2| Translated two
"""
    result = parse_structured(text)
    assert len(result) == 2
    assert result[0] == {"index": 1, "text": "Translated one"}
    assert result[1] == {"index": 2, "text": "Translated two"}


def test_get_scene_lookahead_hint():
    scene_lines = [
        {"original": "Line one\nwith break", "translated": ""},
        {"original": "Line two", "translated": ""},
        {"original": "Line three", "translated": ""}
    ]
    # Default k=2
    hint = get_scene_lookahead_hint(scene_lines, k=2)
    assert hint == "Line one with break | Line two"
    
    # k=1
    hint = get_scene_lookahead_hint(scene_lines, k=1)
    assert hint == "Line one with break"
    
    # Empty
    assert get_scene_lookahead_hint([], k=2) == ""
    assert get_scene_lookahead_hint(scene_lines, k=0) == ""
