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
import re
from typing import Optional

from google.adk.models.google_llm import Gemini
from google.genai import types

# Default retry config shared across all models
RETRY_CONFIG = types.HttpRetryOptions(attempts=5)

# LM Studio default
_DEFAULT_LOCAL_URL = "http://localhost:1234/v1"


# ---------------------------------------------------------------------------
# URL Resolution
# ---------------------------------------------------------------------------

def _resolve_local_base_url(base_url: Optional[str] = None) -> str:
    """Resolve the base URL for the local LLM server."""
    if base_url:
        return base_url

    env_url = os.environ.get("LOCAL_LLM_BASE_URL")
    if env_url:
        return env_url

    try:
        from utils.storage import load_global_config
        cfg = load_global_config()
        if cfg.get("local_llm_base_url"):
            return cfg["local_llm_base_url"]
    except Exception:
        pass

    return _DEFAULT_LOCAL_URL


# ---------------------------------------------------------------------------
# Model Classification
# ---------------------------------------------------------------------------

def is_local_model(model_name: str) -> bool:
    """Check whether a model name refers to a local LLM."""
    return model_name.startswith("local/")


# ---------------------------------------------------------------------------
# Feature 4: Dynamic Chunk Sizing
# ---------------------------------------------------------------------------

def get_recommended_chunk_size(model_name: str) -> int:
    """Return a safe translation chunk size (in subtitle lines) for this model."""
    if not is_local_model(model_name):
        return 300

    name_lower = model_name.lower()

    # Gemma 26B+ has a 128K context window — use much larger chunks
    if "gemma" in name_lower:
        match = re.search(r'(\d+(?:\.\d+)?)\s*-?\s*b(?:\b|it|q)', name_lower)
        if match and float(match.group(1)) >= 26:
            return 350
        elif match and float(match.group(1)) >= 14:
            return 200
        else:
            return 80  # translategemma-4b and similar small models

    match = re.search(r'(\d+(?:\.\d+)?)\s*-?\s*b(?:\b|it|q)', name_lower)
    if match:
        params_b = float(match.group(1))
        if params_b <= 4:
            return 80
        elif params_b <= 9:
            return 120
        elif params_b <= 13:
            return 160
        elif params_b <= 26:
            return 200
        else:
            return 300

    if any(k in name_lower for k in ("mini", "lite", "tiny", "small")):
        return 80
    if any(k in name_lower for k in ("large", "70b", "72b", "65b")):
        return 300

    return 150


# ---------------------------------------------------------------------------
# Feature 2: Generation Parameter Defaults for Local Models
# ---------------------------------------------------------------------------

_LOCAL_GEN_DEFAULTS = {
    # Reduces repetition significantly for local models that loop phrases
    "repetition_penalty": 1.1,
    # Nucleus sampling — keeps output diverse but coherent
    "top_p": 0.9,
    # Top-K sampling — limits vocabulary to most likely tokens
    "top_k": 40,
}

# Gemma-specific override: tuned for its architecture and vocab distribution
# Also disables Gemma 4's built-in thinking mode (wastes tokens on translation)
_GEMMA_GEN_DEFAULTS = {
    "temperature": 0.1,       # Gemma over-generates at 0.3+
    "repetition_penalty": 1.15,
    "top_k": 64,              # Gemma's own recommended default
    "top_p": 0.95,
    "enable_thinking": False, # Disable Gemma 4's thinking mode for speed
}


def _is_gemma_model(model_name: str) -> bool:
    """Check if the local model is a Gemma variant."""
    return "gemma" in model_name.lower()


# ---------------------------------------------------------------------------
# Model Factory
# ---------------------------------------------------------------------------

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
        os.environ["OPENAI_API_BASE"] = resolved_url
        os.environ["OPENAI_API_KEY"] = "not-needed"

        # Feature 2: Apply model-specific generation defaults
        # Gemma gets its own tuned profile; other local models use generic defaults
        if _is_gemma_model(model_name):
            extra_body = dict(_GEMMA_GEN_DEFAULTS)
            if temperature is not None:
                extra_body["temperature"] = temperature  # explicit override wins
        else:
            extra_body = dict(_LOCAL_GEN_DEFAULTS)
            if temperature is not None:
                extra_body["temperature"] = temperature

        return LiteLlm(
            model=f"openai/{actual_model}",
            extra_body=extra_body,
        )
    else:
        # Standard Gemini model — gen_config only populated if temperature given
        kwargs = {"model": model_name, "retry_options": RETRY_CONFIG}
        if gen_config:
            kwargs["generation_config"] = gen_config
        return Gemini(**kwargs)
