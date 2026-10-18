"""
Spherical Context Scaffolding & Prompt Assembly Stress Test Suite

Comprehensive empirical stress tests covering:
1. compile_bible and SHA-256 fingerprint caching in prompt_assembler.py & cache_manager.py
2. enforce_glossary in glossary_enforcer.py with multi-token terms, casing, and boundary edge cases
3. qc_funnel.py multi-stage validation with corrupted and valid subtitle lines
"""

import pytest
from unittest.mock import patch, MagicMock

# Module imports
from services.prompt_assembler import (
    compile_bible,
    assemble_episode_request,
    assemble_scene_request,
    Bible,
    TranslationRequest,
)
from utils.cache_manager import compute_fingerprint, get_or_create_cache, invalidate_cache
from utils.glossary_enforcer import enforce_glossary
from utils.qc_funnel import (
    validate_lines,
    build_repair_prompt,
    build_repair_prompt_items,
    run_repair_pass,
    _latin_ratio,
    _ends_terminal,
    _looks_truncated,
)
from utils.structured_output import (
    SceneTranslationResponse,
    EpisodeTranslationResponse,
    VerifiedEpisodeTranslationResponse,
    parse_structured,
    use_source_echo,
    use_structured_output,
)
from utils.text_normalize import contains_term, tokenize_and_normalize, stem_greek


# ==============================================================================
# SECTION 1: compile_bible & SHA-256 Fingerprint Caching Stress Tests
# ==============================================================================

class TestCompileBibleAndFingerprint:

    def test_fingerprint_deterministic_with_shuffled_terms(self):
        """Fingerprint must be invariant to the order of terms in glossary['terms']."""
        terms_order_1 = [
            {"term": "Alpha", "translation": "Άλφα", "type": "person"},
            {"term": "Beta", "translation": "Βήτα", "type": "location"},
            {"term": "Gamma", "translation": "Γάμμα", "type": "object"},
        ]
        terms_order_2 = [
            {"term": "Gamma", "translation": "Γάμμα", "type": "object"},
            {"term": "Alpha", "translation": "Άλφα", "type": "person"},
            {"term": "Beta", "translation": "Βήτα", "type": "location"},
        ]
        
        fp1 = compute_fingerprint({"terms": terms_order_1}, "Context guide A")
        fp2 = compute_fingerprint({"terms": terms_order_2}, "Context guide A")
        assert fp1 == fp2, f"Fingerprints must match for permuted terms: {fp1} != {fp2}"

    def test_fingerprint_changes_on_term_modification(self):
        """Fingerprint must change when a term's translation, type, or flag changes."""
        base_terms = [{"term": "Mjolnir", "translation": "Μγιόλνιρ", "type": "object"}]
        mod_terms = [{"term": "Mjolnir", "translation": "Μγιόλνιρ (Σφυρί)", "type": "object"}]
        
        fp_base = compute_fingerprint({"terms": base_terms}, "Marvel context")
        fp_mod = compute_fingerprint({"terms": mod_terms}, "Marvel context")
        assert fp_base != fp_mod, "Fingerprint must change when translation is updated"

    def test_fingerprint_changes_on_context_guide_update(self):
        """Fingerprint must change when context guide text changes."""
        terms = [{"term": "Frieren", "translation": "Φρίρεν", "type": "person"}]
        fp1 = compute_fingerprint({"terms": terms}, "Guide v1")
        fp2 = compute_fingerprint({"terms": terms}, "Guide v2")
        assert fp1 != fp2, "Fingerprint must change when context guide changes"

    def test_fingerprint_handles_empty_or_missing_fields(self):
        """Fingerprint should handle empty glossaries, missing terms key, or empty context guides."""
        fp_empty = compute_fingerprint({}, "")
        assert isinstance(fp_empty, str) and len(fp_empty) == 16
        
        fp_none_terms = compute_fingerprint({"terms": []}, "")
        assert fp_empty == fp_none_terms

    def test_compile_bible_structure_and_fingerprint_consistency(self):
        """compile_bible should correctly resolve metadata, instruction, and fingerprint."""
        meta = {
            "target_language": "Greek",
            "glossary": {
                "terms": [
                    {"term": "Saber", "translation": "Σέιμπερ", "type": "person", "gender": "feminine", "case_sensitive": True}
                ]
            },
            "context_guide": "Fate Stay Night universe context"
        }
        
        with patch("utils.storage.load_resolved_project_metadata", return_value=meta):
            bible = compile_bible(
                project_name="Fate",
                project_meta=meta,
                model_name="gemini-2.5-flash",
                target_language="Greek",
            )
            
            assert bible.project_name == "Fate"
            assert bible.target_language == "Greek"
            assert bible.structured is True
            assert bible.fingerprint == compute_fingerprint(meta["glossary"], meta["context_guide"])
            assert "Σέιμπερ" in bible.system_instruction
            assert "Fate Stay Night universe context" in bible.system_instruction

    def test_fingerprint_staleness_detection_in_cache_manager(self):
        """get_or_create_cache must invalidate stale cache when glossary or guide changes."""
        metadata = {
            "show_name": "TestShow",
            "glossary": {"terms": [{"term": "Hero", "translation": "Ήρωας"}]},
            "context_guide": "Guide v1",
            "context_cache_name": "cachedContents/old_cache_123",
            "context_cache_fingerprint": "old_fingerprint_value",
            "context_cache_expiry": "2099-01-01T00:00:00Z",
        }
        
        with patch("utils.cache_manager._get_client") as mock_client_getter, \
             patch("utils.rate_limiter.per_model_rate_limiter.get_limiter") as mock_limiter_getter:
            
            mock_client = MagicMock()
            mock_cache_obj = MagicMock()
            mock_cache_obj.name = "cachedContents/new_cache_456"
            mock_cache_obj.expire_time = None
            mock_client.caches.create.return_value = mock_cache_obj
            mock_client_getter.return_value = mock_client
            
            mock_limiter = MagicMock()
            mock_limiter_getter.return_value = mock_limiter
            
            cache_name, updated_meta = get_or_create_cache(
                metadata=metadata,
                model_name="gemini-2.5-flash",
                system_instruction="Instruction v2",
            )
            
            # Old cache should have been deleted/invalidated and new cache returned
            assert cache_name == "cachedContents/new_cache_456"
            assert updated_meta["context_cache_name"] == "cachedContents/new_cache_456"
            assert updated_meta["context_cache_fingerprint"] == compute_fingerprint(metadata["glossary"], metadata["context_guide"])

    def test_compile_bible_target_language_override(self):
        """compile_bible must respect explicit target_language parameter over metadata."""
        meta = {
            "target_language": "German",
            "glossary": {"terms": []},
            "context_guide": ""
        }
        with patch("utils.storage.load_resolved_project_metadata", return_value=meta):
            bible = compile_bible("TestProject", meta, "gemini-2.5-flash", target_language="Greek")
            assert bible.target_language == "Greek"
            assert "Greek" in bible.system_instruction

    def test_compile_bible_handles_none_metadata_safely(self):
        """compile_bible should handle empty/None project_meta gracefully without crashing."""
        with patch("utils.storage.load_resolved_project_metadata", return_value=None):
            bible = compile_bible("", {}, "gemini-2.5-flash", target_language="Greek")
            assert bible.target_language == "Greek"
            assert isinstance(bible.fingerprint, str)
            assert len(bible.fingerprint) == 16


# ==============================================================================
# SECTION 2: enforce_glossary Stress Tests (Multi-token, Casing, Boundaries)
# ==============================================================================

class TestEnforceGlossary:

    @pytest.fixture(autouse=True)
    def mock_global_config(self):
        with patch("utils.storage.load_global_config", return_value={
            "morphology_normalization": True,
            "morphology_stemming": True
        }):
            yield

    def test_single_token_untranslated_replacement(self):
        """Single-token proper noun leaking into translation must be replaced."""
        glossary = {
            "terms": [
                {"term": "Tohsaka", "translation": "Τοχσάκα", "type": "person", "case_sensitive": True}
            ]
        }
        src = ["Tohsaka is ready."]
        trans = ["Η Tohsaka είναι έτοιμη."]
        
        corrected, count = enforce_glossary(trans, glossary, src, target_lang="Greek")
        assert count == 1
        assert corrected[0] == "Η Τοχσάκα είναι έτοιμη."

    def test_multi_token_term_replacement(self):
        """Multi-token term leaking verbatim into translation must be replaced."""
        glossary = {
            "terms": [
                {"term": "Holy Grail", "translation": "Άγιο Δισκοπότηρο", "type": "location", "case_sensitive": True}
            ]
        }
        src = ["We must protect the Holy Grail at all costs."]
        trans = ["Πρέπει να προστατεύσουμε το Holy Grail πάση θυσία."]
        
        corrected, count = enforce_glossary(trans, glossary, src, target_lang="Greek")
        assert count == 1
        assert corrected[0] == "Πρέπει να προστατεύσουμε το Άγιο Δισκοπότηρο πάση θυσία."

    def test_inflected_target_morphology_no_false_correction(self):
        """When the target line already contains a valid inflected form of the term, no correction should occur."""
        glossary = {
            "terms": [
                {"term": "Captain", "translation": "Καπετάνιος", "type": "person"}
            ]
        }
        # Vocative inflection in Greek: "καπετάνιε" (stem: καπεταν)
        src = ["Greetings, Captain."]
        trans = ["Χαίρετε, καπετάνιε."]
        
        corrected, count = enforce_glossary(trans, glossary, src, target_lang="Greek")
        assert count == 0
        assert corrected[0] == "Χαίρετε, καπετάνιε."

    def test_case_insensitive_flag_handling(self):
        """case_sensitive=False should match and replace lowercase/uppercase variants."""
        glossary = {
            "terms": [
                {"term": "castle", "translation": "κάστρο", "type": "location", "case_sensitive": False}
            ]
        }
        src = ["Enter the Castle now."]
        trans = ["Μπείτε στο Castle τώρα."]
        
        corrected, count = enforce_glossary(trans, glossary, src, target_lang="Greek")
        assert count == 1
        assert corrected[0] == "Μπείτε στο κάστρο τώρα."

    def test_keep_original_flag_bypasses_enforcement(self):
        """Terms with keep_original=True should never be modified or replaced."""
        glossary = {
            "terms": [
                {"term": "Nerv", "translation": "Nerv", "type": "organization", "keep_original": True}
            ]
        }
        src = ["Headquarters of Nerv."]
        trans = ["Το αρχηγείο της Nerv."]
        
        corrected, count = enforce_glossary(trans, glossary, src, target_lang="Greek")
        assert count == 0
        assert corrected[0] == "Το αρχηγείο της Nerv."

    def test_word_boundary_and_substring_safety(self):
        """Short names/terms should not accidentally corrupt longer words containing the substring."""
        glossary = {
            "terms": [
                {"term": "Art", "translation": "Αρτ", "type": "person", "case_sensitive": True}
            ]
        }
        # "Arthur" or "Part" should not trigger violation if "Art" is absent from source as standalone
        src = ["Arthur is taking part in the tournament."]
        trans = ["Ο Arthur παίρνει μέρος στο τουρνουά."]
        
        corrected, count = enforce_glossary(trans, glossary, src, target_lang="Greek")
        # Arthur is not "Art" -> should NOT be replaced by "Αρτhur"
        assert count == 0
        assert "Αρτhur" not in corrected[0]

    def test_empty_and_mismatched_lines(self):
        """enforce_glossary must safely handle empty lines and mismatched line counts."""
        glossary = {"terms": [{"term": "Rin", "translation": "Ριν", "type": "person"}]}
        
        # Empty lines in input
        src = ["", "Rin arrived.", ""]
        trans = ["", "Η Rin έφτασε.", ""]
        corrected, count = enforce_glossary(trans, glossary, src, target_lang="Greek")
        assert count == 1
        assert corrected == ["", "Η Ριν έφτασε.", ""]


# ==============================================================================
# SECTION 3: qc_funnel Multi-Stage Validation Stress Tests
# ==============================================================================

class TestQCFunnelValidation:

    def test_valid_subtitle_lines_generate_zero_tickets(self):
        """Clean, well-formed subtitle lines must produce 0 QC repair tickets."""
        lines = [
            {"original": "Good morning, friend.", "translated": "Καλημέρα, φίλε μου.", "translations": {"el": "Καλημέρα, φίλε μου."}},
            {"original": "<i>Where</i> are you going?", "translated": "<i>Πού</i> πας;", "translations": {"el": "<i>Πού</i> πας;"}},
            {"original": "Chapter 4: The Beginning.", "translated": "Κεφάλαιο 4: Η Αρχή.", "translations": {"el": "Κεφάλαιο 4: Η Αρχή."}},
        ]
        tickets = validate_lines(lines, target_lang_code="el")
        assert tickets == []

    def test_corrupted_inline_tags_detection(self):
        """Lines with dropped, added, or mismatched formatting tags must be ticketed."""
        lines = [
            {"original": "<b>Warning:</b> danger ahead.", "translated": "Προσοχή: κίνδυνος μπροστά.", "translations": {"el": "Προσοχή: κίνδυνος μπροστά."}}, # dropped <b>
            {"original": "{\\an8}Top subtitle", "translated": "Πάνω υπότιτλος", "translations": {"el": "Πάνω υπότιτλος"}}, # dropped ASS tag
            {"original": "Normal text", "translated": "<i>Πλάγιο κείμενο</i>", "translations": {"el": "<i>Πλάγιο κείμενο</i>"}}, # added <i>
        ]
        tickets = validate_lines(lines, target_lang_code="el")
        assert len(tickets) == 3
        for t in tickets:
            assert any("inline formatting tags" in issue for issue in t["issues"])

    def test_untranslated_latin_detection_in_non_latin_targets(self):
        """Lines that leak Latin text into non-Latin targets (Greek, Russian, etc.) must be ticketed."""
        lines = [
            {"original": "This entire sentence was left in English.", "translated": "This entire sentence was left in English.", "translations": {"el": "This entire sentence was left in English."}},
            {"original": "He said hello and walked away.", "translated": "He said hello and έφυγε.", "translations": {"el": "He said hello and έφυγε."}}, # mixed, majority Latin
        ]
        tickets = validate_lines(lines, target_lang_code="el")
        assert len(tickets) == 2
        for t in tickets:
            assert any("untranslated" in issue for issue in t["issues"])

    def test_untranslated_detection_skipped_for_latin_target_languages(self):
        """Latin targets (e.g. Dutch, German, French) should not flag Latin text as untranslated script violation."""
        lines = [
            {"original": "Hello my friend", "translated": "Hallo mein Freund", "translations": {"de": "Hallo mein Freund"}},
            {"original": "Good night", "translated": "Goedenacht", "translations": {"nl": "Goedenacht"}},
        ]
        tickets_de = validate_lines(lines[:1], target_lang_code="de")
        tickets_nl = validate_lines(lines[1:], target_lang_code="nl")
        assert tickets_de == []
        assert tickets_nl == []

    def test_truncated_long_lines_detection(self):
        """Long source sentences (>60 chars) translated to very short, non-terminal fragments must be ticketed."""
        long_source = "Because of the tremendous power hidden within the artifact, we cannot allow anyone to approach it."
        truncated_trans = "Λόγω της τεράστιας δύναμης,"  # ends with comma, far < 0.6 length
        
        lines = [
            {"original": long_source, "translated": truncated_trans, "translations": {"el": truncated_trans}}
        ]
        tickets = validate_lines(lines, target_lang_code="el")
        assert len(tickets) == 1
        assert any("truncated" in issue for issue in tickets[0]["issues"])

    def test_foreign_script_and_mixed_alphabet_detection(self):
        """Foreign alphabet characters and spliced letters inside target words must be ticketed."""
        lines = [
            # Hebrew he spliced in
            {"original": "Commander!", "translated": "Ταγματάρχη!", "translations": {"el": "Ταγματάρχη!"}},
            # Latin spliced inside Greek word
            {"original": "Listen to me.", "translated": "Ακούsheτε με.", "translations": {"el": "Ακούsheτε με."}},
        ]
        tickets = validate_lines(lines, target_lang_code="el")
        assert len(tickets) >= 1
        assert any("mixes alphabets" in issue or "writing system" in issue for t in tickets for issue in t["issues"])

    def test_build_repair_prompt_structure(self):
        """build_repair_prompt must format numbered source, current text, and issues cleanly."""
        lines = [
            {"original": "<i>Run!</i>", "translated": "Τρέξε!", "translations": {"el": "Τρέξε!"}}
        ]
        tickets = [{"index": 0, "issues": ["inline formatting tags were changed"]}]
        prompt = build_repair_prompt(lines, tickets, "el", "Greek")
        
        assert "1| EN: <i>Run!</i>" in prompt
        assert "CURRENT: Τρέξε!" in prompt
        assert "ISSUES: inline formatting tags were changed" in prompt
        assert "Fix the following Greek subtitle lines" in prompt

    @pytest.mark.asyncio
    async def test_run_repair_pass_applies_batched_fixes(self):
        """run_repair_pass must send exactly one batched request and update lines accurately."""
        lines = [
            {"original": "<b>Stop</b>", "translated": "Στοπ", "translations": {"el": "Στοπ"}},
            {"original": "Come here", "translated": "Come here", "translations": {"el": "Come here"}},
        ]
        tickets = [
            {"index": 0, "issues": ["inline formatting tags were changed"]},
            {"index": 1, "issues": ["line appears untranslated"]},
        ]
        
        mock_response = "1| <b>Στοπ</b>\n2| Έλα εδώ"
        
        class MockLimiter:
            async def acquire(self):
                pass
        
        with patch("adk_agents.llm_factory.generate", return_value=mock_response) as mock_gen:
            fixed_count = await run_repair_pass(
                parsed_srt=lines,
                tickets=tickets,
                target_lang_code="el",
                target_lang_name="Greek",
                primary_code="el",
                model_name="gemini-2.5-flash",
                rate_limiter=MockLimiter(),
            )
            
            assert mock_gen.call_count == 1
            assert fixed_count == 2
            assert lines[0]["translations"]["el"] == "<b>Στοπ</b>"
            assert lines[0]["translated"] == "<b>Στοπ</b>"
            assert lines[1]["translations"]["el"] == "Έλα εδώ"
            assert lines[1]["translated"] == "Έλα εδώ"
            assert "needs_review" not in lines[0]
            assert "needs_review" not in lines[1]
