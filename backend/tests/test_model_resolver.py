import pytest
from utils.model_resolver import resolve_model
from unittest.mock import MagicMock, patch

@pytest.fixture
def mock_storage():
    with patch("utils.model_resolver.storage") as mock:
        def side_effect(metadata, key, default=None):
            if metadata and "settings" in metadata:
                if key in metadata["settings"]:
                    return metadata["settings"][key]
            
            global_config = {
                "default_translation_model": "gemini-flash-lite-latest",
                "default_scan_model": "gemini-flash-lite-latest",
                "review_model": "gemini-2.5-pro",
            }
            mapping = {
                "translation_model": "default_translation_model",
                "scan_model": "default_scan_model",
            }
            mapped_key = mapping.get(key, key)
            return global_config.get(mapped_key, default)
            
        mock.get_project_setting.side_effect = side_effect
        yield mock


def test_resolve_model_defaults(mock_storage):
    """Test default role model resolution to Google Gemini models."""
    assert resolve_model("translation", {}, check_exhaustion=False) == "gemini-flash-lite-latest"
    assert resolve_model("scan", {}, check_exhaustion=False) == "gemini-flash-lite-latest"
    assert resolve_model("review", {}, check_exhaustion=False) == "gemini-2.5-pro"


def test_resolve_model_explicit_project_override(mock_storage):
    """Test that explicit project settings override defaults."""
    metadata = {
        "settings": {
            "translation_model": "gemini-2.5-flash"
        }
    }
    assert resolve_model("translation", metadata, check_exhaustion=False) == "gemini-2.5-flash"


def test_resolve_model_call_override(mock_storage):
    """Test model_override argument takes precedence."""
    assert resolve_model("translation", {}, model_override="gemini-custom", check_exhaustion=False) == "gemini-custom"


def test_resolve_model_fallback_on_daily_exhaustion(mock_storage, monkeypatch):
    """Test that if primary model is daily exhausted, resolve_model returns configured fallback model."""
    from utils.rate_limiter import per_model_rate_limiter
    limiter = per_model_rate_limiter.get_limiter("gemini-flash-lite-latest")
    limiter.trigger_daily_limit()

    metadata = {
        "settings": {
            "translation_model": "gemini-flash-lite-latest",
            "fallback_translation_model": "gemini-3.1-flash-lite"
        }
    }
    assert resolve_model("translation", metadata) == "gemini-3.1-flash-lite"
    limiter.clear_daily_limit()
