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
import asyncio
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
    """Resolve and normalize the base URL for the local LLM server."""
    url = None
    if base_url:
        url = base_url
    elif os.environ.get("LOCAL_LLM_BASE_URL"):
        url = os.environ.get("LOCAL_LLM_BASE_URL")
    else:
        try:
            from utils.storage import load_global_config
            cfg = load_global_config()
            if cfg.get("local_llm_base_url"):
                url = cfg["local_llm_base_url"]
        except Exception:
            pass

    if not url:
        url = _DEFAULT_LOCAL_URL

    # Normalization: Ensure it ends with /v1 and has no trailing slash
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    
    return url


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

    # Gemma drifts/over-generates on long numbered lists regardless of parameter
    # count, so cap it small rather than sizing by params like other families.
    if "gemma" in name_lower:
        return 80

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
    "enable_thinking": False, 
}


def _is_gemma_model(model_name: str) -> bool:
    """Check if the local model is a Gemma variant."""
    return "gemma" in model_name.lower()


def _build_local_extra_body(
    model_name: str,
    temperature: Optional[float] = None,
    top_k: Optional[int] = None,
    top_p: Optional[float] = None,
    response_schema: Optional[Type[BaseModel]] = None,
) -> dict:
    """Build the OpenAI-compatible ``extra_body`` for a local model.

    Single source of truth shared by ``create_model`` (ADK Runner path) and
    ``generate`` (direct litellm path) so both apply identical sampling defaults,
    per-family overrides, and structured-output handling.
    """
    if _is_gemma_model(model_name):
        extra_body = dict(_GEMMA_GEN_DEFAULTS)
    else:
        extra_body = dict(_LOCAL_GEN_DEFAULTS)

    # Explicit overrides win over family defaults.
    if temperature is not None:
        extra_body["temperature"] = temperature
    if top_k is not None:
        extra_body["top_k"] = top_k
    if top_p is not None:
        extra_body["top_p"] = top_p

    if response_schema is not None:
        # Strict GBNF/json_schema grammars frequently conflict with local "thinking"
        # tokens, so ask for json_object mode and nudge sampling away from loops.
        extra_body["response_format"] = {"type": "json_object"}
        extra_body["repetition_penalty"] = extra_body.get("repetition_penalty", 1.0) * 1.05

    return extra_body


# ---------------------------------------------------------------------------
# Thinking budgets (v2 plan, D2)
# ---------------------------------------------------------------------------

# Mechanical passes don't benefit from extended thinking, but Gemini models
# think by default and bill thinking tokens as output. -1 = keep model default.
_DEFAULT_THINKING_BUDGETS = {
    "translation": 0,
    "repair": 0,
    "condense": 0,
    "reconciliation": 0,
    "summary": 0,
    "consistency": 0,
    "scan": 0,
    "glossary": -1,
    "context": -1,
    "review": -1,
    "research": -1,
}


def resolve_thinking_budget(role: Optional[str]) -> Optional[int]:
    """Thinking budget for a role: config override > built-in default > None.

    Returns None (model default) for unknown roles or budgets < 0.
    """
    if not role:
        return None
    budget = _DEFAULT_THINKING_BUDGETS.get(role, -1)
    try:
        from utils.storage import load_global_config
        overrides = load_global_config().get("thinking_budgets") or {}
        if role in overrides and overrides[role] is not None:
            budget = int(overrides[role])
    except Exception:
        pass
    return budget if budget >= 0 else None


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
    role: Optional[str] = None,
):
    """
    Create an ADK-compatible model instance.

    Args:
        model_name: Model identifier.
                     - "gemini-3.5-flash" → Gemini
                     - "local/mistral-7b" → LiteLlm
        base_url: Override for local LLM server URL.
        temperature: Optional generation temperature.
        response_schema: Optional Pydantic model to enforce via manual JSON Schema injection.
        role: Agent role (translation/review/…) used to resolve the thinking budget.

    Returns:
        An ADK model object (Gemini or LiteLlm).
    """
    if not model_name:
        from utils.storage import load_global_config
        cfg = load_global_config()
        model_name = cfg.get("default_translation_model", "gemini-flash-lite-latest")

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

        # Shared sampling/structured-output config (see _build_local_extra_body).
        extra_body = _build_local_extra_body(
            model_name, temperature=temperature, top_k=top_k, top_p=top_p,
            response_schema=response_schema,
        )

        return LiteLlm(
            model=f"openai/{actual_model}",
            extra_body=extra_body,
            timeout=1200,
            max_retries=0
        )
    else:
        # Standard Gemini model
        kwargs = {"model": model_name, "retry_options": RETRY_CONFIG}
        if response_schema is not None:
            gen_config["response_mime_type"] = "application/json"
            gen_config["response_schema"] = response_schema

        # Thinking budget (D2): disable thinking for mechanical agent roles.
        budget = resolve_thinking_budget(role)
        is_thinking_model = "2.5" in model_name or "3." in model_name or "thinking" in model_name.lower()
        if is_thinking_model and budget is not None and (budget > 0 or "pro" not in model_name.lower()):
            try:
                gen_config["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)
            except Exception:
                pass

        if gen_config:
            kwargs["generation_config"] = gen_config
        return Gemini(**kwargs)


async def generate(
    model_name: str,
    prompt: str,
    system_instruction: Optional[str] = None,
    temperature: Optional[float] = None,
    top_k: Optional[int] = None,
    top_p: Optional[float] = None,
    response_schema: Optional[Type[BaseModel]] = None,
    base_url: Optional[str] = None,
    cache_name: Optional[str] = None,
    role: Optional[str] = None,
    thinking_budget: Optional[int] = None,
) -> str:
    """
    Unified entry point for LLM generation — handles both Cloud (Gemini) and Local.

    Centralizes:
    - Factory dispatch (Cloud vs Local)
    - Response cleaning (strips <think> blocks)
    - Thinking-budget resolution per role (D2) and usage telemetry (D8)
    """
    from utils.llm_utils import strip_reasoning_blocks

    if is_local_model(model_name):
        # LOCAL PATH — call the OpenAI-compatible endpoint directly via litellm.
        # (ADK's LiteLlm only exposes the streaming generate_content_async(LlmRequest)
        # interface — it has NO synchronous .generate(messages); calling it that way
        # raised AttributeError and silently disabled every non-translation pass for
        # local models. A direct litellm.acompletion is the simple, correct seam.)
        try:
            import litellm
        except ImportError:
            raise ImportError(
                "litellm is required for local LLM support. "
                "Install it with: pip install litellm openai"
            )

        actual_model = model_name[len("local/"):]
        resolved_url = _resolve_local_base_url(base_url)
        extra_body = _build_local_extra_body(
            model_name, temperature=temperature, top_k=top_k, top_p=top_p,
            response_schema=response_schema,
        )

        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        resp = await litellm.acompletion(
            model=f"openai/{actual_model}",
            messages=messages,
            api_base=resolved_url,
            api_key="not-needed",
            timeout=1200,
            extra_body=extra_body,
        )
        try:
            from utils.telemetry import extract_usage, record_usage
            record_usage(model_name, role or "other", extract_usage(resp))
        except Exception:
            pass
        try:
            text = resp.choices[0].message.content or ""
        except (AttributeError, IndexError, KeyError):
            text = ""
        return strip_reasoning_blocks(text)
    else:
        # CLOUD PATH
        # We leverage the existing cached_translator which is already optimized for Gemini
        from services.cached_translator import generate_cached

        # Gemini-specific: structured output handling
        from utils.structured_output import use_structured_output
        mime_type = "application/json" if response_schema and use_structured_output(model_name) else None

        budget = thinking_budget if thinking_budget is not None else resolve_thinking_budget(role)

        text = await generate_cached(
            model=model_name,
            user_prompt=prompt,
            cache_name=cache_name,
            system_instruction=system_instruction or "",
            temperature=temperature if temperature is not None else 0.3,
            response_schema=response_schema,
            response_mime_type=mime_type,
            thinking_budget=budget,
            role=role or "other",
        )
        return strip_reasoning_blocks(text)

