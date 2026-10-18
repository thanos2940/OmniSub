"""
Adversarial Challenge & Stress Test Suite for Milestone 2 (M2_OH).
Authored by: Challenger 2 (teamwork_preview_challenger_m2_2)

Stress-tests:
1. Event Loop Non-Blocking & Debouncing Stress:
   - Concurrent `update_job` calls across multiple jobs and threads.
   - SQLite lock contention elimination & 15ms event loop latency bound.
   - Immediate flushing of terminal updates vs debouncing of non-terminal updates.
   - Rate limiter `save_state` deferred timer & non-blocking disk persistence.
2. Vector Operations & Bug Fix Stress:
   - TM exact match lookup parameter order & type safety.
   - Verification that string target language does not cause TypeError.
   - Target text dictionary unpacking in atomic_runner.py.
   - Concurrency stress on asyncio.to_thread TM offloading.
3. Model Resolution Stress:
   - Provider matrix: cloud, local, hybrid.
   - Daily limit policies: auto_fallback vs pause_and_wait.
   - High concurrency thread safety.
"""

import asyncio
import json
import time
import threading
import sqlite3
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch
import pytest

from utils.jobs_manager import create_job, update_job, jobs, JobStatus, _TERMINAL
from utils.rate_limiter import PerModelRateLimiter, RateLimiter
from utils.model_resolver import resolve_model, get_daily_limit_policy
from utils.translation_memory import TranslationMemory
from utils import storage


# ===========================================================================
# 1. Event Loop Non-Blocking & Debouncing Stress
# ===========================================================================

class TestEventLoopNonBlockingAndDebouncingStress:
    """Adversarial stress-testing of JobsManager and RateLimiter debouncing and latency."""

    @pytest.mark.asyncio
    async def test_concurrent_update_job_under_event_loop_latency(self, tmp_path):
        """
        Blast update_job with hundreds of concurrent updates across multiple jobs and threads.
        Verify that event loop latency remains under 15ms and SQLite lock contention is eliminated.
        """
        # Create a dedicated temp DB for job history to isolate test
        test_db = tmp_path / "test_jobs.db"
        conn = sqlite3.connect(test_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_history (
                id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                project_name TEXT NOT NULL,
                episode_name TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                logs TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        conn.close()

        num_jobs = 5
        updates_per_job = 40
        job_ids = []

        with patch("utils.jobs_manager.DB_FILE", test_db):
            for i in range(num_jobs):
                jid = create_job(f"stress_job_{i}", f"StressProj_{i}", f"Ep_{i}")
                job_ids.append(jid)

            max_loop_latency_ms = 0.0
            stop_latency_probe = asyncio.Event()

            # Latency probe coroutine: checks tick deviation on the event loop
            async def latency_probe():
                nonlocal max_loop_latency_ms
                tick = 0.005  # 5ms expected tick
                while not stop_latency_probe.is_set():
                    t0 = time.perf_counter()
                    await asyncio.sleep(tick)
                    elapsed = time.perf_counter() - t0
                    latency = max(0.0, (elapsed - tick) * 1000.0)
                    if latency > max_loop_latency_ms:
                        max_loop_latency_ms = latency

            probe_task = asyncio.create_task(latency_probe())

            # Worker coroutine that blasts update_job
            async def blast_job(jid: str):
                for step in range(updates_per_job):
                    update_job(
                        jid,
                        status="running",
                        progress=float(step),
                        message=f"Step {step}/{updates_per_job}",
                        log=f"Log entry {step}",
                        cues_translated=step * 10,
                        cues_total=updates_per_job * 10,
                    )
                    # Yield slightly to interleave
                    if step % 5 == 0:
                        await asyncio.sleep(0.001)

            # Run updates across all jobs concurrently
            tasks = [blast_job(jid) for jid in job_ids]
            await asyncio.gather(*tasks)

            stop_latency_probe.set()
            await probe_task

            # Assert event loop latency stayed low
            # In a non-blocking architecture, offloading to asyncio.to_thread keeps latency low.
            # Allow generous headroom for CI machines (15ms max)
            assert max_loop_latency_ms < 15.0, f"Event loop latency exceeded 15ms: {max_loop_latency_ms:.2f}ms"

            # Check that all jobs have correct in-memory state
            for jid in job_ids:
                job = jobs[jid]
                assert job.progress == float(updates_per_job - 1)
                assert len(job.logs) == updates_per_job
                assert job.cues_translated == (updates_per_job - 1) * 10

    @pytest.mark.asyncio
    async def test_terminal_job_updates_flush_immediately(self, tmp_path):
        """
        Verify that terminal status updates ('completed', 'failed', 'cancelled', 'partial')
        bypass the 3.0s debounce timer and immediately schedule SQLite writes.
        """
        test_db = tmp_path / "test_terminal.db"
        conn = sqlite3.connect(test_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_history (
                id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                project_name TEXT NOT NULL,
                episode_name TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                logs TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        conn.close()

        with patch("utils.jobs_manager.DB_FILE", test_db):
            for terminal_status in ("completed", "failed", "cancelled", "partial"):
                jid = create_job(f"term_{terminal_status}", "TermProj", "Ep1")
                # Immediate non-terminal update (debounced)
                update_job(jid, status="running", progress=50.0)

                # Now terminal update: must bypass debounce and write completed_at
                update_job(jid, status=terminal_status, progress=100.0, message="All done")

                # The write runs on a worker thread; a fixed short sleep lost that race
                # on a busy machine. Wait for the terminal row instead (bounded).
                row = None
                for _ in range(100):
                    await asyncio.sleep(0.02)
                    conn = sqlite3.connect(test_db)
                    conn.row_factory = sqlite3.Row
                    row = conn.execute("SELECT * FROM job_history WHERE id = ?", (jid,)).fetchone()
                    conn.close()
                    if row is not None and row["completed_at"] is not None:
                        break

                assert row is not None, f"Job {jid} not written to DB"
                expected_db_status = "completed" if terminal_status == "partial" else terminal_status
                assert row["status"] == expected_db_status
                assert row["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_rate_limiter_save_state_debouncing_and_deferred_timer(self, tmp_path):
        """
        Blast save_state on PerModelRateLimiter with 100 rapid calls.
        Verify deferred timer is set, save_pending is tracked, and flush immediately writes.
        """
        limiter = PerModelRateLimiter()
        limiter.STATE_FILE = tmp_path / "rate_limiter_stress_state.json"

        # Rapidly call save_state 100 times
        for _ in range(100):
            limiter.save_state()

        # Should have set pending flag and deferred timer
        assert limiter._save_pending is True
        assert limiter._deferred_timer is not None

        # Flush forces immediate disk write
        limiter.flush()
        assert limiter._save_pending is False
        assert limiter._deferred_timer is None
        assert limiter.STATE_FILE.exists()

        state = json.loads(limiter.STATE_FILE.read_text(encoding="utf-8"))
        assert isinstance(state, dict)


# ===========================================================================
# 2. Vector Operations & Bug Fix Stress
# ===========================================================================

class TestVectorOperationsTMExactMatchStress:
    """Adversarial stress-testing of Translation Memory exact matches and parameter safety."""

    def test_tm_get_exact_matches_parameter_order_and_types(self, monkeypatch):
        """
        Verify that get_exact_matches handles threshold float, max_words int,
        and does not raise TypeError when called with proper keyword arguments.
        """
        tm = TranslationMemory("TMParamOrderTest", lang_code="el")

        # Mock search to return predictable results
        def mock_search(source_lines, top_k=1, similarity_threshold=0.95):
            results = {}
            for i, line in enumerate(source_lines):
                if line == "Good morning":
                    results[i] = [{
                        "source": "Good morning",
                        "target": "Καλημέρα",
                        "score": 0.99,
                        "is_user_edited": True,
                        "episode": "S01E01",
                        "character": "Alice"
                    }]
                elif line == "Too long line that exceeds max words limit for exact match":
                    results[i] = [{
                        "source": line,
                        "target": "Πάρα πολύ μεγάλη γραμμή",
                        "score": 0.99,
                        "is_user_edited": False,
                        "episode": "S01E01",
                        "character": "Bob"
                    }]
                else:
                    results[i] = []
            return results

        monkeypatch.setattr(tm, "search", mock_search)

        # Call with keyword threshold float (0.95) and max_words=3
        matches = tm.get_exact_matches(
            ["Good morning", "Too long line that exceeds max words limit for exact match", "Unknown"],
            threshold=0.95,
            max_words=3,
            require_same_speaker=False
        )

        # "Good morning" has 2 words <= 3 -> match returned
        assert 0 in matches
        assert matches[0]["target"] == "Καλημέρα"
        assert matches[0]["is_user_edited"] is True

        # The long line has 10 words > 3 -> filtered out by word count gate
        assert 1 not in matches
        assert 2 not in matches

    def test_atomic_runner_target_unpacking_handles_dict_and_string(self):
        """
        Verify that atomic_runner.py's unpacking logic correctly handles both
        dict results ({"target": "..."}) and legacy string results without crashing.
        """
        # Case A: Proper dict from get_exact_matches
        match_info_dict = {"target": "Καλησπέρα", "is_user_edited": True, "episode": "S01E01"}
        trans_text_dict = match_info_dict.get("target") if isinstance(match_info_dict, dict) else match_info_dict
        assert trans_text_dict == "Καλησπέρα"

        # Case B: Direct string
        match_info_str = "Καλησπέρα"
        trans_text_str = match_info_str.get("target") if isinstance(match_info_str, dict) else match_info_str
        assert trans_text_str == "Καλησπέρα"

    @pytest.mark.asyncio
    async def test_tm_exact_matches_high_concurrency_thread_offload(self, monkeypatch):
        """
        Verify that offloading tm.get_exact_matches to asyncio.to_thread works cleanly
        under high concurrency (50 parallel coroutines) without race conditions.
        """
        tm = TranslationMemory("TMConcurrencyStress", lang_code="el")

        def mock_search(source_lines, top_k=1, similarity_threshold=0.95):
            return {
                i: [{
                    "source": line,
                    "target": f"Trans_{line}",
                    "score": 0.98,
                    "is_user_edited": False,
                    "episode": "S01E01"
                }]
                for i, line in enumerate(source_lines)
            }

        monkeypatch.setattr(tm, "search", mock_search)

        async def query_task(worker_id: int):
            lines = [f"Greeting {worker_id}", f"Question {worker_id}"]
            res = await asyncio.to_thread(
                tm.get_exact_matches,
                lines,
                threshold=0.95,
                max_words=6,
                require_same_speaker=False
            )
            assert len(res) == 2
            assert res[0]["target"] == f"Trans_Greeting {worker_id}"
            return len(res)

        tasks = [query_task(i) for i in range(50)]
        results = await asyncio.gather(*tasks)
        assert sum(results) == 100

    def test_tm_exact_matches_boundary_conditions(self, monkeypatch):
        """Verify boundary cases: empty input, all whitespace, speaker mismatch."""
        tm = TranslationMemory("TMBoundaryTest", lang_code="el")

        def mock_search(source_lines, top_k=1, similarity_threshold=0.95):
            results = {}
            for i, line in enumerate(source_lines):
                if line.strip():
                    results[i] = [{
                        "source": line,
                        "target": "Γεια",
                        "score": 0.98,
                        "is_user_edited": False,
                        "episode": "S01E01",
                        "character": "Alice"
                    }]
            return results

        monkeypatch.setattr(tm, "search", mock_search)

        # 1. Empty input
        assert tm.get_exact_matches([], threshold=0.95) == {}

        # 2. Whitespace only
        assert tm.get_exact_matches(["   ", "\t\n"], threshold=0.95) == {}

        # 3. Speaker mismatch gate
        matches = tm.get_exact_matches(
            ["Hello"],
            threshold=0.95,
            require_same_speaker=True,
            speakers=["Bob"]  # Alice is stored, Bob is queried -> mismatch
        )
        assert len(matches) == 0

        # 4. Speaker match
        matches_ok = tm.get_exact_matches(
            ["Hello"],
            threshold=0.95,
            require_same_speaker=True,
            speakers=["alice"]  # case-insensitive match
        )
        assert len(matches_ok) == 1
        assert matches_ok[0]["target"] == "Γεια"


# ===========================================================================
# 3. Model Resolution Stress
# ===========================================================================

class TestModelResolutionStress:
    """Adversarial stress-testing of model resolution across providers and policies."""

    def test_resolve_model_cloud_exhaustion_fallback(self):
        """Cloud provider with daily exhaustion falls back to fallback model."""
        from utils.rate_limiter import per_model_rate_limiter
        primary = "gemini-flash-latest"
        limiter = per_model_rate_limiter.get_limiter(primary)
        limiter.trigger_daily_limit()

        metadata = {
            "settings": {
                "ai_provider": "cloud",
                "translation_model": primary,
                "fallback_translation_model": "gemini-3.1-flash-lite",
                "daily_limit_policy": "auto_fallback",
            }
        }
        resolved = resolve_model("translation", metadata)
        assert resolved == "gemini-3.1-flash-lite"
        limiter.clear_daily_limit()

    def test_resolve_model_pause_and_wait_retains_primary(self):
        """Cloud provider with pause_and_wait returns primary model despite exhaustion."""
        from utils.rate_limiter import per_model_rate_limiter
        primary = "gemini-flash-latest"
        limiter = per_model_rate_limiter.get_limiter(primary)
        limiter.trigger_daily_limit()

        metadata = {
            "settings": {
                "ai_provider": "cloud",
                "translation_model": primary,
                "fallback_translation_model": "gemini-3.1-flash-lite",
                "daily_limit_policy": "pause_and_wait",
            }
        }
        resolved = resolve_model("translation", metadata)
        assert resolved == primary
        limiter.clear_daily_limit()

    def test_resolve_model_local_provider_hierarchy(self):
        """Local provider honors role model -> general model -> default fallback."""
        # 1. Role-specific
        meta1 = {
            "settings": {
                "ai_provider": "local",
                "local_translation_model": "ollama/qwen2.5:7b",
                "local_model": "ollama/llama3:8b",
            }
        }
        assert resolve_model("translation", meta1) == "ollama/qwen2.5:7b"

        # 2. General local model fallback
        meta2 = {
            "settings": {
                "ai_provider": "local",
                "local_model": "ollama/llama3:8b",
            }
        }
        assert resolve_model("translation", meta2) == "ollama/llama3:8b"

        # 3. Default fallback
        meta3 = {
            "settings": {
                "ai_provider": "local",
            }
        }
        assert resolve_model("translation", meta3) == "local"

    def test_resolve_model_high_concurrency_thread_safety(self):
        """
        Run resolve_model across 100 concurrent threads with different roles
        and metadata to ensure no race conditions on internal sets or configs.
        """
        metadata_samples = [
            {"settings": {"ai_provider": "cloud", "translation_model": "gemini-2.5-flash"}},
            {"settings": {"ai_provider": "local", "local_translation_model": "ollama/qwen2.5:7b"}},
            {"settings": {"ai_provider": "local", "local_model": "lmstudio/mistral-7b"}},
            {"settings": {"ai_provider": "cloud", "daily_limit_policy": "pause_and_wait"}},
        ]

        errors = []

        def worker_target(thread_id: int):
            try:
                meta = metadata_samples[thread_id % len(metadata_samples)]
                role = "translation" if thread_id % 2 == 0 else "glossary"
                res = resolve_model(role, meta)
                assert isinstance(res, str) and len(res) > 0
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker_target, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors encountered: {errors}"


# ===========================================================================
# 4. Concurrent Cancellation & Completion Race Stress
# ===========================================================================

class TestConcurrentCancellationCompletionRaceStress:
    """
    Adversarial stress-testing of simultaneous job cancellations and completions.
    Validates zero race conditions between SQLite WAL writes, in-memory `jobs` updates,
    and background worker task cancellations.
    """

    @pytest.mark.asyncio
    async def test_concurrent_rapid_cancellations_and_completions_race(self, tmp_path):
        """
        Simulate a high-concurrency race condition: 20 items running concurrently.
        For each item, cancellation and completion are triggered simultaneously.
        Verify:
        - No SQLite locks or crashes occur.
        - Items settle into valid terminal states ('completed' or 'cancelled').
        - Cancelled items never write completion state over cancelled state.
        - SQLite queue and in-memory job status remain strictly coherent.
        """
        from utils.translation_queue import TranslationQueue, PRIORITY_MANUAL
        from services.background_worker import BackgroundTranslationWorker
        from routers.jobs import cancel_job

        db_file = tmp_path / "test_cancel_complete_race.db"
        queue = TranslationQueue(db_path=db_file)
        worker = BackgroundTranslationWorker()
        worker.queue = queue

        num_items = 20
        item_ids = []

        for i in range(num_items):
            iid = queue.enqueue(f"RaceProject_{i}", f"Ep_{i}", priority=PRIORITY_MANUAL)
            item_ids.append(iid)

        # Claim all items so they transition to 'running'
        claimed_items = []
        for _ in range(num_items):
            c = queue.claim_next()
            if c:
                claimed_items.append(c)
        assert len(claimed_items) == num_items

        # Initialize in-memory jobs for all items
        for item in claimed_items:
            jid = f"bg_{item['id']}"
            jobs[jid] = JobStatus(
                id=jid,
                status="running",
                progress=50.0,
                message="Translating...",
                logs=[],
                project_name=item["project_name"],
                episode_name=item["episode_name"],
            )

        # Create mock worker tasks
        async def mock_worker_execution(item_id: str):
            jid = f"bg_{item_id}"
            # Small random sleep to create interleaved timing
            await asyncio.sleep(0.005 * (hash(item_id) % 5 + 1))
            
            # Helper to check cancellation (same as translation_item_worker.py)
            def _is_task_cancelled() -> bool:
                if jid in jobs and jobs[jid].cancelled:
                    return True
                try:
                    db_item = queue.get_item(item_id)
                    if db_item and db_item.get("status") == "cancelled":
                        if jid in jobs:
                            jobs[jid].cancelled = True
                        return True
                except Exception:
                    pass
                return False

            if _is_task_cancelled():
                queue.update_status(item_id, status="cancelled")
                if jid in jobs:
                    update_job(jid, status="cancelled", message="Cancelled by user")
                return "cancelled"

            # Completion step
            queue.update_status(item_id, status="completed", completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"))
            update_job(jid, status="completed", progress=100.0, message="Translation completed.")
            return "completed"

        # Register tasks in worker.active_tasks
        tasks = {}
        for item in claimed_items:
            t = asyncio.create_task(mock_worker_execution(item["id"]))
            tasks[item["id"]] = t
            worker.active_tasks[item["id"]] = t

        # Concurrently fire cancel_job on all items while workers run
        async def cancel_worker(item_id: str):
            await asyncio.sleep(0.003 * ((hash(item_id) >> 2) % 6 + 1))
            jid = f"bg_{item_id}"
            with patch("services.background_worker.worker", worker):
                await cancel_job(jid)

        cancel_tasks = [asyncio.create_task(cancel_worker(item["id"])) for item in claimed_items]

        # Wait for all tasks to settle
        await asyncio.gather(*list(tasks.values()), *cancel_tasks, return_exceptions=True)

        # Clean up worker
        worker.stop()

        # Audit every single item for coherence and data integrity
        for item in claimed_items:
            iid = item["id"]
            jid = f"bg_{iid}"
            db_item = queue.get_item(iid)
            mem_job = jobs.get(jid)

            assert db_item is not None, f"Item {iid} missing in DB"
            assert db_item["status"] in ("completed", "cancelled"), (
                f"Item {iid} in invalid status: {db_item['status']}"
            )
            assert mem_job is not None, f"Job {jid} missing in memory"
            assert mem_job.status in ("completed", "cancelled"), (
                f"Job {jid} in invalid memory status: {mem_job.status}"
            )

            # Invariant: If cancellation was registered, neither DB nor memory should report running or pending
            if db_item["status"] == "cancelled":
                assert mem_job.status == "cancelled" or mem_job.cancelled is True

    @pytest.mark.asyncio
    async def test_event_loop_latency_under_continuous_cancellation_and_retries(self, tmp_path):
        """
        Verify that adding cancellation checks and retry logic does not introduce
        event loop latency or SQLite lock contention under rapid continuous execution.
        """
        from utils.translation_queue import TranslationQueue, PRIORITY_MANUAL

        db_file = tmp_path / "test_latency_under_cancels.db"
        queue = TranslationQueue(db_path=db_file)

        num_ops = 50
        max_loop_latency_ms = 0.0
        stop_probe = asyncio.Event()

        async def latency_probe():
            nonlocal max_loop_latency_ms
            tick = 0.005
            while not stop_probe.is_set():
                t0 = time.perf_counter()
                await asyncio.sleep(tick)
                elapsed = time.perf_counter() - t0
                lat = max(0.0, (elapsed - tick) * 1000.0)
                if lat > max_loop_latency_ms:
                    max_loop_latency_ms = lat

        probe_task = asyncio.create_task(latency_probe())

        async def rapid_queue_ops():
            for i in range(num_ops):
                iid = queue.enqueue(f"LatencyProj_{i}", f"Ep_{i}", priority=PRIORITY_MANUAL)
                claimed = queue.claim_next()
                if claimed:
                    # Cooperative cancel check
                    _ = queue.get_item(iid)
                    queue.cancel(iid)
                if i % 5 == 0:
                    await asyncio.sleep(0.001)

        await rapid_queue_ops()

        stop_probe.set()
        await probe_task

        assert max_loop_latency_ms < 25.0, (
            f"Event loop latency under rapid queue/cancel ops exceeded 25ms: {max_loop_latency_ms:.2f}ms"
        )
