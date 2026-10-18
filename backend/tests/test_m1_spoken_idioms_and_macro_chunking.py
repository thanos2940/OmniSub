"""Tests for Milestone 1 (M1: Spoken Idiomatic Translation & Synchronization).

Covers:
1. Spoken Modern Greek (δημοτική) idiom adaptation & system instructions (translator_agent.py).
2. Main cast character profile fallback when character names are unmentioned (character_profiles.py).
3. Long-form subtitle macro-chunking (TV <= 2 calls, Movies <= 5 calls) and batched missing line repair (atomic_runner.py).
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from adk_agents.translator_agent import (
    _spoken_greek_idioms_rule,
    build_translator_instruction,
    build_translation_prompt,
)
from utils.character_profiles import CharacterProfileManager, CharacterProfile
from services.translation.atomic_runner import build_macro_chunks


# ===========================================================================
# 1. Spoken Greek Idiom Rule & System Instructions
# ===========================================================================

def test_spoken_greek_idioms_rule_greek_variants():
    """Verify that Greek language codes return comprehensive spoken idiom instructions."""
    for lang in ("Greek", "el", "ελληνικά", "GREEK", "EL"):
        rule = _spoken_greek_idioms_rule(lang)
        assert "SPOKEN GREEK IDIOMATIC NATURALNESS" in rule
        assert "ΔΗΜΟΤΙΚΗ" in rule
        assert "fair enough" in rule and "πάσο" in rule
        assert "give me a break" in rule and "έλα τώρα!" in rule
        assert "come on" in rule
        assert "you know" in rule
        assert "for real" in rule
        assert "cut it out" in rule
        assert "my bad" in rule and "δικό μου λάθος" in rule
        assert "on it" in rule
        assert "take it easy" in rule
        assert "make sense" in rule and "βγάζει νόημα" in rule
        assert "tell you what" in rule and "άκου να δεις" in rule
        assert "I got you" in rule
        assert "take your time" in rule and "με την ησυχία σου" in rule
        assert "don't sweat it" in rule
        assert "hear me out" in rule
        assert "no hard feelings" in rule
        assert "are you out of your mind?" in rule
        assert "looking forward to" in rule and "ανυπομονώ" in rule


def test_spoken_greek_idioms_rule_non_greek():
    """Verify non-Greek languages get generic idiom guidance."""
    rule = _spoken_greek_idioms_rule("French")
    assert "IDIOMATIC NATURALNESS" in rule
    assert "French" in rule
    assert "ΔΗΜΟΤΙΚΗ" not in rule


def test_system_instruction_includes_spoken_greek_idiom_rule():
    """Verify both structured and standard system instructions include all linguistic directives."""
    for structured in (True, False):
        instr = build_translator_instruction("Greek", {"terms": []}, "", structured=structured)
        assert "SPOKEN GREEK IDIOMATIC NATURALNESS" in instr
        assert "fair enough" in instr
        assert "ONE LINE PER CUE" in instr
        assert "SECOND PERSON" in instr
        assert "GLOSSARY MORPHOLOGY" in instr
        assert "PRO-DROP (NULL SUBJECT)" in instr
        assert "ACTIVE OVER PASSIVE VOICE" in instr
        assert "NATURAL CONTRACTIONS & ELISIONS" in instr
        assert "REGISTER BOUNDARIES & CONVERSATIONAL PARTICLES" in instr
        assert "AUTHENTIC COLLOQUIAL PROFANITY" in instr
        assert "SUBTITLE CADENCE & CONCISENESS" in instr


# ===========================================================================
# 2. Character Profile Baseline Fallback
# ===========================================================================

def test_character_profiles_fallback_when_names_unmentioned(tmp_path, monkeypatch):
    """When character names do not appear in dialogue cues, fallback to main cast profiles."""
    import utils.storage as storage
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path / "projects")

    storage.create_project("TestProj", {"target_language": "Greek"})
    mgr = CharacterProfileManager("TestProj")

    mgr.update_profile("Frieren", {
        "gender": "feminine",
        "formality": "informal",
        "speech_patterns": "Calm and understated",
        "verbal_tics": ["I see"],
    })
    mgr.update_profile("Fern", {
        "gender": "feminine",
        "formality": "mixed",
        "speech_patterns": "Formal with elders, direct",
    })
    mgr.update_profile("Stark", {
        "gender": "masculine",
        "formality": "informal",
        "speech_patterns": "Timid but brave",
    })

    # Case A: Character name mentioned in dialogue -> returns exact match
    dialogue_with_name = ["Where are you going, Frieren?", "Just taking a walk."]
    matched = mgr.get_profiles_for_chunk(dialogue_with_name, glossary={})
    assert len(matched) == 1
    assert matched[0].name == "Frieren"

    # Case B: No character names in dialogue -> returns main cast fallback profiles
    dialogue_nameless = ["I was tired after the long journey.", "Let us rest here."]
    fallback_profs = mgr.get_profiles_for_chunk(dialogue_nameless, glossary={}, fallback_to_main_cast=True)
    assert len(fallback_profs) >= 2
    names = {p.name for p in fallback_profs}
    assert "Frieren" in names
    assert "Fern" in names

    # Case C: Fallback disabled -> returns empty list
    no_fallback = mgr.get_profiles_for_chunk(dialogue_nameless, glossary={}, fallback_to_main_cast=False)
    assert len(no_fallback) == 0


def test_build_profile_context_with_fallback_header():
    """Verify build_profile_context formats properly with normal and fallback headers."""
    mgr = CharacterProfileManager("TestProj")
    profs = [
        CharacterProfile(name="Frieren", gender="feminine", formality="informal", speech_patterns="Calm"),
        CharacterProfile(name="Himmel", gender="masculine", formality="informal"),
    ]

    normal_ctx = mgr.build_profile_context(profs, is_fallback=False)
    assert "Characters in this scene:" in normal_ctx
    assert "Frieren (f, informal)" in normal_ctx
    assert "Himmel (m, informal)" in normal_ctx

    fallback_ctx = mgr.build_profile_context(profs, is_fallback=True)
    assert "Main cast reference for speaker voice/gender alignment:" in fallback_ctx
    assert "Frieren (f, informal)" in fallback_ctx


# ===========================================================================
# 3. Macro-Chunking (TV <= 2 calls, Movie <= 5 calls)
# ===========================================================================

def _generate_mock_cues(count: int, gap_interval: int = 200, gap_duration_ms: int = 5000):
    """Generate mock subtitle cues with natural scene pauses every gap_interval cues."""
    cues = []
    current_ms = 0
    for i in range(count):
        start_ms = current_ms
        end_ms = start_ms + 1500  # 1.5s subtitle duration
        
        # Insert a natural scene pause (>3000ms) periodically
        if (i + 1) % gap_interval == 0:
            current_ms = end_ms + gap_duration_ms
        else:
            current_ms = end_ms + 500  # 0.5s pause between standard cues

        def _fmt(ms):
            s, ms_rem = divmod(ms, 1000)
            m, s = divmod(s, 60)
            h, m = divmod(m, 60)
            return f"{h:02d}:{m:02d}:{s:02d},{ms_rem:03d}"

        cues.append({
            "id": str(i + 1),
            "timecode": f"{_fmt(start_ms)} --> {_fmt(end_ms)}",
            "original": f"Subtitle line {i + 1}",
            "translated": "",
        })
    return cues


def test_macro_chunking_small_episode():
    """Small episodes (<= 450 cues) produce exactly 1 macro-chunk (1 API call)."""
    cues = _generate_mock_cues(350)
    chunks = build_macro_chunks(cues, max_lines_per_chunk=450)
    assert len(chunks) == 1
    assert chunks[0]["start_index"] == 0
    assert chunks[0]["end_index"] == 350
    assert len(chunks[0]["lines"]) == 350


def test_macro_chunking_tv_episodes_stay_within_2_calls():
    """Standard TV episodes (300-800 cues) chunk into <= 2 macro-chunks."""
    # 600-cue TV episode
    cues_600 = _generate_mock_cues(600, gap_interval=300)
    chunks_600 = build_macro_chunks(cues_600, max_lines_per_chunk=450)
    assert len(chunks_600) == 2
    assert chunks_600[0]["start_index"] == 0
    assert chunks_600[0]["end_index"] == chunks_600[1]["start_index"]
    assert chunks_600[1]["end_index"] == 600

    # 800-cue long TV episode
    cues_800 = _generate_mock_cues(800, gap_interval=400)
    chunks_800 = build_macro_chunks(cues_800, max_lines_per_chunk=450)
    assert len(chunks_800) == 2
    assert chunks_800[0]["start_index"] == 0
    assert chunks_800[1]["end_index"] == 800


def test_macro_chunking_movies_stay_within_5_calls():
    """Full-length movies (800-1800 cues) chunk into <= 5 macro-chunks (~350-450 cues each)."""
    # 1000-cue movie -> 3 chunks
    cues_1000 = _generate_mock_cues(1000, gap_interval=330)
    chunks_1000 = build_macro_chunks(cues_1000, max_lines_per_chunk=450)
    assert len(chunks_1000) == 3

    # 1400-cue feature movie -> 4 chunks
    cues_1400 = _generate_mock_cues(1400, gap_interval=350)
    chunks_1400 = build_macro_chunks(cues_1400, max_lines_per_chunk=450)
    assert len(chunks_1400) == 4

    # 1800-cue extended movie -> 5 chunks
    cues_1800 = _generate_mock_cues(1800, gap_interval=360)
    chunks_1800 = build_macro_chunks(cues_1800, max_lines_per_chunk=450)
    assert len(chunks_1800) == 5

    # Verify strict 1:1 continuity across all movie macro-chunks
    all_lines = []
    for c in chunks_1800:
        all_lines.extend(c["lines"])
    assert len(all_lines) == 1800
    for idx, line in enumerate(all_lines):
        assert line["original"] == f"Subtitle line {idx + 1}"


def test_macro_chunking_splits_at_natural_time_gap():
    """Verify macro-chunker selects the largest temporal pause near the midpoint."""
    cues = _generate_mock_cues(600, gap_interval=1000)  # default small gaps
    
    # Manually inject a 10-second scene change pause between cue 295 and 296
    # (near ideal midpoint 300)
    # Cue 295 ends at 00:09:50,000, cue 296 starts at 00:10:00,000 (10000ms gap)
    cues[295]["timecode"] = "00:09:48,000 --> 00:09:50,000"
    cues[296]["timecode"] = "00:10:00,000 --> 00:10:02,000"

    chunks = build_macro_chunks(cues, max_lines_per_chunk=450)
    assert len(chunks) == 2
    # Split should be chosen at cue 296 (index 296)
    assert chunks[0]["end_index"] == 296
    assert chunks[1]["start_index"] == 296
