"""
LLM Model Factory — Gemini or Local LLM via OpenAI-compatible endpoints

Routes model creation based on a naming convention:
- "local/model-name"  → LiteLlm pointing at an OpenAI-compatible server
- anything else        → Google Gemini (ADK native)

The local endpoint URL is resolved from (highest priority first):
1. Explicit `base_url` parameter
2. Environment variable LOCAL_LLM_BASE_URL
3. Global config.json → local_llm_base_url
4. Default: http://localhost:1234/v1  (LM Studio default)
"""

import os
from typing import Optional

from google.adk.models.google_llm import Gemini
from google.genai import types

# Default retry config shared across all models
RETRY_CONFIG = types.HttpRetryOptions(attempts=5)

# LM Studio default
_DEFAULT_LOCAL_URL = "http://localhost:1234/v1"


def _resolve_local_base_url(base_url: Optional[str] = None) -> str:
    """Resolve the base URL for the local LLM server."""
    if base_url:
        return base_url

    env_url = os.environ.get("LOCAL_LLM_BASE_URL")
    if env_url:
        return env_url

    # Try global config
    try:
        from utils.storage import load_global_config
        cfg = load_global_config()
        if cfg.get("local_llm_base_url"):
            return cfg["local_llm_base_url"]
    except Exception:
        pass

    return _DEFAULT_LOCAL_URL


def is_local_model(model_name: str) -> bool:
    """Check whether a model name refers to a local LLM."""
    return model_name.startswith("local/")


def create_model(
    model_name: str,
    base_url: Optional[str] = None,
    temperature: Optional[float] = None,
):
    """
    Create an ADK-compatible model instance.

    Args:
        model_name: Model identifier.
                     - "gemini-2.5-flash" → Gemini
                     - "local/mistral-7b" → LiteLlm
        base_url: Override for local LLM server URL.
        temperature: Optional generation temperature.

    Returns:
        An ADK model object (Gemini or LiteLlm).
    """
    gen_config = {}
    if temperature is not None:
        gen_config["temperature"] = temperature

    if is_local_model(model_name):
        # Strip the "local/" prefix to get the actual model name
        actual_model = model_name[len("local/"):]
        resolved_url = _resolve_local_base_url(base_url)

        try:
            from google.adk.models.lite_llm import LiteLlm
            import litellm
            
            # Critical fix for local models (like translategemma) that 
            # don't support a 'system' role as the first message.
            litellm.push_system_message_to_user_message = True
        except ImportError:
            raise ImportError(
                "litellm is required for local LLM support. "
                "Install it with: pip install litellm openai"
            )

        # LiteLLM uses "openai/<model>" for OpenAI-compatible endpoints
        # Set the base URL via environment variable (LiteLLM convention)
        os.environ["OPENAI_API_BASE"] = resolved_url
        os.environ["OPENAI_API_KEY"] = "not-needed"

        return LiteLlm(
            model=f"openai/{actual_model}",
            **({"generation_config": gen_config} if gen_config else {}),
        )
    else:
        # Standard Gemini model
        kwargs = {"model": model_name, "retry_options": RETRY_CONFIG}
        if gen_config:
            kwargs["generation_config"] = gen_config
        return Gemini(**kwargs)
