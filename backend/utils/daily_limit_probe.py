"""
Response-driven daily-limit probe.

When a model's daily quota is latched as exhausted, we don't guess when it lifts —
we ask the API directly with the cheapest possible request and read the answer:

- success                  -> quota is available again; clear the latch
- per-MINUTE 429           -> daily quota is NOT the problem (it would return a
                              per-day error otherwise) -> treat as available
- per-DAY 429              -> still exhausted; keep the latch
- anything else (auth/net) -> inconclusive; keep the latch, report the reason

The probe deliberately does NOT go through the rate limiter's ``acquire`` (which
would short-circuit on the very latch we're testing).
"""

import asyncio
import logging
from typing import Dict

from utils.api_call_wrapper import is_daily_quota_error

logger = logging.getLogger(__name__)


async def probe_model_quota(model_name: str) -> Dict:
    """Make one minimal request to ``model_name`` and classify the outcome.

    Returns ``{"model", "available": bool, "reason": str}``.
    Local models are always reported available (they have no daily quota).
    """
    if not model_name or model_name.startswith("local/"):
        return {"model": model_name, "available": True, "reason": "local model — no daily quota"}

    def _call():
        from google import genai
        from google.genai import types
        client = genai.Client()
        cfg = types.GenerateContentConfig(max_output_tokens=1, temperature=0.0)
        return client.models.generate_content(model=model_name, contents="ping", config=cfg)

    try:
        await asyncio.to_thread(_call)
        return {"model": model_name, "available": True, "reason": "probe succeeded"}
    except Exception as e:
        msg = str(e)
        is_rate_limit = (
            "ResourceExhausted" in type(e).__name__
            or "TooManyRequests" in type(e).__name__
            or "429" in msg
            or "resource_exhausted" in msg.lower()
            or "quota" in msg.lower()
        )
        if is_rate_limit and is_daily_quota_error(e):
            return {"model": model_name, "available": False, "reason": "daily quota still exhausted"}
        if is_rate_limit:
            # A per-minute throttle means the daily bucket has room again.
            return {"model": model_name, "available": True, "reason": "per-minute throttle only — daily quota available"}
        # Inconclusive (network/auth/server). Don't lift the latch on noise.
        logger.warning(f"Daily-limit probe for {model_name} was inconclusive: {type(e).__name__}: {e}")
        return {"model": model_name, "available": False, "reason": f"probe inconclusive: {type(e).__name__}"}


async def recheck_daily_limits(force: bool = False) -> Dict:
    """Probe daily-exhausted models and lift any whose quota is back.

    ``force=True`` (manual "check now") probes every exhausted model immediately;
    otherwise only those whose scheduled probe window is due are checked. Models
    that pass have their latch cleared; models that fail have their next automatic
    probe deferred. Returns ``{"results": [...], "cleared": [model, ...]}``.
    """
    from utils.rate_limiter import per_model_rate_limiter

    results = []
    cleared = []
    for model in per_model_rate_limiter.exhausted_models():
        limiter = per_model_rate_limiter.get_limiter(model)
        if not force and not limiter.should_probe_daily():
            continue
        result = await probe_model_quota(model)
        if result["available"]:
            limiter.clear_daily_limit()
            cleared.append(model)
            logger.info(f"Daily limit lifted for {model} ({result['reason']}). Resuming work.")
        else:
            limiter.defer_probe()
        results.append(result)

    return {"results": results, "cleared": cleared}
