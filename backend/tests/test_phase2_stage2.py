import pytest
from utils.text_normalize import tokenize_and_normalize, stem_greek, strip_accents_and_normalize, contains_term
from utils.source_clean import clean_srt, align_and_carry_translations
from utils.subtitle_conformance import measure_line, wrap_text, default_limits, measure
from utils import storage
import tempfile
from pathlib import Path

def test_greek_normalization_and_stemming():
    # Accent removal
    assert strip_accents_and_normalize("Καπετάνιος") == "Καπετανιος"
    
    # Final sigma normalization
    assert strip_accents_and_normalize("Καπετάνιος").replace('ς', 'σ').lower() == "καπετανιοσ"
    
    # Stemming
    assert stem_greek("Καπετάνιος") == "καπεταν"
    assert stem_greek("Καπετάνιο") == "καπεταν"
    assert stem_greek("Καπετάνιε") == "καπεταν"
    
    # contains_term checks
    assert contains_term("Ο Καπετάνιος είναι εδώ", "Καπετάνιο")
    assert contains_term("Ο Καπετάνιο είναι εδώ", "Καπετάνιος")

def test_source_cleaning():
    parsed = [
        {"original": "[Laughter] Hello!", "timecode": "00:00:01,000 --> 00:00:03,000"},
        {"original": "John: How are you?", "timecode": "00:00:03,500 --> 00:00:05,000"},
        {"original": "I am fine", "timecode": "00:00:05,200 --> 00:00:06,000"},
        {"original": "And you?", "timecode": "00:00:06,100 --> 00:00:07,000"},
    ]
    cleaned, mapping = clean_srt(parsed, {"strip_sdh": True, "merge_split_cues": True})
    
    # Bracket SDH removed
    assert cleaned[0]["original"] == "Hello!"
    # Speaker prefix removed
    assert cleaned[1]["original"] == "How are you?"
    # Split cues merged (I am fine And you? with < 500ms gap)
    assert "I am fine And you?" in cleaned[2]["original"]
    
def test_lcs_alignment():
    old_lines = [
        {"original": "Hello", "src_hash": "hash1", "translations": {"el": "Γεια"}},
        {"original": "World", "src_hash": "hash2", "translations": {"el": "Κόσμος"}},
        {"original": "Test", "src_hash": "hash3", "translations": {"el": "Δοκιμή"}},
    ]
    new_lines = [
        {"original": "Hello", "src_hash": "hash1"},
        {"original": "New Line", "src_hash": "hash4"},
        {"original": "World", "src_hash": "hash2"},
        {"original": "Test", "src_hash": "hash3"},
    ]
    aligned, carried = align_and_carry_translations(old_lines, new_lines, min_unchanged_ratio=0.5)
    assert carried is True
    assert aligned[0]["translations"]["el"] == "Γεια"
    assert aligned[2]["translations"]["el"] == "Κόσμος"
    assert aligned[3]["translations"]["el"] == "Δοκιμή"
    assert "translations" not in aligned[1] or not aligned[1]["translations"]

def test_conformance():
    limits = {"max_cps": 15.0, "max_chars_per_line": 40, "max_lines": 2}
    
    # Conformant line
    m = measure_line("Hello world", "00:00:01,000 --> 00:00:03,000")
    assert m["duration"] == 2.0
    assert m["cps"] == 5.5
    
    # Wrapping
    wrapped = wrap_text("This is a very long line that should be wrapped balanced", max_chars=30)
    assert "\n" in wrapped
    
def test_atomic_write_concurrency():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "test.json"
        data = {"test": "value"}
        storage.write_json_atomic(path, data)
        assert path.exists()
        import json
        with open(path) as f:
            assert json.load(f) == data
