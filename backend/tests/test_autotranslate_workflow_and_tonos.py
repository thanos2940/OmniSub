import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from utils.text_normalize import (
    shift_greek_accent_to_first_syllable,
    fix_japanese_name_greek_tonos,
    _CANONICAL_JAPANESE_NAMES_GREEK,
)
from utils.structured_output import parse_structured


class TestJapaneseNamesGreekTonos:
    def test_canonical_tanjiro(self):
        # Explicit test from prompt: Tanjiro -> Τάντζιρο
        assert fix_japanese_name_greek_tonos("Ταντζίρο") == "Τάντζιρο"
        assert fix_japanese_name_greek_tonos("Ταντζιρό") == "Τάντζιρο"
        assert fix_japanese_name_greek_tonos("Τάντζιρο") == "Τάντζιρο"
        assert fix_japanese_name_greek_tonos("Ταντζιρο") == "Τάντζιρο"
        # All-caps Greek typography: no internal tonos
        assert fix_japanese_name_greek_tonos("ΤΑΝΤΖΊΡΟ") == "ΤΑΝΤΖΙΡΟ"
        assert fix_japanese_name_greek_tonos("ΤΑΝΤΖΙΡΟ") == "ΤΑΝΤΖΙΡΟ"

    def test_canonical_demon_slayer_names(self):
        # Zenitsu -> Ζένιτσου (not Ζενίτσου, not Ζενιτσού)
        assert fix_japanese_name_greek_tonos("Ζενίτσου") == "Ζένιτσου"
        assert fix_japanese_name_greek_tonos("Ζενιτσου") == "Ζένιτσου"
        # Nezuko -> Νέζουκο (not Νεζούκο)
        assert fix_japanese_name_greek_tonos("Νεζούκο") == "Νέζουκο"
        assert fix_japanese_name_greek_tonos("Νεζουκο") == "Νέζουκο"
        # Inosuke -> Ίνοσουκε (first syllable accent)
        assert fix_japanese_name_greek_tonos("Ινοσούκε") == "Ίνοσουκε"
        assert fix_japanese_name_greek_tonos("Ινόσουκε") == "Ίνοσουκε"
        # Kibutsuji -> Κίμπουτσουτζι (first syllable accent)
        assert fix_japanese_name_greek_tonos("Κιμπουτσούτζι") == "Κίμπουτσουτζι"
        assert fix_japanese_name_greek_tonos("Κιμπούτσουτζι") == "Κίμπουτσουτζι"

    def test_canonical_naruto_names(self):
        # Naruto -> Νάρουτο
        assert fix_japanese_name_greek_tonos("Ναρούτο") == "Νάρουτο"
        assert fix_japanese_name_greek_tonos("Ναρουτο") == "Νάρουτο"
        # Sasuke -> Σάσκε
        assert fix_japanese_name_greek_tonos("Σασκέ") == "Σάσκε"
        assert fix_japanese_name_greek_tonos("Σασουκε") == "Σάσουκε"
        # Sakura -> Σάκουρα
        assert fix_japanese_name_greek_tonos("Σακούρα") == "Σάκουρα"
        assert fix_japanese_name_greek_tonos("Σακουρα") == "Σάκουρα"

    def test_digraph_syllable_shift(self):
        # "ου" in Greek is a single phonetic diphthong (vowel sound), not two syllables
        # If the word starts with a consonant + diphthong like Μου...
        shifted = shift_greek_accent_to_first_syllable("Μουζάν")
        assert shifted == "Μούζαν"

    def test_full_name_phrases(self):
        # Handles multi-word Japanese full names (both words shifted to first syllable: Kamado -> Κάμαντο, Tanjiro -> Τάντζιρο)
        result = fix_japanese_name_greek_tonos("Καμάντο Ταντζίρο")
        assert result == "Κάμαντο Τάντζιρο"

    def test_dialogue_sentence(self):
        sentence = "Ταντζίρο, πρόσεχε! Έρχεται ο Μουζάν!"
        assert fix_japanese_name_greek_tonos(sentence) == "Τάντζιρο, πρόσεχε! Έρχεται ο Μούζαν!"

    def test_unaccented_word_gets_accent_on_first_syllable(self):
        # If an unaccented Greek name is passed, first syllable gets the accent
        result = shift_greek_accent_to_first_syllable("Ναρουτο")
        assert result == "Νάρουτο"


class TestTolerantStructuredOutputParsing:
    def test_standard_lines_json(self):
        json_text = '{"lines": [{"i": 1, "t": "Γεια σας"}, {"i": 2, "t": "Τι κάνετε;"}]}'
        res = parse_structured(json_text)
        assert len(res) == 2
        assert res[0]["index"] == 1
        assert res[0]["text"] == "Γεια σας"
        assert res[1]["index"] == 2
        assert res[1]["text"] == "Τι κάνετε;"

    def test_uppercase_keys(self):
        # Models sometimes capitalize JSON keys: I and T
        json_text = '{"lines": [{"I": 1, "T": "Πρώτη γραμμή"}, {"I": 2, "T": "Δεύτερη γραμμή"}]}'
        res = parse_structured(json_text)
        assert len(res) == 2
        assert res[0]["index"] == 1
        assert res[0]["text"] == "Πρώτη γραμμή"

    def test_numeric_dict_keys(self):
        # Models returning a dictionary directly mapping line number to text: {"1": "...", "2": "..."}
        json_text = '{"1": "Πρώτη γραμμή", "2": "Δεύτερη γραμμή"}'
        res = parse_structured(json_text)
        assert len(res) == 2
        assert res[0]["index"] == 1
        assert res[0]["text"] == "Πρώτη γραμμή"
        assert res[1]["index"] == 2
        assert res[1]["text"] == "Δεύτερη γραμμή"

    def test_numeric_dict_with_objects(self):
        # Models returning a map from line number to dict: {"1": {"t": "..."}, "2": {"t": "..."}}
        json_text = '{"1": {"t": "Γραμμή 1"}, "2": {"t": "Γραμμή 2"}}'
        res = parse_structured(json_text)
        assert len(res) == 2
        assert res[0]["index"] == 1
        assert res[0]["text"] == "Γραμμή 1"

    def test_thinking_tag_stripping(self):
        # Local or reasoning models emitting <think> ... </think> before JSON
        raw = '<think>I will translate these lines into Greek.</think>```json\n{"lines": [{"i": 1, "t": "Καλημέρα"}]}\n```'
        res = parse_structured(raw)
        assert len(res) == 1
        assert res[0]["text"] == "Καλημέρα"


class TestAnimeAssPreference:
    def test_prefer_ass_for_anime_series_type(self):
        from integrations.media_sync_engine import prefer_ass_for_project
        meta = {"series_type": "anime", "genres": []}
        assert prefer_ass_for_project(meta) is True

    def test_prefer_ass_for_anime_genre(self):
        from integrations.media_sync_engine import prefer_ass_for_project
        meta = {"genres": ["Animation", "Anime"]}
        assert prefer_ass_for_project(meta) is True

    def test_prefer_ass_for_is_anime_flag(self):
        from integrations.media_sync_engine import prefer_ass_for_project
        meta = {"is_anime": True}
        assert prefer_ass_for_project(meta) is True

    def test_regular_show_does_not_prefer_ass(self):
        from integrations.media_sync_engine import prefer_ass_for_project
        meta = {"series_type": "standard", "genres": ["Drama"]}
        assert prefer_ass_for_project(meta) is False


@pytest.mark.asyncio
class TestGlossaryEnhancementAcrossEpisodes:
    async def test_harvest_and_merge_terms_into_project_glossary(self, tmp_path):
        from utils import storage
        from services.term_harvester import harvest_terms_from_lines

        project_name = "test_anime_proj"
        proj_dir = tmp_path / "projects" / project_name
        proj_dir.mkdir(parents=True)

        initial_meta = {
            "name": project_name,
            "target_language": "Greek",
            "series_type": "anime",
            "glossary": {
                "terms": [
                    {"term": "Tanjiro", "translation": "Τάντζιρο", "type": "person"}
                ]
            }
        }

        # Mock LLM generation for harvesting terms
        mock_harvest_response = '{"discovered_terms": [{"term": "Nezuko", "translation": "Νεζούκο", "type": "person"}]}'

        with patch("utils.storage.PROJECTS_DIR", tmp_path / "projects"):
            storage.save_project_metadata(project_name, initial_meta)

            with patch("adk_agents.llm_factory.generate", AsyncMock(return_value=mock_harvest_response)):
                new_terms = await harvest_terms_from_lines(
                    source_lines=["Hello Nezuko!"],
                    target_lines=["Γεια σου Νέζουκο!"],
                    target_language="Greek",
                    existing_glossary=initial_meta["glossary"],
                )

                assert len(new_terms) == 1
                assert new_terms[0]["term"] == "Nezuko"
                # Check that Greek tonos was automatically fixed: Νεζούκο -> Νέζουκο
                assert new_terms[0]["translation"] == "Νέζουκο"

                # Simulate episode completion glossary update
                loaded_meta = storage.load_project_metadata(project_name)
                terms_list = loaded_meta.setdefault("glossary", {}).setdefault("terms", [])
                terms_list.extend(new_terms)
                storage.save_project_metadata(project_name, loaded_meta)

                # Subsequent episode loads project metadata and sees Nezuko
                subsequent_meta = storage.load_project_metadata(project_name)
                glossary_terms = subsequent_meta["glossary"]["terms"]
                assert len(glossary_terms) == 2
                terms_dict = {t["term"]: t["translation"] for t in glossary_terms}
                assert terms_dict["Tanjiro"] == "Τάντζιρο"
                assert terms_dict["Nezuko"] == "Νέζουκο"


@pytest.mark.asyncio
class TestSubtitleAutoDetection:
    async def test_episode_store_detects_video_file_when_media_path_empty(self, tmp_path):
        from utils import storage

        project_name = "test_video_scan_proj"
        ep_name = "S01E01"
        ep_dir = tmp_path / "projects" / project_name / "episodes" / ep_name
        ep_dir.mkdir(parents=True)

        # Create a mock mkv file in the episode directory
        video_file = ep_dir / "S01E01.mkv"
        video_file.write_text("dummy video binary")

        ep_meta = {
            "name": ep_name,
            "has_source_sub": False,
            "line_count": 0,
            "arr_media_path": None,
        }

        with patch("utils.storage.PROJECTS_DIR", tmp_path / "projects"):
            storage.save_episode_metadata(project_name, ep_name, ep_meta)
            episodes = storage.list_episodes_with_metadata(project_name)
            assert len(episodes) == 1
            # Verify arr_media_path was detected and auto-populated from disk
            assert episodes[0]["metadata"]["arr_media_path"] == str(video_file)

    async def test_episode_store_detects_video_file_in_project_directory(self, tmp_path):
        from utils import storage

        project_name = "test_proj_level_video"
        ep_name = "S01E02"
        proj_dir = tmp_path / "projects" / project_name
        proj_dir.mkdir(parents=True)
        ep_dir = proj_dir / "episodes" / ep_name
        ep_dir.mkdir(parents=True)

        # Video file placed directly in project directory (not inside episode directory)
        video_file = proj_dir / "Show.S01E02.mkv"
        video_file.write_text("dummy binary")

        ep_meta = {
            "name": ep_name,
            "has_source_sub": False,
            "line_count": 0,
            "arr_media_path": None,
        }

        with patch("utils.storage.PROJECTS_DIR", tmp_path / "projects"):
            storage.save_episode_metadata(project_name, ep_name, ep_meta)
            episodes = storage.list_episodes_with_metadata(project_name)
            assert len(episodes) == 1
            assert episodes[0]["metadata"]["arr_media_path"] == str(video_file)

    async def test_extract_embedded_none_ep_meta_does_not_crash(self, tmp_path):
        from services.embedded_extractor import extract_embedded_for_episode
        from utils import storage

        project_name = "test_none_meta_proj"
        ep_name = "S01E01"
        proj_dir = tmp_path / "projects" / project_name
        proj_dir.mkdir(parents=True)
        ep_dir = proj_dir / "episodes" / ep_name
        ep_dir.mkdir(parents=True)

        video_file = ep_dir / "S01E01.mkv"
        video_file.write_text("dummy")

        with patch("utils.storage.PROJECTS_DIR", tmp_path / "projects"):
            storage.save_project_metadata(project_name, {"show_name": project_name, "target_language": "Greek"})
            # ep_meta is None initially
            assert storage.load_episode_metadata(project_name, ep_name) is None

            with patch("services.embedded_extractor._get_tools_and_config", return_value=(None, {})):
                with pytest.raises(ValueError, match="ffmpeg / ffprobe not found"):
                    # Verifies it got past ep_meta check without AttributeError on None
                    await extract_embedded_for_episode(project_name, ep_name)


class TestAtomicRunnerTonosGuard:
    def test_greek_tonos_post_processor_fixes_translated_dialogue(self):
        from utils.text_normalize import fix_japanese_name_greek_tonos
        # Dialogue line containing misaccented Tanjiro and unaccented Nezuko
        line = "Ο Ταντζίρο και η Νεζουκο πρέπει να προστατευτούν από τον Μουζάν."
        fixed = fix_japanese_name_greek_tonos(line)
        assert fixed == "Ο Τάντζιρο και η Νέζουκο πρέπει να προστατευτούν από τον Μούζαν."

    def test_greek_tonos_post_processor_all_caps(self):
        from utils.text_normalize import fix_japanese_name_greek_tonos
        line = "ΤΑΝΤΖΊΡΟ, ΠΡΟΣΕΧΕ!"
        fixed = fix_japanese_name_greek_tonos(line)
        assert fixed == "ΤΑΝΤΖΙΡΟ, ΠΡΟΣΕΧΕ!"
