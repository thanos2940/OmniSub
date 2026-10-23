"""
Direct google-genai call for the cloud path.

Used by the unified ``generate()`` seam (and the context-caching path, Plan 02).
v2 additions:
  - per-call ``thinking_budget`` (D2): Gemini 2.5+ models think by default and bill
    thinking tokens as output — mechanical passes (translation/repair/summary) set 0
    (utils/gemini_models.py decides what each model accepts);
  - usage telemetry (D8): every response's usage_metadata is recorded.
Every request carries Omnisub's Gemini safety thresholds (utils/gemini_safety.py); an answer a
filter stopped is logged with the reason and reported to ``on_blocked``.
"""

import asyncio
import logging

logger = logging.getLogger("omnisub.cached_translator")


async def generate_cached(model: str, user_prompt: str, cache_name=None,
                          system_instruction: str = "", temperature: float = 0.3,
                          response_schema = None, response_mime_type: str = None,
                          thinking_budget: int = None, role: str = "other", on_blocked=None,
                          api_key: str = None, meta: dict = None) -> str:
    """``api_key``: use this key instead of the configured one. ``meta``: filled with ``model_version`` (the
    model that answered, which tells what an alias points to) and ``thinking`` (the budget sent, or None)."""
    from google import genai
    from google.genai import types
    from utils.gemini_safety import describe_block, safety_settings
    from utils.gemini_models import drop_rejected_thinking, thinking_config

    client = genai.Client(api_key=api_key) if api_key else genai.Client()

    # Merge configs
    kwargs = {"temperature": temperature}
    if cache_name:
        kwargs["cached_content"] = cache_name
    else:
        kwargs["system_instruction"] = system_instruction or None

    if response_mime_type:
        kwargs["response_mime_type"] = response_mime_type
    if response_schema:
        kwargs["response_schema"] = response_schema

    # Thinking budget (D2): 0 disables thinking where the model allows it; None = model default.
    thinking = thinking_config(model, thinking_budget)
    if thinking is not None:
        kwargs["thinking_config"] = thinking

    settings = safety_settings()
    if settings:
        kwargs["safety_settings"] = settings

    cfg = types.GenerateContentConfig(**kwargs)

    from utils.api_call_wrapper import rate_limited_call
    from utils.rate_limiter import per_model_rate_limiter

    limiter = per_model_rate_limiter.get_limiter(model)

    async def _do_call():
        return await asyncio.to_thread(
            client.models.generate_content, model=model, contents=user_prompt, config=cfg
        )

    try:
        resp = await rate_limited_call(_do_call, rate_limiter=limiter)
    except Exception as exc:
        # A model that refuses the thinking setting (an alias that moved to a newer model, a model that
        # only works in thinking mode) is asked once more without it.
        if not drop_rejected_thinking(model, cfg, exc):
            raise
        resp = await rate_limited_call(_do_call, rate_limiter=limiter)

    # Telemetry (D8) — never let it break the call.
    try:
        from utils.telemetry import extract_usage, record_usage
        record_usage(model, role, extract_usage(resp))
    except Exception:
        pass

    if meta is not None:
        meta["model_version"] = getattr(resp, "model_version", None)
        meta["thinking"] = cfg.thinking_config.thinking_budget if cfg.thinking_config is not None else None
    text = getattr(resp, "text", "") or ""
    if not text.strip():
        reason = describe_block(resp)
        if reason:
            logger.warning(f"Gemini ({model}, {role}) answered without text: {reason}")
            if on_blocked:
                on_blocked(reason)
    return text
