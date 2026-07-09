import pytest
import os
from unittest.mock import patch, MagicMock
from adk_agents.llm_factory import _resolve_local_base_url

def test_resolve_local_base_url_normalization():
    """Test that local LLM base URLs are normalized to include /v1."""
    # Test cases: (input, expected_normalized)
    test_cases = [
        # Bare hosts
        ("http://localhost:8080", "http://localhost:8080/v1"),
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434/v1"),
        ("https://my-llm-server.com", "https://my-llm-server.com/v1"),
        
        # Hosts with trailing slash
        ("http://localhost:8080/", "http://localhost:8080/v1"),
        
        # Hosts already having /v1
        ("http://localhost:1234/v1", "http://localhost:1234/v1"),
        ("http://localhost:1234/v1/", "http://localhost:1234/v1"),
        
        # LM Studio / Ollama styles
        ("http://localhost:1234", "http://localhost:1234/v1"),
        ("http://localhost:11434", "http://localhost:11434/v1"),
    ]
    
    for input_url, expected in test_cases:
        assert _resolve_local_base_url(input_url) == expected

def test_resolve_local_base_url_env_normalization():
    """Test that URLs from environment variables are also normalized."""
    with patch.dict(os.environ, {"LOCAL_LLM_BASE_URL": "http://localhost:8080"}):
        # We pass None to ensure it looks at env
        assert _resolve_local_base_url(None) == "http://localhost:8080/v1"

def test_resolve_local_base_url_config_normalization():
    """Test that URLs from config are normalized."""
    mock_config = {"local_llm_base_url": "http://localhost:11434"}
    
    with patch("adk_agents.llm_factory.os.environ", {}), \
         patch("utils.storage.load_global_config", return_value=mock_config):
        assert _resolve_local_base_url(None) == "http://localhost:11434/v1"
