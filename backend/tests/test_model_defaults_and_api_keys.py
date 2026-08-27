import os
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from main import app
from utils import storage
from routers.settings import get_api_key, has_api_key

@pytest.fixture
def mock_storage(monkeypatch, tmp_path):
    temp_config = tmp_path / "config.json"
    temp_projects = tmp_path / "projects"
    temp_projects.mkdir()
    monkeypatch.setattr(storage, "CONFIG_FILE", temp_config)
    monkeypatch.setattr(storage, "PROJECTS_DIR", temp_projects)
    return {"config": temp_config, "projects": temp_projects}


def test_new_project_inherits_user_configured_model_parameters(mock_storage, monkeypatch):
    # 1. User configures custom global model defaults
    custom_global_config = {
        "temperature": 0.75,
        "top_k": 80,
        "top_p": 0.85,
        "default_translation_model": "gemini-1.5-pro",
        "default_scan_model": "gemini-1.5-flash",
        "default_context_model": "gemini-1.5-pro",
        "default_glossary_model": "gemini-1.5-pro",
        "review_model": "gemini-1.5-flash",
        "ai_provider": "hybrid",
        "default_target_language": "Greek",
        "apply_subtitle_edit_fixes": True
    }
    storage.save_global_config(custom_global_config)

    # 2. Create a new project
    proj = storage.create_project("TestAnimeSeries", {
        "show_name": "TestAnimeSeries",
        "type": "show"
    })

    # 3. Assert project metadata settings correctly inherited user's custom global defaults
    settings = proj.get("settings", {})
    assert settings.get("temperature") == 0.75
    assert settings.get("top_k") == 80
    assert settings.get("top_p") == 0.85
    assert settings.get("translation_model") == "gemini-1.5-pro"
    assert settings.get("scan_model") == "gemini-1.5-flash"
    assert settings.get("context_model") == "gemini-1.5-pro"
    assert settings.get("glossary_model") == "gemini-1.5-pro"
    assert settings.get("review_model") == "gemini-1.5-flash"
    assert settings.get("ai_provider") == "hybrid"
    assert proj.get("target_language") == "Greek"


def test_get_project_setting_resolves_global_fallback(mock_storage):
    storage.save_global_config({
        "temperature": 0.45,
        "top_k": 55,
        "default_translation_model": "gemini-2.5-flash"
    })
    
    # Project with empty settings dictionary
    metadata = {"settings": {}}
    assert storage.get_project_setting(metadata, "temperature") == 0.45
    assert storage.get_project_setting(metadata, "top_k") == 55
    assert storage.get_project_setting(metadata, "translation_model") == "gemini-2.5-flash"

    # Project with explicit overrides
    metadata_custom = {"settings": {"temperature": 0.9, "top_k": 100}}
    assert storage.get_project_setting(metadata_custom, "temperature") == 0.9
    assert storage.get_project_setting(metadata_custom, "top_k") == 100


def test_update_settings_persists_api_key(mock_storage, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    client = TestClient(app)
    
    # Post settings including a new Gemini API key
    resp = client.post("/api/settings", json={
        "api_key": "AIzaSyCustomUserKey999",
        "temperature": 0.55
    })
    assert resp.status_code == 200
    assert resp.json() == {"status": "success"}

    # Verify key was set in environment and persisted to config
    assert os.environ.get("GOOGLE_API_KEY") == "AIzaSyCustomUserKey999"
    assert get_api_key() == "AIzaSyCustomUserKey999"
    assert has_api_key() is True

    # Test status endpoint
    status_resp = client.get("/api/config/api-key")
    assert status_resp.status_code == 200
    assert status_resp.json() == {"has_key": True}


def test_test_gemini_key_endpoint_validation(mock_storage, monkeypatch):
    client = TestClient(app)

    # When no key is configured or sent
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    resp = client.post("/api/config/test-gemini-key", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert "No API key" in data["error"]
