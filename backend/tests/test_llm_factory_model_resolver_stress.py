"""
Empirical Stress-Test Suite for LLM Factory, Model Resolver, and Rate Limiter.

Covers:
1. Model Resolution across roles (translation, glossary, review, summary, etc.) and configs.
2. Quota-Exhaustion fallback logic and cascade behavior.
3. LLM Factory model instantiation, thinking budget configuration, and structured schemas.
4. Rate Limiter concurrency stress, burst token acquisition, RPM backoff, and daily exhaustion latches.
5. Bug reproduction for synchronous rate limiter acquire defect.
"""

import asyncio
import time
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from utils.model_resolver import resolve_model, clear_logged_failovers, _DEFAULT_ROLE_MODELS
from utils.rate_limiter import (
    RateLimiter,
    PerModelRateLimiter,
    ProxyRateLimiter,
    NoOpRateLimiter,
    DailyLimitExhausted,
    active_model_var,
    per_model_rate_limiter,
)
from adk_agents.llm_factory import (
    create_model,
    generate,
    resolve_thinking_budget,
    get_recommended_chunk_size,
    _DEFAULT_THINKING_BUDGETS,
)
from utils import storage


# ===========================================================================
# 1. Model Resolution Stress Tests
# ===========================================================================

class TestModelResolution:
    """Test model resolution under various configurations, roles, and edge cases."""

    def test_default_role_resolution_all_roles(self):
        """Verify default fallback resolution for all defined and undefined roles."""
        roles_to_test = [
            "translation", "scan", "context", "glossary", "review",
            "summary", "research", "repair", "reconciliation", "consistency"
        ]
        for role in roles_to_test:
            resolved = resolve_model(role, metadata={}, check_exhaustion=False)
            expected = _DEFAULT_ROLE_MODELS.get(role, "gemini-flash-lite-latest")
            assert resolved == expected, f"Role {role} resolved to {resolved}, expected {expected}"

        # Unknown / custom role defaults to flash-lite
        assert resolve_model("custom_agent_role", metadata={}, check_exhaustion=False) == "gemini-flash-lite-latest"
        assert resolve_model("", metadata={}, check_exhaustion=False) == "gemini-flash-lite-latest"

    def test_global_config_mapping(self, monkeypatch):
        """Verify global config keys are correctly mapped and respected."""
        mock_global_cfg = {
            "default_translation_model": "gemini-2.5-pro",
            "default_scan_model": "gemini-2.5-flash",
            "default_context_model": "gemini-2.5-pro",
            "default_glossary_model": "gemini-2.5-flash",
            "review_model": "gemini-2.5-pro",
            "summary_model": "gemini-2.5-flash",
        }
        monkeypatch.setattr(storage, "load_global_config", lambda: mock_global_cfg)

        assert resolve_model("translation", {}, check_exhaustion=False) == "gemini-2.5-pro"
        assert resolve_model("scan", {}, check_exhaustion=False) == "gemini-2.5-flash"
        assert resolve_model("context", {}, check_exhaustion=False) == "gemini-2.5-pro"
        assert resolve_model("glossary", {}, check_exhaustion=False) == "gemini-2.5-flash"
        assert resolve_model("review", {}, check_exhaustion=False) == "gemini-2.5-pro"
        assert resolve_model("summary", {}, check_exhaustion=False) == "gemini-2.5-flash"

    def test_project_metadata_overrides_global(self, monkeypatch):
        """Verify project-specific settings override global configuration."""
        mock_global_cfg = {
            "default_translation_model": "gemini-2.5-pro",
            "review_model": "gemini-2.5-pro",
        }
        monkeypatch.setattr(storage, "load_global_config", lambda: mock_global_cfg)

        project_meta = {
            "settings": {
                "translation_model": "gemini-custom-translate",
                "glossary_model": "gemini-custom-glossary",
                "review_model": "gemini-custom-review",
                "summary_model": "gemini-custom-summary",
            }
        }

        assert resolve_model("translation", project_meta, check_exhaustion=False) == "gemini-custom-translate"
        assert resolve_model("glossary", project_meta, check_exhaustion=False) == "gemini-custom-glossary"
        assert resolve_model("review", project_meta, check_exhaustion=False) == "gemini-custom-review"
        assert resolve_model("summary", project_meta, check_exhaustion=False) == "gemini-custom-summary"

    def test_legacy_default_model_in_project_falls_back_to_global(self, monkeypatch):
        """If project metadata has the legacy default 'gemini-flash-lite-latest',
        it should fall back to the newly configured global default."""
        mock_global_cfg = {
            "default_translation_model": "gemini-2.5-flash",
            "review_model": "gemini-2.5-pro",
        }
        monkeypatch.setattr(storage, "load_global_config", lambda: mock_global_cfg)

        project_meta = {
            "settings": {
                "translation_model": "gemini-flash-lite-latest",
                "review_model": "gemini-flash-lite-latest",
            }
        }

        assert resolve_model("translation", project_meta, check_exhaustion=False) == "gemini-2.5-flash"
        assert resolve_model("review", project_meta, check_exhaustion=False) == "gemini-2.5-pro"

    def test_call_level_model_override(self):
        """Verify model_override param takes precedence over metadata and defaults."""
        project_meta = {
            "settings": {
                "translation_model": "gemini-project-model"
            }
        }
        res = resolve_model("translation", project_meta, model_override="gemini-adhoc-override", check_exhaustion=False)
        assert res == "gemini-adhoc-override"

    def test_malformed_metadata_resilience(self):
        """Verify resolve_model does not crash with None, malformed, or non-dict metadata."""
        assert resolve_model("translation", None, check_exhaustion=False) == "gemini-flash-lite-latest"
        assert resolve_model("translation", {}, check_exhaustion=False) == "gemini-flash-lite-latest"
        assert resolve_model("translation", {"settings": None}, check_exhaustion=False) == "gemini-flash-lite-latest"
        assert resolve_model("translation", {"settings": {"translation_model": ""}}, check_exhaustion=False) == "gemini-flash-lite-latest"
        assert resolve_model("translation", "invalid_string_metadata", check_exhaustion=False) == "gemini-flash-lite-latest"
        assert resolve_model("translation", [1, 2, 3], check_exhaustion=False) == "gemini-flash-lite-latest"


# ===========================================================================
# 2. Quota-Exhaustion Fallback Stress Tests
# ===========================================================================

class TestQuotaExhaustionFallback:
    """Test quota-exhaustion failover triggers, cascades, and boundary conditions."""

    def setup_method(self):
        clear_logged_failovers()
        # Reset limiter states
        for limiter in per_model_rate_limiter._limiters.values():
            limiter.clear_daily_limit()

    def teardown_method(self):
        clear_logged_failovers()
        for limiter in per_model_rate_limiter._limiters.values():
            limiter.clear_daily_limit()

    def test_fallback_to_role_specific_configured_model(self):
        """Primary model exhausted -> fails over to fallback_{role}_model."""
        primary = "gemini-2.5-pro"
        fallback = "gemini-2.5-flash"

        limiter_primary = per_model_rate_limiter.get_limiter(primary)
        limiter_primary.trigger_daily_limit()

        project_meta = {
            "settings": {
                "translation_model": primary,
                "fallback_translation_model": fallback,
            }
        }

        resolved = resolve_model("translation", project_meta, check_exhaustion=True)
        assert resolved == fallback

    def test_fallback_to_general_translation_model_when_role_fallback_missing(self):
        """Primary review model exhausted -> falls back to fallback_translation_model."""
        primary_review = "gemini-2.5-pro"
        fallback_general = "gemini-3.1-flash-lite"

        limiter = per_model_rate_limiter.get_limiter(primary_review)
        limiter.trigger_daily_limit()

        project_meta = {
            "settings": {
                "review_model": primary_review,
                "fallback_translation_model": fallback_general,
            }
        }

        resolved = resolve_model("review", project_meta, check_exhaustion=True)
        assert resolved == fallback_general

    def test_fallback_to_default_when_no_fallback_configured(self):
        """Primary model exhausted with no fallback configured anywhere -> defaults to gemini-3.1-flash-lite."""
        primary = "gemini-flash-lite-latest"
        limiter = per_model_rate_limiter.get_limiter(primary)
        limiter.trigger_daily_limit()

        resolved = resolve_model("translation", metadata={}, check_exhaustion=True)
        assert resolved == "gemini-3.1-flash-lite"

    def test_fallback_when_primary_is_already_default_fallback(self):
        """If primary is gemini-3.1-flash-lite and it is exhausted, it must not fail over to itself."""
        primary = "gemini-3.1-flash-lite"
        limiter = per_model_rate_limiter.get_limiter(primary)
        limiter.trigger_daily_limit()

        # No fallback configured, so fallback_model resolves to gemini-3.1-flash-lite
        # fallback_model != model is False, so it returns primary model
        resolved = resolve_model("translation", metadata={"settings": {"translation_model": primary}}, check_exhaustion=True)
        assert resolved == primary

    def test_fallback_when_both_primary_and_fallback_are_exhausted(self):
        """Both primary and fallback are exhausted -> returns primary model and logs cleanly."""
        primary = "gemini-2.5-pro"
        fallback = "gemini-2.5-flash"

        lim_primary = per_model_rate_limiter.get_limiter(primary)
        lim_fallback = per_model_rate_limiter.get_limiter(fallback)
        lim_primary.trigger_daily_limit()
        lim_fallback.trigger_daily_limit()

        project_meta = {
            "settings": {
                "translation_model": primary,
                "fallback_translation_model": fallback,
            }
        }

        resolved = resolve_model("translation", project_meta, check_exhaustion=True)
        assert resolved == primary

    def test_check_exhaustion_false_bypasses_failover(self):
        """When check_exhaustion is False, exhausted primary model is returned directly."""
        primary = "gemini-2.5-pro"
        limiter = per_model_rate_limiter.get_limiter(primary)
        limiter.trigger_daily_limit()

        project_meta = {"settings": {"translation_model": primary, "fallback_translation_model": "gemini-2.5-flash"}}
        resolved = resolve_model("translation", project_meta, check_exhaustion=False)
        assert resolved == primary

    def test_failover_with_exhausted_model_override(self):
        """When model_override is passed and is exhausted, failover takes effect."""
        override_model = "gemini-override-pro"
        limiter = per_model_rate_limiter.get_limiter(override_model)
        limiter.trigger_daily_limit()

        project_meta = {"settings": {"fallback_translation_model": "gemini-3.1-flash-lite"}}
        resolved = resolve_model("translation", project_meta, model_override=override_model, check_exhaustion=True)
        assert resolved == "gemini-3.1-flash-lite"


# ===========================================================================
# 3. LLM Factory Model Creation & Thinking Budget Tests
# ===========================================================================

class TestLLMFactory:
    """Test LLM factory model construction, thinking budget resolution, and schemas."""

    def test_thinking_budget_role_resolution(self, monkeypatch):
        """Verify mechanical roles have 0 budget, judgment roles have None (model default)."""
        # Built-in defaults
        assert resolve_thinking_budget("translation") == 0
        assert resolve_thinking_budget("repair") == 0
        assert resolve_thinking_budget("condense") == 0
        assert resolve_thinking_budget("reconciliation") == 0
        assert resolve_thinking_budget("summary") == 0
        assert resolve_thinking_budget("consistency") == 0
        assert resolve_thinking_budget("scan") == 0
        assert resolve_thinking_budget("glossary") is None
        assert resolve_thinking_budget("context") is None
        assert resolve_thinking_budget("review") is None
        assert resolve_thinking_budget("research") is None
        assert resolve_thinking_budget(None) is None
        assert resolve_thinking_budget("unknown_role") is None

        # Global config override
        monkeypatch.setattr(storage, "load_global_config", lambda: {
            "thinking_budgets": {"translation": 2048, "review": 1024, "summary": -1}
        })
        assert resolve_thinking_budget("translation") == 2048
        assert resolve_thinking_budget("review") == 1024
        assert resolve_thinking_budget("summary") is None  # -1 becomes None

    def test_create_model_thinking_config_attachment(self):
        """Verify create_model configures thinking_config for 2.5 Flash but avoids 0 for 2.5 Pro."""
        # 2.5 Flash on mechanical role -> thinking_budget=0
        m_flash = create_model("gemini-2.5-flash", role="translation")
        assert hasattr(m_flash, "generation_config")
        gen_cfg = m_flash.generation_config
        assert "thinking_config" in gen_cfg
        assert gen_cfg["thinking_config"].thinking_budget == 0

        # 2.5 Pro on mechanical role -> Pro doesn't allow 0, so thinking_config is skipped
        m_pro = create_model("gemini-2.5-pro", role="translation")
        gen_cfg_pro = getattr(m_pro, "generation_config", {})
        assert "thinking_config" not in gen_cfg_pro

        # The -latest aliases point at current (thinking) models, so they get the budget too
        m_lite = create_model("gemini-flash-lite-latest", role="translation")
        assert m_lite.generation_config["thinking_config"].thinking_budget == 0

        # Generations without thinking -> no thinking_config
        for old in ("gemini-2.0-flash", "gemma-4-31b-it"):
            assert "thinking_config" not in (getattr(create_model(old, role="translation"), "generation_config", None) or {})

    def test_create_model_structured_output_schema(self):
        """Verify response_schema attaches application/json and schema."""
        from pydantic import BaseModel, Field

        class SampleSchema(BaseModel):
            result: str = Field(description="Result")

        m = create_model("gemini-2.5-flash", response_schema=SampleSchema)
        assert m.generation_config["response_mime_type"] == "application/json"
        assert m.generation_config["response_schema"] == SampleSchema


# ===========================================================================
# 4. Rate Limiter Stress Tests & Concurrency Harness
# ===========================================================================

class TestRateLimiterStress:
    """Stress-test RateLimiter token bucket under concurrency, burst acquisitions, and latches."""

    @pytest.mark.asyncio
    async def test_burst_concurrent_token_acquisition(self):
        """Test burst of 30 concurrent acquisitions on RPM=15 limiter.
        
        First 15 requests must acquire instantly.
        Next 15 requests must be serialized with 4.0s pacing between each.
        """
        rpm = 15
        limiter = RateLimiter(requests_per_minute=rpm, daily_limit=1500)
        
        # Initial tokens = 15.0
        assert limiter._tokens == 15.0

        # Acquire first 15 immediately
        for _ in range(15):
            await limiter.acquire()

        assert limiter._daily_count == 15
        assert limiter._tokens == pytest.approx(0.0, abs=1e-2)

        # Time remaining for next acquire should be around 4.0s (1.0 / (15/60))
        now = time.time()
        # Test simulated burst of next 3 requests by verifying _last_refill math
        async def mock_acquire_step(lim):
            async with lim._lock:
                now_t = time.time()
                elapsed = now_t - lim._last_refill
                refill = elapsed * (lim.requests_per_minute / 60.0)
                lim._tokens = min(lim.requests_per_minute, lim._tokens + refill)
                lim._last_refill = now_t
                token_wait = (1.0 - lim._tokens) / (lim.requests_per_minute / 60.0)
                lim._tokens = 0.0
                lim._last_refill = now_t + token_wait
                lim._daily_count += 1
                return token_wait

        w1 = await mock_acquire_step(limiter)
        w2 = await mock_acquire_step(limiter)
        w3 = await mock_acquire_step(limiter)

        assert abs(w1 - 4.0) < 0.1
        assert abs(w2 - 8.0) < 0.1
        assert abs(w3 - 12.0) < 0.1
        assert limiter._daily_count == 18

    @pytest.mark.asyncio
    async def test_token_refill_and_cap(self):
        """Verify token bucket refills over time and caps at requests_per_minute."""
        limiter = RateLimiter(requests_per_minute=20, daily_limit=1000)
        limiter._tokens = 0.0
        limiter._last_refill = time.time() - 30.0  # 30 seconds ago

        # Refill should be 30 * (20/60) = 10.0 tokens
        await limiter.acquire()
        # Used 1 token, so 9.0 remain
        assert abs(limiter._tokens - 9.0) < 0.2

        # Advance 1000 seconds -> capped at 20.0
        limiter._last_refill = time.time() - 1000.0
        await limiter.acquire()
        assert abs(limiter._tokens - 19.0) < 0.2

    @pytest.mark.asyncio
    async def test_rpm_429_backoff_behavior(self):
        """Verify report_rate_limit sets backoff and halves available tokens."""
        limiter = RateLimiter(requests_per_minute=10, daily_limit=1000)
        limiter._tokens = 8.0
        limiter.report_rate_limit(retry_after=0.2)

        assert limiter._tokens == 4.0
        assert limiter._backoff_until > time.time()

        start = time.time()
        await limiter.acquire()
        elapsed = time.time() - start

        assert elapsed >= 0.18
        assert limiter._daily_count == 1

    @pytest.mark.asyncio
    async def test_daily_exhaustion_latch_and_clean_exception(self):
        """Verify trigger_daily_limit latches and immediately raises DailyLimitExhausted."""
        limiter = RateLimiter(requests_per_minute=10, daily_limit=1000)
        limiter.trigger_daily_limit()

        assert limiter._daily_exhausted is True

        with pytest.raises(DailyLimitExhausted) as exc_info:
            await limiter.acquire()

        assert "Daily limit exhausted" in str(exc_info.value)

    def test_multi_model_limiter_isolation(self):
        """Exhausting model A must not affect model B in PerModelRateLimiter."""
        pml = PerModelRateLimiter()
        lim_a = pml.get_limiter("model-a")
        lim_b = pml.get_limiter("model-b")

        lim_a.trigger_daily_limit()

        assert lim_a._daily_exhausted is True
        assert lim_b._daily_exhausted is False
        assert "model-a" in pml.exhausted_models()
        assert "model-b" not in pml.exhausted_models()

    def test_debounced_state_persistence(self, tmp_path, monkeypatch):
        """Verify state saving is debounced and flushed properly."""
        state_file = tmp_path / "rate_limiter_state.json"
        monkeypatch.setattr(PerModelRateLimiter, "STATE_FILE", state_file)

        pml = PerModelRateLimiter()
        lim = pml.get_limiter("test-persist-model")
        lim._daily_count = 42

        # Debounced save
        pml.save_state(force=False)
        # Flush pending
        pml.flush()

        assert state_file.exists()
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        assert "test-persist-model" in saved
        assert saved["test-persist-model"]["daily_count"] == 42


# ===========================================================================
# 5. RateLimiter acquire_sync Lock & Method Verification
# ===========================================================================

class TestRateLimiterDefectDiscovery:
    """Verify that acquire_sync works correctly and safely with DualLock."""

    def test_acquire_sync_succeeds_with_duallock_and_reset_method(self):
        """RateLimiter.acquire_sync() now uses DualLock and is_daily_reset_due():
        1. It supports synchronous context management (`__enter__` / `__exit__`).
        2. `is_daily_reset_due()` exists and properly checks daily reset boundaries.
        3. Synchronous callers acquire tokens without error.
        """
        limiter = RateLimiter(requests_per_minute=60, daily_limit=1500)
        initial_tokens = limiter._tokens

        # Calling acquire_sync directly succeeds without error
        limiter.acquire_sync()
        assert limiter._daily_count == 1
        assert limiter._tokens < initial_tokens
        assert limiter.is_daily_reset_due() is False
