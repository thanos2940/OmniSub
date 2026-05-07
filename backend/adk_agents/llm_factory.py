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
from typing import Optional, Type
from pydantic import BaseModel

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
        # match = re.search(r'(\d+(?:\.\d+)?)\s*-?\s*b(?:\b|it|q)', name_lower)
        # if match and float(match.group(1)) >= 26:
        #     return 350
        # elif match and float(match.group(1)) >= 14:
        return 80
        # else:
        #     return 80  # translategemma-4b and similar small models

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
_GEMMA_GEN_DEFAULTS = {
    "temperature": 0.4,       # Gemma over-generates at 0.3+
    "repetition_penalty": 1.1,
    "top_k": 64,              # Gemma's own recommended default
    "top_p": 0.90,
    "enable_thinking": True, 
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
    top_k: Optional[int] = None,
    top_p: Optional[float] = None,
    response_schema: Optional[Type[BaseModel]] = None,
):
    """
    Create an ADK-compatible model instance.

    Args:
        model_name: Model identifier.
                     - "gemini-2.5-flash" → Gemini
                     - "local/mistral-7b" → LiteLlm
        base_url: Override for local LLM server URL.
        temperature: Optional generation temperature.
        response_schema: Optional Pydantic model to enforce via manual JSON Schema injection.

    Returns:
        An ADK model object (Gemini or LiteLlm).
    """
    os.environ["LITELLM_LOG"] = "INFO"

    gen_config = {}
    if temperature is not None: gen_config["temperature"] = temperature
    if top_k is not None: gen_config["top_k"] = top_k
    if top_p is not None: gen_config["top_p"] = top_p

    if is_local_model(model_name):
        actual_model = model_name[len("local/"):]
        resolved_url = _resolve_local_base_url(base_url)

        try:
            from google.adk.models.lite_llm import LiteLlm
            import litellm
        except ImportError:
            raise ImportError(
                "litellm is required for local LLM support. "
                "Install it with: pip install litellm openai"
            )

        # LiteLLM uses "openai/<model>" for OpenAI-compatible endpoints
        os.environ["OPENAI_API_BASE"] = resolved_url
        os.environ["OPENAI_API_KEY"] = "not-needed"

        # Applying model-specific defaults, but parameters passed in override them.
        if _is_gemma_model(model_name):
            extra_body = dict(_GEMMA_GEN_DEFAULTS)
        else:
            extra_body = dict(_LOCAL_GEN_DEFAULTS)
        
        # EXPLICIT OVERRIDES WIN
        if temperature is not None: extra_body["temperature"] = temperature
        if top_k is not None: extra_body["top_k"] = top_k
        if top_p is not None: extra_body["top_p"] = top_p
                
        if response_schema is not None:
            # We disable strict BNF grammars (json_schema) for local models
            # because they frequently conflict with 'Internal Thinking' tokens.
            extra_body["response_format"] = {"type": "json_object"}
            # Help prevent stuck sampling loops by slightly increasing penalty
            extra_body["repetition_penalty"] = extra_body.get("repetition_penalty", 1.0) * 1.05

        return LiteLlm(
            model=f"openai/{actual_model}",
            extra_body=extra_body,
            timeout=1200,    
            max_retries=0    
        )
    else:
        # Standard Gemini model
        kwargs = {"model": model_name, "retry_options": RETRY_CONFIG}
        if gen_config:
            kwargs["generation_config"] = gen_config
        return Gemini(**kwargs)
