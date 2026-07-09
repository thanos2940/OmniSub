import pytest
from routers.schemas import SettingsRequest

def test_settings_request_ai_provider_defaults():
    """Test that ai_provider and local model fields have correct defaults."""
    req = SettingsRequest()
    assert req.ai_provider == "cloud"
    assert req.local_llm_base_url == "http://localhost:11434"
    assert req.local_translation_model is None
    assert req.local_scan_model is None

def test_settings_request_validation():
    """Test that settings request validates correctly with provided local fields."""
    data = {
        "ai_provider": "local",
        "local_llm_base_url": "http://localhost:8080/v1",
        "local_translation_model": "mistral-7b-v0.1",
        "local_scan_model": "gemma-7b"
    }
    req = SettingsRequest(**data)
    assert req.ai_provider == "local"
    assert req.local_llm_base_url == "http://localhost:8080/v1"
    assert req.local_translation_model == "mistral-7b-v0.1"
    assert req.local_scan_model == "gemma-7b"
