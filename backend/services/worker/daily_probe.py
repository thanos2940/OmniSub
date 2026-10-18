"""
Daily Rate Limit Recovery and Probe Coordination for Queue Workers.
"""

import asyncio
import logging

from utils.rate_limiter import per_model_rate_limiter

logger = logging.getLogger(__name__)


async def recover_daily_limits(queue) -> None:
    """Recover daily-exhausted models and unpause eligible items in the translation queue.
    
    1. Actively probe the API for any model whose recheck window is due (lifts the latch
       early if quota came back before the local midnight boundary).
    2. maybe_reset_daily() lifts the latch at the local reset boundary even when no acquire() runs.
    """
    try:
        from utils.daily_limit_probe import recheck_daily_limits
        await asyncio.wait_for(recheck_daily_limits(force=False), timeout=30.0)
    except asyncio.TimeoutError:
        logger.warning("Auto daily-limit recheck timed out; will retry next loop.")
    except Exception as probe_err:
        logger.warning(f"Auto daily-limit recheck failed: {probe_err}")

    def _is_exhausted(m: str) -> bool:
        return per_model_rate_limiter.get_limiter(m).maybe_reset_daily()

    queue.unpause_daily_limit_items(_is_exhausted)
