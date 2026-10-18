"""
Milestone 2 Test Suite: Unified Queue-First Architecture & Event Loop.

Covers:
1. Queue Triggers & Single Execution Path (Webhook, Scheduled Sync, Single Translate, Retranslate/Compare).
2. Elimination of Stage 3 Fake Completion in Multi-Stage Wizards (Lifecycle, Live Telemetry, Partial/Failed, Daily Limit Pauses, Cascading Cancellation).
3. Model Resolver & AI Provider Resolution (Cloud, Local, Hybrid, Daily Limit Policies).
4. Event Loop Hygiene & Non-Blocking Offloading (Jobs Manager debouncing, Rate Limiter debouncing, TM exact match signature bug fix & thread offload).
"""

import asyncio
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from utils.translation_queue import (
    TranslationQueue,
    PRIORITY_WEBHOOK,
    PRIORITY_MANUAL,
    PRIORITY_SYNC,
    PRIORITY_BACKLOG,
)
from utils.model_resolver import resolve_model, get_daily_limit_policy
from utils.jobs_manager import create_job, update_job, jobs, JobStatus
from utils.rate_limiter import PerModelRateLimiter, RateLimiter
from utils import storage
from routers.schemas import TranslateRequest, SettingsRequest


# ===========================================================================
# 1. Model Resolver & AI Provider Tests
# ===========================================================================

class TestModelResolverMilestone2:
    """Test AI provider resolution ('cloud', 'local', 'hybrid') and daily limit policies."""

    def test_local_provider_role_resolution(self):
        metadata = {
            "settings": {
                "ai_provider": "local",
                "local_translation_model": "ollama/qwen2.5:7b",
                "local_model": "ollama/llama3:8b",
            }
        }
        assert resolve_model("translation", metadata, check_exhaustion=False) == "ollama/qwen2.5:7b"

    def test_local_provider_fallback_to_general_local_model(self):
        metadata = {
            "settings": {
                "ai_provider": "local",
                "local_model": "lmstudio/mistral-7b-instruct",
            }
        }
        assert resolve_model("translation", metadata, check_exhaustion=False) == "lmstudio/mistral-7b-instruct"

    def test_local_provider_skips_cloud_daily_exhaustion(self):
        from utils.rate_limiter import per_model_rate_limiter
        limiter = per_model_rate_limiter.get_limiter("ollama/qwen2.5:7b")
        limiter.trigger_daily_limit()

        metadata = {
            "settings": {
                "ai_provider": "local",
                "local_translation_model": "ollama/qwen2.5:7b",
                "fallback_translation_model": "gemini-3.1-flash-lite",
            }
        }
        # Local models should not failover to Gemini cloud fallback
        assert resolve_model("translation", metadata) == "ollama/qwen2.5:7b"
        limiter.clear_daily_limit()

    def test_pause_and_wait_policy_returns_primary_model(self):
        from utils.rate_limiter import per_model_rate_limiter
        primary = "gemini-test-exhaust-pml"
        limiter = per_model_rate_limiter.get_limiter(primary)
        limiter.trigger_daily_limit()

        metadata = {
            "settings": {
                "translation_model": primary,
                "fallback_translation_model": "gemini-3.1-flash-lite",
                "daily_limit_policy": "pause_and_wait",
            }
        }
        # Under pause_and_wait, resolve_model returns primary so worker can pause cleanly
        assert resolve_model("translation", metadata) == primary
        limiter.clear_daily_limit()

    def test_auto_fallback_policy_fails_over_to_fallback(self):
        from utils.rate_limiter import per_model_rate_limiter
        primary = "gemini-test-exhaust-auto"
        limiter = per_model_rate_limiter.get_limiter(primary)
        limiter.trigger_daily_limit()

        metadata = {
            "settings": {
                "translation_model": primary,
                "fallback_translation_model": "gemini-3.1-flash-lite",
                "daily_limit_policy": "auto_fallback",
            }
        }
        assert resolve_model("translation", metadata) == "gemini-3.1-flash-lite"
        limiter.clear_daily_limit()

    def test_settings_request_schema_defaults(self):
        settings = SettingsRequest()
        assert settings.ai_provider == "cloud"
        assert settings.daily_limit_policy == "auto_fallback"
        assert settings.default_local_model == "local"


# ===========================================================================
# 2. Queue Triggers & Single Execution Path Tests
# ===========================================================================

class TestQueueTriggersMilestone2:
    """Test that all translation entry points route through the unified queue."""

    @pytest.mark.asyncio
    async def test_retranslate_episode_routes_through_queue(self, monkeypatch):
        from routers.episodes import retranslate_episode

        project_name = "RetranslateShow"
        storage.create_project(project_name, {
            "show_name": "RetranslateShow",
            "target_language": "Greek",
            "settings": {"translation_model": "gemini-flash-lite-latest"}
        })

        enqueued = []
        def mock_enqueue(proj, ep, prio, opts=None, lang=""):
            enqueued.append((proj, ep, prio, opts, lang))
            return "retrans_12345"

        monkeypatch.setattr("services.queue_service.enqueue_translation", mock_enqueue)

        req = TranslateRequest(model="gemini-2.5-pro", enhance_glossary=True)
        res = await retranslate_episode(project_name, "S01E01", req)

        assert res["job_id"] == "bg_retrans_12345"
        assert res["item_id"] == "retrans_12345"
        assert "comparison_key" in res
        assert len(enqueued) == 1
        opts = enqueued[0][3]
        assert opts["comparison_mode"] is True
        assert opts["model"] == "gemini-2.5-pro"
        assert opts["translation_type"] == "comparison"

    @pytest.mark.asyncio
    async def test_single_episode_translate_routes_target_language(self, monkeypatch):
        from routers.pipeline import translate_episode

        project_name = "TranslateRouteShow"
        storage.create_project(project_name, {
            "show_name": "TranslateRouteShow",
            "target_language": "Greek",
            "target_languages": ["Greek", "Spanish"],
        })

        enqueued = []
        def mock_enqueue(proj, ep, prio, opts=None, lang=""):
            enqueued.append((proj, ep, prio, opts, lang))
            return "single_trans_1"

        monkeypatch.setattr("services.queue_service.enqueue_translation", mock_enqueue)

        req = TranslateRequest(lang="es", target_language_name="Spanish")
        res = await translate_episode(project_name, "S01E01", req)

        assert res["job_id"] == "bg_single_trans_1"
        assert len(enqueued) == 1
        assert enqueued[0][4] == "es"
        assert enqueued[0][3]["target_language_name"] == "Spanish"


# ===========================================================================
# 3. Wizard Lifecycle & Stage 3 Monitoring Tests
# ===========================================================================

class TestWizardLifecycleMilestone2:
    """Test eliminating fake completion: live progress, scene aggregation, cascading cancellation."""

    @pytest.mark.asyncio
    async def test_monitor_stage3_aggregates_scene_progress_and_completes(self):
        from routers.pipeline import _monitor_stage3_queue_progress
        from utils.translation_queue import TranslationQueue

        job_id = create_job("test_pipeline_stage3", "TestProj")
        item_id1 = "item_ep1"
        item_id2 = "item_ep2"

        # Mock queue with in-memory store
        class FakeQueue:
            def __init__(self):
                self.items = {
                    item_id1: {"id": item_id1, "status": "running", "episode_name": "S01E01", "project_name": "TestProj"},
                    item_id2: {"id": item_id2, "status": "pending", "episode_name": "S01E02", "project_name": "TestProj"},
                }
            def get_all(self, limit=1000):
                return list(self.items.values())
            def get_item(self, iid):
                return self.items.get(iid)
            def cancel(self, iid):
                if iid in self.items:
                    self.items[iid]["status"] = "cancelled"

        fake_queue = FakeQueue()

        # Mock child worker job in jobs registry
        child_job1 = JobStatus(
            id=f"bg_{item_id1}",
            status="running",
            progress=50.0,
            message="Translating scene 1...",
            logs=[],
            scene_status={"Scene 1": "completed", "Scene 2": "running"}
        )
        jobs[f"bg_{item_id1}"] = child_job1

        with patch("utils.translation_queue.TranslationQueue", return_value=fake_queue):
            # Run monitor in background task
            monitor_task = asyncio.create_task(
                _monitor_stage3_queue_progress(job_id, [item_id1, item_id2], base_progress=50.0, poll_interval=0.05)
            )

            await asyncio.sleep(0.1)

            # Assert parent job is RUNNING in pipeline_stage='translate' and progress aggregated
            parent = jobs[job_id]
            assert parent.status == "running"
            assert parent.pipeline_stage == "translate"
            assert parent.sub_jobs == [item_id1, item_id2]
            # Ep1 is 50% (1/2 scenes), Ep2 is 0%. Stage3 fraction = (0.5 + 0.0) / 2 = 0.25
            # Progress = 50 + (100 - 50) * 0.25 = 62.5%
            assert parent.progress >= 60.0 and parent.progress <= 65.0
            assert parent.scenes_done == 1
            assert parent.scenes_total == 2

            # Advance Ep1 to completed, Ep2 to running
            fake_queue.items[item_id1]["status"] = "completed"
            fake_queue.items[item_id2]["status"] = "running"
            child_job2 = JobStatus(
                id=f"bg_{item_id2}",
                status="running",
                progress=100.0,
                message="Finalizing...",
                logs=[],
                scene_status={"Scene 1": "completed"}
            )
            jobs[f"bg_{item_id2}"] = child_job2

            await asyncio.sleep(0.1)
            parent = jobs[job_id]
            assert parent.status == "running"
            assert parent.progress >= 95.0

            # Complete Ep2
            fake_queue.items[item_id2]["status"] = "completed"
            await asyncio.sleep(0.1)

            await monitor_task

            # Terminal check: parent MUST now be completed at 100.0%
            parent = jobs[job_id]
            assert parent.status == "completed"
            assert parent.progress == 100.0
            assert "S01E01" in parent.completed_episodes
            assert "S01E02" in parent.completed_episodes

    @pytest.mark.asyncio
    async def test_monitor_stage3_partial_failure_handling(self):
        from routers.pipeline import _monitor_stage3_queue_progress

        job_id = create_job("test_pipeline_partial", "TestProj")
        item_id1 = "item_p1"
        item_id2 = "item_p2"

        class FakeQueue:
            def __init__(self):
                self.items = {
                    item_id1: {"id": item_id1, "status": "completed", "episode_name": "S01E01"},
                    item_id2: {"id": item_id2, "status": "failed", "episode_name": "S01E02"},
                }
            def get_all(self, limit=1000):
                return list(self.items.values())
            def get_item(self, iid):
                return self.items.get(iid)
            def cancel(self, iid): pass

        with patch("utils.translation_queue.TranslationQueue", return_value=FakeQueue()):
            await _monitor_stage3_queue_progress(job_id, [item_id1, item_id2], base_progress=50.0, poll_interval=0.02)

            parent = jobs[job_id]
            assert parent.status == "partial"
            assert parent.progress == 100.0
            assert "S01E01" in parent.completed_episodes
            assert "S01E02" in parent.failed_episodes

    @pytest.mark.asyncio
    async def test_monitor_stage3_daily_limit_pause_reflection(self):
        from routers.pipeline import _monitor_stage3_queue_progress

        job_id = create_job("test_pipeline_paused", "TestProj")
        item_id1 = "item_q1"

        class FakeQueue:
            def __init__(self):
                self.items = {
                    item_id1: {"id": item_id1, "status": "paused_daily_limit", "episode_name": "S01E01"},
                }
            def get_all(self, limit=1000):
                return list(self.items.values())
            def get_item(self, iid):
                return self.items.get(iid)
            def cancel(self, iid): pass

        with patch("utils.translation_queue.TranslationQueue", return_value=FakeQueue()):
            task = asyncio.create_task(
                _monitor_stage3_queue_progress(job_id, [item_id1], base_progress=50.0, poll_interval=0.05)
            )
            await asyncio.sleep(0.1)

            parent = jobs[job_id]
            assert parent.status == "paused"
            assert parent.daily_limit_reached is True
            assert "Daily API limit reached" in parent.message

            # Cancel job to exit task
            parent.cancelled = True
            await asyncio.sleep(0.1)
            await task

    @pytest.mark.asyncio
    async def test_cascading_cancellation_from_parent_to_queue_and_workers(self):
        from routers.jobs import cancel_job

        parent_id = create_job("pipeline_wizard_cancel_test", "CancelProj")
        child_id1 = "child_item_1"
        child_id2 = "child_item_2"

        update_job(parent_id, status="running", sub_jobs=[child_id1, child_id2])

        # Register child jobs in memory
        child_job1 = JobStatus(id=f"bg_{child_id1}", status="running", progress=10.0, message="Translating...", logs=[])
        child_job2 = JobStatus(id=f"bg_{child_id2}", status="pending", progress=0.0, message="Pending...", logs=[])
        jobs[f"bg_{child_id1}"] = child_job1
        jobs[f"bg_{child_id2}"] = child_job2

        cancelled_queue_items = []
        with patch("utils.translation_queue.TranslationQueue.cancel", side_effect=lambda cid: cancelled_queue_items.append(cid)):
            res = await cancel_job(parent_id)

            assert res["status"] == "cancelled"
            assert jobs[parent_id].status == "cancelled"
            assert jobs[parent_id].cancelled is True

            # Child in-memory jobs cancelled
            assert child_job1.cancelled is True
            assert child_job1.status == "cancelled"
            assert child_job2.cancelled is True
            assert child_job2.status == "cancelled"

            # TranslationQueue.cancel called for all sub_jobs
            assert child_id1 in cancelled_queue_items
            assert child_id2 in cancelled_queue_items


# ===========================================================================
# 4. Event Loop Hygiene & Non-Blocking Offloading Tests
# ===========================================================================

class TestEventLoopHygieneMilestone2:
    """Test debouncing in JobsManager and RateLimiter, and TM exact match fix."""

    def test_jobs_manager_debouncing_and_telemetry_fields(self):
        job_id = create_job("test_telemetry_job", "TelemetryProj", "Ep1")

        # Update telemetry fields
        update_job(
            job_id,
            eta_seconds=125.5,
            cues_translated=45,
            cues_total=200,
            scenes_done=2,
            scenes_total=8,
            pipeline_stage="translate",
            pipeline_mode="step",
            sub_jobs=["item1", "item2"]
        )

        job = jobs[job_id]
        assert job.eta_seconds == 125.5
        assert job.cues_translated == 45
        assert job.cues_total == 200
        assert job.scenes_done == 2
        assert job.scenes_total == 8
        assert job.pipeline_stage == "translate"
        assert job.pipeline_mode == "step"
        assert job.sub_jobs == ["item1", "item2"]

    @pytest.mark.asyncio
    async def test_rate_limiter_debouncing_and_deferred_timer(self, tmp_path):
        limiter = PerModelRateLimiter()
        limiter.STATE_FILE = tmp_path / "rate_limiter_state_test.json"

        # Rapidly call save_state multiple times
        limiter.save_state()
        first_save_time = limiter._last_save

        limiter.save_state()
        assert limiter._save_pending is True
        assert limiter._deferred_timer is not None

        # Flush forces immediate disk write
        limiter.flush()
        assert limiter._save_pending is False
        assert limiter.STATE_FILE.exists()

        content = json.loads(limiter.STATE_FILE.read_text(encoding="utf-8"))
        assert isinstance(content, dict)

    @pytest.mark.asyncio
    async def test_tm_exact_matches_parameter_bug_fixed(self, monkeypatch):
        """Verify that passing target_lang or threshold does not trigger TypeError in get_exact_matches."""
        from utils.translation_memory import TranslationMemory

        tm = TranslationMemory("TMExactMatchBugTest", lang_code="el")

        # Mock search to return a mock match
        def mock_search(source_lines, top_k=1, similarity_threshold=0.95):
            results = {}
            for i, line in enumerate(source_lines):
                if line == "Hello world":
                    results[i] = [{
                        "target": "Γεια σου κόσμε",
                        "is_user_edited": True,
                        "episode": "S01E01",
                        "character": "Alice"
                    }]
                else:
                    results[i] = []
            return results

        monkeypatch.setattr(tm, "search", mock_search)

        # Call with keyword threshold float (as fixed in atomic_runner.py)
        exact = await asyncio.to_thread(
            tm.get_exact_matches,
            ["Hello world", "Unknown line"],
            threshold=0.95,
            max_words=6,
            require_same_speaker=False
        )

        assert 0 in exact
        assert exact[0]["target"] == "Γεια σου κόσμε"
        assert 1 not in exact
