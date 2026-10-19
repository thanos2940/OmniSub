"""
Tests for storage.load_episode_data() and compatibility with term_harvester.py.
"""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from utils import storage
from utils.storage_modules import atomic_io, project_store, episode_store
from services import term_harvester


@pytest.fixture(autouse=True)
def model_engine():
    """These tests drive the Gemini model path (the default engine is the agent CLI: test_agy_first.py)."""
    storage.save_global_config({"translation_engine": "model"})


class TestLoadEpisodeData:
    """Test storage.load_episode_data normalization and edge cases."""

    def test_load_episode_data_nonexistent_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(atomic_io, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(project_store, "PROJECTS_DIR", tmp_path)

        res = storage.load_episode_data("NonExistentProject", "S01E01")
        assert res is None

    def test_load_episode_data_normalizes_legacy_list_and_translated_field(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(atomic_io, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(project_store, "PROJECTS_DIR", tmp_path)

        project_name = "LegacyShow"
        episode_name = "S01E01"
        storage.create_project(project_name, {"target_language": "Greek"})

        # Write raw legacy list directly to data.json
        ep_dir = tmp_path / project_name / "episodes" / episode_name
        ep_dir.mkdir(parents=True, exist_ok=True)
        raw_list = [
            {"index": 1, "original": "Good morning.", "translated": "Καλημέρα."},
            {"index": 2, "original": "How are you?", "translated": "Πώς είσαι;"},
            {"index": 3, "original": "I am fine.", "translated": "Είμαι καλά."},
        ]
        with open(ep_dir / "data.json", "w", encoding="utf-8") as f:
            json.dump(raw_list, f)

        loaded = storage.load_episode_data(project_name, episode_name)
        assert loaded is not None
        assert len(loaded) == 3
        for item in loaded:
            assert "translation" in item
            assert "translations" in item
            assert "src_hash" in item

        assert loaded[0]["translation"] == "Καλημέρα."
        assert loaded[1]["translation"] == "Πώς είσαι;"
        assert loaded[2]["translation"] == "Είμαι καλά."

    def test_load_episode_data_normalizes_translations_dictionary(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(atomic_io, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(project_store, "PROJECTS_DIR", tmp_path)

        project_name = "MultiLangShow"
        episode_name = "S01E01"
        storage.create_project(project_name, {"target_language": "Greek"})

        ep_dir = tmp_path / project_name / "episodes" / episode_name
        ep_dir.mkdir(parents=True, exist_ok=True)
        dict_data = {
            "data": [
                {"index": 1, "original": "Welcome home.", "translations": {"el": "Καλώς ήρθες σπίτι."}},
                {"index": 2, "original": "Thank you.", "translations": {"fr": "Merci."}},
                {"index": 3, "original": "Untranslated line.", "translations": {}},
            ]
        }
        with open(ep_dir / "data.json", "w", encoding="utf-8") as f:
            json.dump(dict_data, f)

        loaded = storage.load_episode_data(project_name, episode_name)
        assert loaded is not None
        assert len(loaded) == 3
        assert loaded[0]["translation"] == "Καλώς ήρθες σπίτι."
        assert loaded[1]["translation"] == "Merci."
        # Untranslated line does not have 'translation' key populated
        assert "translation" not in loaded[2]

    def test_load_episode_data_preserves_explicit_translation_field(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(atomic_io, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(project_store, "PROJECTS_DIR", tmp_path)

        project_name = "ExplicitShow"
        episode_name = "S01E01"
        storage.create_project(project_name, {"target_language": "Greek"})

        ep_dir = tmp_path / project_name / "episodes" / episode_name
        ep_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "data": [
                {"index": 1, "original": "Direct line", "translation": "Άμεση γραμμή", "translated": "Old"},
            ]
        }
        with open(ep_dir / "data.json", "w", encoding="utf-8") as f:
            json.dump(data, f)

        loaded = storage.load_episode_data(project_name, episode_name)
        assert loaded is not None
        # Explicit 'translation' takes precedence
        assert loaded[0]["translation"] == "Άμεση γραμμή"


class TestTermHarvesterIntegration:
    """Test term_harvester service calling storage.load_episode_data()."""

    @pytest.mark.asyncio
    async def test_harvest_terms_for_episode_full_flow(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(atomic_io, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(project_store, "PROJECTS_DIR", tmp_path)

        project_name = "MarvelUniverse"
        episode_name = "Avengers_S01E01"

        storage.create_project(project_name, {
            "target_language": "Greek",
            "glossary": {
                "terms": [
                    {"term": "Iron Man", "translation": "Άιρον Μαν"},
                ]
            }
        })

        data = [
            {"index": 1, "original": "Look, it's Doctor Strange!", "translated": "Κοίτα, είναι ο Δόκτωρ Στρέιντζ!"},
            {"index": 2, "original": "He is holding the Eye of Agamotto.", "translated": "Κρατάει το Μάτι του Αγκαμότο."},
            {"index": 3, "original": "Iron Man is also here.", "translated": "Ο Άιρον Μαν είναι επίσης εδώ."},
        ]
        storage.save_episode(project_name, episode_name, data)

        mock_response_json = {
            "discovered_terms": [
                {
                    "term": "Doctor Strange",
                    "translation": "Δόκτωρ Στρέιντζ",
                    "type": "character",
                    "gender": "masculine",
                    "description": "Master of the Mystic Arts",
                    "context_snippet": "Look, it's Doctor Strange!",
                    "confidence": 0.98,
                },
                {
                    "term": "Eye of Agamotto",
                    "translation": "Μάτι του Αγκαμότο",
                    "type": "object",
                    "gender": "neuter",
                    "description": "Mystical relic containing the Time Stone",
                    "context_snippet": "He is holding the Eye of Agamotto.",
                    "confidence": 0.95,
                },
                # Duplicate term that should be filtered out because it's in existing glossary
                {
                    "term": "Iron Man",
                    "translation": "Άιρον Μαν",
                    "type": "character",
                    "gender": "masculine",
                    "description": "Armored superhero",
                    "context_snippet": "Iron Man is also here.",
                    "confidence": 0.99,
                }
            ]
        }

        with patch("adk_agents.llm_factory.generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = json.dumps(mock_response_json)
            terms = await term_harvester.harvest_terms_for_episode(project_name, episode_name)

        assert len(terms) == 2
        term_names = {t["term"] for t in terms}
        assert "Doctor Strange" in term_names
        assert "Eye of Agamotto" in term_names
        assert "Iron Man" not in term_names  # Filtered because already in glossary

        # Verify generate call
        assert mock_gen.called
        call_kwargs = mock_gen.call_args.kwargs
        prompt_content = call_kwargs.get("prompt", "")
        assert "Target Language: Greek" in prompt_content
        assert "[EN] Look, it's Doctor Strange!" in prompt_content
        assert "[EL] Κοίτα, είναι ο Δόκτωρ Στρέιντζ!" in prompt_content

    @pytest.mark.asyncio
    async def test_harvest_terms_for_episode_empty_or_missing_handling(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(atomic_io, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(project_store, "PROJECTS_DIR", tmp_path)

        project_name = "EmptyShow"
        storage.create_project(project_name, {"target_language": "Greek"})

        # Nonexistent episode
        assert await term_harvester.harvest_terms_for_episode(project_name, "NonExistent") == []

        # Nonexistent project
        assert await term_harvester.harvest_terms_for_episode("NonExistentProject", "S01E01") == []

        # Episode with untranslated lines
        storage.save_episode(project_name, "S01E01", [
            {"index": 1, "original": "Only english", "translation": ""}
        ])
        assert await term_harvester.harvest_terms_for_episode(project_name, "S01E01") == []
