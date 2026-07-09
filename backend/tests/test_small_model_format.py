import pytest
from utils.llm_utils import parse_translations_from_text

def test_parse_small_model_quirks():
    """Test that our parser can handle common quirks of smaller models."""
    
    # 1. Extra preamble
    text = "Sure, here are the translations:\n1| Hello\n2| World"
    parsed = parse_translations_from_text(text)
    assert len(parsed) == 2
    assert parsed[0]['text'] == "Hello"
    
    # 2. Reasoning blocks (should be stripped by strip_reasoning_blocks, but let's see if parse handles them too)
    text = "<think>\nTranslate carefully.\n</think>\n1| Hello\n2| World"
    # Actually strip_reasoning_blocks should be called before parse, 
    # but parse_translations_from_text uses regex findall which should ignore it.
    parsed = parse_translations_from_text(text)
    assert len(parsed) == 2
    
    # 3. Incomplete lines or missing pipes (should work with dot-fallback)
    text = "1. Hello\n2. World"
    parsed = parse_translations_from_text(text)
    assert len(parsed) == 2
    assert parsed[0]['index'] == 1
    assert parsed[0]['text'] == "Hello"
    assert parsed[1]['index'] == 2
    assert parsed[1]['text'] == "World"
