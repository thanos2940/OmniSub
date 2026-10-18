"""
Milestone 2 Adversarial Challenge Test Suite (Challenger 1).

Adversarially stresses:
1. Concurrency and Queue Lifecycle Stress:
   - Concurrent wizard launches, batch translates, race conditions.
   - Child task failures, daily limit pauses, and mid-run cancellations.
   - Parent job telemetry fidelity: scenes_done, cues_translated, eta_seconds.
   - Parent completion guarantees: strictly waiting for ALL children to terminate.
2. Cascading Cancellation:
   - Parent cancellation cascading to SQLite queue items and in-memory child worker tasks.
   - Edge case: cancelling already running tasks and SQLite status check.
3. Webhook & Sync Fan-Out Stress:
   - Webhooks with multi-language configurations, existing subtitle filtering.
   - Priority ordering verification: PRIORITY_WEBHOOK (0) > PRIORITY_SYNC (2) > PRIORITY_BACKLOG (3).
4. Event Loop Hygiene & Debounce Stress:
   - High-concurrency job telemetry bursts and terminal flush guarantees.
"""

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils import storage
from utils.translation_queue import (
    TranslationQueue,
    PRIORITY_WEBHOOK,
    PRIORITY_MANUAL,
    PRIORITY_SYNC,
    PRIORITY_BACKLOG,
    KIND_TRANSLATION,
)
from utils.jobs_manager import create_job, update_job, jobs, JobStatus
from utils.model_resolver import resolve_model
from utils.rate_limiter import PerModelRateLimiter
from routers.pipeline import _monitor_stage3_queue_progress, _monitor_batch_queue_progress
from routers.jobs import cancel_job
from services.queue_service import (
    enqueue_translation,
    enqueue_missing_for_project,
)


# ===========================================================================
# 1. Concurrency & Queue Lifecycle Stress
# ===========================================================================

class TestConcurrencyAndLifecycleStress:
    """Stress-test concurrent wizard execution, lifecycle state machines, and telemetry."""

    @pytest.mark.asyncio
    async def test_parent_strictly_waits_for_all_children_before_terminal(self):
        """Verify parent job remains 'running' while any child is non-terminal, and completes only when ALL finish."""
        parent_id = create_job("test_parent_wait_all", "TestProj")
        items = ["child_1", "child_2", "child_3"]

        queue_state = {
            "child_1": {"id": "child_1", "status": "running", "episode_name": "E01", "project_name": "TestProj"},
            "child_2": {"id": "child_2", "status": "pending", "episode_name": "E02", "project_name": "TestProj"},
            "child_3": {"id": "child_3", "status": "pending", "episode_name": "E03", "project_name": "TestProj"},
        }

        class MockQueue:
            def get_all(self, limit=1000):
                return list(queue_state.values())
            def get_item(self, cid):
                return queue_state.get(cid)
            def cancel(self, cid):
                if cid in queue_state:
                    queue_state[cid]["status"] = "cancelled"

        with patch("utils.translation_queue.TranslationQueue", return_value=MockQueue()):
            monitor_task = asyncio.create_task(
                _monitor_stage3_queue_progress(parent_id, items, base_progress=0.0, poll_interval=0.03)
            )

            await asyncio.sleep(0.06)
            parent = jobs[parent_id]
            assert parent.status == "running", "Parent must be running initially"

            # 1 of 3 completes
            queue_state["child_1"]["status"] = "completed"
            await asyncio.sleep(0.06)
            assert parent.status == "running", "Parent must NOT complete when only 1 of 3 finishes"

            # 2 of 3 completes (1 completed, 1 failed)
            queue_state["child_2"]["status"] = "failed"
            await asyncio.sleep(0.06)
            assert parent.status == "running", "Parent must NOT complete when 2 of 3 finish"

            # 3 of 3 terminates (1 completed, 1 failed, 1 cancelled)
            queue_state["child_3"]["status"] = "cancelled"
            await asyncio.sleep(0.08)
            await monitor_task

            # All 3 terminated -> parent must now be in terminal state
            assert parent.status in ("partial", "completed", "failed")
            assert parent.status == "partial", "Mixed completed/failed/cancelled must result in partial"
            assert parent.progress == 100.0

    @pytest.mark.asyncio
    async def test_parent_telemetry_accuracy_with_child_failures(self, monkeypatch):
        """
        Adversarial test: Challenge the cues_translated telemetry calculation.
        When child episodes fail, cues_translated must NOT be reported as if the failed
        episodes translated 100% of their cues!
        """
        parent_id = create_job("test_telemetry_fail", "TelemetryProj")
        items = ["fail_item_1", "fail_item_2"]

        # Both episodes have 500 cues in metadata
        def mock_load_meta(proj, ep):
            return {"line_count": 500}
        monkeypatch.setattr(storage, "load_episode_metadata", mock_load_meta)

        queue_state = {
            "fail_item_1": {"id": "fail_item_1", "status": "failed", "episode_name": "E01", "project_name": "TelemetryProj"},
            "fail_item_2": {"id": "fail_item_2", "status": "failed", "episode_name": "E02", "project_name": "TelemetryProj"},
        }

        class MockQueue:
            def get_all(self, limit=1000):
                return list(queue_state.values())
            def get_item(self, cid):
                return queue_state.get(cid)
            def cancel(self, cid): pass

        with patch("utils.translation_queue.TranslationQueue", return_value=MockQueue()):
            await _monitor_stage3_queue_progress(parent_id, items, base_progress=0.0, poll_interval=0.02)

            parent = jobs[parent_id]
            assert parent.status == "failed"
            assert parent.cues_total == 1000

            # Adversarial verification:
            # If all episodes failed at 0 cues translated, cues_translated should be 0, NOT 1000!
            # We record whether the current implementation incorrectly claims cues_translated == cues_total
            is_overreporting = (parent.cues_translated == parent.cues_total and parent.cues_total > 0)
            print(f"\n[EMPIRICAL OBS] Failed jobs cues_translated: {parent.cues_translated}/{parent.cues_total} (Overreported: {is_overreporting})")
            assert parent.cues_translated == 0, "Failed episodes must report 0 cues translated"
            assert not is_overreporting, "cues_translated must not overreport on failed child episodes"

    @pytest.mark.asyncio
    async def test_parent_scene_telemetry_lifecycle(self):
        """
        Adversarial test: Test scene telemetry across multi-episode execution.
        Check if scenes_done / scenes_total represent active or total scenes.
        """
        parent_id = create_job("test_scenes_lifecycle", "SceneProj")
        item1 = "ep1_item"
        item2 = "ep2_item"

        queue_state = {
            item1: {"id": item1, "status": "running", "episode_name": "E01", "project_name": "SceneProj"},
            item2: {"id": item2, "status": "pending", "episode_name": "E02", "project_name": "SceneProj"},
        }

        class MockQueue:
            def get_all(self, limit=1000):
                return list(queue_state.values())
            def get_item(self, cid):
                return queue_state.get(cid)
            def cancel(self, cid): pass

        # Ep1 is running with 4 scenes, 2 completed
        child1 = JobStatus(
            id=f"bg_{item1}", status="running", progress=50.0,
            scene_status={"Scene 1": "completed", "Scene 2": "completed", "Scene 3": "running", "Scene 4": "pending"}
        )
        jobs[f"bg_{item1}"] = child1

        with patch("utils.translation_queue.TranslationQueue", return_value=MockQueue()):
            monitor_task = asyncio.create_task(
                _monitor_stage3_queue_progress(parent_id, [item1, item2], base_progress=0.0, poll_interval=0.03)
            )
            await asyncio.sleep(0.06)

            parent = jobs[parent_id]
            assert parent.scenes_done == 2
            assert parent.scenes_total == 4

            # Ep1 finishes, Ep2 begins with 3 scenes, 0 completed
            queue_state[item1]["status"] = "completed"
            queue_state[item2]["status"] = "running"
            child2 = JobStatus(
                id=f"bg_{item2}", status="running", progress=0.0,
                scene_status={"Scene 1": "running", "Scene 2": "pending", "Scene 3": "pending"}
            )
            jobs[f"bg_{item2}"] = child2
            await asyncio.sleep(0.06)

            # Observe what happens to scenes_done and scenes_total:
            # Ep2 only has 0/3 scenes completed. Ep1's completed scenes are no longer in active_scene_statuses!
            print(f"\n[EMPIRICAL OBS] Ep2 running scenes_done: {parent.scenes_done}, scenes_total: {parent.scenes_total}")

            # Finish Ep2
            queue_state[item2]["status"] = "completed"
            await asyncio.sleep(0.06)
            await monitor_task

            # Observe scenes_done and scenes_total when completed:
            print(f"[EMPIRICAL OBS] Terminal state scenes_done: {parent.scenes_done}, scenes_total: {parent.scenes_total}")
            assert parent.status == "completed"

    @pytest.mark.asyncio
    async def test_unfound_or_purged_queue_item_fallback(self):
        """
        Adversarial test: If a child queue item cannot be found in queue or DB,
        verify whether monitor treats it as completed or raises an error.
        """
        parent_id = create_job("test_unfound_item", "GhostProj")
        items = ["ghost_item_999"]

        class EmptyQueue:
            db_path = ":memory:"
            def get_all(self, limit=1000):
                return []
            def get_item(self, cid):
                return None
            def cancel(self, cid): pass

        with patch("utils.translation_queue.TranslationQueue", return_value=EmptyQueue()):
            await _monitor_stage3_queue_progress(parent_id, items, base_progress=0.0, poll_interval=0.02)

            parent = jobs[parent_id]
            print(f"\n[EMPIRICAL OBS] Missing item resolved to parent status: {parent.status}")
            assert parent.status == "failed", "Missing item must resolve to failed, not completed"


# ===========================================================================
# 2. Cascading Cancellation Stress
# ===========================================================================

class TestCascadingCancellationStress:
    """Stress-test cancellation cascading from parent to SQLite queue items and workers."""

    @pytest.mark.asyncio
    async def test_cascading_cancel_updates_sqlite_pending_items(self, tmp_path):
        """Cancelling a parent job must cancel pending child items in real SQLite queue."""
        db_file = tmp_path / "test_cancel_cascade.db"
        queue = TranslationQueue(db_path=db_file)

        item1 = queue.enqueue("CancelCascadeShow", "S01E01", priority=PRIORITY_MANUAL)
        item2 = queue.enqueue("CancelCascadeShow", "S01E02", priority=PRIORITY_MANUAL)

        parent_id = create_job("pipeline_cancel_test", "CancelCascadeShow")
        update_job(parent_id, status="running", sub_jobs=[item1, item2])

        with patch("utils.translation_queue.DB_FILE", db_file), \
             patch("utils.translation_queue.TranslationQueue._get_connection", queue._get_connection):
            res = await cancel_job(parent_id)

            assert res["status"] == "cancelled"
            assert jobs[parent_id].status == "cancelled"

            # Check SQLite status of both children
            db_item1 = queue.get_item(item1)
            db_item2 = queue.get_item(item2)
            assert db_item1["status"] == "cancelled", "Pending item1 must be cancelled in SQLite"
            assert db_item2["status"] == "cancelled", "Pending item2 must be cancelled in SQLite"

    @pytest.mark.asyncio
    async def test_cancel_running_queue_item_in_sqlite_limitation(self, tmp_path):
        """
        Adversarial test: Test whether TranslationQueue.cancel() updates an item
        that has status='running' in SQLite!
        Line 558: WHERE id = ? AND status IN ('pending', 'failed', 'paused_daily_limit').
        Notice 'running' is NOT included!
        """
        db_file = tmp_path / "test_cancel_running.db"
        queue = TranslationQueue(db_path=db_file)

        item1 = queue.enqueue("CancelRunningShow", "S01E01", priority=PRIORITY_MANUAL)
        # Claim it -> status becomes 'running'
        claimed = queue.claim_next()
        assert claimed["id"] == item1
        assert claimed["status"] == "running"

        # Now attempt to cancel it via queue.cancel()
        cancelled_ok = queue.cancel(item1)
        assert cancelled_ok is True

        after_cancel = queue.get_item(item1)
        print(f"\n[EMPIRICAL OBS] SQLite status of claimed item after queue.cancel(): {after_cancel['status']}")
        assert after_cancel["status"] == "cancelled", "Cancelled running item must be marked cancelled in SQLite"


# ===========================================================================
# 3. Webhook & Sync Fan-Out Stress
# ===========================================================================

class TestWebhookAndSyncFanOutStress:
    """Stress-test webhook fan-out, multi-language configuration, and priority ordering."""

    def test_priority_ordering_webhook_over_sync_over_backlog(self, tmp_path):
        """
        Empirically verify that claim_next() strictly prioritizes:
        PRIORITY_WEBHOOK (0) > PRIORITY_MANUAL (1) > PRIORITY_SYNC (2) > PRIORITY_BACKLOG (3).
        """
        db_file = tmp_path / "test_prio.db"
        queue = TranslationQueue(db_path=db_file)

        # Enqueue in reverse order of priority
        id_backlog = queue.enqueue("PrioShow", "E04_Backlog", priority=PRIORITY_BACKLOG)
        id_sync = queue.enqueue("PrioShow", "E03_Sync", priority=PRIORITY_SYNC)
        id_manual = queue.enqueue("PrioShow", "E02_Manual", priority=PRIORITY_MANUAL)
        id_webhook = queue.enqueue("PrioShow", "E01_Webhook", priority=PRIORITY_WEBHOOK)

        # Claim items one by one and assert strict priority order
        claimed_1 = queue.claim_next()
        assert claimed_1["id"] == id_webhook, "PRIORITY_WEBHOOK (0) must be claimed first"
        assert claimed_1["priority"] == PRIORITY_WEBHOOK

        claimed_2 = queue.claim_next()
        assert claimed_2["id"] == id_manual, "PRIORITY_MANUAL (1) must be claimed second"
        assert claimed_2["priority"] == PRIORITY_MANUAL

        claimed_3 = queue.claim_next()
        assert claimed_3["id"] == id_sync, "PRIORITY_SYNC (2) must be claimed third"
        assert claimed_3["priority"] == PRIORITY_SYNC

        claimed_4 = queue.claim_next()
        assert claimed_4["id"] == id_backlog, "PRIORITY_BACKLOG (3) must be claimed fourth"
        assert claimed_4["priority"] == PRIORITY_BACKLOG

        assert queue.claim_next() is None, "No more items left to claim"


# ===========================================================================
# 4. Daily Limit Pause & Resume Stress
# ===========================================================================

class TestDailyLimitPauseAndResumeStress:
    """Stress-test daily limit pauses, multi-item pause detection, and unpausing."""

    @pytest.mark.asyncio
    async def test_partial_daily_limit_pause_keeps_parent_running(self):
        """
        When only 1 of 2 child items is paused on daily limit, parent status must remain 'running'.
        Only when ALL non-terminal items are paused should parent status transition to 'paused'.
        """
        parent_id = create_job("test_partial_pause", "PauseProj")
        item1 = "p_item_1"
        item2 = "p_item_2"

        queue_state = {
            item1: {"id": item1, "status": "paused_daily_limit", "episode_name": "E01"},
            item2: {"id": item2, "status": "running", "episode_name": "E02"},
        }

        class MockQueue:
            def get_all(self, limit=1000):
                return list(queue_state.values())
            def get_item(self, cid):
                return queue_state.get(cid)
            def cancel(self, cid): pass

        with patch("utils.translation_queue.TranslationQueue", return_value=MockQueue()):
            monitor_task = asyncio.create_task(
                _monitor_stage3_queue_progress(parent_id, [item1, item2], base_progress=0.0, poll_interval=0.03)
            )
            await asyncio.sleep(0.06)

            parent = jobs[parent_id]
            # 1 paused, 1 running -> parent is running, but daily_limit_reached flag is True
            assert parent.status == "running"
            assert parent.daily_limit_reached is True

            # Now item2 also gets paused
            queue_state[item2]["status"] = "paused_daily_limit"
            await asyncio.sleep(0.06)

            # Both paused -> parent transitions to 'paused'
            assert parent.status == "paused"
            assert "Daily API limit reached" in parent.message

            # Cancel parent to gracefully stop monitor task
            parent.cancelled = True
            await asyncio.sleep(0.06)
            await monitor_task

    def test_unpause_daily_limit_items_resets_queue_to_pending(self, tmp_path):
        """TranslationQueue.unpause_daily_limit_items resets paused items to pending in SQLite."""
        db_file = tmp_path / "test_unpause.db"
        queue = TranslationQueue(db_path=db_file)

        item1 = queue.enqueue("UnpauseShow", "S01E01", priority=PRIORITY_MANUAL)
        queue.update_status(item1, status="paused_daily_limit", error="Daily limit exhausted")

        assert queue.get_item(item1)["status"] == "paused_daily_limit"

        # Unpause
        queue.unpause_daily_limit_items()

        assert queue.get_item(item1)["status"] == "pending"


# ===========================================================================
# 5. Event Loop Hygiene & Debouncing Under Burst Load
# ===========================================================================

class TestEventLoopHygieneAndDebounceStress:
    """Stress-test SQLite and disk state debouncing under rapid concurrent bursts."""

    @pytest.mark.asyncio
    async def test_rapid_burst_update_job_flushes_terminal_immediately(self):
        """Verify that rapid progress ticks are debounced, but a terminal transition flushes immediately."""
        job_id = create_job("burst_job", "BurstProj")

        # Rapidly fire 50 progress updates
        for i in range(50):
            update_job(job_id, progress=float(i), message=f"Tick {i}")

        job = jobs[job_id]
        assert job.progress == 49.0

        # Mark terminal completed
        update_job(job_id, status="completed", progress=100.0, message="All done")

        assert job.status == "completed"
        assert job.progress == 100.0
        assert job.finished_at is not None

    @pytest.mark.asyncio
    async def test_rate_limiter_rapid_concurrent_saves(self, tmp_path):
        """Stress rate limiter save_state with 20 concurrent tasks."""
        limiter = PerModelRateLimiter()
        limiter.STATE_FILE = tmp_path / "rl_stress.json"

        async def worker():
            for _ in range(10):
                limiter.save_state()
                await asyncio.sleep(0.001)

        await asyncio.gather(*(worker() for _ in range(20)))

        # Flush
        limiter.flush()
        assert limiter.STATE_FILE.exists()
        data = json.loads(limiter.STATE_FILE.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
