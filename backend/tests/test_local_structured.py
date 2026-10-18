import pytest
from unittest.mock import patch
from utils.structured_output import use_structured_output, use_source_echo, parse_structured, SceneTranslationResponse


def test_use_structured_output_enabled_by_default():
    """Test structured output is enabled for Gemini models."""
    mock_config = {"structured_output_enabled": True}
    with patch("utils.storage.load_global_config", return_value=mock_config):
        assert use_structured_output("gemini-2.5-flash") is True
        assert use_structured_output("gemini-flash-lite-latest") is True


def test_use_structured_output_disabled():
    """Test structured output can be globally disabled."""
    mock_config = {"structured_output_enabled": False}
    with patch("utils.storage.load_global_config", return_value=mock_config):
        assert use_structured_output("gemini-2.5-flash") is False


def test_use_source_echo_setting():
    """Test source-echo guard depends on structured output and setting."""
    mock_config = {"structured_output_enabled": True, "source_echo_enabled": True}
    with patch("utils.storage.load_global_config", return_value=mock_config):
        assert use_source_echo("gemini-2.5-flash") is True

    mock_config_off = {"structured_output_enabled": True, "source_echo_enabled": False}
    with patch("utils.storage.load_global_config", return_value=mock_config_off):
        assert use_source_echo("gemini-2.5-flash") is False


def test_parse_structured_json():
    """Test parsing structured output JSON responses."""
    json_text = '{"lines": [{"i": 1, "t": "Γεια σας"}, {"i": 2, "t": "Κόσμε"}]}'
    res = parse_structured(json_text)
    assert len(res) == 2
    assert res[0]["index"] == 1
    assert res[0]["text"] == "Γεια σας"
    assert res[1]["index"] == 2
    assert res[1]["text"] == "Κόσμε"
