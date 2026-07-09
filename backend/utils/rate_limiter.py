import asyncio
import time
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional, Callable
import contextvars
from datetime import datetime, time as datetime_time, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Task-local active model name
active_model_var = contextvars.ContextVar("active_model", default="default")

def _calculate_next_daily_reset(now_ts: float) -> float:
    try:
        tz = ZoneInfo("America/Los_Angeles")
    except Exception:
        # If timezone info is missing (e.g. bare Windows without tzdata), use rolling 24 hour fallback
        return now_ts + 86400
    try:
        dt = datetime.fromtimestamp(now_ts, tz)
        tomorrow = dt.date() + timedelta(days=1)
        next_midnight = datetime.combine(tomorrow, datetime_time.min, tzinfo=tz)
        return next_midnight.timestamp()
    except Exception:
        return now_ts + 86400

def _daily_recheck_seconds() -> float:
    """How long to wait between automatic probes of a daily-exhausted model.

    Driven by global config ``daily_limit_recheck_minutes`` (default 30).
    Clamped to a 1-minute floor so a misconfigured value can't hammer the API.
    """
    try:
        from utils.storage import load_global_config
        minutes = load_global_config().get("daily_limit_recheck_minutes", 30)
        return max(60.0, float(minutes) * 60.0)
    except Exception:
        return 1800.0


class DailyLimitExhausted(Exception):
    """Raised when the daily API request limit has been reached."""
    def __init__(self, daily_count, daily_limit, reset_time=None):
        self.daily_count = daily_count
        self.daily_limit = daily_limit
        self.reset_time = reset_time
        super().__init__(f"Daily limit exhausted: {daily_count}/{daily_limit}")

class RateLimiter:
    def __init__(self, requests_per_minute: int = 15, daily_limit: int = 1500, on_change: Optional[Callable[[], None]] = None, model_name: str = "default"):
        self.requests_per_minute = requests_per_minute
        self.daily_limit = daily_limit
        self.on_change = on_change
        self.model_name = model_name

        self._tokens = float(requests_per_minute)
        self._last_refill = time.time()
        self._daily_count = 0
        self._daily_reset = _calculate_next_daily_reset(time.time())
        self._lock = asyncio.Lock()
        self._backoff_until = 0.0
        self._daily_exhausted = False
        # When (epoch secs) we may next auto-probe the API to see if a latched
        # daily limit has lifted. 0.0 = no probe scheduled (not exhausted).
        self._next_daily_probe = 0.0

    async def acquire(self):
        """Acquire a token, waiting if necessary."""
        wait_time = 0.0
        async with self._lock:
            # Check daily reset
            now = time.time()
            if now >= self._daily_reset:
                self._daily_count = 0
                self._daily_exhausted = False
                self._daily_reset = _calculate_next_daily_reset(now)
                if self.on_change:
                    self.on_change()

            # Daily exhaustion is now detected from the API's own 429 responses
            # (see trigger_daily_limit / api_call_wrapper), NOT from counting
            # requests against a guessed per-model limit. We only honor the latch
            # that a real daily-quota response set, so we stop hammering the API
            # once it has actually told us we're out for the day.
            if self._daily_exhausted:
                raise DailyLimitExhausted(self._daily_count, self.daily_limit, self._daily_reset)

            # Calculate backoff wait
            backoff_wait = 0.0
            if now < self._backoff_until:
                backoff_wait = self._backoff_until - now

            # Refill tokens
            elapsed = now - self._last_refill
            refill_amount = elapsed * (self.requests_per_minute / 60.0)
            self._tokens = min(self.requests_per_minute, self._tokens + refill_amount)
            self._last_refill = now

            # If no tokens, calculate token wait
            token_wait = 0.0
            if self._tokens < 1.0:
                token_wait = (1.0 - self._tokens) / (self.requests_per_minute / 60.0)
                self._tokens = 0.0  # Reset tokens to 0 since we are waiting for the next one
                # Adjust self._last_refill to when the token will actually be ready,
                # so that subsequent requests calculate their wait time relative to that point.
                self._last_refill = now + token_wait
            else:
                self._tokens -= 1.0

            # The total wait time is the maximum of backoff wait and token wait
            wait_time = max(backoff_wait, token_wait)
            
            # Increment daily count since we are committing to make a request
            self._daily_count += 1
            if self.on_change:
                self.on_change()

        # Sleep outside the lock
        if wait_time > 0.0:
            await asyncio.sleep(wait_time)

        # Adaptive concurrency: a successful acquire (no 429) is an "increase" signal.
        try:
            from utils.concurrency_controller import concurrency_manager
            concurrency_manager.on_success(self.model_name)
        except Exception:
            pass

    def report_rate_limit(self, retry_after: float = None):
        """Called when a 429 is received."""
        now = time.time()
        delay = retry_after if retry_after is not None else 60.0
        self._backoff_until = now + delay
        # Temporarily halve tokens to slow down proactively
        self._tokens = self._tokens * 0.5
        # Adaptive concurrency: a 429 is a "decrease" signal.
        try:
            from utils.concurrency_controller import concurrency_manager
            concurrency_manager.on_rate_limited(self.model_name)
        except Exception:
            pass
        if self.on_change:
            self.on_change()

    def trigger_daily_limit(self, reset_time: float = None):
        """Latch the daily limit as exhausted.

        Called when the API itself returns a daily-quota 429 (RESOURCE_EXHAUSTED
        with a per-day quota id). This is the authoritative signal — we no longer
        guess exhaustion by counting. ``reset_time`` may carry an API-provided
        retry hint; otherwise we fall back to the next local daily-reset boundary.
        """
        self._daily_exhausted = True
        if reset_time and reset_time > time.time():
            self._daily_reset = reset_time
        # Schedule the first automatic re-check.
        self._next_daily_probe = time.time() + _daily_recheck_seconds()
        if self.on_change:
            self.on_change()

    def should_probe_daily(self) -> bool:
        """True when this model is daily-latched and its next probe window is due."""
        return self._daily_exhausted and time.time() >= self._next_daily_probe

    def defer_probe(self) -> None:
        """Push the next automatic probe out by the configured interval.

        Called after a probe confirms the daily limit is still in effect.
        """
        self._next_daily_probe = time.time() + _daily_recheck_seconds()
        if self.on_change:
            self.on_change()

    def clear_daily_limit(self) -> None:
        """Lift the daily-exhausted latch (a probe found the quota available again).

        Resets the informational counter and recomputes the next reset boundary so
        normal accounting resumes. Idempotent.
        """
        self._daily_exhausted = False
        self._daily_count = 0
        self._next_daily_probe = 0.0
        self._daily_reset = _calculate_next_daily_reset(time.time())
        if self.on_change:
            self.on_change()

    def clear_daily_exhausted_latch(self) -> None:
        """Clear the exhausted flag without resetting the daily count."""
        if self._daily_exhausted:
            self._daily_exhausted = False
            self._next_daily_probe = 0.0
            if self.on_change:
                self.on_change()

    def maybe_reset_daily(self) -> bool:
        """Clear the daily-exhausted latch if we've passed the reset boundary.

        ``acquire`` clears the latch on reset, but when every item is paused on a
        daily limit no ``acquire`` runs — so the latch would never lift. The worker
        calls this each loop to let paused work recover at the daily reset.
        Returns the current exhausted state.
        """
        now = time.time()
        if now >= self._daily_reset:
            self._daily_count = 0
            self._daily_exhausted = False
            self._daily_reset = _calculate_next_daily_reset(now)
            if self.on_change:
                self.on_change()
        return self._daily_exhausted

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
        now = time.time()
        return {
            "daily_count": self._daily_count,
            "daily_limit": self.daily_limit,
            "tokens_available": round(self._tokens, 2),
            "is_backing_off": now < self._backoff_until,
            "backoff_remaining": max(0, round(self._backoff_until - now, 1)),
            # Response-driven daily-limit state (set only by a real API 429).
            "daily_exhausted": self._daily_exhausted,
            "next_probe_in": max(0, round(self._next_daily_probe - now)) if self._daily_exhausted else None,
        }

class NoOpRateLimiter:
    """A rate limiter that never throttles — for local models, which are unmetered.

    Satisfies the same interface ``rate_limited_call`` and the translation pipeline
    expect, so local runs share the exact same call wrapper as cloud without ever
    waiting for tokens or latching a daily limit.
    """
    requests_per_minute = 1_000_000
    daily_limit = 0
    model_name = "local"
    _daily_count = 0
    _daily_exhausted = False

    async def acquire(self):
        return

    def report_rate_limit(self, retry_after: float = None):
        return

    def trigger_daily_limit(self, reset_time: float = None):
        return

    def maybe_reset_daily(self) -> bool:
        return False

    def should_probe_daily(self) -> bool:
        return False

    def clear_daily_exhausted_latch(self) -> None:
        return

    def get_stats(self) -> Dict:
        return {"daily_count": 0, "daily_limit": 0, "tokens_available": None,
                "is_backing_off": False, "daily_exhausted": False, "next_probe_in": None,
                "local": True}


# Shared singleton — stateless, so one instance is safe across all local work.
no_op_rate_limiter = NoOpRateLimiter()


class PerModelRateLimiter:
    STATE_FILE = Path(__file__).resolve().parent.parent / "rate_limiter_state.json"

    # Debounce disk writes: on_change fires on every acquire (i.e. every API call),
    # which previously wrote the whole state JSON synchronously each time. Coalesce to
    # at most one write per interval; flush() forces a pending write.
    _SAVE_MIN_INTERVAL = 2.0

    def __init__(self):
        self._limiters: Dict[str, RateLimiter] = {}
        self._config = {}
        self._last_save = 0.0
        self._save_pending = False
        self.load_config_and_state()

    def load_config_and_state(self):
        # We load config.json
        from utils.storage import load_global_config
        cfg = load_global_config()
        self._config = cfg.get("rate_limits", {})
        
        # Default config if not present
        if "default" not in self._config:
            self._config["default"] = {"rpm": 15, "rpd": 1500}
        
        # Load state
        state = {}
        if self.STATE_FILE.exists():
            try:
                state = json.loads(self.STATE_FILE.read_text(encoding='utf-8'))
            except Exception as e:
                logger.error(f"Failed to load rate limiter state: {e}")

        # Initialize rate limiters from config, applying saved state if valid
        for model, limits in self._config.items():
            rpm = limits.get("rpm", 15)
            rpd = limits.get("rpd", 1500)
            limiter = RateLimiter(requests_per_minute=rpm, daily_limit=rpd, on_change=self.save_state, model_name=model)

            if model in state:
                m_state = state[model]
                limiter._daily_count = m_state.get("daily_count", 0)
                limiter._daily_reset = m_state.get("daily_reset", time.time() + 86400)
                limiter._daily_exhausted = m_state.get("daily_exhausted", False)
                # Exhaustion is API-429-driven, not count-driven (see class docstring).
                # Do NOT lift the latch just because the informational count is under
                # the configured rpd — the real per-model daily quota is often far
                # lower. Only the daily-reset boundary clears it on load, so a key that
                # hit its real quota stays latched across restarts instead of
                # immediately re-hammering the API.
                if limiter._daily_exhausted and time.time() >= limiter._daily_reset:
                    limiter._daily_exhausted = False
                    limiter._daily_count = 0
                    limiter._daily_reset = _calculate_next_daily_reset(time.time())
                limiter._backoff_until = m_state.get("backoff_until", 0.0)
                limiter._tokens = m_state.get("tokens", float(rpm))
                limiter._last_refill = m_state.get("last_refill", time.time())
                limiter._next_daily_probe = 0.0 if not limiter._daily_exhausted else m_state.get("next_daily_probe", 0.0)
                
            self._limiters[model] = limiter

    def save_state(self, force: bool = False):
        """Persist limiter state, debounced. Pass ``force=True`` to write immediately."""
        now = time.time()
        if not force and (now - self._last_save) < self._SAVE_MIN_INTERVAL:
            self._save_pending = True
            return
        state = {}
        for model, limiter in self._limiters.items():
            state[model] = {
                "daily_count": limiter._daily_count,
                "daily_reset": limiter._daily_reset,
                "daily_exhausted": limiter._daily_exhausted,
                "backoff_until": limiter._backoff_until,
                "tokens": limiter._tokens,
                "last_refill": limiter._last_refill,
                "next_daily_probe": limiter._next_daily_probe
            }
        try:
            self.STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding='utf-8')
            self._last_save = now
            self._save_pending = False
        except Exception as e:
            logger.error(f"Failed to save rate limiter state: {e}")

    def flush(self):
        """Write any debounced pending state (call periodically / on shutdown)."""
        if self._save_pending:
            self.save_state(force=True)

    def get_limiter(self, model_name: str) -> RateLimiter:
        # Normalize model_name (e.g. remove local/ prefix if any)
        model_key = model_name
        if model_name.startswith("local/"):
            model_key = model_name[6:]

        if model_key not in self._limiters:
            # Try to get from config, fallback to default
            limits = self._config.get(model_key, self._config.get("default", {"rpm": 15, "rpd": 1500}))
            rpm = limits.get("rpm", 15)
            rpd = limits.get("rpd", 1500)
            self._limiters[model_key] = RateLimiter(requests_per_minute=rpm, daily_limit=rpd, on_change=self.save_state, model_name=model_key)
            self.save_state()
        return self._limiters[model_key]

    def get_all_stats(self) -> Dict:
        return {model: limiter.get_stats() for model, limiter in self._limiters.items()}

    def exhausted_models(self) -> List[str]:
        """Models currently latched as daily-exhausted."""
        return [m for m, lim in self._limiters.items() if lim._daily_exhausted]

class ProxyRateLimiter:
    def __init__(self, per_model_limiter: PerModelRateLimiter):
        self.pml = per_model_limiter

    @property
    def current_limiter(self) -> RateLimiter:
        model_name = active_model_var.get()
        return self.pml.get_limiter(model_name)

    @property
    def requests_per_minute(self) -> int:
        return self.current_limiter.requests_per_minute

    @requests_per_minute.setter
    def requests_per_minute(self, val: int):
        self.current_limiter.requests_per_minute = val
        self.pml.save_state()

    @property
    def daily_limit(self) -> int:
        return self.current_limiter.daily_limit

    @daily_limit.setter
    def daily_limit(self, val: int):
        self.current_limiter.daily_limit = val
        self.pml.save_state()

    @property
    def _daily_count(self) -> int:
        return self.current_limiter._daily_count

    @property
    def _daily_exhausted(self) -> bool:
        return self.current_limiter._daily_exhausted

    @_daily_exhausted.setter
    def _daily_exhausted(self, val: bool):
        self.current_limiter._daily_exhausted = val

    async def acquire(self):
        model_name = active_model_var.get()
        if model_name.startswith("local/"):
            return
        await self.current_limiter.acquire()

    def report_rate_limit(self, retry_after: float = None):
        model_name = active_model_var.get()
        if model_name.startswith("local/"):
            return
        self.current_limiter.report_rate_limit(retry_after)

    def trigger_daily_limit(self, reset_time: float = None):
        self.current_limiter.trigger_daily_limit(reset_time)

    def maybe_reset_daily(self) -> bool:
        return self.current_limiter.maybe_reset_daily()

    def should_probe_daily(self) -> bool:
        return self.current_limiter.should_probe_daily()

    def defer_probe(self) -> None:
        self.current_limiter.defer_probe()

    def clear_daily_limit(self) -> None:
        self.current_limiter.clear_daily_limit()

    def clear_daily_exhausted_latch(self) -> None:
        self.current_limiter.clear_daily_exhausted_latch()

    def estimate_requests(self, episode_names: List[str], avg_scenes_per_episode: float = 8) -> Dict:
        return self.current_limiter.estimate_requests(episode_names, avg_scenes_per_episode)

    def estimate_requests_from_scenes(self, total_scenes: int) -> Dict:
        return self.current_limiter.estimate_requests_from_scenes(total_scenes)

    def get_stats(self) -> Dict:
        return self.current_limiter.get_stats()

# Presets/Global Instances
per_model_rate_limiter = PerModelRateLimiter()
translation_rate_limiter = ProxyRateLimiter(per_model_rate_limiter)
