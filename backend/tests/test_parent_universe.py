import pytest
from fastapi.testclient import TestClient
import os
import shutil
from pathlib import Path

# Set up testing env
from main import app
from utils import storage
from utils.character_profiles import CharacterProfileManager, CharacterProfile

client = TestClient(app)

PARENT_NAME = "_test_parent_universe"
CHILD_NAME = "_test_child_universe"

@pytest.fixture(autouse=True)
def setup_teardown():
    # Cleanup any existing test projects
    storage.delete_project(PARENT_NAME)
    storage.delete_project(CHILD_NAME)
    yield
    # Cleanup after test
    storage.delete_project(PARENT_NAME)
    storage.delete_project(CHILD_NAME)

def test_metadata_and_character_inheritance():
    # 1. Create parent project
    parent_meta = {
        "show_name": "Parent Universe",
        "target_language": "Greek",
        "type": "parent",
        "parent_project": None,
        "glossary": {
            "terms": [
                {"term": "Saber", "translation": "Σέιμπερ", "type": "person", "gender": "feminine"},
                {"term": "Fuyuki", "translation": "Φουγιούκι", "type": "location"}
            ]
        }
    }
    storage.create_project(PARENT_NAME, parent_meta)
    
    # Add parent character profile
    parent_char_mgr = CharacterProfileManager(PARENT_NAME)
    parent_char_mgr.update_profile("Saber", {
        "gender": "feminine",
        "formality": "formal",
        "speech_patterns": "Noble and chivalrous"
    })

    # 2. Create child project linked to parent
    child_meta = {
        "show_name": "Child Show",
        "target_language": "Greek",
        "type": "show",
        "parent_project": PARENT_NAME,
        "glossary": {
            "terms": [
                # Saber override (change translation to phonetic)
                {"term": "saber", "translation": "Σέιμπερ override", "type": "person", "gender": "feminine"},
                # New child-only term
                {"term": "Command Spells", "translation": "Σφραγίδες Εντολών", "type": "item"}
            ]
        },
        "settings": {
            "inherit_glossary": True,
            "inherit_context": True,
            "inherit_characters": True
        }
    }
    storage.create_project(CHILD_NAME, child_meta)
    
    # Add child-only character profile override
    child_char_mgr = CharacterProfileManager(CHILD_NAME)
    child_char_mgr.update_profile("Saber", {
        "gender": "feminine",
        "formality": "mixed",  # Overridden formality
        "speech_patterns": "Chivalrous override"
    })
    child_char_mgr.update_profile("Shirou", {
        "gender": "masculine",
        "formality": "informal"
    })

    # 3. Verify resolved metadata
    resolved_meta = storage.load_resolved_project_metadata(CHILD_NAME)
    terms = resolved_meta["glossary"]["terms"]
    
    # Should have "Saber" (child override), "Fuyuki" (inherited from parent), and "Command Spells" (local)
    term_map = {t["term"].lower(): t for t in terms}
    assert len(terms) == 3
    
    assert term_map["fuyuki"]["inherited"] is True
    assert term_map["fuyuki"]["inherited_from"] == PARENT_NAME
    
    assert term_map["saber"]["inherited"] is False
    assert term_map["saber"]["translation"] == "Σέιμπερ override"
    
    assert term_map["command spells"]["inherited"] is False

    # 4. Verify resolved character profiles
    resolved_chars = child_char_mgr.load_all_resolved()
    assert len(resolved_chars) == 2 # Shirou and Saber
    
    assert resolved_chars["Saber"].inherited is False
    assert resolved_chars["Saber"].formality == "mixed" # Child override took priority
    assert resolved_chars["Saber"].speech_patterns == "Chivalrous override"

def test_sync_wizard_endpoints():
    # Create parent project
    parent_meta = {
        "show_name": "Parent Universe",
        "target_language": "Greek",
        "type": "parent",
        "glossary": {"terms": [{"term": "Saber", "translation": "Σέιμπερ", "type": "person"}]}
    }
    storage.create_project(PARENT_NAME, parent_meta)
    
    # Create child project
    child_meta = {
        "show_name": "Child Show",
        "target_language": "Greek",
        "type": "show",
        "parent_project": PARENT_NAME,
        "glossary": {
            "terms": [
                {"term": "Saber", "translation": "Σέιμπερ", "type": "person"}, # Already in parent
                {"term": "Gae Bolg", "translation": "Γκέι Μπολγκ", "type": "item"} # New candidate!
            ]
        }
    }
    storage.create_project(CHILD_NAME, child_meta)
    
    # Add child character profile (candidate)
    child_char_mgr = CharacterProfileManager(CHILD_NAME)
    child_char_mgr.update_profile("Lancer", {
        "gender": "masculine",
        "formality": "informal"
    })

    # Test sync-candidates GET endpoint
    response = client.get(f"/projects/{PARENT_NAME}/sync-candidates")
    assert response.status_code == 200
    data = response.json()
    
    assert len(data["glossary"]) == 1
    assert data["glossary"][0]["term"] == "Gae Bolg"
    assert data["glossary"][0]["project"] == CHILD_NAME
    
    assert len(data["characters"]) == 1
    assert data["characters"][0]["name"] == "Lancer"
    assert data["characters"][0]["project"] == CHILD_NAME

    # Test sync-import POST endpoint
    import_payload = {
        "terms": [
            {"term": "Gae Bolg", "translation": "Γκέι Μπολγκ", "type": "item", "project": CHILD_NAME}
        ],
        "characters": [
            {"name": "Lancer", "gender": "masculine", "formality": "informal", "project": CHILD_NAME}
        ]
    }
    
    import_res = client.post(f"/projects/{PARENT_NAME}/sync-import", json=import_payload)
    assert import_res.status_code == 200
    assert import_res.json()["imported_terms"] == 1
    assert import_res.json()["imported_characters"] == 1
    
    # Verify they are now in the parent metadata and profiles
    parent_resolved = storage.load_project_metadata(PARENT_NAME)
    p_terms = {t["term"] for t in parent_resolved["glossary"]["terms"]}
    assert "Gae Bolg" in p_terms
    
    parent_char_mgr = CharacterProfileManager(PARENT_NAME)
    p_chars = parent_char_mgr.load_all()
    assert "Lancer" in p_chars
    assert p_chars["Lancer"].gender == "masculine"


def test_parent_cycle_does_not_recurse():
    """A self-parent or a parent loop must not cause RecursionError in either the
    metadata resolver or the character resolver."""
    # Self-parent.
    storage.create_project(PARENT_NAME, {"target_language": "Greek", "parent_project": PARENT_NAME})
    assert storage.load_resolved_project_metadata(PARENT_NAME) is not None
    assert CharacterProfileManager(PARENT_NAME).load_all_resolved() is not None

    # A -> B and B -> A loop.
    storage.create_project(CHILD_NAME, {"target_language": "Greek", "parent_project": PARENT_NAME})
    pmeta = storage.load_project_metadata(PARENT_NAME)
    pmeta["parent_project"] = CHILD_NAME
    storage.save_project_metadata(PARENT_NAME, pmeta)

    assert storage.load_resolved_project_metadata(CHILD_NAME) is not None
    assert CharacterProfileManager(CHILD_NAME).load_all_resolved() is not None


def test_partial_character_override_keeps_inheriting_unedited_fields():
    """Editing one field of an inherited character must override only that field —
    the rest keep inheriting live from the parent (no frozen full copy)."""
    storage.create_project(PARENT_NAME, {"target_language": "Greek"})
    pm = CharacterProfileManager(PARENT_NAME)
    pm.update_profile("Saber", {"gender": "feminine", "formality": "formal", "speech_patterns": "Noble"})

    storage.create_project(CHILD_NAME, {
        "target_language": "Greek", "parent_project": PARENT_NAME,
        "settings": {"inherit_characters": True},
    })
    cm = CharacterProfileManager(CHILD_NAME)
    cm.update_profile("Saber", {"formality": "mixed"})  # override ONE field only

    resolved = cm.load_all_resolved()["Saber"]
    assert resolved.formality == "mixed"          # overridden
    assert resolved.speech_patterns == "Noble"    # still inherited live
    assert resolved.gender == "feminine"          # still inherited live

    # A later parent edit to a non-overridden field propagates to the child.
    pm.update_profile("Saber", {"speech_patterns": "Stoic"})
    resolved2 = cm.load_all_resolved()["Saber"]
    assert resolved2.speech_patterns == "Stoic"
    assert resolved2.formality == "mixed"


def test_update_project_strips_inherited_context_block():
    """Saving the resolved context back must not compound the parent block into the
    child (the unbounded-growth bug)."""
    storage.create_project(PARENT_NAME, {"target_language": "Greek", "context_guide": "PARENT GUIDE"})
    storage.create_project(CHILD_NAME, {
        "target_language": "Greek", "parent_project": PARENT_NAME,
        "context_guide": "CHILD GUIDE", "settings": {"inherit_context": True},
    })

    resolved = client.get(f"/projects/{CHILD_NAME}").json()
    assert "PARENT GUIDE" in resolved["context_guide"]
    assert "--- Universe Context" in resolved["context_guide"]

    # Simulate the editor saving the resolved value back verbatim.
    client.put(f"/projects/{CHILD_NAME}", json={"context_guide": resolved["context_guide"]})

    stored = storage.load_project_metadata(CHILD_NAME)
    assert stored["context_guide"] == "CHILD GUIDE"  # only the child's own context persisted

    # Resolving again yields exactly one parent block, not a compounded chain.
    resolved2 = client.get(f"/projects/{CHILD_NAME}").json()
    assert resolved2["context_guide"].count("--- Universe Context") == 1
