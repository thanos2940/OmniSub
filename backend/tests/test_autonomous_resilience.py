"""
Milestone 1 Test Suite: Autonomous & Self-Healing Translation Engine.

Covers:
1. Model-Aware Chunk Sizing (Flash 450, Pro 300, Local 70).
2. Daily Limit Policy Resolution (auto_fallback vs pause_and_wait).
3. Job-Start Quota Exhaustion Failover (Worker level).
4. Dynamic In-Flight Failover on 429 / DailyLimitExhausted (Atomic Runner level).
5. Automated Localized Sub-Chunk Repair for Dropped Lines (<20% missing cues).
6. Zero Untranslated English Lines in Subtitle Exports (SRT, ASS, source_clean).
7. Timeline Integrity and Non-Destructive Morphology Preservation.
"""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path

from adk_agents.llm_factory import get_recommended_chunk_size
from utils.model_resolver import (
    resolve_model,
    get_daily_limit_policy,
    mark_model_daily_exhausted,
    clear_model_daily_exhaustion,
    clear_logged_failovers,
    is_model_daily_exhausted,
)
from utils.rate_limiter import (
    RateLimiter,
    DailyLimitExhausted,
    per_model_rate_limiter,
    active_model_var,
)
from utils.srt_parser import reconstruct_srt
from utils.ass_parser import reconstruct_ass
from utils.source_clean import reconstruct_cleaned_srt
from utils import storage
from utils.jobs_manager import create_job, jobs


BASE_TEST_CONFIG = {
    "tm_enabled": False,
    "episode_summaries_enabled": False,
    "intra_episode_reconcile_enabled": False,
    "conformance_enabled": False,
    "condense_enabled": False,
    "enable_reviewer": False,
    "character_profiles_enabled": False,
    "glossary_enforce_enabled": False,
    "repair_pass_enabled": False,
    "new_terms_suggest_enabled": False,
    "source_clean_enabled": False,
    "context_cache_enabled": False,
    "max_lines_per_scene": 50,
    "whole_episode_max_cues": 1200,
    "scene_lookahead_lines": 0,
    "incremental_save_throttle_seconds": 0,
    "sequential_scenes": True,
    "episode_request_mode": "scenes",
}


# ===========================================================================
# 1. Model-Aware Chunk Sizing Tests
# ===========================================================================

class TestModelAwareChunkSizing:
    """Verify get_recommended_chunk_size adapts to model family context limits."""

    def test_flash_models_return_macro_chunk_size(self):
        """Gemini Flash / Flash-Lite models return 450 cues to optimize TV episodes in <=2 calls."""
        assert get_recommended_chunk_size("gemini-flash-lite-latest") == 450
        assert get_recommended_chunk_size("gemini-3.1-flash-lite") == 450
        assert get_recommended_chunk_size("gemini-2.5-flash") == 450
        assert get_recommended_chunk_size("gemini-1.5-flash") == 450

    def test_pro_models_return_balanced_chunk_size(self):
        """Gemini Pro models return 300 cues to balance latency and RPM limits."""
        assert get_recommended_chunk_size("gemini-2.5-pro") == 300
        assert get_recommended_chunk_size("gemini-1.5-pro") == 300

    def test_local_and_small_models_return_compact_chunk_size(self):
        """Local and small open-weight models return 70 cues (60-80) to prevent token blowup."""
        assert get_recommended_chunk_size("local/qwen-2.5-7b") == 70
        assert get_recommended_chunk_size("ollama/llama3:8b") == 70
        assert get_recommended_chunk_size("lmstudio/mistral-7b-instruct") == 70
        assert get_recommended_chunk_size("vllm/gemma-2-9b") == 70

    def test_default_and_unrecognized_models(self):
        """Unrecognized model names or None safely default to 300 cues."""
        assert get_recommended_chunk_size(None) == 300
        assert get_recommended_chunk_size("") == 300
        assert get_recommended_chunk_size("some-unknown-model-xyz") == 300


# ===========================================================================
# 2. Daily Limit Policy Resolution Tests
# ===========================================================================

class TestDailyLimitPolicyResolution:
    """Verify daily_limit_policy ('auto_fallback' default, 'pause_and_wait') governs failover."""

    def setup_method(self):
        clear_logged_failovers()
        for limiter in per_model_rate_limiter._limiters.values():
            limiter.clear_daily_limit()

    def teardown_method(self):
        clear_logged_failovers()
        for limiter in per_model_rate_limiter._limiters.values():
            limiter.clear_daily_limit()

    def test_default_policy_is_auto_fallback(self):
        """When not explicitly specified, get_daily_limit_policy defaults to auto_fallback."""
        assert get_daily_limit_policy({}) == "auto_fallback"
        assert get_daily_limit_policy(None) == "auto_fallback"

    def test_project_policy_override(self):
        """Project settings can specify pause_and_wait or auto_fallback."""
        meta_pause = {"settings": {"daily_limit_policy": "pause_and_wait"}}
        assert get_daily_limit_policy(meta_pause) == "pause_and_wait"

        meta_fallback = {"settings": {"daily_limit_policy": "auto_fallback"}}
        assert get_daily_limit_policy(meta_fallback) == "auto_fallback"

    def test_resolve_model_fails_over_under_auto_fallback(self):
        """Under auto_fallback (default), exhausted primary resolves to fallback model."""
        primary = "gemini-2.5-pro"
        fallback = "gemini-3.1-flash-lite"
        mark_model_daily_exhausted(primary)

        meta = {
            "settings": {
                "translation_model": primary,
                "fallback_translation_model": fallback,
                "daily_limit_policy": "auto_fallback",
            }
        }
        resolved = resolve_model("translation", meta, check_exhaustion=True)
        assert resolved == fallback

    def test_resolve_model_does_not_failover_under_pause_and_wait(self):
        """Under pause_and_wait, resolve_model returns primary model without failover."""
        primary = "gemini-2.5-pro"
        fallback = "gemini-3.1-flash-lite"
        mark_model_daily_exhausted(primary)

        meta = {
            "settings": {
                "translation_model": primary,
                "fallback_translation_model": fallback,
                "daily_limit_policy": "pause_and_wait",
            }
        }
        resolved = resolve_model("translation", meta, check_exhaustion=True)
        assert resolved == primary


# ===========================================================================
# 3. Worker Job-Start Quota Exhaustion Failover
# ===========================================================================

class TestWorkerJobStartFailover:
    """Verify translation_item_worker auto-falls back at job start when primary is exhausted."""

    def setup_method(self):
        clear_logged_failovers()
        for limiter in per_model_rate_limiter._limiters.values():
            limiter.clear_daily_limit()

    def teardown_method(self):
        clear_logged_failovers()
        for limiter in per_model_rate_limiter._limiters.values():
            limiter.clear_daily_limit()

    @pytest.mark.asyncio
    async def test_job_start_auto_fallback_continues_execution(self, tmp_path, monkeypatch):
        """When primary model is exhausted at start and policy is auto_fallback, worker switches model."""
        from services.worker.translation_item_worker import process_translation_item
        from services.queue_service import get_queue

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)

        proj = "TestFailoverStart"
        ep = "E01"
        storage.create_project(proj)
        storage.save_project_metadata(proj, {
            "target_language": "Greek",
            "settings": {
                "translation_model": "gemini-2.5-pro",
                "fallback_translation_model": "gemini-3.1-flash-lite",
                "daily_limit_policy": "auto_fallback",
            }
        })
        storage.save_episode(proj, ep, [
            {"id": "1", "timecode": "00:00:01,000 --> 00:00:03,000", "original": "Hello friend."}
        ], {})
        storage.save_original_subtitle(proj, ep, "1\n00:00:01,000 --> 00:00:03,000\nHello friend.\n", "e01.en.srt")

        mark_model_daily_exhausted("gemini-2.5-pro")

        job_id = create_job("translate", f"Translating {ep}")

        queue = get_queue(str(tmp_path / "queue.db"))
        item_id = queue.enqueue(
            project_name=proj,
            episode_name=ep,
            priority=2,
            options={"job_id": job_id},
        )

        mock_atomic = AsyncMock(return_value=True)
        monkeypatch.setattr("services.worker.translation_item_worker.translate_episode_atomic", mock_atomic)
        monkeypatch.setattr("services.worker.translation_item_worker.storage.PROJECTS_DIR", projects_dir)

        item = queue.claim_next()
        await process_translation_item(queue, item, BASE_TEST_CONFIG)

        # Worker called translate_episode_atomic with fallback model
        assert mock_atomic.called
        call_kwargs = mock_atomic.call_args.kwargs
        assert call_kwargs["model_name"] == "gemini-3.1-flash-lite"

    @pytest.mark.asyncio
    async def test_job_start_pause_and_wait_pauses_task(self, tmp_path, monkeypatch):
        """When primary model is exhausted at start and policy is pause_and_wait, worker pauses task."""
        from services.worker.translation_item_worker import process_translation_item
        from services.queue_service import get_queue

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)

        proj = "TestPauseStart"
        ep = "E01"
        storage.create_project(proj)
        storage.save_project_metadata(proj, {
            "target_language": "Greek",
            "settings": {
                "translation_model": "gemini-2.5-pro",
                "fallback_translation_model": "gemini-3.1-flash-lite",
                "daily_limit_policy": "pause_and_wait",
            }
        })
        storage.save_episode(proj, ep, [
            {"id": "1", "timecode": "00:00:01,000 --> 00:00:03,000", "original": "Hello friend."}
        ], {})
        storage.save_original_subtitle(proj, ep, "1\n00:00:01,000 --> 00:00:03,000\nHello friend.\n", "e01.en.srt")

        mark_model_daily_exhausted("gemini-2.5-pro")

        job_id = create_job("translate", f"Translating {ep}")

        queue = get_queue(str(tmp_path / "queue.db"))
        item_id = queue.enqueue(
            project_name=proj,
            episode_name=ep,
            priority=2,
            options={"job_id": job_id},
        )

        mock_atomic = AsyncMock(return_value=True)
        monkeypatch.setattr("services.worker.translation_item_worker.translate_episode_atomic", mock_atomic)

        item = queue.claim_next()
        await process_translation_item(queue, item, BASE_TEST_CONFIG)

        # Atomic runner was NOT called because task paused on daily limit
        assert not mock_atomic.called
        updated_item = queue.get_item(item_id)
        assert updated_item["status"] == "paused_daily_limit"


# ===========================================================================
# 4. Dynamic In-Flight Failover on 429 Mid-Run
# ===========================================================================

class TestInFlightFailover:
    """Verify atomic_runner dynamically fails over to fallback model on 429 mid-run."""

    def setup_method(self):
        clear_logged_failovers()
        for limiter in per_model_rate_limiter._limiters.values():
            limiter.clear_daily_limit()

    def teardown_method(self):
        clear_logged_failovers()
        for limiter in per_model_rate_limiter._limiters.values():
            limiter.clear_daily_limit()

    @pytest.mark.asyncio
    async def test_mid_run_failover_swaps_model_and_completes(self, tmp_path, monkeypatch):
        """Simulate primary model throwing DailyLimitExhausted mid-run; fails over to fallback model."""
        from services.translation.atomic_runner import _translate_episode_atomic

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)
        monkeypatch.setattr(storage, "load_global_config", lambda: dict(BASE_TEST_CONFIG))

        proj = "TestInFlight"
        ep = "E01"
        storage.create_project(proj)
        storage.save_project_metadata(proj, {
            "target_language": "Greek",
            "settings": {
                "translation_model": "gemini-2.5-pro",
                "fallback_translation_model": "gemini-3.1-flash-lite",
                "daily_limit_policy": "auto_fallback",
            }
        })

        cues = [
            {"id": "1", "timecode": "00:00:01,000 --> 00:00:03,000", "original": "First dialogue line."},
            {"id": "2", "timecode": "00:00:04,000 --> 00:00:06,000", "original": "Second dialogue line."},
        ]
        storage.save_episode(proj, ep, cues, {})

        job_id = create_job("translate", f"Translating {ep}")

        call_records = []

        async def fake_rate_limited_call(fn, rate_limiter=None, **kwargs):
            call_records.append(rate_limiter)
            # First call on primary model raises DailyLimitExhausted
            if len(call_records) == 1:
                raise DailyLimitExhausted(daily_count=1500, daily_limit=1500)
            # Second call (after failover) succeeds
            return json.dumps({
                "translations": [
                    {"index": 1, "text": "Πρώτη γραμμή διαλόγου."},
                    {"index": 2, "text": "Δεύτερη γραμμή διαλόγου."},
                ]
            })

        monkeypatch.setattr("services.translation.atomic_runner.rate_limited_call", fake_rate_limited_call)

        success = await _translate_episode_atomic(
            project_name=proj,
            episode_name=ep,
            target_lang="Greek",
            shared_agent=MagicMock(),
            job_id=job_id,
            total_episodes=1,
            model_name="gemini-2.5-pro",
        )

        assert success is True
        assert len(call_records) >= 2
        failover_logs = [l for l in jobs[job_id].logs if "Mid-run dynamic failover" in l]
        assert len(failover_logs) > 0

    @pytest.mark.asyncio
    async def test_concurrent_failover_lock_race(self, tmp_path, monkeypatch):
        """Under concurrent scene execution, multiple tasks hitting 429 must not latch the fallback model."""
        from services.translation.atomic_runner import _translate_episode_atomic

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)

        test_config = dict(BASE_TEST_CONFIG)
        test_config["sequential_scenes"] = False
        test_config["max_lines_per_scene"] = 2
        monkeypatch.setattr(storage, "load_global_config", lambda: test_config)

        proj = "TestConcurrentFailover"
        ep = "E01"
        storage.create_project(proj)
        storage.save_project_metadata(proj, {
            "target_language": "Greek",
            "settings": {
                "translation_model": "gemini-2.5-pro",
                "fallback_translation_model": "gemini-3.1-flash-lite",
                "daily_limit_policy": "auto_fallback",
            }
        })

        # 4 cues = 2 scenes (max_lines_per_scene = 2)
        cues = [
            {"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Line 1"},
            {"id": "2", "timecode": "00:00:03,000 --> 00:00:04,000", "original": "Line 2"},
            {"id": "3", "timecode": "00:00:05,000 --> 00:00:06,000", "original": "Line 3"},
            {"id": "4", "timecode": "00:00:07,000 --> 00:00:08,000", "original": "Line 4"},
        ]
        storage.save_episode(proj, ep, cues, {})

        job_id = create_job("translate", f"Translating {ep}")

        calls_by_model = {"gemini-2.5-pro": 0, "gemini-3.1-flash-lite": 0}

        async def fake_concurrent_call(fn, rate_limiter=None, **kwargs):
            m_name = getattr(rate_limiter, "model_name", None) or "gemini-2.5-pro"
            calls_by_model[m_name] = calls_by_model.get(m_name, 0) + 1
            if m_name == "gemini-2.5-pro":
                # Both concurrent scenes fail with 429 on primary model
                raise DailyLimitExhausted(daily_count=1500, daily_limit=1500)
            # Calls on fallback model succeed
            return json.dumps({
                "translations": [
                    {"index": 1, "text": "Ελληνική γραμμή 1"},
                    {"index": 2, "text": "Ελληνική γραμμή 2"},
                ]
            })

        monkeypatch.setattr("services.translation.atomic_runner.rate_limited_call", fake_concurrent_call)

        success = await _translate_episode_atomic(
            project_name=proj,
            episode_name=ep,
            target_lang="Greek",
            shared_agent=MagicMock(),
            job_id=job_id,
            total_episodes=1,
            model_name="gemini-2.5-pro",
        )

        assert success is True
        # Primary model must be exhausted
        assert is_model_daily_exhausted("gemini-2.5-pro") is True
        # Fallback model must NOT have been latched as daily exhausted!
        assert is_model_daily_exhausted("gemini-3.1-flash-lite") is False
        assert calls_by_model["gemini-3.1-flash-lite"] >= 2


# ===========================================================================
# 5. Automated Localized Sub-Chunk Repair for Dropped Lines
# ===========================================================================

class TestLocalizedSubChunkRepair:
    """Verify atomic_runner runs immediate targeted repair on dropped cues (<20%)."""

    @pytest.mark.asyncio
    async def test_scene_dropped_cues_repaired_automatically(self, tmp_path, monkeypatch):
        """When scene LLM omits 1 cue out of 10 (<20%), sub-chunk repair recovers it."""
        from services.translation.atomic_runner import _translate_episode_atomic

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)
        monkeypatch.setattr(storage, "load_global_config", lambda: dict(BASE_TEST_CONFIG))

        proj = "TestRepairProj"
        ep = "E01"
        storage.create_project(proj)
        storage.save_project_metadata(proj, {"target_language": "Greek", "settings": {}})

        cues = [
            {"id": str(i + 1), "timecode": f"00:00:{i:02d},000 --> 00:00:{i+1:02d},000", "original": f"Line {i + 1}"}
            for i in range(10)
        ]
        storage.save_episode(proj, ep, cues, {})

        job_id = create_job("translate", f"Translating {ep}")

        call_count = {"calls": 0}

        async def mock_call(fn, rate_limiter=None, **kwargs):
            call_count["calls"] += 1
            if call_count["calls"] == 1:
                # Main scene call drops line 5 (returns 9 lines: 1..4, 6..10)
                partial_lines = [
                    {"index": i + 1, "text": f"Γραμμή {i + 1}"}
                    for i in range(10) if i != 4
                ]
                return json.dumps({"translations": partial_lines})
            elif call_count["calls"] == 2:
                # Localized sub-chunk repair call: translates ONLY the dropped line (line 5)
                return json.dumps({"translations": [{"index": 1, "text": "Γραμμή 5 (Επισκευασμένη)"}]})
            return "{}"

        monkeypatch.setattr("services.translation.atomic_runner.rate_limited_call", mock_call)

        success = await _translate_episode_atomic(
            project_name=proj,
            episode_name=ep,
            target_lang="Greek",
            shared_agent=MagicMock(),
            job_id=job_id,
            total_episodes=1,
            model_name="gemini-flash-lite-latest",
        )

        assert success is True
        assert call_count["calls"] >= 2

        # Verify all 10 lines are translated in storage
        ep_data = storage.load_episode(proj, ep)
        assert len(ep_data["data"]) == 10
        for i, row in enumerate(ep_data["data"]):
            assert row.get("translated", "") != "", f"Row {i + 1} was left untranslated!"

        assert ep_data["data"][4]["translated"] == "Γραμμή 5 (Επισκευασμένη)"

    @pytest.mark.asyncio
    async def test_residual_unrepaired_lines_flagged_for_review_without_crashing(self, tmp_path, monkeypatch):
        """When sub-chunk repair fails to recover a cue, line is flagged for review and set empty."""
        from services.translation.atomic_runner import _translate_episode_atomic

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)
        monkeypatch.setattr(storage, "load_global_config", lambda: dict(BASE_TEST_CONFIG))

        proj = "TestResidualProj"
        ep = "E01"
        storage.create_project(proj)
        storage.save_project_metadata(proj, {"target_language": "Greek", "settings": {}})

        cues = [
            {"id": str(i + 1), "timecode": f"00:00:{i:02d},000 --> 00:00:{i+1:02d},000", "original": f"Line {i + 1}"}
            for i in range(10)
        ]
        storage.save_episode(proj, ep, cues, {})

        job_id = create_job("translate", f"Translating {ep}")

        call_count = {"calls": 0}

        async def mock_call(fn, rate_limiter=None, **kwargs):
            call_count["calls"] += 1
            if call_count["calls"] == 1:
                # Main call drops line 3
                return json.dumps({
                    "translations": [{"index": i + 1, "text": f"Γραμμή {i + 1}"} for i in range(10) if i != 2]
                })
            elif call_count["calls"] == 2:
                # Sub-chunk repair call returns empty (model unable to translate)
                return json.dumps({"translations": []})
            return "{}"

        monkeypatch.setattr("services.translation.atomic_runner.rate_limited_call", mock_call)

        success = await _translate_episode_atomic(
            project_name=proj,
            episode_name=ep,
            target_lang="Greek",
            shared_agent=MagicMock(),
            job_id=job_id,
            total_episodes=1,
            model_name="gemini-flash-lite-latest",
        )

        assert success is True
        ep_data = storage.load_episode(proj, ep)
        # Line 3 (index 2) must be flagged for review and empty, not crashing
        assert ep_data["data"][2].get("needs_review") is True
        assert ep_data["data"][2].get("translated") == ""

    @pytest.mark.asyncio
    async def test_atomic_failure_guard_triggers_over_20_percent(self, tmp_path, monkeypatch):
        """When >20% cues are omitted, atomic runner must fail atomically (return False)."""
        from services.translation.atomic_runner import _translate_episode_atomic

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)
        monkeypatch.setattr(storage, "load_global_config", lambda: dict(BASE_TEST_CONFIG))

        proj = "TestAtomicFailProj"
        ep = "E01"
        storage.create_project(proj)
        storage.save_project_metadata(proj, {"target_language": "Greek", "settings": {}})

        # 10 cues
        cues = [
            {"id": str(i + 1), "timecode": f"00:00:{i:02d},000 --> 00:00:{i+1:02d},000", "original": f"Line {i + 1}"}
            for i in range(10)
        ]
        storage.save_episode(proj, ep, cues, {})

        job_id = create_job("translate", f"Translating {ep}")

        # Model drops 3 out of 10 cues (30% missing > 20% guard)
        call_count = {"calls": 0}
        async def mock_call_drop_30_pct(fn, rate_limiter=None, **kwargs):
            call_count["calls"] += 1
            if call_count["calls"] == 1:
                return json.dumps({
                    "translations": [
                        {"index": i + 1, "text": f"Γραμμή {i + 1}"}
                        for i in range(10) if i < 7  # lines 8, 9, 10 dropped
                    ]
                })
            return json.dumps({"translations": []})

        monkeypatch.setattr("services.translation.atomic_runner.rate_limited_call", mock_call_drop_30_pct)

        success = await _translate_episode_atomic(
            project_name=proj,
            episode_name=ep,
            target_lang="Greek",
            shared_agent=MagicMock(),
            job_id=job_id,
            total_episodes=1,
            model_name="gemini-flash-lite-latest",
        )

        # Must fail atomically because 30% lines dropped > 20%
        assert success is False
        atomic_failure_logs = [l for l in jobs[job_id].logs if "failing atomically" in l]
        assert len(atomic_failure_logs) > 0


# ===========================================================================
# 6. Zero Untranslated English Lines in Subtitle Exports
# ===========================================================================

class TestZeroUntranslatedEnglishExport:
    """Verify srt_parser, ass_parser, and source_clean NEVER silently export English source."""

    def test_reconstruct_srt_does_not_export_english_for_untranslated_cues(self):
        """Cues with empty translated text output empty string, never the English original."""
        data = [
            {"id": "1", "timecode": "00:00:01,000 --> 00:00:03,000", "original": "Translated line.", "translated": "Μεταφρασμένη γραμμή."},
            {"id": "2", "timecode": "00:00:04,000 --> 00:00:06,000", "original": "Untranslated line.", "translated": ""},
        ]
        srt = reconstruct_srt(data)
        assert "Μεταφρασμένη γραμμή." in srt
        assert "Untranslated line." not in srt

    def test_reconstruct_srt_preserves_pure_source_files(self):
        """When reconstructing a pure source file (no translation intent), original is preserved."""
        source_data = [
            {"id": "1", "timecode": "00:00:01,000 --> 00:00:03,000", "original": "English original line."},
            {"id": "2", "timecode": "00:00:04,000 --> 00:00:06,000", "original": "Another English line."},
        ]
        srt = reconstruct_srt(source_data)
        assert "English original line." in srt
        assert "Another English line." in srt

    def test_reconstruct_ass_does_not_export_english_for_translatable_dialogue(self):
        """ASS export emits empty dialogue, never English original for translatable events."""
        parsed_data = [
            {
                "id": "1",
                "timecode": "00:00:01,000 --> 00:00:03,000",
                "original": "English dialogue.",
                "translated": "",
                "_ass": {"translatable": True, "prefix_tags": "", "suffix_tags": "", "start_ms": 1000, "end_ms": 3000},
            }
        ]
        ass = reconstruct_ass(parsed_data)
        assert "English dialogue." not in ass

    def test_reconstruct_cleaned_srt_blocks_english_source_fallback(self, tmp_path, monkeypatch):
        """reconstruct_cleaned_srt never leaks raw English when target is Greek."""
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)

        proj = "TestNoEnglish"
        ep = "E01"
        storage.create_project(proj)

        raw_srt = "1\n00:00:01,000 --> 00:00:03,000\nEnglish line 1\n\n2\n00:00:04,000 --> 00:00:06,000\nEnglish line 2\n"
        storage.save_original_subtitle(proj, ep, raw_srt, "e01.en.srt")

        cleaned_data = [
            {
                "id": "1",
                "timecode": "00:00:01,000 --> 00:00:03,000",
                "original": "English line 1",
                "translated": "Ελληνική γραμμή 1",
                "translations": {"el": "Ελληνική γραμμή 1"},
            },
            {
                "id": "2",
                "timecode": "00:00:04,000 --> 00:00:06,000",
                "original": "English line 2",
                "translated": "",
                "translations": {},
            },
        ]
        storage.save_episode_metadata(proj, ep, {"clean_to_orig_map": {"0": [0], "1": [1]}})

        exported_srt = reconstruct_cleaned_srt(proj, ep, cleaned_data, target_lang_code="el")
        assert "Ελληνική γραμμή 1" in exported_srt
        assert "English line 2" not in exported_srt


# ===========================================================================
# 7. Timeline Integrity and Non-Destructive Morphology
# ===========================================================================

class TestTimelineAndMorphologyIntegrity:
    """Verify 1:1 timeline preservation in data.json and non-destructive Greek morphology."""

    def test_internal_data_json_preserves_strict_one_to_one_mapping(self, tmp_path, monkeypatch):
        """data.json must retain identical cue counts, timecodes, and un-wrapped logical lines."""
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)

        proj = "TestTimeline"
        ep = "E01"
        storage.create_project(proj)

        cues = [
            {"id": "1", "timecode": "00:00:01,000 --> 00:00:04,000", "original": "Long sentence that exceeds CPS/CPL thresholds.", "translated": "Μεγάλη πρόταση που υπερβαίνει τα όρια χαρακτήρων ανά γραμμή."},
            {"id": "2", "timecode": "00:00:05,000 --> 00:00:07,000", "original": "Short cue.", "translated": "Σύντομη ατάκα."},
        ]
        storage.save_episode(proj, ep, cues, {})

        loaded = storage.load_episode(proj, ep)
        assert len(loaded["data"]) == 2
        assert loaded["data"][0]["timecode"] == "00:00:01,000 --> 00:00:04,000"
        # No line breaks were injected into data.json
        assert "\n" not in loaded["data"][0]["translated"]

    def test_greek_morphology_enforcement_is_non_destructive(self):
        """Oblique Greek marker (του/τον/της) skips destructive nominative lemma overwrite."""
        from utils.glossary_enforcer import enforce_glossary

        glossary = {"terms": [{"term": "Mount Olympus", "translation": "Όλυμπος", "type": "location"}]}
        source = ["We reached the snowy peak of Mount Olympus."]
        # The translation already declined Mount Olympus into genitive "του Ολύμπου"
        translated = ["Φτάσαμε στη χιονισμένη κορυφή του Ολύμπου."]

        corrected, n_fixed = enforce_glossary(translated, glossary, source, target_lang="Greek")
        # Must not inject nominative "του Όλυμπος"
        assert n_fixed == 0
        assert "του Ολύμπου" in corrected[0]
        assert "του Όλυμπος" not in corrected[0]


# ===========================================================================
# 8. Export Retry Queue Tests (Requirement R1)
# ===========================================================================

class TestExportRetryQueue:
    """Verify transient filesystem/network errors schedule export retries without failing episode."""

    @pytest.mark.asyncio
    async def test_export_retry_queue_transient_failure_and_backoff(self, tmp_path, monkeypatch):
        """When destination media path is unreachable, episode is enqueued for export retry."""
        from services.queue_service import get_queue, enqueue_export_retry
        from services.worker.export_retry_worker import process_export_retry_item
        from services.worker.translation_item_worker import process_translation_item

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)

        proj = "TestExportRetryProj"
        ep = "E01"
        storage.create_project(proj)

        unreachable_dir = tmp_path / "nonexistent_smb_share" / "Season 1"
        media_file = unreachable_dir / "E01.mkv"

        storage.save_project_metadata(proj, {
            "target_language": "Greek",
            "settings": {
                "translation_model": "gemini-3.1-flash-lite",
            }
        })
        storage.save_episode(proj, ep, [
            {"id": "1", "timecode": "00:00:01,000 --> 00:00:03,000", "original": "Hello friend.", "translated": "Γεια σου φίλε."}
        ], {
            "arr_media_path": str(media_file),
            "original_filename": "E01.en.srt",
            "has_source_sub": True,
            "line_count": 1,
        })
        storage.save_original_subtitle(proj, ep, "1\n00:00:01,000 --> 00:00:03,000\nHello friend.\n", "E01.en.srt")

        queue = get_queue(str(tmp_path / "queue.db"))
        job_id = create_job("translate", f"Translating {ep}")

        # Enqueue translation
        item_id = queue.enqueue({
            "project_name": proj,
            "episode_name": ep,
            "priority": 2,
            "options": {"job_id": job_id, "translation_type": "saved"},
        })

        mock_atomic = AsyncMock(return_value=True)
        monkeypatch.setattr("services.worker.translation_item_worker.translate_episode_atomic", mock_atomic)

        item = queue.claim_next()
        await process_translation_item(queue, item, BASE_TEST_CONFIG)

        # Verify internal episode data remains intact
        ep_data = storage.load_episode(proj, ep)
        assert len(ep_data["data"]) == 1
        assert ep_data["data"][0]["translated"] == "Γεια σου φίλε."

        # Episode metadata must NOT be marked failed, but have translation_warning
        ep_meta = storage.load_episode_metadata(proj, ep)
        assert not ep_meta.get("translation_failed")
        assert "Export scheduled for retry" in ep_meta.get("translation_warning", "")

        # Queue must contain an export_retry item
        retry_item = queue.claim_next_export_retry()
        assert retry_item is not None
        assert retry_item["kind"] == "export_retry"
        assert retry_item["project_name"] == proj
        assert retry_item["episode_name"] == ep

        # Step A: process_export_retry_item while destination is still unreachable -> backoff
        await process_export_retry_item(queue, retry_item, BASE_TEST_CONFIG)
        updated_retry = queue.get_item(retry_item["id"])
        assert updated_retry["status"] == "pending"
        assert updated_retry["next_retry_at"] is not None

        # Step B: destination becomes reachable -> retry succeeds
        unreachable_dir.mkdir(parents=True, exist_ok=True)
        # Reset next_retry_at to None to allow immediate claim
        queue.update_status(retry_item["id"], status="pending", next_retry_at=None)
        claimed_again = queue.claim_next_export_retry()
        assert claimed_again is not None

        await process_export_retry_item(queue, claimed_again, BASE_TEST_CONFIG)
        final_item = queue.get_item(retry_item["id"])
        assert final_item["status"] == "completed"

        # Output subtitle file now exists on disk
        target_sub = unreachable_dir / "E01.el.srt"
        assert target_sub.exists()
        assert "Γεια σου φίλε." in target_sub.read_text(encoding="utf-8-sig")

        # Metadata updated with target
        final_meta = storage.load_episode_metadata(proj, ep)
        assert final_meta.get("arr_has_target") is True
        assert final_meta.get("translation_warning") is None
