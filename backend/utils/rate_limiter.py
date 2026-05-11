import asyncio
import time
from dataclasses import dataclass, field
from typing import List, Dict

class DailyLimitExhausted(Exception):
    """Raised when the daily API request limit has been reached."""
    def __init__(self, daily_count, daily_limit, reset_time=None):
        self.daily_count = daily_count
        self.daily_limit = daily_limit
        self.reset_time = reset_time
        super().__init__(f"Daily limit exhausted: {daily_count}/{daily_limit}")

@dataclass
class RateLimiter:
    requests_per_minute: int = 15     # Conservative default (free tier)
    daily_limit: int = 1500           # RPD limit
    
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _daily_count: int = field(init=False, default=0)
    _daily_reset: float = field(init=False)
    _lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock)
    _backoff_until: float = field(init=False, default=0.0)

    def __post_init__(self):
        self._tokens = self.requests_per_minute
        self._last_refill = time.monotonic()
        self._daily_count = 0
        # Reset daily count every 24 hours (simplified)
        self._daily_reset = time.monotonic() + 86400
        self._daily_exhausted = False

    async def acquire(self):
        """Acquire a token, waiting if necessary."""
        async with self._lock:
            # Check daily reset
            now = time.monotonic()
            if now >= self._daily_reset:
                self._daily_count = 0
                self._daily_exhausted = False
                self._daily_reset = now + 86400

            # Check daily limit
            if self._daily_exhausted or self._daily_count >= self.daily_limit:
                self._daily_exhausted = True # Ensure it stays latched
                raise DailyLimitExhausted(self._daily_count, self.daily_limit, self._daily_reset)

            # Wait for backoff if active
            if now < self._backoff_until:
                wait_time = self._backoff_until - now
                await asyncio.sleep(wait_time)
                now = time.monotonic()

            # Refill tokens
            elapsed = now - self._last_refill
            refill_amount = elapsed * (self.requests_per_minute / 60.0)
            self._tokens = min(self.requests_per_minute, self._tokens + refill_amount)
            self._last_refill = now

            # If no tokens, wait
            if self._tokens < 1.0:
                wait_time = (1.0 - self._tokens) / (self.requests_per_minute / 60.0)
                await asyncio.sleep(wait_time)
                self._tokens = 0.0 # Just used the one we waited for
                self._last_refill = time.monotonic()
            else:
                self._tokens -= 1.0

            self._daily_count += 1

    def report_rate_limit(self, retry_after: float = None):
        """Called when a 429 is received."""
        now = time.monotonic()
        delay = retry_after if retry_after is not None else 60.0
        self._backoff_until = now + delay
        # Temporarily halve tokens to slow down proactively
        self._tokens = self._tokens * 0.5

    def trigger_daily_limit(self):
        """Force the daily limit to be reached immediately."""
        self._daily_exhausted = True
        self._daily_count = max(self._daily_count, self.daily_limit)

    def estimate_requests(self, episode_names: List[str], avg_scenes_per_episode: float = 8) -> Dict:
        """Estimate usage for a list of episodes."""
        total_requests = int(len(episode_names) * avg_scenes_per_episode)
        remaining_daily = self.daily_limit - self._daily_count
        exceeds_daily = total_requests > remaining_daily
        
        estimated_minutes = total_requests / self.requests_per_minute
        days_needed = (total_requests // self.daily_limit) + 1
        
        return {
            "total_requests": total_requests,
            "estimated_minutes": estimated_minutes,
            "exceeds_daily": exceeds_daily,
            "days_needed": days_needed,
            "remaining_daily": remaining_daily
        }

    def estimate_requests_from_scenes(self, total_scenes: int) -> Dict:
        """Estimate usage for a known number of scenes."""
        remaining_daily = self.daily_limit - self._daily_count
        exceeds_daily = total_scenes > remaining_daily
        
        estimated_minutes = total_scenes / self.requests_per_minute
        
        return {
            "total_requests": total_scenes,
            "estimated_minutes": estimated_minutes,
            "exceeds_daily": exceeds_daily,
            "remaining_daily": remaining_daily
        }

    def get_stats(self) -> Dict:
        return {
            "daily_count": self._daily_count,
            "daily_limit": self.daily_limit,
            "tokens_available": round(self._tokens, 2),
            "is_backing_off": time.monotonic() < self._backoff_until,
            "backoff_remaining": max(0, round(self._backoff_until - time.monotonic(), 1))
        }

# Presets
GEMINI_FREE_TIER = RateLimiter(requests_per_minute=15, daily_limit=1500)
GEMINI_PAY_AS_YOU_GO = RateLimiter(requests_per_minute=1000, daily_limit=50000)
