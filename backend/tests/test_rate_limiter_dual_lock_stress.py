"""
Stress Tests for RateLimiter, DualLock, and Concurrent Sync/Async Token Acquisition.
"""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import pytest

from utils.rate_limiter import (
    DualLock,
    RateLimiter,
    DailyLimitExhausted,
    PerModelRateLimiter,
    ProxyRateLimiter,
    active_model_var,
    _calculate_next_daily_reset,
)


class TestDualLockMechanism:
    """Empirical tests for DualLock supporting both with and async with."""

    def test_duallock_reentrancy_in_sync_thread(self):
        lock = DualLock()
        with lock:
            with lock:
                with lock:
                    assert True

    @pytest.mark.asyncio
    async def test_duallock_reentrancy_in_async_task(self):
        lock = DualLock()
        async with lock:
            async with lock:
                async with lock:
                    assert True

    def test_duallock_mutual_exclusion_between_threads(self):
        lock = DualLock()
        counter = [0]
        num_threads = 20
        increments = 500

        def worker():
            for _ in range(increments):
                with lock:
                    val = counter[0]
                    # simulate slight work
                    counter[0] = val + 1

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert counter[0] == num_threads * increments


class TestRateLimiterConcurrencyStress:
    """High-concurrency stress testing of RateLimiter with DualLock."""

    def test_multithreaded_acquire_sync_exact_accounting(self):
        # 10,000 RPM so acquire_sync won't sleep long
        limiter = RateLimiter(requests_per_minute=100000, daily_limit=50000)
        num_threads = 25
        calls_per_thread = 40
        errors = []

        def worker():
            for _ in range(calls_per_thread):
                try:
                    limiter.acquire_sync()
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors encountered: {errors}"
        expected_total = num_threads * calls_per_thread
        assert limiter._daily_count == expected_total
        assert limiter._tokens >= 0.0

    @pytest.mark.asyncio
    async def test_async_acquire_exact_accounting(self):
        limiter = RateLimiter(requests_per_minute=100000, daily_limit=50000)
        num_tasks = 25
        calls_per_task = 40

        async def worker():
            for _ in range(calls_per_task):
                await limiter.acquire()

        tasks = [asyncio.create_task(worker()) for _ in range(num_tasks)]
        await asyncio.gather(*tasks)

        expected_total = num_tasks * calls_per_task
        assert limiter._daily_count == expected_total
        assert limiter._tokens >= 0.0

    def test_mixed_sync_threads_and_async_event_loop_concurrency(self):
        """Simulate simultaneous sync background worker threads and async FastAPI requests hitting the same limiter."""
        limiter = RateLimiter(requests_per_minute=100000, daily_limit=50000)
        sync_threads_count = 10
        sync_calls_per_thread = 30

        async_tasks_count = 10
        async_calls_per_task = 30

        errors = []

        def sync_worker():
            for _ in range(sync_calls_per_thread):
                try:
                    limiter.acquire_sync()
                except Exception as e:
                    errors.append(e)

        def async_runner():
            async def async_worker():
                for _ in range(async_calls_per_task):
                    try:
                        await limiter.acquire()
                    except Exception as e:
                        errors.append(e)

            async def main_async():
                tasks = [asyncio.create_task(async_worker()) for _ in range(async_tasks_count)]
                await asyncio.gather(*tasks)

            asyncio.run(main_async())

        threads = [threading.Thread(target=sync_worker) for _ in range(sync_threads_count)]
        async_thread = threading.Thread(target=async_runner)

        for t in threads:
            t.start()
        async_thread.start()

        for t in threads:
            t.join()
        async_thread.join()

        assert len(errors) == 0, f"Encountered errors in mixed sync/async concurrency: {errors}"
        expected_total = (sync_threads_count * sync_calls_per_thread) + (async_tasks_count * async_calls_per_task)
        assert limiter._daily_count == expected_total

    def test_daily_exhaustion_raises_in_both_sync_and_async(self):
        limiter = RateLimiter(requests_per_minute=1000, daily_limit=10)
        limiter.trigger_daily_limit()

        # Sync check
        with pytest.raises(DailyLimitExhausted):
            limiter.acquire_sync()

        # Async check
        async def async_check():
            with pytest.raises(DailyLimitExhausted):
                await limiter.acquire()

        asyncio.run(async_check())

    def test_daily_reset_rollover_clears_exhaustion(self):
        limiter = RateLimiter(requests_per_minute=1000, daily_limit=100)
        limiter.trigger_daily_limit()
        assert limiter._daily_exhausted is True

        # Simulate time moving past daily reset
        future_time = limiter._daily_reset + 1.0
        assert limiter.is_daily_reset_due(future_time) is True

        # maybe_reset_daily
        limiter._daily_reset = time.time() - 10  # in the past
        still_exhausted = limiter.maybe_reset_daily()
        assert still_exhausted is False
        assert limiter._daily_exhausted is False
        assert limiter._daily_count == 0

    def test_backoff_until_and_429_report(self):
        limiter = RateLimiter(requests_per_minute=6000, daily_limit=1000)
        initial_tokens = limiter._tokens

        # Report 429
        limiter.report_rate_limit(retry_after=0.2)
        assert limiter._tokens == initial_tokens * 0.5
        stats = limiter.get_stats()
        assert stats["is_backing_off"] is True
        assert stats["backoff_remaining"] > 0

        # acquire_sync will wait for backoff to expire
        start = time.time()
        limiter.acquire_sync()
        duration = time.time() - start
        assert duration >= 0.15  # Waited approximately the backoff delay


class TestPerModelAndProxyRateLimiter:
    """Test PerModelRateLimiter and ProxyRateLimiter thread safety."""

    def test_proxy_rate_limiter_routes_via_context_var(self, tmp_path, monkeypatch):
        pml = PerModelRateLimiter()
        proxy = ProxyRateLimiter(pml)

        active_model_var.set("gemini-2.5-flash")
        limiter1 = proxy.current_limiter
        assert limiter1.model_name == "gemini-2.5-flash"

        active_model_var.set("gemini-2.5-pro")
        limiter2 = proxy.current_limiter
        assert limiter2.model_name == "gemini-2.5-pro"
        assert limiter1 is not limiter2

    def test_concurrent_get_limiter_creation(self):
        pml = PerModelRateLimiter()
        limiters = []
        errors = []

        def worker(idx):
            try:
                model_name = f"model-{idx % 5}"
                lim = pml.get_limiter(model_name)
                limiters.append(lim)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(pml._limiters) == 6  # default + 5 models
