import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from adk_agents.llm_factory import generate, resolve_thinking_budget, _build_local_extra_body


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
async def test_generate_local_path():
    """Local models call litellm.acompletion directly and strip reasoning."""
    msg = MagicMock()
    msg.content = "<think>internal</think>Real Answer"
    choice = MagicMock()
    choice.message = msg
    mock_resp = MagicMock()
    mock_resp.choices = [choice]
    mock_resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_ac:
        mock_ac.return_value = mock_resp

        resp = await generate("local/mistral", "Hello", system_instruction="Be helpful")

        assert resp == "Real Answer"
        mock_ac.assert_called_once()
        _, kwargs = mock_ac.call_args
        assert kwargs["model"] == "openai/mistral"
        assert kwargs["api_key"] == "not-needed"
        assert "api_base" in kwargs
        messages = kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "Be helpful"}
        assert messages[1] == {"role": "user", "content": "Hello"}


def test_litellm_surface_regression_guard():
    """ADK's LiteLlm exposes generate_content_async, NOT a sync generate(messages).
    The local path must never rely on .generate (the original silent-failure bug)."""
    from google.adk.models.lite_llm import LiteLlm
    assert hasattr(LiteLlm, "generate_content_async")
    assert not hasattr(LiteLlm, "generate")


def test_thinking_budget_defaults():
    """Mechanical roles default to 0 (off); judgment roles keep the model default."""
    assert resolve_thinking_budget("translation") == 0
    assert resolve_thinking_budget("repair") == 0
    assert resolve_thinking_budget("summary") == 0
    assert resolve_thinking_budget("review") is None
    assert resolve_thinking_budget("glossary") is None
    assert resolve_thinking_budget(None) is None


def test_build_local_extra_body():
    """Structured output forces json_object; Gemma gets its own sampling defaults;
    explicit overrides win."""
    from utils.structured_output import SceneTranslationResponse

    default = _build_local_extra_body("local/mistral-7b")
    assert "response_format" not in default

    structured = _build_local_extra_body("local/mistral-7b", response_schema=SceneTranslationResponse)
    assert structured["response_format"] == {"type": "json_object"}

    gemma = _build_local_extra_body("local/gemma-3-12b")
    assert gemma["top_k"] == 64

    override = _build_local_extra_body("local/gemma-3-12b", temperature=0.1)
    assert override["temperature"] == 0.1


def test_telemetry_extract_usage_shapes():
    """extract_usage understands both genai and openai/litellm response shapes."""
    from utils.telemetry import extract_usage

    genai_resp = MagicMock(spec=["usage_metadata"])
    genai_resp.usage_metadata = MagicMock(
        prompt_token_count=100, candidates_token_count=50,
        thoughts_token_count=25, cached_content_token_count=80,
    )
    u = extract_usage(genai_resp)
    assert u == {"prompt_tokens": 100, "output_tokens": 50,
                 "thoughts_tokens": 25, "cached_tokens": 80}

    oa_resp = MagicMock(spec=["usage"])
    oa_resp.usage = MagicMock(prompt_tokens=11, completion_tokens=7)
    u2 = extract_usage(oa_resp)
    assert u2["prompt_tokens"] == 11 and u2["output_tokens"] == 7
