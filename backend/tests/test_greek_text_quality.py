"""
Greek output quality: the Japanese-name accent rule stays inside Japanese works, ordinary Greek is never
rewritten, Greek punctuation is fixed, and the style rules say what they mean for every language.
"""
import pytest

from utils.text_normalize import (
    fix_japanese_name_greek_tonos,
    greek_question_marks,
    looks_like_romaji,
    normalize_japanese_term,
    sanitize_translation_text,
    shift_greek_accent_to_first_syllable,
)
from utils.translation_rules import build_style_guide, _is_greek
from utils.work_origin import is_japanese_work, original_language


ORDINARY_GREEK = [
    "Περίμενε!", "Μαρία, έλα εδώ.", "Η Σοφία έφυγε.", "Πάμε στο Λονδίνο.", "Ζει στον Καναδά.",
    "Πήγε στο Μεξικό.", "Αθήνα", "Τορόντο", "Σικάγο", "Περίμενε λίγο, Κατερίνα.", "Νατάσα", "Βερολίνο",
    "Καλημέρα!", "Είναι σαράντα χρονών.", "Σαράντα μέρες.",
]


class TestLinesAreSafe:
    @pytest.mark.parametrize("line", ORDINARY_GREEK)
    def test_a_line_of_ordinary_greek_is_never_rewritten(self, line):
        assert fix_japanese_name_greek_tonos(line) == line

    def test_known_japanese_names_are_still_fixed_in_a_line(self):
        assert fix_japanese_name_greek_tonos("Ο Ταντζίρο και η Νεζουκο.") == "Ο Τάντζιρο και η Νέζουκο."

    def test_the_guess_is_opt_in(self):
        assert fix_japanese_name_greek_tonos("Κιμέτσου", guess=True) != "Κιμέτσου"
        assert fix_japanese_name_greek_tonos("Κιμέτσου") == "Κιμέτσου"

    def test_a_monosyllable_never_takes_an_accent(self):
        assert shift_greek_accent_to_first_syllable("ζει") == "ζει"
        assert fix_japanese_name_greek_tonos("Ζει στον Καναδά.", guess=True).startswith("Ζει ")

    def test_kyo_is_one_syllable(self):
        assert shift_greek_accent_to_first_syllable("Κιοτο") == "Κιότο"
        assert shift_greek_accent_to_first_syllable("Κιοτζούρο") == "Κιότζουρο"


class TestGlossaryTerms:
    @pytest.mark.parametrize("source,translation", [
        ("London", "Λονδίνο"), ("Maria", "Μαρία"), ("Canada", "Καναδάς"), ("Toronto", "Τορόντο"),
        ("Katerina", "Κατερίνα"), ("Station Road", "Οδός Σταθμού"),
    ])
    def test_outside_a_japanese_work_a_term_keeps_its_accent(self, source, translation):
        assert normalize_japanese_term(source, translation, japanese_work=False) == translation

    @pytest.mark.parametrize("source,translation,expected", [
        ("Tanjiro", "Ταντζίρο", "Τάντζιρο"),
        ("Hashira", "Χασίρα", "Χάσιρα"),
        ("Nichirin Blade", "Σπαθί Νιτσιρίν", "Σπαθί Νίτσιριν"),
        ("Mount Sagiri", "Όρος Σαγκίρι", "Όρος Σάγκιρι"),
        ("Kyojuro Rengoku", "Κιοτζούρο Ρενγκόκου", "Κιότζουρο Ρένγκοκου"),
    ])
    def test_in_a_japanese_work_romaji_names_get_the_first_syllable(self, source, translation, expected):
        assert normalize_japanese_term(source, translation, japanese_work=True) == expected

    @pytest.mark.parametrize("source,translation", [
        ("Titan", "Τιτάνας"), ("Titans", "Τιτάνες"), ("Demon Slayer Corps", "Σώμα Εξολοθρευτών Δαιμόνων"),
        ("Hero", "Ήρωας"), ("Total Concentration Breathing", "Αναπνοή Απόλυτης Συγκέντρωσης"),
    ])
    def test_greek_words_are_not_transliterations(self, source, translation):
        assert normalize_japanese_term(source, translation, japanese_work=True) == translation

    def test_romaji(self):
        assert looks_like_romaji("Tanjiro") and looks_like_romaji("Kamado Nezuko")
        assert looks_like_romaji("Ken'ichi") and looks_like_romaji("Kyōjurō") and looks_like_romaji("Hattori")
        assert not looks_like_romaji("London") and not looks_like_romaji("Daniel")
        assert not looks_like_romaji("Demon Slayer") and not looks_like_romaji("")

    def test_a_harvested_term_in_a_western_show_keeps_its_accent(self, tmp_path):
        from services.agent_task_service import AgentTaskService
        terms = [{"term": "London", "translation": "Λονδίνο", "type": "location"},
                 {"term": "Maria", "translation": "Μαρία", "type": "character"}]
        out = AgentTaskService()._normalize_harvested_terms(terms, target_code="el")
        assert [t["translation"] for t in out] == ["Λονδίνο", "Μαρία"]


class TestWorkOrigin:
    def test_anime_and_japanese(self):
        assert is_japanese_work({"series_type": "anime"})
        assert is_japanese_work({"arr_original_language": "Japanese"})
        assert is_japanese_work({"original_language": {"id": 8, "name": "Japanese"}})
        assert is_japanese_work({"arr_genres": ["Action", "Anime"]})
        assert is_japanese_work({"is_anime": True})
        assert not is_japanese_work({"series_type": "standard", "arr_genres": ["Drama"]})
        assert not is_japanese_work({})
        assert original_language({"arr_original_language": "Korean"}) == "Korean"

    def test_sync_stores_the_original_language(self):
        from integrations.media_sync_engine import work_facts
        assert work_facts({"originalLanguage": {"id": 8, "name": "Japanese"}})["arr_original_language"] == "Japanese"
        assert "arr_original_language" not in work_facts({"originalLanguage": {"id": 0, "name": "Unknown"}})

    def test_the_brief_names_the_original_language_and_anime(self):
        from services import agent_prompts as ap
        from services.project_bootstrap import project_brief
        brief = project_brief({"series_type": "anime", "arr_original_language": "Japanese"})
        prompt = ap.context_prompt(show="X", target_name="Greek", brief=brief, lines=[], want_guide=True,
                                   want_terms=False)
        assert "Original language: Japanese" in prompt
        assert "for an anime series" in prompt


class TestPunctuation:
    def test_greek_question_mark(self):
        assert greek_question_marks("Τι κάνεις?") == "Τι κάνεις;"
        assert greek_question_marks("{\\an8}Πού πας?") == "{\\an8}Πού πας;"
        assert greek_question_marks("<i>Σοβαρά?!</i>") == "<i>Σοβαρά;!</i>"
        assert greek_question_marks("Είσαι OK?") == "Είσαι OK;"

    def test_lines_that_are_mostly_latin_are_left_alone(self):
        assert greek_question_marks("What?") == "What?"
        assert greek_question_marks("Ελ: Are you out of your mind?") == "Ελ: Are you out of your mind?"

    def test_the_sanitizer_applies_it_for_greek_only(self):
        assert sanitize_translation_text("Τι κάνεις?", source_text="What?", target_lang_code="el") == "Τι κάνεις;"
        assert sanitize_translation_text("¿Qué?", source_text="What?", target_lang_code="es") == "¿Qué?"


class TestStyleRules:
    def test_greek_variants(self):
        for name in ("Greek", "el", "EL", "el-GR", "Ελληνικά", "ελληνικα", "Greek (Modern)"):
            assert _is_greek(name), name
        assert not _is_greek("German") and not _is_greek("")

    def test_no_wrong_idioms(self):
        guide = build_style_guide("Greek")
        assert "κλάιν" not in guide           # not a Greek word
        assert "->" not in guide              # answers must not contain arrows; the rules do not show any

    def test_japanese_rule_only_for_japanese_works(self):
        assert "JAPANESE NAMES" in build_style_guide("Greek", japanese_work=True)
        assert "JAPANESE NAMES" not in build_style_guide("Greek", japanese_work=False)

    def test_other_languages_get_no_greek_text(self):
        guide = build_style_guide("Spanish", japanese_work=False)
        assert "Greek" not in guide and "Modern Greek" not in guide
        assert "Spanish text often runs longer than English" in guide
        assert "SPEAKER AND LISTENER GENDER" in guide     # Spanish adjectives agree with the speaker

    def test_gender_rule_is_only_for_gendered_languages(self):
        assert "SPEAKER AND LISTENER GENDER" in build_style_guide("Greek")
        assert "SPEAKER AND LISTENER GENDER" not in build_style_guide("Finnish")

    def test_greek_punctuation_rule(self):
        assert "GREEK PUNCTUATION" in build_style_guide("Greek")
        assert "GREEK PUNCTUATION" not in build_style_guide("French")


class TestCharacters:
    def _manager(self, monkeypatch, profiles):
        from utils.character_profiles import CharacterProfileManager
        mgr = CharacterProfileManager("P")
        monkeypatch.setattr(mgr, "load_all_resolved", lambda: profiles)
        return mgr

    def test_names_match_whole_words_only(self, monkeypatch):
        from utils.character_profiles import CharacterProfile
        mgr = self._manager(monkeypatch, {"Ren": CharacterProfile(name="Ren", gender="masculine")})
        assert mgr.get_profiles_for_chunk(["The current is strong."], fallback_to_main_cast=False) == []
        assert [p.name for p in mgr.get_profiles_for_chunk(["Ren, wait!"])] == ["Ren"]

    def test_the_main_cast_fallback_is_not_presented_as_the_speakers(self, monkeypatch):
        from utils.character_profiles import CharacterProfile
        mgr = self._manager(monkeypatch, {"Mira": CharacterProfile(name="Mira", gender="feminine")})
        fallback = mgr.get_profiles_for_chunk(["Nobody is named here."])
        block = mgr.build_profile_context(fallback)
        assert "none of them is named in these lines" in block
        named = mgr.build_profile_context(mgr.get_profiles_for_chunk(["Mira, come here."]))
        assert named.startswith("Characters named in these lines:")


class TestSpeakers:
    def _cue(self, text, name, start):
        return {"original": text, "timecode": "", "_ass": {"kind": "dialogue", "translatable": True, "name": name,
                                                           "start_ms": start, "end_ms": start + 1500, "style": "Default"}}

    def test_ass_names_reach_the_prompt_without_touching_the_lines(self):
        from services import agent_prompts as ap
        from services import agent_workspace as aw
        cues = [self._cue("I'm ready.", "Tanjiro", 0), self._cue("Me too.", "Tanjiro", 2000),
                self._cue("Are you sure?", "Nezuko", 4000), self._cue("Big sign text", "Signs", 6000),
                self._cue("Who said that?", "", 8000)]
        units, _ = aw.build_units(cues, "el")
        assert [u.speaker for u in units] == ["Tanjiro", "Tanjiro", "Nezuko", "", ""]
        assert ap.speakers_block(units) == "1–2 Tanjiro; 3 Nezuko"
        prompt = ap.translate_prompt("STATIC", title="T", story="", characters="", glossary_lines=[], glossary_note="",
                                     earlier=[], units=units, want_summary=False, want_guide=False)
        assert "## Who speaks" in prompt and "1–2 Tanjiro; 3 Nezuko" in prompt
        assert "1| I'm ready." in prompt                    # the numbered line itself carries no name

    def test_old_plans_without_speakers_still_load(self):
        from services import agent_workspace as aw
        units = aw.units_from_plan({"units": [{"id": 1, "kind": "dialogue", "text": "Hi", "cues": [0]}]})
        assert units[0].speaker == ""

    def test_no_section_without_names(self):
        from services import agent_prompts as ap
        from services import agent_workspace as aw
        units, _ = aw.build_units([{"original": "Hello", "timecode": "00:00:01,000 --> 00:00:02,000"}], "el")
        assert ap.speakers_block(units) == ""


class TestAccentRepair:
    """utils/accent_repair.py: undoing what the old guess wrote, from the project's own evidence only."""

    LINES = [
        "Θα σε περιμένω, περίμενε λίγο ακόμα.", "Πες μου, περίμενε εδώ μαζί μου.",       # evidence: "περίμενε"
        "Το λες σοβαρά τώρα;", "Μιλάω σοβαρά, πίστεψέ με.",                               # evidence: "σοβαρά"
        "Είναι σαράντα χρονών.",
    ]

    def test_a_word_is_fixed_from_lower_case_evidence(self):
        from utils.accent_repair import collect_evidence, repair_line
        ev = collect_evidence(self.LINES)
        assert repair_line("Πέριμενε!", ev)[0] == "Περίμενε!"
        assert repair_line("Σόβαρα; Δεν το πιστεύω.", ev)[0] == "Σοβαρά; Δεν το πιστεύω."
        assert repair_line("Είναι σάραντα χρονών.", ev)[0] == "Είναι σαράντα χρονών."

    def test_names_without_evidence_are_listed_not_changed(self):
        from utils.accent_repair import collect_evidence, repair_line
        new, changes, left = repair_line("Η Μάρια ήρθε στο Λόνδινο.", collect_evidence(self.LINES))
        assert new == "Η Μάρια ήρθε στο Λόνδινο." and not changes
        assert set(left) == {"Μάρια", "Λόνδινο"}

    def test_correct_text_is_left_alone(self):
        from utils.accent_repair import collect_evidence, repair_line
        ev = collect_evidence(self.LINES + ["Πού είσαι; Τι κάνεις;"])
        for line in ("Περίμενε λίγο.", "Σοβαρά;", "Πού είσαι;", "Τάντζιρο, έλα!", "ΠΕΡΙΜΕΝΕ!"):
            assert repair_line(line, ev)[0] == line

    def test_the_script_reports_without_writing(self, tmp_path):
        from scripts.repair_greek_accents import repair_project
        from utils import storage
        storage.create_project("Show", {"show_name": "Show", "target_language": "Greek", "glossary": {"terms": []}})
        cues = [{"id": str(i), "timecode": "00:00:01,000 --> 00:00:02,000", "original": "x",
                 "translated": t, "translations": {"el": t}} for i, t in enumerate(self.LINES + ["Πέριμενε!"])]
        storage.save_episode("Show", "S01E01", cues)
        res = repair_project("Show", apply=False, export=False)
        assert res["episodes"] == ["S01E01"] and res["examples"]["Πέριμενε → Περίμενε"] == 1
        assert storage.load_episode_data("Show", "S01E01")[-1]["translated"] == "Πέριμενε!"   # dry run
        repair_project("Show", apply=True, export=False)
        fixed = storage.load_episode_data("Show", "S01E01")[-1]
        assert fixed["translated"] == "Περίμενε!" and fixed["translations"]["el"] == "Περίμενε!"
