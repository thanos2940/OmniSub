import pytest
from utils.model_resolver import resolve_model
from unittest.mock import MagicMock, patch

@pytest.fixture
def mock_storage():
    with patch("utils.model_resolver.storage") as mock:
        # Define a side effect for get_project_setting
        def side_effect(metadata, key, default=None):
            # Check project settings first
            if metadata and "settings" in metadata:
                if key in metadata["settings"]:
                    return metadata["settings"][key]
            
            # Then fallback to global config (mocked)
            global_config = {
                "ai_provider": "cloud",
                "default_translation_model": "gemini-flash-lite-latest",
                "default_scan_model": "gemini-flash-lite-latest",
                "local_translation_model": "mistral-7b",
                "local_scan_model": "gemma-2b",
                "review_model": "gemini-pro"
            }
            # Handle the specific mapping that storage.py does
            mapping = {
                "translation_model": "default_translation_model",
                "scan_model": "default_scan_model",
            }
            mapped_key = mapping.get(key, key)
            return global_config.get(mapped_key, default)
            
        mock.get_project_setting.side_effect = side_effect
        yield mock

def test_resolve_model_cloud_default(mock_storage):
    """Test cloud provider returns default cloud models."""
    metadata = {"settings": {"ai_provider": "cloud"}}
    assert resolve_model("translation", metadata, check_exhaustion=False) == "gemini-flash-lite-latest"
    assert resolve_model("scan", metadata, check_exhaustion=False) == "gemini-flash-lite-latest"

def test_resolve_model_local_default(mock_storage):
    """Test local provider returns local models."""
    metadata = {"settings": {"ai_provider": "local"}}
    assert resolve_model("translation", metadata, check_exhaustion=False) == "local/mistral-7b"
    assert resolve_model("scan", metadata, check_exhaustion=False) == "local/gemma-2b"

def test_resolve_model_hybrid_default(mock_storage):
    """Test hybrid provider uses local for translation and cloud for scan."""
    metadata = {"settings": {"ai_provider": "hybrid"}}
    assert resolve_model("translation", metadata, check_exhaustion=False) == "local/mistral-7b"
    assert resolve_model("review", metadata, check_exhaustion=False) == "gemini-pro"

def test_resolve_model_explicit_override(mock_storage):
    """Test that explicit project settings override provider defaults."""
    metadata = {
        "settings": {
            "ai_provider": "local",
            "local_translation_model": "custom-local-model"
        }
    }
    assert resolve_model("translation", metadata, check_exhaustion=False) == "local/custom-local-model"


def test_resolve_model_empty_string_fallback(mock_storage):
    """Test that empty string for cloud models falls back to default models."""
    metadata = {
        "settings": {
            "ai_provider": "cloud",
            "translation_model": ""
        }
    }
    assert resolve_model("translation", metadata, check_exhaustion=False) == "gemini-flash-lite-latest"


def test_resolve_model_fallback_on_daily_exhaustion(mock_storage, monkeypatch):
    """Test that if primary model is daily exhausted, resolve_model returns configured fallback model."""
    from utils.rate_limiter import per_model_rate_limiter
    limiter = per_model_rate_limiter.get_limiter("gemini-flash-lite-latest")
    limiter.trigger_daily_limit()

    metadata = {
        "settings": {
            "ai_provider": "cloud",
            "translation_model": "gemini-flash-lite-latest",
            "fallback_translation_model": "gemini-3.1-flash-lite"
        }
    }
    assert resolve_model("translation", metadata) == "gemini-3.1-flash-lite"
    limiter.clear_daily_limit()



