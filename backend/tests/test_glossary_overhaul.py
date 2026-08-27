import pytest
from fastapi.testclient import TestClient
from main import app
from utils import storage
from services.term_harvester import harvest_terms_from_lines

client = TestClient(app)

@pytest.fixture
def setup_universe(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)
    return projects_dir


def test_resolved_metadata_override_and_upstream_modified(setup_universe):
    # 1. Create Parent Universe
    parent_meta = {
        "name": "Marvel Universe",
        "type": "universe",
        "target_language": "Greek",
        "glossary": {
            "terms": [
                {
                    "term": "Mjolnir",
                    "translation": "Μγιόλνιρ",
                    "type": "object",
                    "gender": "neuter",
                    "description": "Thor's hammer",
                    "case_sensitive": True,
                    "keep_original": False,
                },
                {
                    "term": "Valkyrie",
                    "translation": "Βαλκυρία",
                    "type": "character",
                    "gender": "feminine",
                    "description": "Asgardian warrior",
                }
            ]
        }
    }
    storage.create_project("Marvel Universe", parent_meta)

    # 2. Create Child Show with a local override on Mjolnir
    child_meta = {
        "name": "Thor Ragnarok",
        "type": "movie",
        "parent_project": "Marvel Universe",
        "target_language": "Greek",
        "glossary": {
            "terms": [
                {
                    "term": "Mjolnir",
                    "translation": "Μγιόλνιρ (Τοφυρί)",
                    "type": "object",
                    "gender": "neuter",
                    "description": "Custom show description",
                },
                {
                    "term": "Korg",
                    "translation": "Κοργκ",
                    "type": "character",
                    "gender": "masculine",
                    "description": "Kronan warrior",
                }
            ]
        }
    }
    storage.create_project("Thor Ragnarok", child_meta)

    # 3. Resolve child metadata
    resolved = storage.load_resolved_project_metadata("Thor Ragnarok")
    terms = resolved["glossary"]["terms"]
    term_map = {t["term"]: t for t in terms}

    assert "Valkyrie" in term_map
    assert term_map["Valkyrie"]["inherited"] is True
    assert term_map["Valkyrie"]["inherited_from"] == "Marvel Universe"
    assert term_map["Valkyrie"]["is_override"] is False

    assert "Korg" in term_map
    assert term_map["Korg"]["inherited"] is False
    assert term_map["Korg"]["is_override"] is False

    assert "Mjolnir" in term_map
    assert term_map["Mjolnir"]["inherited"] is False
    assert term_map["Mjolnir"]["is_override"] is True
    assert term_map["Mjolnir"]["upstream_modified"] is True
    assert term_map["Mjolnir"]["parent_term"]["translation"] == "Μγιόλνιρ"


def test_batch_promote_and_suppress_and_revert_endpoints(setup_universe):
    parent_meta = {
        "name": "Anime Universe",
        "type": "universe",
        "target_language": "Greek",
        "glossary": {
            "terms": [
                {"term": "Chakra", "translation": "Τσάκρα", "type": "concept"}
            ]
        }
    }
    storage.create_project("Anime Universe", parent_meta)

    child_meta = {
        "name": "Naruto",
        "parent_project": "Anime Universe",
        "target_language": "Greek",
        "glossary": {
            "terms": [
                {"term": "Rasengan", "translation": "Ρασένγκαν", "type": "technique"},
                {"term": "Kunai", "translation": "Κουνάι", "type": "object"}
            ]
        }
    }
    storage.create_project("Naruto", child_meta)

    # 1. Batch Promote Rasengan & Kunai to Anime Universe
    res_promote = client.post("/projects/Naruto/promote-terms-batch", json={
        "terms": [
            {"term": "Rasengan", "translation": "Ρασένγκαν", "type": "technique"},
            {"term": "Kunai", "translation": "Κουνάι", "type": "object"}
        ]
    })
    assert res_promote.status_code == 200
    assert res_promote.json()["count"] == 2

    # Verify parent has terms now
    p_meta = storage.load_project_metadata("Anime Universe")
    p_terms = {t["term"]: t for t in p_meta["glossary"]["terms"]}
    assert "Rasengan" in p_terms
    assert "Kunai" in p_terms

    # 2. Batch Suppress Chakra in Naruto
    res_suppress = client.post("/projects/Naruto/suppress-terms-batch", json={
        "terms": ["Chakra"]
    })
    assert res_suppress.status_code == 200
    n_meta = storage.load_project_metadata("Naruto")
    assert "Chakra" in n_meta["suppressed_terms"]

    # Verify resolution suppresses Chakra
    resolved = storage.load_resolved_project_metadata("Naruto")
    res_terms = [t["term"] for t in resolved["glossary"]["terms"]]
    assert "Chakra" not in res_terms

    # 3. Batch Revert Chakra (unsuppress)
    res_revert = client.post("/projects/Naruto/revert-terms-batch", json={
        "terms": ["Chakra"]
    })
    assert res_revert.status_code == 200
    resolved_after = storage.load_resolved_project_metadata("Naruto")
    res_terms_after = [t["term"] for t in resolved_after["glossary"]["terms"]]
    assert "Chakra" in res_terms_after
