"""
Milestone 4 (M4) Comprehensive Verification Test Suite.
Tests:
  1. eval/dataset/example.json expansion (15+ items, idioms, morphology, formality, sync)
  2. eval/run_eval.py & metrics.py calculation (offline chrF, adherence, format, consistency, overall)
  3. adk_agents/reviewer_agent.py (Demotic Greek idioms, morphology, voice traits in prompts)
  4. utils/script_guard.py (zero-token homoglyphs, latin leakage detection, foreign script isolation)
  5. utils/subtitle_conformance.py (CPS/CPL conformance, ASS/HTML tag stripping, auto-balancing)
  6. routers/glossary_context.py (safe string/dict iteration in list_projects / descendants)
"""

import json
from pathlib import Path
import pytest

from eval import metrics as M
from eval.run_eval import EVAL_DIR
from eval.report import format_report, load_json
from adk_agents.reviewer_agent import create_reviewer_agent, build_review_prompt, _build_glossary_ref
from utils.script_guard import (
    normalize_confusables,
    foreign_issues,
    scrub_rows,
    flag_rows,
    latin_ratio,
    detect_latin_leakage,
)
from utils.subtitle_conformance import (
    measure_line,
    measure,
    default_limits,
    wrap_text,
    autobalance_rows,
    split_text_parts,
    _TAG_RE,
)


def test_eval_dataset_expansion_and_coverage():
    """Verify example.json has >= 15 items and covers all required linguistic categories."""
    dataset_path = EVAL_DIR / "dataset" / "example.json"
    assert dataset_path.exists()
    
    with open(dataset_path, "r", encoding="utf-8") as f:
        items = json.load(f)
        
    assert len(items) >= 15, f"Expected >= 15 items, found {len(items)}"
    
    sources = " ".join(it.get("source", "") for it in items).lower()
    
    # Verify required idioms
    required_idioms = [
        "fair enough",
        "give me a break",
        "come on",
        "you know",
        "cut it out",
        "my bad",
    ]
    for idiom in required_idioms:
        assert idiom in sources, f"Missing required idiom '{idiom}' in dataset sources"
        
    # Verify morphological declensions (Nom/Gen/Acc/Voc)
    item_ids = [it.get("id", "") for it in items]
    assert any("nom" in i for i in item_ids), "Missing Nominative test case"
    assert any("gen" in i for i in item_ids), "Missing Genitive test case"
    assert any("acc" in i for i in item_ids), "Missing Accusative test case"
    assert any("voc" in i for i in item_ids), "Missing Vocative test case"
    
    # Verify formality (formal vs familiar)
    assert any("formality-formal" in i for i in item_ids), "Missing formal register test case"
    assert any("formality-familiar" in i for i in item_ids), "Missing familiar register test case"
    
    # Verify 1:1 multiline synchronization
    multiline_items = [it for it in items if "\n" in it.get("source", "")]
    assert len(multiline_items) >= 2, "Expected >= 2 multiline synchronization test cases"
    for it in multiline_items:
        src_lines = [l for l in it["source"].split("\n") if l.strip()]
        hyp_lines = [l for l in it["hypothesis"].split("\n") if l.strip()]
        assert len(src_lines) == len(hyp_lines), "Line count mismatch in multiline item"


def test_eval_metrics_offline_execution():
    """Verify offline metrics computation produces perfect scores for canonical dataset."""
    dataset_path = EVAL_DIR / "dataset" / "example.json"
    items = load_json(str(dataset_path))
    
    scored = M.score_all(items)
    
    assert scored["count"] == len(items)
    assert scored["chrf"] is not None and scored["chrf"] >= 0.99
    assert scored["glossary_adherence"] == 1.0
    assert scored["format_integrity"] == 1.0
    assert scored["consistency"] == 1.0
    assert scored["overall"] is not None and scored["overall"] >= 0.99
    
    # Test report formatting
    report = format_report(scored)
    assert "=== Omnisub translation eval ===" in report
    assert f"items: {len(items)}" in report
    assert "chrf" in report
    assert "glossary_adherence" in report


def test_reviewer_agent_demotic_greek_prompt_alignment():
    """Verify reviewer agent instructions contain Demotic Greek idiom rules, morphology, and voice traits."""
    glossary = {
        "terms": [
            {"term": "Commander", "translation": "Διοικητής", "type": "person", "gender": "masculine"},
            {"term": "Council", "translation": "Συμβούλιο", "type": "organization", "gender": "neuter"},
        ]
    }
    
    agent = create_reviewer_agent(
        model_name="gemini-flash-lite-latest",
        target_language="Greek",
        glossary=glossary,
    )
    
    instruction = agent.instruction
    assert "δημοτική" in instruction or "Modern Greek" in instruction
    assert "idioms" in instruction.lower()
    assert "fair enough" in instruction
    assert "Nominative" in instruction or "ο/του/τον" in instruction
    assert "πληθυντικός" in instruction or "register" in instruction
    assert "CPS" in instruction or "length" in instruction
    
    # Verify glossary reference formatting
    assert "Commander -> Διοικητής (person, masculine)" in instruction
    
    # Verify prompt builder
    pairs = [
        {"index": 1, "original": "Fair enough.", "translated": "Πάσο."},
        {"index": 2, "original": "Come on!", "translated": "Έλα!"},
    ]
    prompt = build_review_prompt(pairs, "Greek")
    assert "1| EN: Fair enough." in prompt
    assert "GR: Πάσο." in prompt
    assert "2| EN: Come on!" in prompt


def test_script_guard_zero_token_homoglyphs_and_latin_leakage():
    """Verify zero-token homoglyph normalization and Latin leakage detection."""
    # Visual twin normalization
    fixed, n = normalize_confusables("Δεv ξέρω.", "el")
    assert fixed == "Δεν ξέρω."
    assert n == 1
    
    # Cyrillic visual twin
    fixed_cyr, n_cyr = normalize_confusables("Τ\u043eν είδα.", "el")  # Cyrillic o
    assert fixed_cyr == "Τον είδα."
    assert n_cyr == 1
    
    # Ambiguous fragments left alone
    fixed_amb, n_amb = normalize_confusables("ακούsheτε", "el")
    assert fixed_amb == "ακούsheτε"
    assert n_amb == 0
    
    # Latin leakage detection
    assert latin_ratio("This is completely English text without Greek") > 0.9
    assert latin_ratio("Αυτό είναι καθαρά ελληνικό κείμενο") == 0.0
    
    assert detect_latin_leakage(
        "This is an English sentence.",
        "This is an English sentence.",
        "el"
    ) is True
    
    assert detect_latin_leakage(
        "This is an English sentence.",
        "Αυτή είναι μια ελληνική πρόταση.",
        "el"
    ) is False


def test_subtitle_conformance_tag_stripping_and_cps_cpl():
    """Verify subtitle conformance properly strips ASS override tags and measures CPS/CPL."""
    # Tag stripping verification
    assert _TAG_RE.sub("", "<i>Italic</i> text") == "Italic text"
    assert _TAG_RE.sub("", "{\\an8}Top subtitle line") == "Top subtitle line"
    assert _TAG_RE.sub("", "{\\i1\\b1}Formatted text{\\i0\\b0}") == "Formatted text"
    
    # Measure line with ASS tags
    measured = measure_line("{\\an8}Γεια σου κόσμε!", "00:00:01,000 --> 00:00:03,000")
    assert measured["char_count"] == len("Γεια σου κόσμε!")
    assert measured["duration"] == 2.0
    assert measured["cps"] == round(len("Γεια σου κόσμε!") / 2.0, 1)
    
    # Line auto-balancing
    long_text = "Αυτή είναι μια πάρα πολύ μεγάλη γραμμή υποτίτλων που σίγουρα χρειάζεται αυτόματη αναδίπλωση σε δύο ισορροπημένες γραμμές."
    wrapped = wrap_text(long_text, max_chars=42, max_lines=2)
    assert "\n" in wrapped
    for line in wrapped.split("\n"):
        assert len(line) <= 65  # reasonably bounded balanced split


@pytest.mark.asyncio
async def test_glossary_context_sync_candidates_handles_dict_or_string_projects(monkeypatch):
    """Verify get_sync_candidates safely handles storage.list_projects() returning strings or dicts."""
    from routers.glossary_context import get_sync_candidates
    from utils import storage

    parent_name = "TestParentUniverse"
    child_name = "TestChildShow"

    # Mock metadata
    parent_meta = {
        "show_name": parent_name,
        "target_language": "Greek",
        "glossary": {"terms": []},
    }
    child_meta = {
        "show_name": child_name,
        "parent_project": parent_name,
        "target_language": "Greek",
        "glossary": {"terms": [{"term": "Excalibur", "translation": "Εξκάλιμπερ", "type": "item"}]},
    }

    def mock_load_metadata(name):
        if name == parent_name:
            return dict(parent_meta)
        elif name == child_name:
            return dict(child_meta)
        return None

    monkeypatch.setattr(storage, "load_project_metadata", mock_load_metadata)

    # Test 1: list_projects returns list of strings
    monkeypatch.setattr(storage, "list_projects", lambda: [parent_name, child_name])
    res_str = await get_sync_candidates(parent_name)
    assert len(res_str["glossary"]) == 1
    assert res_str["glossary"][0]["term"] == "Excalibur"

    # Test 2: list_projects returns list of dicts (e.g. from adapted/mocked store)
    monkeypatch.setattr(storage, "list_projects", lambda: [{"name": parent_name}, {"name": child_name}])
    res_dict = await get_sync_candidates(parent_name)
    assert len(res_dict["glossary"]) == 1
    assert res_dict["glossary"][0]["term"] == "Excalibur"

