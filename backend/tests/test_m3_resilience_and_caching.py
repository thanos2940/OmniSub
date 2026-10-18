"""
M3 Test Suite: Gemini Free-Tier Resilience, Prefix Caching & Fallback.

Tests covering:
1. Prompt Assembler: Deterministic byte-order prefix stability (Bible -> Context -> Glossary -> Profiles -> TM -> Blacklist -> Lookahead/Prev -> Cues) and cache fingerprints.
2. DualLock & Rate Limiter: 15 RPM / 1,500 RPD token bucket enforcement, thread/asyncio safety, and daily reset mechanics.
3. API Call Wrapper & Model Resolver: Response-driven 429 classification (RPM vs RPD), parse_retry_after, exponential backoff, and seamless failover to Flash-Lite.
"""

import asyncio
import hashlib
import json
import threading
import time
from unittest.mock import MagicMock, patch, AsyncMock
import pytest
import google.api_core.exceptions

from services.prompt_assembler import (
    compile_bible,
    assemble_episode_request,
    assemble_scene_request,
    extract_prompt_prefix,
    verify_prefix_stability,
    Bible,
    TranslationRequest,
)
from utils.cache_manager import compute_fingerprint
from utils.rate_limiter import (
    DualLock,
    RateLimiter,
    DailyLimitExhausted,
    PerModelRateLimiter,
    ProxyRateLimiter,
    active_model_var,
    per_model_rate_limiter,
    _calculate_next_daily_reset,
)
from utils.api_call_wrapper import (
    parse_retry_after,
    is_daily_quota_error,
    rate_limited_call,
    call_with_retry,
)
from utils.model_resolver import (
    resolve_model,
    mark_model_daily_exhausted,
    is_model_daily_exhausted,
    clear_model_daily_exhaustion,
    clear_logged_failovers,
)
from utils.structured_output import (
    SceneTranslationResponse,
    EpisodeTranslationResponse,
    VerifiedEpisodeTranslationResponse,
)


# ===========================================================================
# 1. Prompt Assembler & Prefix Caching Stability Tests
# ===========================================================================

class TestPromptAssemblerAndPrefixCaching:
    """Validate strict byte-order prefix stability for Gemini implicit context caching."""

    def test_bible_compilation_and_fingerprint_determinism(self):
        """Glossary order permutation produces identical fingerprint and system instruction."""
        terms_1 = [
            {"term": "Zeta", "translation": "Ζήτα", "type": "person"},
            {"term": "Alpha", "translation": "Άλφα", "type": "location"},
            {"term": "Mu", "translation": "Μι", "type": "object"},
        ]
        terms_2 = [
            {"term": "Alpha", "translation": "Άλφα", "type": "location"},
            {"term": "Mu", "translation": "Μι", "type": "object"},
            {"term": "Zeta", "translation": "Ζήτα", "type": "person"},
        ]
        meta_1 = {"glossary": {"terms": terms_1}, "context_guide": "Episode 1 Guide", "target_language": "Greek"}
        meta_2 = {"glossary": {"terms": terms_2}, "context_guide": "Episode 1 Guide", "target_language": "Greek"}

        bible_1 = compile_bible("ShowA", meta_1, "gemini-flash-lite-latest")
        bible_2 = compile_bible("ShowA", meta_2, "gemini-flash-lite-latest")

        assert bible_1.fingerprint == bible_2.fingerprint
        assert len(bible_1.fingerprint) == 16
        assert bible_1.structured is True

    def test_strict_byte_order_prefix_hierarchy(self):
        """Prompt sections must strictly follow the stability hierarchy:
        Conformance/Episode Context -> Priority Glossary -> Character Profiles -> Few-shot TM -> Negative Blacklist -> Forced -> Prev Context -> Lookahead -> Cues.
        """
        bible = Bible(
            project_name="TestProj",
            target_language="Greek",
            system_instruction="System Instruction Header",
            fingerprint="fp12345678901234",
            glossary={"terms": []},
            structured=False,
            model_name="gemini-flash-lite-latest",
        )

        req = assemble_scene_request(
            bible=bible,
            source_texts=["Hello friend.", "Good morning."],
            indices=[0, 1],
            context_hint="Previous scene dialogue",
            few_shot_block="FEW_SHOT_TM_BLOCK",
            priority_block="PRIORITY_GLOSSARY_BLOCK",
            character_block="CHARACTER_PROFILES_BLOCK",
            episode_context="EPISODE_CONTEXT_GUIDE_BLOCK",
            conformance_hint="CONFORMANCE_HINT_BLOCK",
            negative_block="NEGATIVE_BLACKLIST_BLOCK",
            lookahead_hint="LOOKAHEAD_BLOCK",
            forced_translations="FORCED_TRANSLATIONS_BLOCK",
        )

        prompt = req.prompt
        pos_cf = prompt.find("CONFORMANCE_HINT_BLOCK")
        pos_ep = prompt.find("EPISODE_CONTEXT_GUIDE_BLOCK")
        pos_pg = prompt.find("PRIORITY_GLOSSARY_BLOCK")
        pos_ch = prompt.find("CHARACTER_PROFILES_BLOCK")
        pos_fs = prompt.find("FEW_SHOT_TM_BLOCK")
        pos_neg = prompt.find("NEGATIVE_BLACKLIST_BLOCK")
        pos_forced = prompt.find("FORCED_TRANSLATIONS_BLOCK")
        pos_prev = prompt.find("[prev] Previous scene dialogue")
        pos_la = prompt.find("[lookahead (do NOT translate)] LOOKAHEAD_BLOCK")
        pos_cue1 = prompt.find("1| Hello friend.")
        pos_cue2 = prompt.find("2| Good morning.")

        # Assert every section appears in strict forward byte order
        assert pos_cf != -1 and pos_ep != -1 and pos_pg != -1 and pos_ch != -1
        assert pos_fs != -1 and pos_neg != -1 and pos_forced != -1
        assert pos_prev != -1 and pos_la != -1 and pos_cue1 != -1 and pos_cue2 != -1

        assert pos_cf < pos_ep < pos_pg < pos_ch < pos_fs < pos_neg < pos_forced < pos_prev < pos_la < pos_cue1 < pos_cue2

    def test_prefix_stability_verification_across_different_cues(self):
        """Two requests in the same scene with different cues share 100% byte-identical prompt prefix."""
        bible = Bible(
            project_name="TestProj",
            target_language="Greek",
            system_instruction="Fixed System Instruction",
            fingerprint="fp12345678901234",
            glossary={"terms": []},
            structured=False,
            model_name="gemini-flash-lite-latest",
        )

        req1 = assemble_scene_request(
            bible=bible,
            source_texts=["Scene 1 Line 1", "Scene 1 Line 2"],
            indices=[0, 1],
            episode_context="Season 1 Episode 1 Context",
            priority_block="TermA|Όρος Α",
            character_block="Alice: formal, Bob: casual",
        )

        req2 = assemble_scene_request(
            bible=bible,
            source_texts=["Different Line 1", "Different Line 2", "Different Line 3"],
            indices=[2, 3, 4],
            episode_context="Season 1 Episode 1 Context",
            priority_block="TermA|Όρος Α",
            character_block="Alice: formal, Bob: casual",
        )

        assert verify_prefix_stability(req1, req2) is True
        assert req1.extract_prompt_prefix() == req2.extract_prompt_prefix()
        assert req1.compute_cache_fingerprint() == req2.compute_cache_fingerprint()

    def test_extract_prompt_prefix_helper(self):
        """extract_prompt_prefix correctly isolates the prefix from the dynamic cues."""
        prompt = (
            "Translate to Greek:\n\n"
            "Episode Context Here\n\n"
            "Character Profiles Here\n\n"
            "1| First line\n"
            "2| Second line\n"
        )
        prefix = extract_prompt_prefix(prompt)
        assert "1| First line" not in prefix
        assert "2| Second line" not in prefix
        assert "Episode Context Here" in prefix
        assert "Character Profiles Here" in prefix

    def test_episode_request_structured_and_echo_attachment(self):
        """assemble_episode_request attaches schema and source-echo instructions when configured."""
        bible_structured = Bible(
            project_name="EchoTest",
            target_language="Greek",
            system_instruction="System Instruction",
            fingerprint="fp11112222333344",
            glossary={"terms": []},
            structured=True,
            model_name="gemini-2.5-flash",
        )

        lines = [{"original": "Line one"}, {"original": "Line two"}]
        indices = [0, 1]

        with patch("services.prompt_assembler.use_source_echo", return_value=True):
            req_echo = assemble_episode_request(
                bible=bible_structured,
                lines=lines,
                indices=indices,
                collect_new_terms=True,
            )
            assert req_echo.verify_echo is True
            assert req_echo.response_schema is VerifiedEpisodeTranslationResponse
            assert '"s"' in req_echo.system_instruction
            assert "new_terms" in req_echo.system_instruction


# ===========================================================================
# 2. DualLock & Rate Limiter Hardening Tests (15 RPM / 1,500 RPD)
# ===========================================================================

class TestDualLockAndRateLimiterHardening:
    """Empirical verification of DualLock token-bucket rate limiter under concurrency."""

    def test_duallock_thread_and_async_reentrancy(self):
        """DualLock supports synchronous with and async with without deadlock."""
        lock = DualLock()
        with lock:
            with lock:
                assert True

        async def _test_async():
            async with lock:
                async with lock:
                    assert True

        asyncio.run(_test_async())

    def test_rate_limiter_defaults_to_free_tier_constraints(self):
        """RateLimiter defaults to 15 RPM and 1,500 RPD."""
        limiter = RateLimiter()
        assert limiter.requests_per_minute == 15
        assert limiter.daily_limit == 1500
        stats = limiter.get_stats()
        assert stats["daily_limit"] == 1500
        assert stats["daily_count"] == 0
        assert stats["daily_exhausted"] is False

    @pytest.mark.asyncio
    async def test_concurrent_async_token_consumption_exact_count(self):
        """Multiple concurrent asyncio tasks acquire tokens with exact count accounting."""
        limiter = RateLimiter(requests_per_minute=50000, daily_limit=20000)
        num_tasks = 20
        calls_per_task = 25

        async def worker():
            for _ in range(calls_per_task):
                await limiter.acquire()

        tasks = [asyncio.create_task(worker()) for _ in range(num_tasks)]
        await asyncio.gather(*tasks)

        assert limiter._daily_count == num_tasks * calls_per_task
        assert limiter.get_stats()["daily_count"] == num_tasks * calls_per_task

    def test_concurrent_threads_acquire_sync_exact_count(self):
        """Multiple concurrent OS threads acquire tokens with exact count accounting."""
        limiter = RateLimiter(requests_per_minute=50000, daily_limit=20000)
        num_threads = 15
        calls_per_thread = 20

        def worker():
            for _ in range(calls_per_thread):
                limiter.acquire_sync()

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert limiter._daily_count == num_threads * calls_per_thread

    def test_rate_limiter_backoff_until_handling(self):
        """Reporting a 429 sets backoff_until and halves token bucket."""
        limiter = RateLimiter(requests_per_minute=15, daily_limit=1500)
        initial_tokens = limiter._tokens

        limiter.report_rate_limit(retry_after=0.1)
        assert limiter._tokens == initial_tokens * 0.5
        stats = limiter.get_stats()
        assert stats["is_backing_off"] is True
        assert stats["backoff_remaining"] > 0

        # Sync acquire waits for backoff
        t0 = time.time()
        limiter.acquire_sync()
        elapsed = time.time() - t0
        assert elapsed >= 0.08

    def test_daily_exhaustion_latching_and_reset(self):
        """trigger_daily_limit latches exhaustion; maybe_reset_daily clears on boundary."""
        limiter = RateLimiter(requests_per_minute=1000, daily_limit=1500)
        limiter.trigger_daily_limit()

        assert limiter._daily_exhausted is True
        stats = limiter.get_stats()
        assert stats["daily_exhausted"] is True

        with pytest.raises(DailyLimitExhausted):
            limiter.acquire_sync()

        # Simulate boundary crossing
        limiter._daily_reset = time.time() - 1.0
        assert limiter.is_daily_reset_due() is True
        exhausted = limiter.maybe_reset_daily()
        assert exhausted is False
        assert limiter._daily_exhausted is False
        assert limiter._daily_count == 0

    def test_per_model_rate_limiter_isolation_and_flush(self, tmp_path, monkeypatch):
        """PerModelRateLimiter manages separate limiters per model and flushes debounced state."""
        state_file = tmp_path / "rate_limits_test.json"
        monkeypatch.setattr(PerModelRateLimiter, "STATE_FILE", state_file)

        pml = PerModelRateLimiter()
        lim_flash = pml.get_limiter("gemini-flash-latest")
        lim_lite = pml.get_limiter("gemini-3.1-flash-lite")

        lim_flash.trigger_daily_limit()
        assert pml.exhausted_models() == ["gemini-flash-latest"]
        assert "gemini-3.1-flash-lite" not in pml.exhausted_models()

        pml.flush()
        assert state_file.exists()
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        assert saved["gemini-flash-latest"]["daily_exhausted"] is True
        assert saved["gemini-3.1-flash-lite"]["daily_exhausted"] is False


# ===========================================================================
# 3. API Call Wrapper & Model Resolver Quota Fallback Tests
# ===========================================================================

class TestApiCallWrapperAndModelResolverFallback:
    """Validate 429 classification, parse_retry_after, and automated fallback to Flash-Lite."""

    def test_parse_retry_after_patterns(self):
        """parse_retry_after correctly extracts delay from various exception text patterns."""
        e1 = Exception("429 ResourceExhausted: retry after 25 seconds")
        assert parse_retry_after(e1) == 25.0

        e2 = Exception("429 Quota exceeded: retry in 42.5s")
        assert parse_retry_after(e2) == 42.5

        e3 = Exception("Rate limit hit: {'retryDelay': '18s'}")
        assert parse_retry_after(e3) == 18.0

        e4 = Exception("429 ResourceExhausted: reset in 30 seconds")
        assert parse_retry_after(e4) == 30.0

        e5 = Exception("Generic 429 with no delay specified")
        assert parse_retry_after(e5) == 60.0

    def test_is_daily_quota_error_classification(self):
        """is_daily_quota_error distinguishes daily exhaustion from per-minute throttling."""
        # Daily exhaustion markers
        daily_1 = Exception("429 RESOURCE_EXHAUSTED: quotaId: GenerateRequestsPerDayPerProjectPerModel")
        daily_2 = Exception("429 Quota exceeded for requests per day")
        daily_3 = Exception("429 You have exhausted your daily quota. Please wait until tomorrow.")
        daily_4 = Exception("429 Free tier limit reached: daily quota exhausted")
        daily_5 = Exception("429 Resource exhausted: daily request limit exceeded")

        assert is_daily_quota_error(daily_1) is True
        assert is_daily_quota_error(daily_2) is True
        assert is_daily_quota_error(daily_3) is True
        assert is_daily_quota_error(daily_4) is True
        assert is_daily_quota_error(daily_5) is True

        # Per-minute throttling markers
        rpm_1 = Exception("429 RESOURCE_EXHAUSTED: quotaId: GenerateRequestsPerMinutePerProjectPerModel")
        rpm_2 = Exception("429 Quota exceeded: requests per minute")
        rpm_3 = Exception("429 Too Many Requests: tokens per minute exceeded (TPM)")
        rpm_4 = Exception("429 rate limit: 15 RPM exceeded")

        assert is_daily_quota_error(rpm_1) is False
        assert is_daily_quota_error(rpm_2) is False
        assert is_daily_quota_error(rpm_3) is False
        assert is_daily_quota_error(rpm_4) is False

    @pytest.mark.asyncio
    async def test_rate_limited_call_handles_transient_rpm_backoff(self):
        """rate_limited_call catches per-minute 429, backs off, and retries successfully."""
        limiter = RateLimiter(requests_per_minute=1000, daily_limit=1500)
        attempts = {"count": 0}

        async def api_coro():
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise google.api_core.exceptions.TooManyRequests("429 requests per minute exceeded, retry in 0.05s")
            return "SUCCESS_TRANSLATION"

        res = await rate_limited_call(
            coro_factory=api_coro,
            rate_limiter=limiter,
            max_retries=2,
            base_delay=0.05,
        )

        assert res == "SUCCESS_TRANSLATION"
        assert attempts["count"] == 2
        assert limiter._daily_exhausted is False

    @pytest.mark.asyncio
    async def test_rate_limited_call_latches_daily_limit_and_raises(self):
        """rate_limited_call catches daily quota 429, latches the limiter, and raises DailyLimitExhausted."""
        limiter = RateLimiter(requests_per_minute=1000, daily_limit=1500)

        async def failing_api():
            raise google.api_core.exceptions.ResourceExhausted(
                "429 quotaId: GenerateRequestsPerDayPerProjectPerModel exceeded. daily quota limit reached."
            )

        with pytest.raises(DailyLimitExhausted):
            await rate_limited_call(
                coro_factory=failing_api,
                rate_limiter=limiter,
                max_retries=3,
            )

        assert limiter._daily_exhausted is True
        assert limiter.get_stats()["daily_exhausted"] is True

    def test_model_resolver_seamless_fallback_to_flash_lite(self):
        """When primary model (gemini-flash-latest) is daily exhausted, resolve_model automatically fails over."""
        clear_logged_failovers()
        primary_model = "gemini-flash-latest"
        fallback_model = "gemini-3.1-flash-lite"

        # Initially resolves to primary
        meta = {
            "settings": {
                "translation_model": primary_model,
                "fallback_translation_model": fallback_model,
            }
        }
        assert resolve_model("translation", meta) == primary_model

        # Mark primary model as daily exhausted
        mark_model_daily_exhausted(primary_model)
        assert is_model_daily_exhausted(primary_model) is True

        # resolve_model now seamlessly returns fallback model
        resolved = resolve_model("translation", meta)
        assert resolved == fallback_model

        # Clean up
        clear_model_daily_exhaustion(primary_model)
        clear_logged_failovers()
        assert resolve_model("translation", meta) == primary_model

    def test_model_resolver_default_fallback_hierarchy(self):
        """When no explicit fallback is configured, falls back to gemini-3.1-flash-lite without failure."""
        clear_logged_failovers()
        primary = "gemini-2.5-flash"
        mark_model_daily_exhausted(primary)

        meta = {"settings": {"translation_model": primary}}
        resolved = resolve_model("translation", meta)
        assert resolved == "gemini-3.1-flash-lite"

        clear_model_daily_exhaustion(primary)
        clear_logged_failovers()

    @pytest.mark.asyncio
    async def test_call_with_retry_universal_wrapper(self):
        """call_with_retry wraps arbitrary callable with proactive rate limiting."""
        limiter = RateLimiter(requests_per_minute=1000, daily_limit=1500)

        def sync_compute(a, b):
            return a + b

        res = await call_with_retry(sync_compute, 10, 20, rate_limiter=limiter)
        assert res == 30
        assert limiter._daily_count == 1
