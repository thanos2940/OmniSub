"""
LLM Model Factory — Google Gemini models via Google GenAI SDK & Google ADK.

Standardizes all model creation and single-turn generation strictly on Google Gemini models.
Provides unified thinking token budget resolution (D2), telemetry recording (D8),
and response cleaning.
"""

import os
import re
import asyncio
import logging
from typing import Optional, Type, Dict, Any, Callable
from pydantic import BaseModel

from google.adk.models.google_llm import Gemini
from google.genai import types

logger = logging.getLogger("omnisub.llm_factory")

# Default retry config shared across all Gemini models
RETRY_CONFIG = types.HttpRetryOptions(attempts=5)


# ---------------------------------------------------------------------------
# Dynamic Chunk Sizing
# ---------------------------------------------------------------------------

def get_recommended_chunk_size(model_name: Optional[str] = None) -> int:
    """Return a safe translation chunk size (in subtitle lines) based on model architecture and context window.

    - Gemini Flash models (Flash, Flash-Lite): 450 cues (optimizing TV in <=2 calls, movies in <=5 calls).
    - Gemini Pro models: 300 cues (balancing context with latency and RPM budgets).
    - Local / small models (Ollama, LM Studio, vLLM, Llama, Qwen, Mistral, Gemma): 70 cues (prevents token blowup & truncation).
    - Default / Unknown: 300 cues.
    """
    if not model_name:
        return 300

    m = model_name.lower()

    # Local and smaller open-weight models (limited context / output tokens)
    local_patterns = ("local", "ollama", "lmstudio", "vllm", "llama", "qwen", "mistral", "gemma")
    if any(pattern in m for pattern in local_patterns):
        return 70

    # Gemini Flash models (high output tokens, fast throughput)
    if "flash" in m:
        return 450

    # Gemini Pro models
    if "pro" in m:
        return 300

    return 300


# ---------------------------------------------------------------------------
# Thinking budgets (v2 plan, D2)
# ---------------------------------------------------------------------------

# Mechanical passes don't benefit from extended thinking, but Gemini models
# think by default and bill thinking tokens as output. -1 = keep model default.
_DEFAULT_THINKING_BUDGETS: Dict[str, int] = {
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

class SafetyAwareGemini(Gemini):
    """ADK's Gemini model, sending Omnisub's Gemini safety thresholds (utils/gemini_safety.py)
    with every request. ADK builds a request's config from the agent's
    ``generate_content_config``, which Omnisub's agents leave empty, so they are added here."""

    async def generate_content_async(self, llm_request, stream: bool = False):
        try:
            from utils.gemini_safety import safety_settings
            settings = safety_settings()
            if settings:
                if llm_request.config is None:
                    llm_request.config = types.GenerateContentConfig()
                if not llm_request.config.safety_settings:
                    llm_request.config.safety_settings = settings
        except Exception as e:
            logger.debug(f"Gemini safety settings not applied: {e}")

        try:
            gen_cfg = getattr(self, "generation_config", None)
            if isinstance(gen_cfg, dict):
                if llm_request.config is None:
                    llm_request.config = types.GenerateContentConfig()
                from utils.gemini_models import refused
                for key in ("temperature", "thinking_config", "response_mime_type", "response_schema", "top_k", "top_p"):
                    if key == "thinking_config" and refused(self.model):
                        continue
                    val = gen_cfg.get(key)
                    if val is not None and getattr(llm_request.config, key, None) is None:
                        try:
                            setattr(llm_request.config, key, val)
                        except Exception:
                            pass
        except Exception as e:
            logger.debug(f"Generation config not applied: {e}")

        from utils.gemini_models import drop_rejected_thinking
        answered = False
        try:
            async for response in super().generate_content_async(llm_request, stream):
                answered = True
                yield response
        except Exception as exc:
            # The API refused the thinking setting (see utils/gemini_models.py): ask once more without it.
            if answered or not drop_rejected_thinking(self.model, llm_request.config, exc):
                raise
            async for response in super().generate_content_async(llm_request, stream):
                yield response


def create_model(
    model_name: Optional[str] = None,
    temperature: Optional[float] = None,
    top_k: Optional[int] = None,
    top_p: Optional[float] = None,
    response_schema: Optional[Type[BaseModel]] = None,
    role: Optional[str] = None,
    **kwargs: Any,
) -> Gemini:
    """
    Create an ADK-compatible Google Gemini model instance.

    Args:
        model_name: Gemini model identifier (e.g. "gemini-flash-lite-latest", "gemini-2.5-flash").
        temperature: Optional generation temperature.
        top_k: Optional top-k sampling parameter.
        top_p: Optional top-p nucleus sampling parameter.
        response_schema: Optional Pydantic model to enforce structured JSON output.
        role: Agent role (translation/review/...) used to resolve the thinking budget.

    Returns:
        An ADK Gemini model object.
    """
    if not model_name:
        from utils.storage import load_global_config
        cfg = load_global_config()
        model_name = cfg.get("default_translation_model", "gemini-flash-lite-latest")

    gen_config: Dict[str, Any] = {}
    if temperature is not None:
        gen_config["temperature"] = temperature
    if top_k is not None:
        gen_config["top_k"] = top_k
    if top_p is not None:
        gen_config["top_p"] = top_p

    if response_schema is not None:
        gen_config["response_mime_type"] = "application/json"
        gen_config["response_schema"] = response_schema

    # Thinking budget (D2): disable thinking for mechanical agent roles where the model allows it.
    from utils.gemini_models import thinking_config
    thinking = thinking_config(model_name, resolve_thinking_budget(role))
    if thinking is not None:
        gen_config["thinking_config"] = thinking

    model_kwargs: Dict[str, Any] = {"model": model_name, "retry_options": RETRY_CONFIG}
    model = SafetyAwareGemini(**model_kwargs)
    if gen_config:
        try:
            object.__setattr__(model, "generation_config", gen_config)
        except Exception:
            pass

    return model


async def generate(
    model_name: str,
    prompt: str,
    system_instruction: Optional[str] = None,
    temperature: Optional[float] = None,
    top_k: Optional[int] = None,
    top_p: Optional[float] = None,
    response_schema: Optional[Type[BaseModel]] = None,
    cache_name: Optional[str] = None,
    role: Optional[str] = None,
    thinking_budget: Optional[int] = None,
    on_blocked: Optional[Callable[[str], None]] = None,
    **kwargs: Any,
) -> str:
    """
    Unified entry point for single-turn Gemini LLM generation.

    Centralizes:
    - Route through cached_translator (optimized for Gemini SDK + rate limiting)
    - Response cleaning (strips <think> blocks)
    - Thinking-budget resolution per role (D2) and telemetry accounting (D8)
    - Omnisub's Gemini safety thresholds; ``on_blocked(reason)`` is called when a filter
      stopped the answer (the text is then empty)
    """
    from utils.llm_utils import strip_reasoning_blocks
    from services.cached_translator import generate_cached
    from utils.structured_output import use_structured_output

    mime_type = "application/json" if response_schema and use_structured_output(model_name) else None
    budget = thinking_budget if thinking_budget is not None else resolve_thinking_budget(role)
    extra = {"on_blocked": on_blocked} if on_blocked else {}

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
        **extra,
    )
    return strip_reasoning_blocks(text)
