"""
Direct google-genai call for the cloud path.

Used by the unified ``generate()`` seam (and the context-caching path, Plan 02).
v2 additions:
  - per-call ``thinking_budget`` (D2): Gemini 2.5 models think by default and bill
    thinking tokens as output — mechanical passes (translation/repair/summary) set 0;
  - usage telemetry (D8): every response's usage_metadata is recorded.
"""

import asyncio


def _supports_thinking(model: str) -> bool:
    """Only Gemini 2.5+ and 3.x models (or explicitly named thinking models) support thinking_config."""
    m = (model or "").lower()
    return "2.5" in m or "3." in m or "thinking" in m


def _supports_thinking_budget_zero(model: str) -> bool:
    """gemini-2.5-pro cannot fully disable thinking; flash/flash-lite can."""
    return "pro" not in (model or "").lower()


async def generate_cached(model: str, user_prompt: str, cache_name=None,
                          system_instruction: str = "", temperature: float = 0.3,
                          response_schema = None, response_mime_type: str = None,
                          thinking_budget: int = None, role: str = "other") -> str:
    from google import genai
    from google.genai import types

    client = genai.Client()

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

    # Thinking budget (D2): 0 disables thinking on flash/flash-lite; None = model default.
    if _supports_thinking(model) and thinking_budget is not None and thinking_budget >= 0:
        if thinking_budget > 0 or _supports_thinking_budget_zero(model):
            try:
                kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)
            except Exception:
                pass  # older SDK — silently keep model default

    cfg = types.GenerateContentConfig(**kwargs)

    from utils.api_call_wrapper import rate_limited_call
    from utils.rate_limiter import per_model_rate_limiter

    limiter = per_model_rate_limiter.get_limiter(model)

    async def _do_call():
        return await asyncio.to_thread(
            client.models.generate_content, model=model, contents=user_prompt, config=cfg
        )

    resp = await rate_limited_call(_do_call, rate_limiter=limiter)

    # Telemetry (D8) — never let it break the call.
    try:
        from utils.telemetry import extract_usage, record_usage
        record_usage(model, role, extract_usage(resp))
    except Exception:
        pass

    return getattr(resp, "text", "") or ""
