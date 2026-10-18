"""
Unit tests for DualLock, rate_limiter sync/async concurrency, and SQLite queue concurrency hardening.
"""

import asyncio
import threading
import time
import pytest

from utils.rate_limiter import DualLock, RateLimiter, DailyLimitExhausted
from utils.translation_queue import TranslationQueue


def test_duallock_sync_and_async():
    lock = DualLock()

    # Sync context
    with lock:
        assert True

    # Async context
    async def async_lock_test():
        async with lock:
            assert True

    asyncio.run(async_lock_test())


def test_rate_limiter_multithreaded_acquire_sync():
    limiter = RateLimiter(requests_per_minute=6000, daily_limit=100000)
    errors = []

    def worker():
        try:
            for _ in range(50):
                limiter.acquire_sync()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert limiter._daily_count == 500


def test_translation_queue_reset_orphaned_running(tmp_path):
    db_file = tmp_path / "test_queue.db"
    queue = TranslationQueue(db_path=db_file)

    item1_id = queue.enqueue("ProjA", "Ep01", priority=1)
    item2_id = queue.enqueue("ProjA", "Ep02", priority=1)

    # Claim item1 so it becomes 'running'
    claimed = queue.claim_next()
    assert claimed is not None
    assert claimed["status"] == "running"

    # Reset orphaned running
    reset_count = queue.reset_orphaned_running()
    assert reset_count == 1

    summary = queue.get_summary()
    assert summary["running"] == 0
    assert summary["pending"] == 2
