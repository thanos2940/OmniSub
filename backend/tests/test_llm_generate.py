import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from adk_agents.llm_factory import generate, resolve_thinking_budget


@pytest.mark.asyncio
async def test_generate_cloud_path():
    """Cloud models route through generate_cached with role/thinking plumbed."""
    with patch("services.cached_translator.generate_cached", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = "Cloud Response"

        resp = await generate("gemini-3.1-flash-lite", "Hello", role="translation")

        assert resp == "Cloud Response"
        mock_gen.assert_called_once()
        _, kwargs = mock_gen.call_args
        assert kwargs["user_prompt"] == "Hello"
        assert kwargs["model"] == "gemini-3.1-flash-lite"
        # D2: mechanical role -> thinking disabled.
        assert kwargs["thinking_budget"] == 0
        assert kwargs["role"] == "translation"


@pytest.mark.asyncio
async def test_generate_strips_reasoning():
    """Generate strips thinking blocks from the output."""
    with patch("services.cached_translator.generate_cached", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = "<think>internal thoughts</think>Final Output"

        resp = await generate("gemini-2.5-flash", "Hello", role="translation")
        assert resp == "Final Output"


def test_thinking_budget_defaults():
    """Mechanical roles default to 0 (off); judgment roles keep the model default."""
    assert resolve_thinking_budget("translation") == 0
    assert resolve_thinking_budget("repair") == 0
    assert resolve_thinking_budget("summary") == 0
    assert resolve_thinking_budget("review") is None
    assert resolve_thinking_budget("glossary") is None
    assert resolve_thinking_budget(None) is None


def test_telemetry_extract_usage_shapes():
    """extract_usage extracts token counts from genai response shape."""
    from utils.telemetry import extract_usage

    genai_resp = MagicMock(spec=["usage_metadata"])
    genai_resp.usage_metadata = MagicMock(
        prompt_token_count=100, candidates_token_count=50,
        thoughts_token_count=25, cached_content_token_count=80,
    )
    u = extract_usage(genai_resp)
    assert u == {"prompt_tokens": 100, "output_tokens": 50,
                 "thoughts_tokens": 25, "cached_tokens": 80}
