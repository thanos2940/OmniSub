"""
Unit and regression tests for translation dual-text sanitization and purity enforcement.
Ensures contaminated lines like 'How are you -> Πως είσαι' or 'EN: ... EL: ...' are stripped
at every layer: parser, runner, corrector, and export reconstructor.
"""

import pytest
from utils.text_normalize import sanitize_translation_text
from utils.structured_output import parse_structured
from utils.llm_utils import parse_translations_from_text
from utils.source_clean import reconstruct_cleaned_srt
from adk_agents.translator_agent import build_translator_instruction, _spoken_greek_idioms_rule, _japanese_names_greek_rule


class TestSanitizeTranslationText:
    """Tests for sanitize_translation_text across all dual-format permutations."""

    def test_clean_input_unchanged(self):
        assert sanitize_translation_text("Πώς είσαι;") == "Πώς είσαι;"
        assert sanitize_translation_text("Καλημέρα κόσμε.") == "Καλημέρα κόσμε."
        assert sanitize_translation_text("") == ""
        assert sanitize_translation_text(None) == ""

    def test_arrow_delimiters(self):
        assert sanitize_translation_text("How are you -> Πως είσαι") == "Πως είσαι"
        assert sanitize_translation_text("How are you => Πως είσαι") == "Πως είσαι"
        assert sanitize_translation_text("How are you → Πως είσαι") == "Πως είσαι"
        assert sanitize_translation_text('"How are you" -> "Πως είσαι"') == "Πως είσαι"
        assert sanitize_translation_text("'How are you' => 'Πως είσαι'") == "Πως είσαι"

    def test_real_world_solo_leveling_cases(self):
        # Case 1: HTML italics on both sides
        s1 = "<i>An E-rank hunter volunteered to be a porter in an A-rank dungeon.</i> -> <i>Ένας κυνηγός βαθμίδας E προσφέρθηκε εθελοντικά να γίνει αχθοφόρος σε ένα μπουντρούμι βαθμίδας A.</i>"
        assert sanitize_translation_text(s1) == "<i>Ένας κυνηγός βαθμίδας E προσφέρθηκε εθελοντικά να γίνει αχθοφόρος σε ένα μπουντρούμι βαθμίδας A.</i>"

        # Case 2: ASS position tags on both sides
        s2 = r"{\an8}Anything besides this? -> {\an8}Κάτι άλλο εκτός από αυτό;"
        assert sanitize_translation_text(s2) == r"{\an8}Κάτι άλλο εκτός από αυτό;"

        # Case 3: Character name question
        s3 = "Hunter Cha? -> Κυνηγός Τσα;"
        assert sanitize_translation_text(s3) == "Κυνηγός Τσα;"

        # Case 4: Identical both sides
        s4 = "Λάθος στην ανίχνευση της μαγικής δύναμης; -> Λάθος στην ανίχνευση της μαγικής δύναμης;"
        assert sanitize_translation_text(s4) == "Λάθος στην ανίχνευση της μαγικής δύναμης;"

        # Case 5: Wrapped in italics across arrow
        s5 = "<i>How are you -> Πως είσαι</i>"
        assert sanitize_translation_text(s5) == "<i>Πως είσαι</i>"

        # Case 6: Wrapped in ASS position tag across arrow
        s6 = r"{\an8}Anything besides this? -> Κάτι άλλο εκτός από αυτό;"
        assert sanitize_translation_text(s6) == r"{\an8}Κάτι άλλο εκτός από αυτό;"

    def test_labeled_dual_text(self):
        # Single line EN: ... EL: ...
        assert sanitize_translation_text("EN: How are you EL: Πως είσαι") == "Πως είσαι"
        # Multi line
        assert sanitize_translation_text("EN: How are you\nEL: Πως είσαι") == "Πως είσαι"
        # With pipe
        assert sanitize_translation_text("English: How are you | Greek: Πως είσαι") == "Πως είσαι"
        # Bracketed labels
        assert sanitize_translation_text("[EN] How are you [EL] Πως είσαι") == "Πως είσαι"
        assert sanitize_translation_text("[EN] How are you [EL]: Πως είσαι") == "Πως είσαι"
        # Source / Target labels
        assert sanitize_translation_text("Source: How are you - Target: Πως είσαι") == "Πως είσαι"
        assert sanitize_translation_text("Original: How are you \n Translation: Πως είσαι") == "Πως είσαι"

    def test_single_target_prefixes(self):
        assert sanitize_translation_text("EL: Πως είσαι") == "Πως είσαι"
        assert sanitize_translation_text("Greek: Πως είσαι") == "Πως είσαι"
        assert sanitize_translation_text("Target: Πως είσαι") == "Πως είσαι"
        assert sanitize_translation_text("Translation: Πως είσαι") == "Πως είσαι"
        assert sanitize_translation_text("[EL] Πως είσαι") == "Πως είσαι"

    def test_dialogue_dashes_preserved(self):
        # Single line dialogue dash
        assert sanitize_translation_text("- How are you?") == "- How are you?"
        assert sanitize_translation_text("— Καλημέρα.") == "— Καλημέρα."
        # Dialogue dash after arrow
        assert sanitize_translation_text("- How are you? -> - Πως είσαι;") == "- Πως είσαι;"
        assert sanitize_translation_text("[EL] - Πως είσαι;") == "- Πως είσαι;"

    def test_multiline_support(self):
        # Arrows across multiple lines in a single cue
        raw = "How are you? -> Πώς είσαι;\nI am fine. -> Είμαι καλά."
        expected = "Πώς είσαι;\nΕίμαι καλά."
        assert sanitize_translation_text(raw) == expected

        # Multiline with dialogue dashes
        raw_dash = "- How are you? -> - Πώς είσαι;\n- Fine! -> - Καλά!"
        expected_dash = "- Πώς είσαι;\n- Καλά!"
        assert sanitize_translation_text(raw_dash) == expected_dash

    def test_vocabulary_preservation_no_false_positives(self):
        # Sentences starting with label words must NOT have their first words stripped
        assert sanitize_translation_text("Target acquired.") == "Target acquired."
        assert sanitize_translation_text("Greek cuisine is wonderful.") == "Greek cuisine is wonderful."
        assert sanitize_translation_text("Translation memory is active.") == "Translation memory is active."

    def test_outer_quotes_and_tags(self):
        # Outer quotes wrapping the whole arrow expression
        assert sanitize_translation_text('"How are you -> Πως είσαι"') == "Πως είσαι"
        assert sanitize_translation_text("'How are you -> Πως είσαι'") == "Πως είσαι"
        assert sanitize_translation_text("<b>How are you -> Πως είσαι</b>") == "<b>Πως είσαι</b>"

    def test_source_text_interaction(self):
        # Arrow that was intentionally in the source text must not be stripped
        assert sanitize_translation_text("MP: 100 -> 200", source_text="MP: 100 -> 200") == "MP: 100 -> 200"
        # Source text prefix stripped even without arrow
        assert sanitize_translation_text("Hunter Cha? - Κυνηγός Τσα;", source_text="Hunter Cha?") == "Κυνηγός Τσα;"

    def test_preserves_srt_timecodes(self):
        tc = "00:00:01,000 --> 00:00:04,000"
        assert sanitize_translation_text(tc) == tc

    def test_target_language_custom_labels(self):
        assert sanitize_translation_text("English: Hello | Spanish: Hola", target_lang="Spanish", target_lang_code="es") == "Hola"
        assert sanitize_translation_text("EN: Hello ES: Hola", target_lang="Spanish", target_lang_code="es") == "Hola"


class TestParserSanitization:
    """Tests that parsers automatically sanitize translations from raw model output."""

    def test_parse_structured_json_sanitizes(self):
        json_output = '{"lines": [{"i": 1, "t": "How are you -> Πως είσαι"}, {"i": 2, "t": "Fine, thanks."}]}'
        parsed = parse_structured(json_output, expected_count=2)
        assert len(parsed) == 2
        assert parsed[0]["text"] == "Πως είσαι"
        assert parsed[1]["text"] == "Fine, thanks."

    def test_parse_translations_from_text_pipe_sanitizes(self):
        text_output = "1| How are you -> Πως είσαι\n2| I am fine -> Είμαι καλά"
        parsed = parse_translations_from_text(text_output)
        assert len(parsed) == 2
        assert parsed[0]["text"] == "Πως είσαι"
        assert parsed[1]["text"] == "Είμαι καλά"

    def test_parse_translations_from_text_xml_sanitizes(self):
        text_output = '<line index="1">How are you -> Πως είσαι</line>\n<line index="2">Good</line>'
        parsed = parse_translations_from_text(text_output)
        assert len(parsed) == 2
        assert parsed[0]["text"] == "Πως είσαι"

    def test_parse_translations_from_text_colon_sanitizes(self):
        text_output = "1: How are you -> Πως είσαι\n2: All good"
        parsed = parse_translations_from_text(text_output)
        assert len(parsed) == 2
        assert parsed[0]["text"] == "Πως είσαι"


class TestReconstructCleanedSrtSanitization:
    """Tests that reconstruct_cleaned_srt strips dual translations at export/render level."""

    def test_reconstruct_cleaned_srt_cleans_dirty_data(self, tmp_path, monkeypatch):
        # Setup temporary project storage
        monkeypatch.setattr("utils.storage.PROJECTS_DIR", tmp_path)
        proj_dir = tmp_path / "TestProj" / "episodes" / "E01"
        proj_dir.mkdir(parents=True)

        raw_srt = "1\n00:00:01,000 --> 00:00:03,000\nHow are you?\n\n"
        (proj_dir / "original.srt").write_text(raw_srt, encoding="utf-8")
        meta = {
            "clean_to_orig_map": {"0": [0]},
            "original_filename": "original.srt",
        }
        import json
        (proj_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

        # Dirty cleaned_data containing contaminated translation
        cleaned_data = [{
            "id": "1",
            "timecode": "00:00:01,000 --> 00:00:03,000",
            "original": "How are you?",
            "translated": "How are you? -> Πώς είσαι;",
            "translations": {"el": "How are you? -> Πώς είσαι;"},
        }]

        out_srt = reconstruct_cleaned_srt("TestProj", "E01", cleaned_data, target_lang_code="el")
        cue_text_lines = [l for l in out_srt.splitlines() if not l.isdigit() and "-->" not in l and l.strip()]
        assert len(cue_text_lines) == 1
        assert cue_text_lines[0] == "Πώς είσαι;"
        assert "How are you?" not in cue_text_lines[0]
        assert "->" not in cue_text_lines[0]


class TestPromptInstructionsPurity:
    """Tests that prompts explicitly instruct against dual text and do not leak arrows."""

    def test_instructions_contain_purity_rule(self):
        for structured in (True, False):
            instr = build_translator_instruction("Greek", {"terms": []}, structured=structured)
            assert "TRANSLATION PURITY" in instr
            assert "NEVER repeat or include the original source text" in instr

    def test_idiom_rule_no_arrows(self):
        rule = _spoken_greek_idioms_rule("Greek")
        assert "->" not in rule
        assert "fair enough" in rule
        assert "πάσο" in rule

    def test_japanese_names_rule_no_arrows(self):
        rule = _japanese_names_greek_rule("Greek")
        assert "->" not in rule
        assert "Tanjiro" in rule
        assert "Τάντζιρο" in rule
