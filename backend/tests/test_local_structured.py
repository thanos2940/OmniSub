import pytest
from unittest.mock import MagicMock, patch
from utils.structured_output import use_structured_output

def test_use_structured_output_local_default():
    """Test that structured output is default-off for local models."""
    mock_config = {"structured_output_enabled": True}
    with patch("utils.storage.load_global_config", return_value=mock_config):
        assert use_structured_output("local/mistral") is False

def test_use_structured_output_local_opt_in():
    """Test that structured output can be opted-in for local models."""
    mock_config = {
        "structured_output_enabled": True,
        "experimental_local_structured_output": True
    }
    with patch("utils.storage.load_global_config", return_value=mock_config):
        # Should now be True even for local
        assert use_structured_output("local/mistral") is True
        
def test_use_structured_output_gemma_override():
    """Test that Gemma still defaults to off unless explicitly forced."""
    mock_config = {
        "structured_output_enabled": True,
        "experimental_local_structured_output": True
    }
    with patch("utils.storage.load_global_config", return_value=mock_config):
        # Gemma is notoriously bad at JSON schemas in some engines, 
        # but if the user opted-in, we should probably allow it to test.
        assert use_structured_output("local/gemma-2b") is True
