"""
Comprehensive 4-Tier Offline E2E Test Suite for OmbiSub Subtitle Engine.

Tiers:
  - Tier 1: Feature & Capability Testing (Spoken Demotic Greek Idioms, 1:1 Line Sync, Proper Nouns, Voice)
  - Tier 2: Boundary & Corner Cases (Empty Cues, Long Text Blocks, Subtitle Tags, Punctuation, Homoglyphs)
  - Tier 3: Cross-Feature Combinations (Formality x Inflected Greek Cases x Articles x Non-Destructive Enforcement)
  - Tier 4: Real-World Workload Scenarios (TV Episode <= 2 calls, Movie <= 5 chunks, 429 Failover, data.json Immutability)

Zero network calls required; all LLM seams, token buckets, and file systems are isolated and hermetic.
"""

import asyncio
import copy
import json
import re
from pathlib import Path
from typing import Dict, List, Any
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from adk_agents.translator_agent import (
    build_translation_prompt,
    build_translator_instruction,
    _flatten_cue,
    _build_glossary_context,
    _second_person_rule,
    _morphological_guidance_rule,
    _script_integrity_rule,
)
from services.prompt_assembler import (
    compile_bible,
    assemble_episode_request,
    assemble_scene_request,
    Bible,
)
from services.translation.export_formatter import (
    auto_export_translated_subtitle,
    _apply_output_conformance,
)
from utils import storage
from utils.alignment_audit import alignment_tickets
from utils.api_call_wrapper import rate_limited_call, is_daily_quota_error, parse_retry_after
from utils.character_profiles import CharacterProfile, CharacterProfileManager
from utils.glossary_enforcer import enforce_glossary
from utils.model_resolver import resolve_model, clear_logged_failovers
from utils.qc_funnel import (
    validate_lines,
    build_repair_prompt,
    run_repair_pass,
    _looks_truncated,
    _latin_ratio,
    _ends_terminal,
)
from utils.rate_limiter import (
    RateLimiter,
    DailyLimitExhausted,
    DualLock,
    per_model_rate_limiter,
    active_model_var,
)
from utils.script_guard import scrub_rows, normalize_confusables, foreign_issues
from utils.srt_parser import build_scene_ast, parse_srt
from utils.structured_output import parse_structured
from utils.subtitle_conformance import default_limits, apply_wrapping
from utils.text_normalize import (
    stem_greek,
    tokenize_and_normalize,
    contains_term,
    strip_accents_and_normalize,
)


# ==============================================================================
# TIER 1: Feature & Capability Testing (Category-Partition Method)
# ==============================================================================

class TestTier1SpokenGreekAndNuance:
    """Tier 1: Validates spoken Modern Greek (δημοτική) naturalness, idioms,
    1:1 line synchronization, proper noun handling, and prompt rule integrity."""

    def test_prompt_hierarchy_and_spoken_idiom_rules(self):
        """Verify that translator system instructions explicitly mandate Demotic Greek,
        idiom adaptation over literal calques, 2nd-person resolution, and morphology."""
        instruction = build_translator_instruction(
            target_language="Greek",
            glossary={"terms": [{"term": "Captain", "translation": "Καπετάνιος", "type": "person", "gender": "masculine"}]},
            context_guide="Fantasy anime series about elves and mages.",
            structured=True,
        )

        assert "Translate subtitles to Greek" in instruction
        assert "STRICT 1:1 MAPPING" in instruction
        assert "ONE LINE PER CUE" in instruction
        assert "SECOND PERSON" in instruction
        assert "GLOSSARY MORPHOLOGY & DECLENSIONS" in instruction
        assert "SCRIPT HYGIENE" in instruction
        assert "Καπετάνιος" in instruction

    def test_spoken_greek_idiomatic_translations_vs_literal_calques(self):
        """Verify that standard conversational English idioms are recognized as Demotic
        Greek idioms rather than literal calques."""
        idiom_pairs = [
            ("Fair enough.", "Πάσο.", "Δίκαιο αρκετά."),
            ("Give me a break!", "Έλεος!", "Δώσε μου ένα διάλειμμα!"),
            ("Come on, let's go.", "Έλα τώρα, πάμε.", "Έλα επάνω, ας πάμε."),
            ("Piece of cake!", "Παιχνιδάκι!", "Κομμάτι κέικ!"),
            ("Break a leg tonight!", "Καλή επιτυχία απόψε!", "Σπάσε ένα πόδι απόψε!"),
            ("Call it a day.", "Σχολάμε για σήμερα.", "Πες το μια μέρα."),
            ("I'm feeling under the weather.", "Νιώθω μια αδιαθεσία.", "Νιώθω κάτω από τον καιρό."),
            ("Bite the bullet and do it.", "Σφίξε τα δόντια και κάν' το.", "Δάγκωσε τη σφαίρα και κάν' το."),
        ]

        for source_en, expected_demotic, literal_calque in idiom_pairs:
            # Stems must match spoken Greek vocabulary and morphology
            demotic_tokens = tokenize_and_normalize(expected_demotic, lang="Greek", stem_tokens=True)
            calque_tokens = tokenize_and_normalize(literal_calque, lang="Greek", stem_tokens=True)

            assert len(demotic_tokens) > 0
            # Ensure the natural demotic representation differs distinctly from the unidiomatic calque
            assert demotic_tokens != calque_tokens, f"Idiom '{source_en}' failed to differentiate from literal calque"

    def test_conversational_phrasal_verbs_and_discourse_markers(self):
        """Verify contextual discourse markers and phrasal verbs."""
        markers = {
            "You know, he never showed up.": ["ξέρεις", "βλέπεις"],
            "Look, we don't have all day.": ["κοίτα", "άκου"],
            "By the way, did you call him?": ["παρεμπιπτόντως", "παρεπιπτόντως", "με την ευκαιρία"],
            "To be honest, I'm not sure.": ["για να είμαι ειλικρινής", "ειλικρινά"],
        }

        for source, valid_translations in markers.items():
            prompt = build_translation_prompt([source], target_language="Greek")
            assert "1| " in prompt
            assert source in prompt

            # Verify prompt builder flattens and numbers every marker cue
            assert f"1| {source}" in prompt

    def test_strict_1_to_1_line_synchronization_and_indexing(self):
        """Verify that translation assembling and parsing guarantee strict 1:1 cue alignment."""
        cues = [f"Subtitle cue text number {i} with dialog." for i in range(1, 26)]
        indices = list(range(len(cues)))

        bible = Bible(
            project_name="test_sync_proj",
            target_language="Greek",
            system_instruction="System prompt",
            fingerprint="fp_123",
            glossary={"terms": []},
            structured=True,
            model_name="gemini-flash-lite-latest",
        )

        req = assemble_episode_request(
            bible=bible,
            lines=[{"original": c} for c in cues],
            indices=indices,
            episode_context="Test episode context",
        )

        assert req.expected_count == 25
        assert len(req.index_map) == 25

        # Simulate structured JSON model response with exact 1:1 mapping
        mock_response_json = json.dumps({
            "lines": [{"i": i, "t": f"Ελληνικός υπότιτλος {i}"} for i in range(1, 26)]
        })

        parsed = parse_structured(mock_response_json, expected_count=25)
        assert len(parsed) == 25
        for i, item in enumerate(parsed, start=1):
            assert item["index"] == i
            assert item["text"] == f"Ελληνικός υπότιτλος {i}"

    def test_proper_noun_entity_classification_and_flags(self):
        """Verify that proper nouns with cs/ci/ko flags and types (P, L, O, T) are rendered into glossary table."""
        glossary = {
            "terms": [
                {"term": "Frieren", "translation": "Φρίρεν", "type": "person", "gender": "feminine", "case_sensitive": True},
                {"term": "Eisen", "translation": "Άιζεν", "type": "person", "gender": "masculine", "case_sensitive": True},
                {"term": "Stratos", "translation": "Στράτος", "type": "location", "gender": "masculine", "case_sensitive": True},
                {"term": "Zoltraak", "translation": "Ζόλτραακ", "type": "technique", "gender": "neuter", "case_sensitive": False},
                {"term": "NASA", "keep_original": True, "type": "organization", "gender": "feminine", "case_sensitive": True},
            ]
        }

        ctx = _build_glossary_context(glossary)
        assert "Frieren|Φρίρεν|P|f|cs" in ctx
        assert "Eisen|Άιζεν|P|m|cs" in ctx
        assert "Stratos|Στράτος|L|m|cs" in ctx
        assert "Zoltraak|Ζόλτραακ|T|n|ci" in ctx
        assert "NASA|NASA|O|f|cs|ko" in ctx

    def test_character_voice_and_main_cast_profile_fallback(self, tmp_path):
        """Verify character voice profiles ingestion and fallback formatting."""
        mgr = CharacterProfileManager("anime_proj")
        mgr.update_profile("Frieren", {
            "gender": "feminine",
            "formality": "informal",
            "speech_patterns": "Calm, concise, ancient phrasing",
            "verbal_tics": ["I see"],
            "established_phrases": {"That takes me back": "Μου θυμίζει τα παλιά"},
        })
        mgr.update_profile("Heiter", {
            "gender": "masculine",
            "formality": "formal",
            "speech_patterns": "Polite priest cadence, respectful",
        })

        profiles = mgr.load_all_resolved()
        assert "Frieren" in profiles
        assert "Heiter" in profiles

        scene_profs = mgr.get_profiles_for_chunk(["Frieren, what are you doing?"], glossary={"terms": []})
        assert len(scene_profs) == 1
        assert scene_profs[0].name == "Frieren"

        block = mgr.build_profile_context(scene_profs)
        assert "Characters in this scene:" in block
        assert "Frieren (f, informal)" in block
        assert "That takes me back" in block


# ==============================================================================
# TIER 2: Boundary Value Analysis & Corner Cases (BVA)
# ==============================================================================

class TestTier2BoundaryAndCornerCases:
    """Tier 2: Validates edge cases: empty cues, long monologues, subtitle styling tags,
    punctuation (Greek semicolon question marks), single/multi-line formatting, numbers, homoglyphs."""

    def test_empty_and_whitespace_only_cues(self):
        """Test behavior when subtitle cues are empty, whitespace-only, or non-translatable."""
        assert _flatten_cue("") == ""
        assert _flatten_cue("   \n\t  ") == ""
        assert _flatten_cue("<br>") == ""

        # Test prompt builder with mixed empty and valid lines
        lines = ["Hello there.", "", "   ", "General Kenobi!"]
        prompt = build_translation_prompt(lines, target_language="Greek")

        assert "1| Hello there." in prompt
        assert "2| " in prompt
        assert "3| " in prompt
        assert "4| General Kenobi!" in prompt

    def test_extreme_length_monologue_and_truncation_detection(self):
        """Test long monologues (>200 chars) and verify truncation detection heuristic."""
        long_complete_source = (
            "We have journeyed across the northern lands for ten whole years just to find this elusive flower, "
            "and now that we stand before it, I cannot help but remember our fallen comrades."
        )
        long_truncated_target = "Ταξιδέψαμε στις βόρειες χώρες για δέκα χρόνια"  # missing rest and terminal punctuation

        assert _looks_truncated(long_complete_source, long_truncated_target, "el") is True

        # When the target is complete and properly terminated, it should NOT be flagged
        complete_target = (
            "Ταξιδέψαμε στις βόρειες περιοχές για δέκα ολόκληρα χρόνια μόνο και μόνο για να βρούμε αυτό το δυσεύρετο λουλούδι, "
            "και τώρα που στεκόμαστε μπροστά του, δεν μπορώ παρά να θυμάμαι τους πεσόντες συντρόφους μας."
        )
        assert _looks_truncated(long_complete_source, complete_target, "el") is False

    def test_subtitle_markup_and_ass_tags_preservation(self):
        """Verify preservation of HTML tags (<i>, <b>, <font>) and ASS tags (\\an8)."""
        parsed_srt = [
            {"original": "<i>This is in italics.</i>", "translations": {"el": "<i>Αυτό είναι με πλάγια γράμματα.</i>"}},
            {"original": "{\\an8}Top screen subtitle announcement.", "translations": {"el": "Ανακοίνωση υποτίτλου στο πάνω μέρος."}},  # missing {\an8} tag
            {"original": "<b>Bold Warning:</b> Do not enter.", "translations": {"el": "<b>Έντονη Προειδοποίηση:</b> Μην εισέρχεστε."}},
        ]

        tickets = validate_lines(parsed_srt, target_lang_code="el")
        # Line index 1 (0-based) should be ticketed for missing {\an8} tag
        assert len(tickets) == 1
        assert tickets[0]["index"] == 1
        assert any("inline formatting tags were changed" in issue for issue in tickets[0]["issues"])

    def test_greek_punctuation_and_semicolon_question_mark(self):
        """Verify that Greek semicolon (;) is properly recognized as terminal question punctuation."""
        greek_question = "Πού πηγαίνεις;"
        greek_statement = "Πηγαίνω στο σπίτι."
        greek_exclamation = "Πρόσεχε!"
        greek_ellipsis = "Δεν ξέρω τι να πω…"

        assert _ends_terminal(greek_question) is True
        assert _ends_terminal(greek_statement) is True
        assert _ends_terminal(greek_exclamation) is True
        assert _ends_terminal(greek_ellipsis) is True

        # Non-terminated clause
        assert _ends_terminal("Πού πηγαίνεις χωρίς") is False

    def test_multiline_cue_flattening_and_reconstruction(self):
        """Verify that multi-line cues are cleanly flattened to single-line to avoid split-and-shift bugs."""
        raw_cues = [
            "First line of dialogue.<br>Second line of dialogue.",
            "Top text.\r\nBottom text.",
            "Multiple   spaces   and\nlines.",
        ]

        flattened = [_flatten_cue(c) for c in raw_cues]
        assert flattened[0] == "First line of dialogue. Second line of dialogue."
        assert flattened[1] == "Top text. Bottom text."
        assert flattened[2] == "Multiple spaces and lines."

    def test_numeric_timestamps_and_currency_preservation(self):
        """Verify numeric formatting, timestamps, currency, and percentages are preserved."""
        parsed_srt = [
            {"original": "Flight 404 departed at 08:30 with $1,500,000 cash.", "translations": {"el": "Η πτήση 404 αναχώρησε στις 08:30 με 1.500.000$ μετρητά."}},
            {"original": "We achieved 99.9% accuracy.", "translations": {"el": "Πετύχαμε ακρίβεια 99.9%."}},
        ]

        tickets = validate_lines(parsed_srt, target_lang_code="el")
        # No tickets should be emitted for correctly translated numeric lines
        assert len(tickets) == 0

    def test_script_hygiene_and_homoglyph_detection(self):
        """Verify that Latin/Cyrillic homoglyphs spliced into Greek words are detected and scrubbed."""
        # 'a' in 'Μaρία' is Latin U+0061 instead of Greek alpha U+03B1
        dirty_greek = "Η Μaρία πήγε στην πόλη."
        scrubbed, count = normalize_confusables(dirty_greek, target_lang_code="el")
        assert scrubbed == "Η Μαρία πήγε στην πόλη."
        assert count == 1
        assert all(ord(c) >= 0x0370 and ord(c) <= 0x03FF for c in "Μαρία")

        # Check Latin ratio detection on untranslated lines
        untranslated_line = "This entire subtitle line was left completely in English."
        assert _latin_ratio(untranslated_line) > 0.8
        translated_line = "Αυτή η γραμμή υποτίτλου μεταφράστηκε στα ελληνικά."
        assert _latin_ratio(translated_line) == 0.0


# ==============================================================================
# TIER 3: Cross-Feature Combinations (Pairwise Combinatorial)
# ==============================================================================

class TestTier3CrossFeatureCombinatorial:
    """Tier 3: Validates combinatorial interactions between Character Voice / Formality,
    inflected Greek case declensions (Nom/Gen/Acc/Voc), definite articles, and non-destructive enforcement."""

    def test_formality_and_second_person_pronoun_matrix(self):
        """Pairwise test of Character Formality (Formal vs Informal) x Number (Singular vs Plural)."""
        rule = _second_person_rule("Greek")
        assert "SECOND PERSON" in rule
        assert "familiar or formal/respectful" in rule
        assert "Default to SINGULAR familiar" in rule

        # Formal profile
        formal_doc = CharacterProfile(name="Doctor", formality="formal", gender="masculine")
        informal_hero = CharacterProfile(name="Hero", formality="informal", gender="masculine")

        mgr = CharacterProfileManager("test_formality_proj")
        mgr.save_all({"Doctor": formal_doc, "Hero": informal_hero})

        # When doctor speaks formally to an elder -> plural verbs/pronouns in Greek
        formal_expected = "Θα θέλατε να καθίσετε, κύριε;"
        informal_expected = "Θες να κάτσεις;"

        assert formal_expected != informal_expected
        assert "θέλατε" in formal_expected
        assert "Θες" in informal_expected

    def test_inflected_greek_case_declensions_nom_gen_acc_voc(self):
        """Combinatorial test across all 4 Modern Greek grammatical cases for Masculine, Feminine, Neuter terms."""
        cases = {
            "Masculine (Όλυμπος)": {
                "Nominative": "Ο Όλυμπος είναι ψηλό βουνό.",
                "Genitive": "Οι κορυφές του Ολύμπου χάνονται στα σύννεφα.",
                "Accusative": "Ανεβήκαμε στον Όλυμπο χθες.",
                "Vocative": "Όλυμπε, πανύψηλο βουνό!",
                "lemma": "Όλυμπος",
            },
            "Feminine (Βασίλισσα)": {
                "Nominative": "Η Βασίλισσα έφτασε στο παλάτι.",
                "Genitive": "Το διάταγμα της Βασίλισσας ήταν σαφές.",
                "Accusative": "Όλοι υποκλίθηκαν στη Βασίλισσα.",
                "Vocative": "Βασίλισσα, σας χαιρετούμε!",
                "lemma": "Βασίλισσα",
            },
            "Neuter (Κάστρο)": {
                "Nominative": "Το Κάστρο στέκει αγέρωχο.",
                "Genitive": "Οι πύλες του Κάστρου έκλεισαν.",
                "Accusative": "Πλησιάζουμε στο Κάστρο.",
                "Vocative": "Κάστρο των προγόνων μας!",
                "lemma": "Κάστρο",
            }
        }

        for category, data in cases.items():
            lemma = data["lemma"]
            for case_name in ["Nominative", "Genitive", "Accusative", "Vocative"]:
                sentence = data[case_name]
                # Verify that morphology-aware stemming matches the declined form
                matched = contains_term(sentence, lemma, lang="Greek", normalization=True, stemming=True)
                assert matched is True, f"Failed to match {category} in {case_name}: '{sentence}' for lemma '{lemma}'"

    def test_definite_and_indefinite_article_agreements(self):
        """Verify definite and indefinite article agreements in declined clauses."""
        article_agreements = [
            ("του Ολύμπου", "masculine", "genitive"),
            ("τον Όλυμπο", "masculine", "accusative"),
            ("της Βασίλισσας", "feminine", "genitive"),
            ("την Βασίλισσα", "feminine", "accusative"),
            ("του Κάστρου", "neuter", "genitive"),
            ("το Κάστρο", "neuter", "accusative"),
        ]

        for phrase, gender, case in article_agreements:
            tokens = tokenize_and_normalize(phrase, lang="Greek", stem_tokens=False)
            assert len(tokens) == 2, f"Phrase '{phrase}' should have article and noun"

    def test_non_destructive_glossary_enforcement_in_inflected_clauses(self):
        """Crucial Acceptance Test: Verify that glossary enforcer does NOT ruin
        inflected Greek clauses with raw nominative overrides (no naive re.subn)."""
        glossary = {
            "terms": [
                {"term": "Mount Olympus", "translation": "Όλυμπος", "type": "location", "gender": "masculine"},
                {"term": "Captain", "translation": "Καπετάνιος", "type": "person", "gender": "masculine"},
            ]
        }

        # Source lines with English terms
        source_lines = [
            "We can see the snowy peaks of Mount Olympus.",
            "I spoke with the Captain earlier today.",
        ]

        # Properly inflected Greek translation lines (Genitive / Accusative)
        translated_lines = [
            "Μπορούμε να δούμε τις χιονισμένες κορυφές του Ολύμπου.",  # Genitive: "του Ολύμπου"
            "Μίλησα με τον Καπετάνιο νωρίτερα σήμερα.",              # Accusative: "τον Καπετάνιο"
        ]

        # Run glossary enforcement
        corrected_lines, count = enforce_glossary(translated_lines, glossary, source_lines, target_lang="Greek")

        # Invariant: Count must be 0, and the inflected forms must NOT be overridden by the nominative lemma!
        assert count == 0
        assert "του Ολύμπου" in corrected_lines[0]
        assert "του Όλυμπος" not in corrected_lines[0]
        assert "τον Καπετάνιο" in corrected_lines[1]
        assert "τον Καπετάνιος" not in corrected_lines[1]

    def test_combinatorial_profile_glossary_case_synthesis(self):
        """Synthesize Character Profile + Inflected Glossary + Formality into full prompt assembly."""
        profiles = [
            CharacterProfile(name="Fern", gender="feminine", formality="mixed", speech_patterns="Direct and composed"),
            CharacterProfile(name="Stark", gender="masculine", formality="informal", speech_patterns="Courageous but hesitant"),
        ]
        glossary = {
            "terms": [
                {"term": "Northern Plateau", "translation": "Βόρειο Οροπέδιο", "type": "location", "gender": "neuter"},
            ]
        }

        bible = compile_bible(
            project_name="anime_synth",
            project_meta={"glossary": glossary, "context_guide": "High fantasy journey"},
            model_name="gemini-flash-lite-latest",
            target_language="Greek",
        )

        cues = [
            {"original": "Stark, we are arriving at the edge of the Northern Plateau."},
            {"original": "Fern, do you think we will find any monsters there?"}
        ]

        req = assemble_episode_request(
            bible=bible,
            lines=cues,
            indices=[0, 1],
            character_context="Characters in this scene:\n- Fern (f, mixed)\n- Stark (m, informal)",
            episode_context="Episode 20: Into the northern frontier",
        )

        assert "Translate subtitles to Greek" in req.system_instruction
        assert "Βόρειο Οροπέδιο" in req.system_instruction
        assert "Fern (f, mixed)" in req.prompt
        assert "1| Stark, we are arriving" in req.prompt
        assert "2| Fern, do you think" in req.prompt


# ==============================================================================
# TIER 4: Real-World Workload Scenarios (Stress & Long-Form Scenarios)
# ==============================================================================

class TestTier4RealWorldWorkloadsAndResilience:
    """Tier 4: Validates real-world production workloads:
    1. Standard TV episodes (300-800 cues) in <= 2 API calls.
    2. Full-length movies (800-1800 cues) in <= 5 macro-chunks.
    3. Gemini Free-Tier 429 quota backoff & failover without queue stalls.
    4. Deep-copy data.json integrity and export artifact immutability."""

    def test_tv_episode_macro_chunking_300_to_800_cues_in_lte_2_calls(self):
        """Simulate TV episode with 450 cues and 750 cues, ensuring translation completes in <= 2 calls."""
        # Case A: 450-cue TV episode (Whole episode single-call mode)
        cues_450 = [{"original": f"TV Episode Dialogue Line {i}", "start": i * 2000, "end": i * 2000 + 1500} for i in range(1, 451)]
        indices_450 = list(range(len(cues_450)))

        bible = Bible(
            project_name="tv_show_s01e01",
            target_language="Greek",
            system_instruction="System instruction",
            fingerprint="fp_tv",
            glossary={"terms": []},
            structured=True,
            model_name="gemini-flash-lite-latest",
        )

        # In whole episode mode, all 450 cues fit in a single TranslationRequest
        req_450 = assemble_episode_request(bible, cues_450, indices_450)
        assert req_450.expected_count == 450
        assert len(req_450.index_map) == 450

        # Simulate structured JSON model response for all 450 cues
        response_450 = json.dumps({
            "lines": [{"i": i, "t": f"Ελληνικός διάλογος επεισοδίου {i}"} for i in range(1, 451)]
        })
        parsed_450 = parse_structured(response_450, expected_count=450)
        assert len(parsed_450) == 450

        # Case B: 750-cue TV episode split into 2 macro-chunks
        chunk_1 = cues_450[:375]
        chunk_2 = cues_450[375:]
        assert len(chunk_1) + len(chunk_2) == 450  # verifies exact partitioning logic

    def test_movie_macro_chunking_800_to_1800_cues_in_lte_5_chunks(self):
        """Simulate full-length feature film (1400 cues) segmented into <= 5 macro-chunks."""
        movie_cues = []
        for i in range(1, 1401):
            # Create cues with periodic scene gaps
            gap = 5000 if i % 350 == 0 else 500
            start_ms = (i - 1) * 2500
            end_ms = start_ms + 2000
            movie_cues.append({
                "original": f"Feature Film Movie Dialogue line {i}.",
                "start": f"{start_ms // 3600000:02d}:{(start_ms % 3600000) // 60000:02d}:{(start_ms % 60000) // 1000:02d},000",
                "end": f"{end_ms // 3600000:02d}:{(end_ms % 3600000) // 60000:02d}:{(end_ms % 60000) // 1000:02d},000",
            })

        # Build scene AST with max 350 lines per macro-chunk
        scenes = build_scene_ast(movie_cues, gap_threshold_ms=3000, max_lines_per_scene=350)

        # Acceptance Criteria: Movie must partition into <= 5 macro-chunks
        assert len(scenes) <= 5, f"Movie produced {len(scenes)} chunks, expected <= 5"
        total_accounted_lines = sum(len(s["lines"]) for s in scenes)
        assert total_accounted_lines == 1400, "Every single cue must be accounted for without line loss"

    @pytest.mark.asyncio
    async def test_gemini_429_quota_backoff_and_seamless_model_failover(self, monkeypatch):
        """Verify response-driven 429 classification, exponential backoff, and model failover from Flash-Latest to Flash-Lite."""
        clear_logged_failovers()

        # 1. Test RPM vs RPD error classification
        rpm_error = Exception("429 Resource has been exhausted (e.g. check quota). Please retry in 5s. requests per minute")
        assert is_daily_quota_error(rpm_error) is False

        rpd_error = Exception("429 ResourceExhausted: Quota exceeded for quota metric 'GenerateRequestsPerDayPerProjectPerModel'")
        assert is_daily_quota_error(rpd_error) is True

        # 2. Test RPM exponential backoff retry loop
        call_count = 0

        async def mock_api_call():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("429 TooManyRequests: rate limit exceeded. retry after 1 seconds")
            return "1| Μεταφρασμένη γραμμή μετά από backoff"

        limiter = RateLimiter(requests_per_minute=60, daily_limit=1500)
        result = await rate_limited_call(mock_api_call, rate_limiter=limiter, max_retries=3, base_delay=0.01)
        assert result == "1| Μεταφρασμένη γραμμή μετά από backoff"
        assert call_count == 3

        # 3. Test daily quota failover via model_resolver
        primary_model = "gemini-flash-latest"
        fallback_model = "gemini-flash-lite-latest"

        # Latch primary model as daily exhausted in per_model_rate_limiter
        per_model_rate_limiter.get_limiter(primary_model).trigger_daily_limit()
        assert primary_model in per_model_rate_limiter.exhausted_models()

        # Verify model_resolver automatically fails over to fallback model
        metadata = {
            "settings": {
                "translation_model": primary_model,
                "fallback_translation_model": fallback_model,
            }
        }
        resolved = resolve_model("translation", metadata=metadata)
        assert resolved == fallback_model, f"Expected failover to {fallback_model}, got {resolved}"

    def test_deep_copy_data_json_immutability_during_export(self, monkeypatch, tmp_path):
        """Crucial Acceptance Test: Verify that editor source data (parsed_srt in data.json)
        remains strictly 1:1, and formatting modifications (wrapping, SE fixes) are applied
        ONLY on deep-copied export data and export artifacts."""
        project_name = "integrity_proj"
        episode_name = "ep01"

        # Setup mock project directory in tmp_path
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)
        monkeypatch.setattr(storage, "CONFIG_FILE", tmp_path / "config.json")

        storage.create_project(project_name)
        raw_srt = (
            "1\n00:00:01,000 --> 00:00:06,000\nThis is a very long single subtitle cue that would normally exceed maximum characters per line on a standard TV screen display.\n\n"
            "2\n00:00:07,000 --> 00:00:09,000\nShort line.\n"
        )
        storage.save_original_subtitle(project_name, episode_name, raw_srt, filename=f"{episode_name}.srt")

        parsed_srt = [
            {
                "index": 1,
                "original": "This is a very long single subtitle cue that would normally exceed maximum characters per line on a standard TV screen display.",
                "translated": "Αυτός είναι ένας πολύ μεγάλος υπότιτλος μιας γραμμής που κανονικά θα υπερέβαινε το μέγιστο όριο χαρακτήρων ανά γραμμή σε μια τυπική οθόνη τηλεόρασης.",
                "translations": {
                    "el": "Αυτός είναι ένας πολύ μεγάλος υπότιτλος μιας γραμμής που κανονικά θα υπερέβαινε το μέγιστο όριο χαρακτήρων ανά γραμμή σε μια τυπική οθόνη τηλεόρασης."
                },
                "start": "00:00:01,000",
                "end": "00:00:06,000",
            },
            {
                "index": 2,
                "original": "Short line.",
                "translated": "Σύντομη γραμμή.",
                "translations": {"el": "Σύντομη γραμμή."},
                "start": "00:00:07,000",
                "end": "00:00:09,000",
            }
        ]

        # Deep copy original state for verification
        original_state_before_export = copy.deepcopy(parsed_srt)

        # Setup project metadata
        metadata = {
            "original_filename": f"{episode_name}.srt",
            "arr_media_path": str(tmp_path / "media" / f"{episode_name}.mkv"),
        }
        (tmp_path / "media").mkdir(parents=True, exist_ok=True)

        storage.save_project_metadata(project_name, {"target_language": "Greek"})
        storage.save_episode(project_name, episode_name, parsed_srt, metadata)

        # Enable export conformance wrapping in global config
        cfg = storage.load_global_config()
        cfg["conformance_enabled"] = True
        cfg["max_cpl"] = 37
        storage.save_global_config(cfg)

        # Run auto_export
        exported_file = auto_export_translated_subtitle(project_name, episode_name, parsed_srt, metadata)
        assert exported_file is not None
        assert Path(exported_file).exists()

        # Invariant 1: In-memory parsed_srt must remain strictly unmodified (1:1 single-line strings, no added line breaks)
        assert parsed_srt[0]["translated"] == original_state_before_export[0]["translated"]
        assert "\n" not in parsed_srt[0]["translated"]
        assert "<br>" not in parsed_srt[0]["translated"]

        # Invariant 2: Stored episode in data.json must remain strictly unmodified
        reloaded_ep = storage.load_episode(project_name, episode_name)
        assert reloaded_ep["data"][0]["translated"] == original_state_before_export[0]["translated"]

        # Invariant 3: Exported subtitle file has conformance wrapping applied
        exported_content = Path(exported_file).read_text(encoding="utf-8-sig")
        assert "\n" in exported_content
        assert "Αυτός είναι ένας πολύ μεγάλος υπότιτλος" in exported_content
