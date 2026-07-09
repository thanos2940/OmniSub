import pytest
from utils.language_codes import (
    to_code,
    variants_for,
    strip_lang_tag,
    output_filename,
    output_filename_from_original,
    matches_language,
    LANG_CODES,
)

def test_to_code():
    # Exact name match (case-insensitive)
    assert to_code("Greek") == "el"
    assert to_code("greek") == "el"
    assert to_code("English") == "en"
    assert to_code("spanish") == "es"

    # Known code
    assert to_code("el") == "el"
    assert to_code("en") == "en"

    # Substring/prefix match
    assert to_code("Spanish (Spain)") == "es"
    assert to_code("French (Canada)") == "fr"

    # Fallback
    assert to_code("Swahili") == "sw"
    assert to_code("xyz") == "xy"
    assert to_code("") == "xx"
    assert to_code(None) == "xx"

def test_variants_for():
    assert "el" in variants_for("el")
    assert "ell" in variants_for("el")
    assert "greek" in variants_for("el")
    assert "eng" in variants_for("en")
    assert "unknown" in variants_for("unknown")

def test_strip_lang_tag():
    assert strip_lang_tag("Show.S01E01.en") == "Show.S01E01"
    assert strip_lang_tag("Show.S01E01.eng") == "Show.S01E01"
    assert strip_lang_tag("Show.S01E01") == "Show.S01E01"

def test_output_filename():
    assert output_filename("Show.S01E01", "el") == "Show.S01E01.el.srt"
    assert output_filename("Show.S01E01.en", "el") == "Show.S01E01.el.srt"
    assert output_filename("Show.S01E01", "el", ext="ass") == "Show.S01E01.el.ass"
    assert output_filename("Show.S01E01.en", "el", ext="ssa") == "Show.S01E01.el.ssa"

def test_output_filename_from_original():
    assert output_filename_from_original("Show.S01E01.en.srt", "el") == "Show.S01E01.el.srt"
    assert output_filename_from_original("Show.S01E01.srt", "el") == "Show.S01E01.el.srt"
    assert output_filename_from_original("Show.S01E01.en.ass", "el") == "Show.S01E01.el.ass"
    assert output_filename_from_original("Show.S01E01.ssa", "el") == "Show.S01E01.el.ssa"

def test_matches_language():
    # Matching Greek variants
    assert matches_language("Show.S01E01.el.srt", "Show.S01E01", "el") is True
    assert matches_language("Show.S01E01.ell.srt", "Show.S01E01", "el") is True
    assert matches_language("Show.S01E01.greek.srt", "Show.S01E01", "el") is True
    assert matches_language("Show.S01E01.en.srt", "Show.S01E01", "el") is False

    # English matching
    assert matches_language("Show.S01E01.en.srt", "Show.S01E01", "en") is True
    assert matches_language("Show.S01E01.srt", "Show.S01E01", "en") is True
    assert matches_language("Show.S01E01.srt", "Show.S01E01", "el") is False

    # ASS/SSA matching
    assert matches_language("Show.S01E01.el.ass", "Show.S01E01", "el") is True
    assert matches_language("Show.S01E01.greek.ssa", "Show.S01E01", "el") is True
    assert matches_language("Show.S01E01.ass", "Show.S01E01", "en") is True
    assert matches_language("Show.S01E01.ssa", "Show.S01E01", "el") is False
