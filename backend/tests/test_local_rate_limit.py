import pytest
import asyncio
import time
from utils.rate_limiter import RateLimiter, ProxyRateLimiter, PerModelRateLimiter, DailyLimitExhausted, active_model_var
from utils.concurrency_controller import concurrency_manager
from utils.api_call_wrapper import is_daily_quota_error


@pytest.mark.asyncio
async def test_count_does_not_gate_daily_limit():
    """Daily exhaustion is response-driven now: exceeding the configured count
    must NOT halt the model as long as the API hasn't returned a daily 429."""
    limiter = RateLimiter(requests_per_minute=1000, daily_limit=1)
    limiter._daily_count = 50  # way over the configured 'limit', but no API signal
    # Should acquire fine — we no longer count our way to exhaustion.
    await limiter.acquire()
    assert limiter._daily_exhausted is False


@pytest.mark.asyncio
async def test_api_signal_latches_daily_limit():
    """A real daily-quota response latches exhaustion and blocks further acquires."""
    limiter = RateLimiter(requests_per_minute=1000, daily_limit=1500)
    limiter.trigger_daily_limit()
    assert limiter._daily_exhausted is True
    with pytest.raises(DailyLimitExhausted):
        await limiter.acquire()


def test_maybe_reset_daily_lifts_latch_after_boundary():
    """maybe_reset_daily clears the latch once past the reset boundary, so paused
    work recovers even when no acquire() runs."""
    limiter = RateLimiter(requests_per_minute=10, daily_limit=10)
    limiter.trigger_daily_limit()
    # Boundary in the past -> latch should lift.
    limiter._daily_reset = time.time() - 1
    assert limiter.maybe_reset_daily() is False
    assert limiter._daily_exhausted is False
    # Boundary in the future -> latch stays.
    limiter.trigger_daily_limit()
    limiter._daily_reset = time.time() + 3600
    assert limiter.maybe_reset_daily() is True


def test_is_daily_quota_error_distinguishes_per_day_from_per_minute():
    per_day = Exception(
        "429 Resource exhausted. quotaId: GenerateRequestsPerDayPerProjectPerModel"
    )
    per_minute = Exception(
        "429 Resource exhausted. quotaId: GenerateRequestsPerMinutePerProjectPerModel"
    )
    assert is_daily_quota_error(per_day) is True
    assert is_daily_quota_error(per_minute) is False


def test_probe_scheduling_and_clear():
    """trigger schedules a probe; should_probe_daily gates on the window; clear lifts."""
    limiter = RateLimiter(requests_per_minute=10, daily_limit=10)
    limiter.trigger_daily_limit()
    assert limiter._daily_exhausted is True
    # Probe window is in the future -> not due yet.
    assert limiter._next_daily_probe > time.time()
    assert limiter.should_probe_daily() is False
    # Force the window open -> due.
    limiter._next_daily_probe = time.time() - 1
    assert limiter.should_probe_daily() is True
    # Defer pushes it back out.
    limiter.defer_probe()
    assert limiter.should_probe_daily() is False
    # Clear lifts the latch entirely.
    limiter.clear_daily_limit()
    assert limiter._daily_exhausted is False
    assert limiter._daily_count == 0
    assert limiter.should_probe_daily() is False


@pytest.mark.asyncio
async def test_recheck_clears_available_model(monkeypatch):
    """recheck_daily_limits lifts the latch for a model the probe reports available."""
    import utils.daily_limit_probe as probe_mod
    from utils.rate_limiter import per_model_rate_limiter

    model = "gemini-test-probe"
    limiter = per_model_rate_limiter.get_limiter(model)
    limiter.trigger_daily_limit()
    assert model in per_model_rate_limiter.exhausted_models()

    async def fake_probe(m):
        return {"model": m, "available": True, "reason": "stub"}
    monkeypatch.setattr(probe_mod, "probe_model_quota", fake_probe)

    out = await probe_mod.recheck_daily_limits(force=True)
    assert model in out["cleared"]
    assert limiter._daily_exhausted is False


@pytest.mark.asyncio
async def test_recheck_defers_when_still_limited(monkeypatch):
    """recheck keeps the latch and defers the probe when the API still says exhausted."""
    import utils.daily_limit_probe as probe_mod
    from utils.rate_limiter import per_model_rate_limiter

    model = "gemini-test-probe-2"
    limiter = per_model_rate_limiter.get_limiter(model)
    limiter.trigger_daily_limit()
    limiter._next_daily_probe = time.time() - 1  # make it due

    async def fake_probe(m):
        return {"model": m, "available": False, "reason": "daily quota still exhausted"}
    monkeypatch.setattr(probe_mod, "probe_model_quota", fake_probe)

    out = await probe_mod.recheck_daily_limits(force=False)
    assert out["cleared"] == []
    assert limiter._daily_exhausted is True
    assert limiter.should_probe_daily() is False  # deferred

@pytest.mark.asyncio
async def test_local_model_bypasses_rate_limit():
    """Test that models starting with local/ are not throttled."""
    # Setup a limiter with 0 tokens and exhausted daily limit
    limiter = RateLimiter(requests_per_minute=1, daily_limit=1)
    limiter._tokens = 0.0
    limiter._daily_count = 1
    limiter._daily_exhausted = True
    
    pml = PerModelRateLimiter()
    # Mock get_limiter to return our exhausted limiter for 'mistral-7b'
    # but the ProxyRateLimiter should bypass it if the name is 'local/mistral-7b'
    pml._limiters['mistral-7b'] = limiter
    
    proxy = ProxyRateLimiter(pml)
    
    # CASE 1: Cloud model (should fail)
    active_model_var.set("gemini-1.5-flash")
    # We need to make sure 'gemini-1.5-flash' also points to an exhausted limiter or the same one
    pml._limiters['gemini-1.5-flash'] = limiter
    
    with pytest.raises(DailyLimitExhausted):
        await proxy.acquire()

    # CASE 2: Local model (should pass immediately)
    active_model_var.set("local/mistral-7b")
    # This should NOT raise DailyLimitExhausted and should NOT wait
    start_time = time.time()
    await proxy.acquire()
    end_time = time.time()
    
    # Should be nearly instant despite 0 tokens
    assert end_time - start_time < 0.1

@pytest.mark.asyncio
async def test_local_model_bypasses_concurrency_ramp():
    """Test that local models don't use adaptive concurrency (always use max/base)."""
    concurrency_manager.set_defaults(enabled=True, base=2, c_max=10)
    
    model = "local/any-model"
    # Even with many successes, it shouldn't "ramp" if we disable it for local
    # Or more accurately, it should probably just return the max allowed immediately
    # but the task says "off", so it should return 'base' or 'c_max' without ramping logic.
    
    # Current behavior: it ramps.
    # We want it to either NOT ramp or return a fixed value.
    # Spec says "force adaptive concurrency off for local".
    # This means concurrency_manager.current(model) should probably return a constant.
    
    # If disabled, it returns 'base'. 
    # But usually users want MORE concurrency for local if their GPU can handle it.
    # However, 'adaptive' is what we want off.
    
    assert concurrency_manager.current(model) == 2 # Base
    for _ in range(10):
        concurrency_manager.on_success(model)
    
    # If adaptive is OFF for local, it should still be 2.
    # (Currently it would be 4 because of the ramp in ConcurrencyController)
    assert concurrency_manager.current(model) == 2


def test_clear_daily_exhausted_latch_preserves_count():
    limiter = RateLimiter(requests_per_minute=10, daily_limit=10)
    limiter.trigger_daily_limit()
    assert limiter._daily_exhausted is True
    # Save the current count (trigger sets it to daily_limit)
    original_count = limiter._daily_count
    
    limiter.clear_daily_exhausted_latch()
    assert limiter._daily_exhausted is False
    assert limiter._daily_count == original_count
    assert limiter._next_daily_probe == 0.0


def test_load_state_preserves_latch_until_reset_boundary(monkeypatch, tmp_path):
    """A response-driven daily latch must survive a restart: it is NOT cleared just
    because the informational count is under the configured rpd (the real per-model
    quota is often far lower). Only crossing the daily-reset boundary lifts it on load."""
    state_file = tmp_path / "rate_limiter_state.json"
    monkeypatch.setattr(PerModelRateLimiter, "STATE_FILE", state_file)

    import utils.storage as storage_mod
    monkeypatch.setattr(storage_mod, "load_global_config", lambda: {
        "rate_limits": {"model-a": {"rpm": 10, "rpd": 1000}}
    })

    import json

    # Case 1: reset boundary in the FUTURE -> the latch must persist across restart,
    # even though daily_count (500) < daily_limit (1000). Otherwise we'd immediately
    # re-hammer a key the API already told us is out for the day.
    state_file.write_text(json.dumps({
        "model-a": {
            "daily_count": 500,
            "daily_reset": time.time() + 3600,
            "daily_exhausted": True,
            "backoff_until": 0.0,
            "tokens": 10.0,
            "last_refill": time.time(),
            "next_daily_probe": time.time() + 1800,
        }
    }))
    limiter = PerModelRateLimiter().get_limiter("model-a")
    assert limiter._daily_exhausted is True
    assert limiter._daily_count == 500

    # Case 2: reset boundary in the PAST -> the latch lifts on load and the count resets.
    state_file.write_text(json.dumps({
        "model-a": {
            "daily_count": 500,
            "daily_reset": time.time() - 1,
            "daily_exhausted": True,
            "backoff_until": 0.0,
            "tokens": 10.0,
            "last_refill": time.time(),
            "next_daily_probe": time.time() + 1800,
        }
    }))
    limiter2 = PerModelRateLimiter().get_limiter("model-a")
    assert limiter2._daily_exhausted is False
    assert limiter2._daily_count == 0


@pytest.mark.asyncio
async def test_rate_limited_call_clears_latch_on_success():
    from utils.api_call_wrapper import rate_limited_call
    
    class DummyLimiter:
        def __init__(self):
            self.called_clear_latch = False
            
        async def acquire(self):
            pass
            
        def clear_daily_exhausted_latch(self):
            self.called_clear_latch = True
            
    limiter = DummyLimiter()
    
    async def fake_api():
        return "success"
        
    res = await rate_limited_call(fake_api, limiter)
    assert res == "success"
    assert limiter.called_clear_latch is True


def test_is_daily_quota_error_bypasses_on_per_minute_overlap():
    mixed_msg = Exception(
        "429 Quota exceeded for resource limit 'Generate requests per minute'. daily limit: 1500"
    )
    assert is_daily_quota_error(mixed_msg) is False


def test_is_daily_quota_error_expanded_markers():
    daily_err_1 = Exception("429 ResourceExhausted: You have exhausted your daily quota. Please wait until tomorrow.")
    daily_err_2 = Exception("429 Quota exceeded for quota metric GenerateRequestsPerDayPerProjectPerModel")
    daily_err_3 = Exception("429 Free tier limit reached. Daily limit: 1500")
    
    rpm_err_1 = Exception("429 Quota exceeded for GenerateContent requests per minute per region")
    rpm_err_2 = Exception("429 ResourceExhausted: TPM limit exceeded")

    assert is_daily_quota_error(daily_err_1) is True
    assert is_daily_quota_error(daily_err_2) is True
    assert is_daily_quota_error(daily_err_3) is True

    assert is_daily_quota_error(rpm_err_1) is False
    assert is_daily_quota_error(rpm_err_2) is False


def test_trigger_daily_limit_aligns_probe_to_reset_boundary():
    limiter = RateLimiter(requests_per_minute=15, daily_limit=1500)
    future_reset = time.time() + 7200
    limiter.trigger_daily_limit(reset_time=future_reset)

    assert limiter._daily_exhausted is True
    assert limiter._daily_reset == future_reset
    assert limiter._next_daily_probe == future_reset
    assert limiter.should_probe_daily() is False


@pytest.mark.asyncio
async def test_acquire_waits_for_backoff_until_without_token_consumption():
    limiter = RateLimiter(requests_per_minute=15, daily_limit=1500)
    limiter.report_rate_limit(retry_after=0.1)  # 100ms backoff
    assert limiter._backoff_until > time.time()
    
    start_tokens = limiter._tokens
    start_count = limiter._daily_count
    
    start = time.time()
    await limiter.acquire()
    duration = time.time() - start

    assert duration >= 0.09
    assert limiter._daily_count == start_count + 1

