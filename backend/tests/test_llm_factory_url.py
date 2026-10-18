import pytest
from unittest.mock import patch, MagicMock
from adk_agents.llm_factory import create_model, get_recommended_chunk_size, resolve_thinking_budget, RETRY_CONFIG
from google.adk.models.google_llm import Gemini


def test_create_model_gemini_defaults():
    """Test that create_model creates a Gemini model with default retry config."""
    model = create_model("gemini-flash-lite-latest")
    assert isinstance(model, Gemini)
    assert model.model == "gemini-flash-lite-latest"


def test_create_model_with_generation_config():
    """Test create_model applies temperature, top_k, top_p, and thinking configs."""
    model = create_model(
        "gemini-2.5-flash",
        temperature=0.7,
        top_k=40,
        top_p=0.9,
        role="translation",
    )
    assert isinstance(model, Gemini)
    assert model.model == "gemini-2.5-flash"


def test_get_recommended_chunk_size():
    """Test model-aware chunk size recommendations."""
    assert get_recommended_chunk_size("gemini-flash-lite-latest") == 450
    assert get_recommended_chunk_size("gemini-2.5-flash") == 450
    assert get_recommended_chunk_size("gemini-2.5-pro") == 300
    assert get_recommended_chunk_size("local/qwen-2.5") == 70
    assert get_recommended_chunk_size(None) == 300
