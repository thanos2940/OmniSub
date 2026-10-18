import pytest
from routers.schemas import SettingsRequest


def test_settings_request_cloud_defaults():
    """Test that SettingsRequest has correct Gemini defaults."""
    req = SettingsRequest()
    assert req.default_translation_model == "gemini-flash-lite-latest"
    assert req.fallback_translation_model == "gemini-3.1-flash-lite"
    assert req.default_scan_model == "gemini-flash-lite-latest"
    assert req.default_context_model == "gemini-flash-lite-latest"
    assert req.default_glossary_model == "gemini-flash-lite-latest"
    assert req.default_target_language == "Greek"


def test_settings_request_validation():
    """Test that settings request validates correctly with custom model fields."""
    data = {
        "default_translation_model": "gemini-2.5-flash",
        "default_target_language": "English",
        "temperature": 0.5,
    }
    req = SettingsRequest(**data)
    assert req.default_translation_model == "gemini-2.5-flash"
    assert req.default_target_language == "English"
    assert req.temperature == 0.5
