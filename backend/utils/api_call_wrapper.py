import asyncio
import logging
from typing import Any, Callable, Awaitable
import google.api_core.exceptions
import re

logger = logging.getLogger(__name__)

def parse_retry_after(exception: Exception) -> float:
    """
    Attempt to extract retry-after time from Gemini exception message or headers.
    """
    message = str(exception)
    # Search for "retry after X seconds" or similar patterns
    match = re.search(r"retry after (\d+)\.?\d* seconds", message, re.IGNORECASE)
    if match:
        return float(match.group(1))
    
    # Try looking for retryDelay or retry in seconds patterns in JSON-like strings
    match_delay = re.search(r"retryDelay':\s*'(\d+)s'", message, re.IGNORECASE)
    if match_delay:
        return float(match_delay.group(1))

    match_retry_in = re.search(r"retry in (\d+)\.?\d*s", message, re.IGNORECASE)
    if match_retry_in:
        return float(match_retry_in.group(1))
    
    # Default to 60s if we know it's a rate limit but can't find a time
    return 60.0

async def rate_limited_call(
    coro_factory: Callable[[], Awaitable[Any]],
    rate_limiter: Any,
    max_retries: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
) -> Any:
    """
    Wraps an API call with proactive throttling and reactive backoff.
    """
    last_exception = None
    
    for attempt in range(max_retries + 1):
        await rate_limiter.acquire()
        
        try:
            return await coro_factory()
        
        except (google.api_core.exceptions.ResourceExhausted, 
                google.api_core.exceptions.TooManyRequests) as e:
            last_exception = e
            retry_after = parse_retry_after(e)
            
            logger.warning(f"Rate limit hit (429). Attempt {attempt + 1}/{max_retries + 1}. Retrying after {retry_after}s.")
            rate_limiter.report_rate_limit(retry_after)
            
            if attempt == max_retries:
                logger.error("Max retries reached for rate limit.")
                raise
            
            # Exponential backoff
            delay = min(base_delay * (2 ** attempt), max_delay)
            # Ensure we wait at least as long as the API suggested
            delay = max(delay, retry_after)
            await asyncio.sleep(delay)
            
        except (google.api_core.exceptions.ServiceUnavailable,
                google.api_core.exceptions.DeadlineExceeded) as e:
            # Transient 503 or 504 errors
            last_exception = e
            logger.warning(f"Transient error ({type(e).__name__}). Attempt {attempt + 1}/{max_retries + 1}. Retrying...")
            
            if attempt == max_retries:
                logger.error(f"Max retries reached for transient error: {e}")
                raise
            
            delay = min(base_delay * (2 ** attempt), max_delay)
            await asyncio.sleep(delay)
            
        except Exception as e:
            # Check for rate limit indicators in the exception type or message
            # This catches internal types like _ResourceExhaustedError or gRPC variants
            e_name = type(e).__name__
            msg = str(e).lower()
            
            is_rate_limit = (
                "ResourceExhausted" in e_name or 
                "TooManyRequests" in e_name or
                "429" in msg or
                "resource_exhausted" in msg or
                "quota exceeded" in msg
            )
            
            if is_rate_limit:
                last_exception = e
                retry_after = parse_retry_after(e)
                
                # Critical: Detect if this is a DAILY limit hit
                if any(x in msg for x in ["per day", "daily", "perday", "limit: 500"]):
                    logger.error("Daily API quota exceeded. Triggering global halt.")
                    rate_limiter.trigger_daily_limit()
                    from utils.rate_limiter import DailyLimitExhausted
                    raise DailyLimitExhausted(rate_limiter._daily_count, rate_limiter.daily_limit)
                
                logger.warning(f"Throttling (caught as {e_name}). Attempt {attempt + 1}/{max_retries + 1}. Retrying...")
                rate_limiter.report_rate_limit(retry_after)
                
                if attempt == max_retries:
                    raise
                
                delay = min(base_delay * (2 ** attempt), max_delay)
                delay = max(delay, retry_after)
                await asyncio.sleep(delay)
                continue

            # Other errors (400, 401, 500 etc) should probably fail immediately
            logger.error(f"Non-retryable error in rate_limited_call: {e_name}: {e}")
            raise

    if last_exception:
        raise last_exception
