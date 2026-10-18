"""
Empirical stress-testing suite for SQLite WAL queue concurrency, transaction safety,
priority scheduling, orphan job recovery, and busy_timeout resilience.
"""

import os
import time
import uuid
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from utils.translation_queue import (
    TranslationQueue,
    PRIORITY_WEBHOOK,
    PRIORITY_MANUAL,
    PRIORITY_SYNC,
    PRIORITY_BACKLOG,
    KIND_TRANSLATION,
    KIND_EXTRACTION,
)


def test_concurrent_threads_atomic_claim_zero_double_claims(tmp_path):
    """
    Stress-test queue claims under 20 concurrent worker threads.
    Uses threading.Barrier to release all threads simultaneously against the queue.
    Asserts BEGIN IMMEDIATE ensures zero double-claims and exactly all items claimed.
    """
    db_file = tmp_path / "stress_claim.db"
    queue = TranslationQueue(db_path=db_file)

    num_items = 100
    num_workers = 20

    # Enqueue 100 items with interleaved priorities
    enqueued_ids = []
    for i in range(num_items):
        prio = i % 4
        item_id = queue.enqueue("StressProject", f"Episode_{i:04d}", priority=prio)
        enqueued_ids.append(item_id)

    barrier = threading.Barrier(num_workers)
    claimed_results = []  # tuples of (worker_id, item_dict)
    errors = []

    def worker_thread(worker_id):
        try:
            # Wait for all workers to be ready to maximize simultaneous hit
            barrier.wait(timeout=10.0)
            while True:
                item = queue.claim_next()
                if not item:
                    break
                claimed_results.append((worker_id, item))
                # Micro-sleep to simulate brief processing
                time.sleep(0.001)
        except Exception as e:
            errors.append((worker_id, e))

    threads = [threading.Thread(target=worker_thread, args=(i,)) for i in range(num_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)

    # 1. Zero exceptions / deadlock errors
    assert len(errors) == 0, f"Worker errors encountered: {errors}"

    # 2. Exactly 100 claims made
    assert len(claimed_results) == num_items, f"Expected {num_items} claims, got {len(claimed_results)}"

    # 3. Zero double-claims: all claimed IDs are unique
    claimed_ids = [item["id"] for _, item in claimed_results]
    assert len(set(claimed_ids)) == num_items, "Duplicate item claims detected!"
    assert set(claimed_ids) == set(enqueued_ids)

    # 4. In SQLite, verify all rows are 'running' with attempts == 1
    summary = queue.get_summary()
    assert summary["running"] == num_items
    assert summary["pending"] == 0


def test_concurrent_extraction_and_translation_lanes_isolation(tmp_path):
    """
    Stress-test two distinct work lanes (translation vs extraction) running concurrently.
    Ensures extraction claims never pull translation items, and vice-versa.
    """
    db_file = tmp_path / "stress_lanes.db"
    queue = TranslationQueue(db_path=db_file)

    num_items = 40
    num_workers = 10

    trans_ids = []
    extract_ids = []
    for i in range(num_items):
        t_id = queue.enqueue("ProjectLanes", f"Ep_T_{i}", priority=1, kind=KIND_TRANSLATION)
        e_id = queue.enqueue("ProjectLanes", f"Ep_E_{i}", priority=1, kind=KIND_EXTRACTION)
        trans_ids.append(t_id)
        extract_ids.append(e_id)

    barrier = threading.Barrier(num_workers * 2)
    claimed_trans = []
    claimed_extract = []
    errors = []

    def translation_worker(wid):
        try:
            barrier.wait(timeout=10.0)
            while True:
                item = queue.claim_next()
                if not item:
                    break
                claimed_trans.append(item)
                time.sleep(0.001)
        except Exception as e:
            errors.append(("translation", wid, e))

    def extraction_worker(wid):
        try:
            barrier.wait(timeout=10.0)
            while True:
                item = queue.claim_next_extraction()
                if not item:
                    break
                claimed_extract.append(item)
                time.sleep(0.001)
        except Exception as e:
            errors.append(("extraction", wid, e))

    threads = []
    for i in range(num_workers):
        threads.append(threading.Thread(target=translation_worker, args=(i,)))
        threads.append(threading.Thread(target=extraction_worker, args=(i,)))

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)

    assert len(errors) == 0, f"Errors: {errors}"
    assert len(claimed_trans) == num_items
    assert len(claimed_extract) == num_items

    # Verify kinds are strictly partitioned
    assert all(item["kind"] == KIND_TRANSLATION for item in claimed_trans)
    assert all(item["kind"] == KIND_EXTRACTION for item in claimed_extract)
    assert set(item["id"] for item in claimed_trans) == set(trans_ids)
    assert set(item["id"] for item in claimed_extract) == set(extract_ids)


def test_concurrent_high_load_mixed_crud_and_busy_timeout(tmp_path):
    """
    Stress-test heavy multi-threaded CRUD operations (enqueue, claim, update, batch-claim,
    repend, get_summary, clear) under high write contention.
    Verifies SQLite WAL concurrency and busy_timeout=30000 prevent 'database is locked' errors.
    """
    db_file = tmp_path / "stress_crud.db"
    queue = TranslationQueue(db_path=db_file)

    num_threads = 16
    iterations_per_thread = 40
    errors = []

    stop_flag = threading.Event()

    def crud_worker(tid):
        try:
            for it in range(iterations_per_thread):
                op = (tid + it) % 6
                if op == 0:
                    # Enqueue
                    queue.enqueue(f"Proj_{tid}", f"Ep_{it}_{uuid.uuid4().hex[:6]}", priority=(it % 4))
                elif op == 1:
                    # Claim & Update
                    item = queue.claim_next()
                    if item:
                        queue.update_status(item["id"], status="completed", review_count=it % 3)
                elif op == 2:
                    # Batch claim & repend
                    batch = queue.claim_backlog_window(limit=5)
                    for b in batch:
                        queue.repend(b["id"])
                elif op == 3:
                    # Read summary & priority counts
                    _ = queue.get_summary()
                    _ = queue.counts_by_priority()
                    _ = queue.get_all(limit=10)
                elif op == 4:
                    # Clear completed/failed
                    queue.clear()
                elif op == 5:
                    # Orphan recovery & prioritize
                    queue.reset_orphaned_running()
                time.sleep(0.0005)
        except Exception as e:
            errors.append((tid, e))

    threads = [threading.Thread(target=crud_worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=45.0)

    assert len(errors) == 0, f"Encountered CRUD concurrency errors: {errors}"

    # Verify SQLite DB is in a healthy, uncorrupted state
    with queue._get_connection() as conn:
        res = conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert res == "ok"


def test_priority_scheduling_with_offpeak_ceiling(tmp_path):
    """
    Verify strict priority ordering (0=webhook > 1=manual > 2=sync > 3=backlog)
    and respect for off-peak max_priority ceiling filter.
    """
    db_file = tmp_path / "priority.db"
    queue = TranslationQueue(db_path=db_file)

    id_sync = queue.enqueue("P", "SyncEp", priority=PRIORITY_SYNC)
    time.sleep(0.002)
    id_backlog = queue.enqueue("P", "BacklogEp", priority=PRIORITY_BACKLOG)
    time.sleep(0.002)
    id_manual = queue.enqueue("P", "ManualEp", priority=PRIORITY_MANUAL)
    time.sleep(0.002)
    id_webhook = queue.enqueue("P", "WebhookEp", priority=PRIORITY_WEBHOOK)

    # 1. Off-peak ceiling: max_priority = 1 (only allows 0 and 1, ignores 2 and 3)
    claimed_1 = queue.claim_next(max_priority=1)
    assert claimed_1["id"] == id_webhook
    assert claimed_1["priority"] == PRIORITY_WEBHOOK

    claimed_2 = queue.claim_next(max_priority=1)
    assert claimed_2["id"] == id_manual
    assert claimed_2["priority"] == PRIORITY_MANUAL

    # No more items with priority <= 1
    assert queue.claim_next(max_priority=1) is None

    # 2. Prioritize backlog item directly to priority 0
    queue.prioritize(id_backlog)

    # Now with max_priority=1, the reprioritized backlog item should be claimable
    claimed_3 = queue.claim_next(max_priority=1)
    assert claimed_3["id"] == id_backlog
    assert claimed_3["priority"] == 0

    # 3. Unrestricted claim claims remaining sync item (priority 2)
    claimed_4 = queue.claim_next()
    assert claimed_4["id"] == id_sync
    assert claimed_4["priority"] == PRIORITY_SYNC

    assert queue.claim_next() is None


def test_orphan_job_recovery_after_simulated_worker_crash(tmp_path):
    """
    Simulate a crash while worker threads have jobs in 'running'.
    Verify reset_orphaned_running resets all 'running' items to 'pending',
    preserves attempts, and allows them to be successfully claimed again.
    """
    db_file = tmp_path / "orphan_recovery.db"
    queue = TranslationQueue(db_path=db_file)

    num_items = 20
    ids = [queue.enqueue("CrashProject", f"Ep_{i}", priority=1) for i in range(num_items)]

    # Claim first 10 items (status -> 'running', attempts -> 1)
    claimed = []
    for _ in range(10):
        item = queue.claim_next()
        assert item is not None
        claimed.append(item["id"])

    summary_before = queue.get_summary()
    assert summary_before["running"] == 10
    assert summary_before["pending"] == 10

    # Simulate crash & restart recovery
    recovered_count = queue.reset_orphaned_running()
    assert recovered_count == 10

    summary_after = queue.get_summary()
    assert summary_after["running"] == 0
    assert summary_after["pending"] == num_items

    # Re-claim items: orphaned items now have attempts == 2 upon re-claim
    reclaimed_items = []
    for _ in range(num_items):
        item = queue.claim_next()
        assert item is not None
        reclaimed_items.append(item)

    for item in reclaimed_items:
        if item["id"] in claimed:
            assert item["attempts"] == 2
        else:
            assert item["attempts"] == 1


def test_retry_backoff_and_delayed_claim_scheduling(tmp_path):
    """
    Verify queue honors next_retry_at backoff timestamps:
    - Items with future next_retry_at are skipped even if higher priority.
    - Items with past next_retry_at or NULL are claimed.
    - retry_now clears next_retry_at and makes the item immediately claimable.
    """
    db_file = tmp_path / "retry_backoff.db"
    queue = TranslationQueue(db_path=db_file)

    now = datetime.now(timezone.utc)
    future_time = (now + timedelta(hours=2)).isoformat()
    past_time = (now - timedelta(minutes=5)).isoformat()

    # Item A: Priority 0 (webhook) BUT next_retry_at in the future (+2 hours)
    id_a = queue.enqueue("P", "EpA", priority=PRIORITY_WEBHOOK)
    queue.update_status(id_a, status="pending", next_retry_at=future_time, attempts=1)

    # Item B: Priority 2 (sync) with next_retry_at in the past (-5 mins)
    id_b = queue.enqueue("P", "EpB", priority=PRIORITY_SYNC)
    queue.update_status(id_b, status="pending", next_retry_at=past_time, attempts=1)

    # Item C: Priority 3 (backlog) with next_retry_at is NULL
    id_c = queue.enqueue("P", "EpC", priority=PRIORITY_BACKLOG)

    # 1. Claim: Item A is skipped due to future retry; Item B is claimed first (priority 2), then Item C (priority 3)
    claimed_1 = queue.claim_next()
    assert claimed_1["id"] == id_b

    claimed_2 = queue.claim_next()
    assert claimed_2["id"] == id_c

    # No more eligible items
    assert queue.claim_next() is None

    # 2. Force retry on Item A
    queue.retry_now(id_a)

    # Item A is now immediately claimable
    claimed_3 = queue.claim_next()
    assert claimed_3["id"] == id_a
    assert claimed_3["attempts"] == 1  # retry_now resets attempts to 0, claim increments to 1


def test_sqlite_busy_timeout_contention_and_wal_durability(tmp_path):
    """
    Empirically test that a concurrent long-running exclusive write transaction
    causes other connections to wait and succeed via busy_timeout=30000 instead of failing.
    """
    db_file = tmp_path / "busy_test.db"
    queue = TranslationQueue(db_path=db_file)

    # Seed an item
    item_id = queue.enqueue("BusyProj", "Ep1", priority=1)

    lock_acquired = threading.Event()
    lock_released = threading.Event()
    errors = []

    def holding_connection_thread():
        try:
            # Open a separate connection and hold an exclusive IMMEDIATE write lock for 0.4s
            conn = sqlite3.connect(db_file, timeout=30.0)
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE translation_queue SET logs = 'lock_held' WHERE id = ?", (item_id,))
            lock_acquired.set()
            time.sleep(0.4)
            conn.commit()
            conn.close()
            lock_released.set()
        except Exception as e:
            errors.append(("holder", e))

    def contending_worker_thread():
        try:
            # Wait until lock is actively held by the first thread
            lock_acquired.wait(timeout=5.0)
            # This enqueue call must wait out the lock and succeed within busy_timeout
            new_id = queue.enqueue("BusyProj", "Ep2", priority=1)
            assert new_id is not None
        except Exception as e:
            errors.append(("contender", e))

    t1 = threading.Thread(target=holding_connection_thread)
    t2 = threading.Thread(target=contending_worker_thread)

    t1.start()
    t2.start()

    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    assert len(errors) == 0, f"Lock contention produced unexpected errors: {errors}"
    assert lock_released.is_set()

    # Both items exist in the queue
    summary = queue.get_summary()
    assert summary["pending"] == 2


def test_daily_rate_limit_pause_and_unpause_workflow(tmp_path):
    """
    Verify daily rate limit pause sets status to 'paused_daily_limit',
    and unpause_daily_limit_items safely unpauses eligible models based on status probe callback.
    """
    db_file = tmp_path / "daily_limit.db"
    queue = TranslationQueue(db_path=db_file)

    id_model_a1 = queue.enqueue("P", "EpA1", priority=1, options={"model": "gemini-2.5-flash"})
    id_model_a2 = queue.enqueue("P", "EpA2", priority=1, options={"model": "gemini-2.5-flash"})
    id_model_b1 = queue.enqueue("P", "EpB1", priority=1, options={"model": "gemini-2.5-pro"})

    # Mark all three as paused_daily_limit
    queue.update_status(id_model_a1, status="paused_daily_limit")
    queue.update_status(id_model_a2, status="paused_daily_limit")
    queue.update_status(id_model_b1, status="paused_daily_limit")

    summary = queue.get_summary()
    assert summary["paused_daily_limit"] == 3
    assert summary["pending"] == 0

    # Unpause callback: gemini-2.5-flash is recovered (is_exhausted=False), gemini-2.5-pro is still exhausted (is_exhausted=True)
    def mock_is_exhausted(model_name: str) -> bool:
        if "pro" in model_name:
            return True
        return False

    queue.unpause_daily_limit_items(is_model_exhausted_fn=mock_is_exhausted)

    summary_after = queue.get_summary()
    assert summary_after["pending"] == 2  # A1 and A2 unpaused
    assert summary_after["paused_daily_limit"] == 1  # B1 remains paused

    # Now unpause unconditionally (e.g. daily boundary reset for all)
    queue.unpause_daily_limit_items(is_model_exhausted_fn=None)
    summary_final = queue.get_summary()
    assert summary_final["pending"] == 3
    assert summary_final["paused_daily_limit"] == 0
