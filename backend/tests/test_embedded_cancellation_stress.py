"""
Empirical stress tests for embedded subtitle extraction cooperative cancellation
and deduplication logic in backend/routers/embedded.py.

Testing:
1. Cancellation fired before the loop starts (non-empty episodes list, empty episodes list, missing tools).
2. Cancellation fired on the last episode (during last episode, immediately after last episode).
3. Multiple concurrent extraction requests for the same project and different projects.
4. Cancellation status persistence (exceptions during cancellation, preventing status overwrites).
5. Fast cancellation race: cancellation fired while extraction is in flight.
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from fastapi.testclient import TestClient
from main import app
from utils import storage
from utils.jobs_manager import jobs, create_job, update_job, JobStatus
from integrations import embedded_subs
from integrations.embedded_subs import SubtitleTrack
from routers.embedded import _run_batch_extract


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_jobs(isolate_test_data_dir, monkeypatch):
    """Ensure in-memory jobs and translation queue are clean."""
    test_db = isolate_test_data_dir / "omnisub_test.db"
    monkeypatch.setattr("utils.translation_queue.DB_FILE", test_db)
    monkeypatch.setattr("utils.jobs_manager.DB_FILE", test_db)
    from utils import metadata_index
    monkeypatch.setattr(metadata_index, "backfill", lambda: 0)
    jobs.clear()
    yield
    jobs.clear()


@pytest.fixture
def test_project(isolate_test_data_dir, tmp_path):
    """Setup a sample project with media files for extraction tests."""
    proj_name = "StressProject"
    storage.create_project(proj_name, {
        "show_name": "Stress Project Show",
        "target_language": "Greek",
        "settings": {
            "prefer_ass_format": "auto",
        }
    })

    episodes = ["S01E01", "S01E02", "S01E03"]
    media_paths = {}
    for ep in episodes:
        media_file = tmp_path / f"Stress.{ep}.mkv"
        media_file.write_bytes(b"dummy mkv content for stress test")
        storage.save_episode(
            proj_name,
            ep,
            [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": f"Line for {ep}", "translated": ""}],
            metadata={"arr_media_path": str(media_file), "original_format": "srt"}
        )
        media_paths[ep] = media_file

    return proj_name, episodes, media_paths


class TestCancellationBeforeLoop:
    """Stress tests: cancellation fired before _run_batch_extract loop begins."""

    @pytest.mark.asyncio
    async def test_cancellation_before_loop_with_episodes(self, test_project, monkeypatch):
        """When cancelled before loop starts with episodes present, 0 episodes are extracted and status is 'cancelled'."""
        proj_name, episodes, _ = test_project
        job_id = create_job("batch_extract_embedded", project_name=proj_name)

        # Pre-cancel the job
        jobs[job_id].cancelled = True
        jobs[job_id].status = "cancelled"

        extract_called = 0
        async def fake_extract(*args, **kwargs):
            nonlocal extract_called
            extract_called += 1
            return "dummy"

        monkeypatch.setattr(embedded_subs, "resolve_tools", lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))
        monkeypatch.setattr(embedded_subs, "probe_subtitle_tracks", lambda *a, **k: [SubtitleTrack(index=2, codec="ass", language="eng")])
        monkeypatch.setattr(embedded_subs, "extract_track", fake_extract)

        await _run_batch_extract(job_id, proj_name, episodes, force=True, migrate_srt=False)

        assert extract_called == 0, "No extraction should have been performed"
        assert jobs[job_id].cancelled is True
        assert jobs[job_id].status == "cancelled", f"Status should be 'cancelled', got '{jobs[job_id].status}'"
        assert "cancelled by user" in jobs[job_id].message.lower()

    @pytest.mark.asyncio
    async def test_cancellation_before_loop_with_empty_target_list(self, test_project, monkeypatch):
        """Edge case: When cancelled before loop starts and target list is empty (0 episodes),
        does status remain 'cancelled' or get stuck in 'running'?"""
        proj_name, _, _ = test_project
        job_id = create_job("batch_extract_embedded", project_name=proj_name)

        # Cancel job prior to execution
        jobs[job_id].cancelled = True
        jobs[job_id].status = "cancelled"

        monkeypatch.setattr(embedded_subs, "resolve_tools", lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))

        # Invoke with empty list of episodes
        await _run_batch_extract(job_id, proj_name, [], force=True, migrate_srt=False)

        assert jobs[job_id].cancelled is True
        # Critical verification: did the empty loop leave status as 'running' because line 217 set it to 'running'
        # and the loop body never ran to reset it to 'cancelled'?
        assert jobs[job_id].status == "cancelled", (
            f"VULNERABILITY DETECTED: Empty episode list caused cancelled job to remain in '{jobs[job_id].status}' status!"
        )


class TestCancellationOnLastEpisode:
    """Stress tests: cancellation fired on or after the last episode."""

    @pytest.mark.asyncio
    async def test_cancellation_during_last_episode(self, test_project, monkeypatch):
        """Cancellation triggered while extracting the last episode (episode 3 of 3)."""
        proj_name, episodes, _ = test_project
        job_id = create_job("batch_extract_embedded", project_name=proj_name)

        fake_ass = (
            "[Script Info]\nTitle: Test\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Stress Last Ep Dialogue\n"
        )
        fake_tracks = [SubtitleTrack(index=2, codec="ass", language="eng", title="Full Dialogue", frames=1)]

        monkeypatch.setattr(embedded_subs, "resolve_tools", lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))
        monkeypatch.setattr(embedded_subs, "probe_subtitle_tracks", lambda *a, **k: fake_tracks)

        extract_count = 0
        async def fake_extract(*args, **kwargs):
            nonlocal extract_count
            extract_count += 1
            if extract_count == 3:  # Last episode!
                # User cancels during extraction of last episode
                jobs[job_id].cancelled = True
                jobs[job_id].status = "cancelled"
            return fake_ass

        monkeypatch.setattr(embedded_subs, "extract_track", fake_extract)

        await _run_batch_extract(job_id, proj_name, episodes, force=True, migrate_srt=False)

        assert extract_count == 3
        # Must NOT be completed despite all 3 having extracted!
        assert jobs[job_id].status == "cancelled", f"Status must be 'cancelled', got '{jobs[job_id].status}'"
        assert jobs[job_id].status != "completed"
        assert jobs[job_id].result is None, "Result dict should not be populated on cancelled job"

    @pytest.mark.asyncio
    async def test_cancellation_immediately_after_last_episode_loop(self, test_project, monkeypatch):
        """Cancellation triggered immediately after loop finishes but before status='completed' update."""
        proj_name, episodes, _ = test_project
        job_id = create_job("batch_extract_embedded", project_name=proj_name)

        fake_ass = (
            "[Script Info]\nTitle: Test\nScriptType: v4.00+\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Stress Dialogue\n"
        )
        fake_tracks = [SubtitleTrack(index=2, codec="ass", language="eng", title="Full Dialogue", frames=1)]

        monkeypatch.setattr(embedded_subs, "resolve_tools", lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))
        monkeypatch.setattr(embedded_subs, "probe_subtitle_tracks", lambda *a, **k: fake_tracks)

        # Hook into storage._invalidate_episodes_list_cache to simulate cancellation right after loop
        orig_invalidate = storage._invalidate_episodes_list_cache
        def cancel_hook(pn):
            jobs[job_id].cancelled = True
            jobs[job_id].status = "cancelled"
            return orig_invalidate(pn)

        monkeypatch.setattr(storage, "_invalidate_episodes_list_cache", cancel_hook)
        monkeypatch.setattr(embedded_subs, "extract_track", AsyncMock(return_value=fake_ass))

        await _run_batch_extract(job_id, proj_name, episodes, force=True, migrate_srt=False)

        assert jobs[job_id].status == "cancelled", (
            f"VULNERABILITY DETECTED: Cancellation during post-loop cleanup was overwritten with '{jobs[job_id].status}'!"
        )


class TestDeduplicationAndConcurrency:
    """Stress tests: deduplication and concurrent access."""

    def test_concurrent_endpoint_dispatches_same_project(self, client, test_project):
        """Repeated concurrent calls to batch_extract_embedded for same project return exact same job_id."""
        proj_name, _, _ = test_project

        with patch("routers.embedded._run_batch_extract", new_callable=MagicMock):
            results = []
            for _ in range(10):
                resp = client.post(f"/projects/{proj_name}/embedded/extract-all", json={"force": True})
                assert resp.status_code == 200
                results.append(resp.json()["job_id"])

            # All 10 rapid dispatches must return the identical job_id
            assert len(set(results)) == 1, f"Expected 1 unique job_id across 10 dispatches, got {len(set(results))}: {results}"

    def test_concurrent_dispatches_different_projects(self, client, test_project, isolate_test_data_dir):
        """Different projects must receive distinct job IDs and not block each other."""
        proj_name_1, _, _ = test_project
        proj_name_2 = "SecondProject"
        storage.create_project(proj_name_2, {"show_name": "Second Show"})

        with patch("routers.embedded._run_batch_extract", new_callable=MagicMock):
            resp1 = client.post(f"/projects/{proj_name_1}/embedded/extract-all", json={})
            resp2 = client.post(f"/projects/{proj_name_2}/embedded/extract-all", json={})

            assert resp1.status_code == 200
            assert resp2.status_code == 200
            assert resp1.json()["job_id"] != resp2.json()["job_id"]

    def test_dedup_resumes_after_cancellation_and_after_completion(self, client, test_project):
        """After a job completes or is cancelled, subsequent calls must generate fresh job IDs."""
        proj_name, _, _ = test_project

        with patch("routers.embedded._run_batch_extract", new_callable=MagicMock):
            # 1. First job
            r1 = client.post(f"/projects/{proj_name}/embedded/extract-all", json={})
            job_1 = r1.json()["job_id"]

            # Cancel job 1
            client.post(f"/jobs/{job_1}/cancel")
            assert jobs[job_1].cancelled is True
            assert jobs[job_1].status == "cancelled"

            # 2. Fresh job allowed after cancellation
            r2 = client.post(f"/projects/{proj_name}/embedded/extract-all", json={})
            job_2 = r2.json()["job_id"]
            assert job_2 != job_1

            # Complete job 2
            update_job(job_2, status="completed")

            # 3. Fresh job allowed after completion
            r3 = client.post(f"/projects/{proj_name}/embedded/extract-all", json={})
            job_3 = r3.json()["job_id"]
            assert job_3 != job_2
            assert job_3 != job_1


class TestStatusPersistenceUnderErrors:
    """Stress tests: status persistence when exceptions occur alongside cancellation."""

    @pytest.mark.asyncio
    async def test_exception_during_cancelled_episode(self, test_project, monkeypatch):
        """When an exception occurs on an episode where cancellation was signaled, status remains 'cancelled'."""
        proj_name, episodes, _ = test_project
        job_id = create_job("batch_extract_embedded", project_name=proj_name)

        monkeypatch.setattr(embedded_subs, "resolve_tools", lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))
        monkeypatch.setattr(embedded_subs, "probe_subtitle_tracks", lambda *a, **k: [SubtitleTrack(index=2, codec="ass", language="eng")])

        async def failing_extract(*args, **kwargs):
            # Cancel job and then fail
            jobs[job_id].cancelled = True
            jobs[job_id].status = "cancelled"
            raise RuntimeError("Extraction binary segfault simulated")

        monkeypatch.setattr(embedded_subs, "extract_track", failing_extract)

        await _run_batch_extract(job_id, proj_name, episodes, force=True, migrate_srt=False)

        assert jobs[job_id].status == "cancelled"
        assert jobs[job_id].status != "failed"

    @pytest.mark.asyncio
    async def test_outer_exception_does_not_overwrite_cancelled_status(self, test_project, monkeypatch):
        """When an unhandled exception occurs in the outer try block, cancelled job is not marked 'failed'."""
        proj_name, episodes, _ = test_project
        job_id = create_job("batch_extract_embedded", project_name=proj_name)

        jobs[job_id].cancelled = True
        jobs[job_id].status = "cancelled"

        # Force outer block exception by making storage.load_project_metadata raise
        def exploding_metadata(pn):
            raise ValueError("Corrupt metadata database")

        monkeypatch.setattr(storage, "load_project_metadata", exploding_metadata)

        await _run_batch_extract(job_id, proj_name, episodes, force=True, migrate_srt=False)

        # Line 405-407: if cancelled, returns early without setting status="failed"
        assert jobs[job_id].status == "cancelled"
        assert jobs[job_id].status != "failed"
