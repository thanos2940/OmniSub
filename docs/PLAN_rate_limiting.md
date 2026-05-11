# Implementation Plan: Rate Limiting & Resilient Batch Translation

## Goal

Make batch translation resilient to Gemini API rate limits (RPM and RPD). Jobs that hit rate limits should slow down and retry — not fail. Jobs should be resumable at the episode level, with failed episodes retried atomically (not partially).

## Key Design Decisions

- **Episodes are atomic**: if any scene/chunk in an episode fails after retries, discard that episode's partial results and re-queue it. Don't try to resume mid-episode.
- **Proactive throttling**: a token-bucket rate limiter prevents most 429s before they happen.
- **Reactive backoff**: when 429s still occur, exponential backoff with `Retry-After` header parsing handles them gracefully.
- **RPD awareness**: before starting a batch, estimate total requests and warn if the daily budget is insufficient. Pause and resume across days if needed.

---

## Step 1: Create `backend/utils/rate_limiter.py`

Create a new file with a `RateLimiter` class. This is the core utility everything else depends on.

```python
import asyncio
import time
from dataclasses import dataclass, field

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
```

### Required methods:

**`async def acquire(self)`**
- Token bucket algorithm: refill tokens based on elapsed time since last refill
- If no tokens available, calculate wait time and `await asyncio.sleep(wait)`
- If `_backoff_until` is in the future (set by a recent 429), wait until that time
- Increment `_daily_count`
- If `_daily_count >= daily_limit`, raise a `DailyLimitExhausted` exception (don't silently wait forever)

**`def report_rate_limit(self, retry_after: float = None)`**
- Called when a 429 is received
- Sets `_backoff_until = time.monotonic() + retry_after` (default to 60s if no header)
- Temporarily halves `_tokens` to slow down

**`def estimate_requests(self, episodes: list, avg_scenes_per_episode: float = 8) -> dict`**
- Returns `{"total_requests": int, "estimated_minutes": float, "exceeds_daily": bool, "days_needed": int}`
- Used to warn the user before starting a large batch

**`def get_stats(self) -> dict`**
- Returns current state: `daily_count`, `tokens_available`, `is_backing_off`

### Rate limit presets (class methods or module-level constants):

```python
GEMINI_FREE_TIER = RateLimiter(requests_per_minute=15, daily_limit=1500)
GEMINI_PAY_AS_YOU_GO = RateLimiter(requests_per_minute=1000, daily_limit=50000) # adjust to actual limits
```

Also define a custom exception:

```python
class DailyLimitExhausted(Exception):
    """Raised when the daily API request limit has been reached."""
    def __init__(self, daily_count, daily_limit, reset_time=None):
        self.daily_count = daily_count
        self.daily_limit = daily_limit
        self.reset_time = reset_time
```

---

## Step 2: Create `backend/utils/api_call_wrapper.py`

A wrapper that combines rate limiting with retry logic. This is what translation code calls instead of hitting the API directly.

```python
async def rate_limited_call(
    coro_factory,        # Callable that returns the awaitable API call
    rate_limiter,        # RateLimiter instance
    max_retries=3,
    base_delay=2.0,
    max_delay=60.0,
) -> Any:
```

### Logic:

```
for attempt in range(max_retries + 1):
    await rate_limiter.acquire()
    try:
        return await coro_factory()
    except google.api_core.exceptions.ResourceExhausted as e:
        retry_after = parse_retry_after(e)  # extract from headers/message
        rate_limiter.report_rate_limit(retry_after)
        if attempt == max_retries:
            raise
        delay = min(base_delay * (2 ** attempt), max_delay)
        if retry_after:
            delay = max(delay, retry_after)
        await asyncio.sleep(delay)
    except google.api_core.exceptions.ServiceUnavailable:
        # 503 — transient, retry with backoff
        if attempt == max_retries:
            raise
        await asyncio.sleep(base_delay * (2 ** attempt))
```

**Important**: detect 429s specifically. Gemini returns `google.api_core.exceptions.ResourceExhausted` for rate limits. Check the actual exception type used in the ADK/google-genai SDK — it may be `google.genai.errors.ClientError` with status 429. Test this by examining what the ADK runner raises when rate-limited.

---

## Step 3: Create a shared `RateLimiter` instance

In `backend/main.py` (or a new `backend/config.py`), create a module-level rate limiter:

```python
from utils.rate_limiter import RateLimiter

# TODO: make configurable via global_config or .env
translation_rate_limiter = RateLimiter(requests_per_minute=15, daily_limit=1500)
```

Expose it via an endpoint so the frontend can show stats:

```python
@app.get("/rate-limit/stats")
async def get_rate_limit_stats():
    return translation_rate_limiter.get_stats()
```

Also add an endpoint to configure limits:

```python
@app.post("/rate-limit/configure")
async def configure_rate_limit(rpm: int = 15, rpd: int = 1500):
    translation_rate_limiter.requests_per_minute = rpm
    translation_rate_limiter.daily_limit = rpd
    return {"status": "updated"}
```

---

## Step 4: Modify `_process_batch_translation` in `backend/main.py`

This is the core change. The function starts at **line 1674**. Current flow:

```
for each episode:
    build scenes
    skip completed scenes
    for each pending scene (with semaphore):
        translate scene  ← no rate limiting, no 429 handling
    save episode
```

### New flow:

```
# Pre-flight check
estimate = rate_limiter.estimate_requests(episode_names)
if estimate["exceeds_daily"]:
    update_job(job_id, log=f"Warning: batch needs ~{estimate['total_requests']} requests, daily limit is {rate_limiter.daily_limit}. Will process as many as possible.")

completed_episodes = []
failed_episodes = []

for episode_name in episode_names:
    try:
        result = await _translate_episode_atomic(job_id, project_name, episode_name, ...)
        completed_episodes.append(episode_name)
        # Update episode metadata: translated=True
    except DailyLimitExhausted:
        update_job(job_id, log=f"Daily limit reached after {len(completed_episodes)} episodes. Remaining episodes queued for retry.")
        failed_episodes.extend(remaining_episodes)
        break
    except Exception as e:
        update_job(job_id, log=f"Episode '{episode_name}' failed: {e}. Will retry later.")
        failed_episodes.append(episode_name)

# Retry pass for non-RPD failures
for episode_name in failed_episodes_non_rpd:
    try:
        await _translate_episode_atomic(...)
        completed_episodes.append(episode_name)
    except:
        update_job(job_id, log=f"Episode '{episode_name}' failed on retry, skipping.")

update_job(job_id,
    status="completed" if not failed_episodes else "partial",
    message=f"Translated {len(completed_episodes)}/{total} episodes. {len(failed_episodes)} failed."
)
```

### Key change — `_translate_episode_atomic`:

Extract the per-episode translation logic into its own function. This function:

1. Loads the episode data
2. Builds scenes via `build_scene_ast`
3. Translates ALL scenes (using `rate_limited_call` for each scene's API call)
4. If ALL scenes succeed → save the episode, return success
5. If ANY scene fails after retries → **do not save**, raise the exception
6. This means partial translations are never persisted — the episode is either fully translated or untouched

```python
async def _translate_episode_atomic(
    job_id: str,
    project_name: str,
    episode_name: str,
    metadata: dict,
    rate_limiter: RateLimiter,
    semaphore: asyncio.Semaphore,
    ... # other params (model, glossary, context cache, etc.)
) -> bool:
```

Inside this function, the scene translation calls should be wrapped:

```python
# Instead of directly calling the ADK runner:
translated_text = await rate_limited_call(
    lambda: translate_scene(runner, scene, ...),
    rate_limiter=rate_limiter,
)
```

**Important**: keep the existing scene-level semaphore (`concurrent_scenes`) to limit parallelism. The rate limiter handles API-level throttling; the semaphore handles memory/CPU concurrency.

---

## Step 5: Wire rate limiter into scene translation

Find where the actual Gemini API call happens for scene translation. This is inside `_process_batch_translation` around **lines 1750-1850** where it calls the ADK runner or `translate_batch_adk()`.

Wrap each scene's translation call with `rate_limited_call`. The `coro_factory` should be a lambda/closure that performs the actual API call for that scene.

If scenes are translated via `runner.run_async()` (ADK runner), the wrapper goes around the runner call:

```python
async def _translate_single_scene(runner, session_id, scene_prompt):
    response_text = ""
    async for event in runner.run_async(
        user_id="ombisub", session_id=session_id, new_message=...
    ):
        # accumulate response
    return response_text

# Wrapped call:
result = await rate_limited_call(
    lambda: _translate_single_scene(runner, session_id, prompt),
    rate_limiter=translation_rate_limiter,
)
```

---

## Step 6: Update episode metadata schema

In `backend/utils/storage.py`, ensure episode metadata supports these fields:

```python
{
    "translated": bool,              # existing — True only when ALL scenes done
    "translation_status": str,       # "pending" | "in_progress" | "completed" | "failed" | "partial"
    "translation_error": str | None, # last error message if failed
    "last_translation_attempt": str, # ISO timestamp
}
```

Update `save_episode` calls in the batch translation to set these fields.

The `_translate_episode_atomic` function should:
- Set `translation_status = "in_progress"` at the start (metadata only, not data)
- Set `translation_status = "completed"` + `translated = True` on full success
- Set `translation_status = "failed"` + `translation_error = str(e)` on failure
- **Never** set `translated = True` on partial completion

---

## Step 7: Add batch estimation endpoint

```python
@app.post("/projects/{project_name}/batch-translate/estimate")
async def estimate_batch(project_name: str, request: BatchTranslateRequest):
    """Estimate API usage before starting a batch."""
    episodes = request.episode_names
    total_scenes = 0
    for ep_name in episodes:
        data = storage.load_episode(project_name, ep_name)
        scenes = build_scene_ast(data)
        total_scenes += len(scenes)

    estimate = translation_rate_limiter.estimate_requests_from_scenes(total_scenes)
    return {
        "total_episodes": len(episodes),
        "total_scenes": total_scenes,
        "estimated_api_calls": total_scenes,  # 1 call per scene
        "estimated_minutes": total_scenes / translation_rate_limiter.requests_per_minute,
        "exceeds_daily_limit": total_scenes > translation_rate_limiter.daily_limit - translation_rate_limiter._daily_count,
        "daily_remaining": translation_rate_limiter.daily_limit - translation_rate_limiter._daily_count,
    }
```

---

## Step 8: Update `JobStatus` model

Around **line 160** in `main.py`, add fields to `JobStatus`:

```python
class JobStatus(BaseModel):
    # ... existing fields ...
    completed_episodes: List[str] = []
    failed_episodes: List[str] = []
    rate_limit_hits: int = 0
    daily_limit_reached: bool = False
```

Update `_process_batch_translation` to populate these as it runs.

---

## Step 9: Frontend changes (optional but recommended)

### Pre-batch estimation warning

In the component that triggers batch translation (likely around the batch translate button), call the estimate endpoint first:

```javascript
const estimate = await api.post(`/projects/${name}/batch-translate/estimate`, { episode_names });
if (estimate.data.exceeds_daily_limit) {
    // Show warning modal: "This batch needs ~X requests but you have Y remaining today. Continue?"
}
```

### Job progress enhancement

In `JobProgressWidget.jsx`, show:
- `completed_episodes` / total count
- Rate limit status if `rate_limit_hits > 0`
- "Daily limit reached — will resume later" if `daily_limit_reached`

### Rate limit settings

Add a settings section (in project settings or global settings) to configure RPM/RPD:
- Dropdown: "Free tier" (15 RPM / 1500 RPD) vs "Pay-as-you-go" (custom)
- Save to `global_config`

---

## File Summary

| File | Action |
|------|--------|
| `backend/utils/rate_limiter.py` | **CREATE** — RateLimiter class, DailyLimitExhausted exception, presets |
| `backend/utils/api_call_wrapper.py` | **CREATE** — rate_limited_call wrapper with retry + backoff |
| `backend/main.py` | **MODIFY** — wire rate limiter into batch translation, add estimate endpoint, update JobStatus |
| `backend/utils/storage.py` | **MODIFY** — ensure metadata supports translation_status fields (may already work since metadata is freeform dict) |
| Frontend (optional) | **MODIFY** — estimation warning, progress display, rate limit settings |

## Testing

1. **Unit test `RateLimiter`**: verify token refill, backoff timing, daily limit cutoff
2. **Integration test**: mock Gemini to return 429 after N requests, verify batch completes with retries
3. **Manual test**: run a batch of 5+ episodes, observe logs for rate limiter activity
4. **Edge case**: start a batch that exceeds daily limit mid-run, verify it stops cleanly and reports partial completion

## What NOT to change

- **Scene building logic** (`srt_parser.py`) — no changes needed
- **ADK agent definitions** — the agents themselves don't need modification
- **Glossary/context cache logic** — rate limiting only applies to translation API calls
- **Existing single-episode translate endpoint** — keep it simple, rate limiting is for batch operations
- **Existing retry logic in pipeline mode** (lines 2225-2258) — that handles missing-line retries within a single response, which is orthogonal to rate limiting
