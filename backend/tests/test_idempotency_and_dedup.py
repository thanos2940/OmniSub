"""
Tests for Pre-Deployment Requirement R1: Backend Process Deduping and Idempotency.

Covers:
1. translate_episode deduplication against translation_queue (pending/running).
2. batch_translate deduplication by project and target_episodes set match.
3. start_auto_translate deduplication against active auto_translate and simple_pipeline.
4. start_pipeline deduplication against active concurrent pipeline runs.
5. batch_extract_embedded_endpoint deduplication against active extraction jobs.
6. _run_batch_extract cooperative cancellation halts loop and sets terminal status to cancelled.
7. Cancelled jobs are bypassed by deduplication guards so fresh jobs can be initiated.
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from main import app
from utils import storage
from utils.jobs_manager import jobs, create_job, update_job, JobStatus
from utils.translation_queue import TranslationQueue, KIND_TRANSLATION
from integrations import embedded_subs
from integrations.embedded_subs import SubtitleTrack
from routers.embedded import _run_batch_extract


if not hasattr(TranslationQueue, "clear"):
    def _clear_queue(self):
        with self._conn() as conn:
            conn.execute("DELETE FROM translation_queue")
            conn.commit()
    TranslationQueue.clear = _clear_queue


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_jobs_and_queue(isolate_test_data_dir, monkeypatch):
    """Ensure in-memory jobs and translation queue are pristine for each test."""
    test_db = isolate_test_data_dir / "omnisub_test.db"
    monkeypatch.setattr("utils.translation_queue.DB_FILE", test_db)
    monkeypatch.setattr("utils.jobs_manager.DB_FILE", test_db)
    monkeypatch.setattr("utils.paths.DB_FILE", test_db)

    # Isolate storage modules to prevent Windows file locking collisions
    projects_dir = isolate_test_data_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("utils.storage_modules.atomic_io.PROJECTS_DIR", projects_dir)
    monkeypatch.setattr("utils.storage_modules.episode_store.PROJECTS_DIR", projects_dir)
    monkeypatch.setattr("utils.storage_modules.project_store.PROJECTS_DIR", projects_dir)
    monkeypatch.setattr("utils.paths.PROJECTS_DIR", projects_dir)
    monkeypatch.setattr("utils.storage.PROJECTS_DIR", projects_dir)

    from utils import metadata_index
    monkeypatch.setattr(metadata_index, "backfill", lambda: 0)

    from routers.pipeline import _batch_target_episodes
    jobs.clear()
    _batch_target_episodes.clear()
    TranslationQueue().clear()
    yield
    TranslationQueue().clear()
    jobs.clear()
    _batch_target_episodes.clear()


@pytest.fixture
def project_with_episodes(isolate_test_data_dir):
    """Create a sample project with episodes for deduplication tests."""
    import uuid
    proj_name = f"DedupTest_{uuid.uuid4().hex[:8]}"
    storage.create_project(proj_name, {
        "show_name": "Dedup Test Show",
        "target_language": "Greek",
        "settings": {
            "translation_model": "gemini-2.5-flash",
            "context_model": "gemini-2.5-flash",
            "glossary_model": "gemini-2.5-flash",
        }
    })
    # Create episodes
    storage.save_episode(proj_name, "S01E01", [
        {"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hello", "translated": ""}
    ])
    storage.save_episode(proj_name, "S01E02", [
        {"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "World", "translated": ""}
    ])
    storage.save_episode(proj_name, "S01E03", [
        {"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "OmbiSub", "translated": ""}
    ])
    yield proj_name
    try:
        storage.delete_project(proj_name)
    except Exception:
        pass



class TestTranslateEpisodeDedup:
    """Test single episode translation deduplication against translation_queue."""

    def test_translate_episode_deduplication(self, client, project_with_episodes):
        proj_name = project_with_episodes
        ep_name = "S01E01"

        # First dispatch
        resp1 = client.post(f"/projects/{proj_name}/episodes/{ep_name}/translate", json={})
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert "job_id" in data1
        assert "item_id" in data1
        item_id_1 = data1["item_id"]
        job_id_1 = data1["job_id"]

        # Verify queue state
        queue = TranslationQueue()
        item = queue.get_item(item_id_1)
        assert item is not None
        assert item["status"] == "pending"
        assert item["attempts"] == 0

        # Second dispatch with identical project/episode while pending
        resp2 = client.post(f"/projects/{proj_name}/episodes/{ep_name}/translate", json={})
        assert resp2.status_code == 200
        data2 = resp2.json()

        # Idempotency: exact same job_id and item_id returned without churn
        assert data2["job_id"] == job_id_1
        assert data2["item_id"] == item_id_1

        # Verify no duplicate queue rows and attempts was not modified
        with queue._conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM translation_queue WHERE LOWER(project_name) = LOWER(?) AND LOWER(episode_name) = LOWER(?) AND (lang = '' OR lang IS NULL)",
                (proj_name, ep_name)
            )
            matching = [dict(r) for r in cursor.fetchall()]
        assert len(matching) == 1
        assert matching[0]["attempts"] == 0


    def test_translate_episode_different_lang_or_ep(self, client, project_with_episodes):
        proj_name = project_with_episodes

        # Enqueue primary language
        resp1 = client.post(f"/projects/{proj_name}/episodes/S01E01/translate", json={})
        assert resp1.status_code == 200
        id1 = resp1.json()["item_id"]

        # Different episode: should create a distinct queue item
        resp2 = client.post(f"/projects/{proj_name}/episodes/S01E02/translate", json={})
        assert resp2.status_code == 200
        id2 = resp2.json()["item_id"]
        assert id2 != id1

        # Same episode with explicit different secondary language
        resp3 = client.post(f"/projects/{proj_name}/episodes/S01E01/translate", json={"lang": "es"})
        assert resp3.status_code == 200
        id3 = resp3.json()["item_id"]
        assert id3 != id1

    def test_translate_episode_re_enqueues_after_completion(self, client, project_with_episodes):
        proj_name = project_with_episodes
        ep_name = "S01E01"

        resp1 = client.post(f"/projects/{proj_name}/episodes/{ep_name}/translate", json={})
        item_id_1 = resp1.json()["item_id"]

        # Mark item as completed in queue
        queue = TranslationQueue()
        queue.update_status(item_id_1, status="completed")

        # Now re-translate should re-activate item
        resp2 = client.post(f"/projects/{proj_name}/episodes/{ep_name}/translate", json={})
        assert resp2.status_code == 200
        item = queue.get_item(item_id_1)
        assert item["status"] == "pending"


class TestBatchTranslateDedup:
    """Test batch translation deduplication by project and target_episodes set match."""

    @patch("routers.pipeline._monitor_batch_queue_progress", new_callable=MagicMock)
    def test_batch_translate_exact_set_deduplication(self, mock_mon, client, project_with_episodes):
        proj_name = project_with_episodes

        # First batch dispatch
        resp1 = client.post(
            f"/projects/{proj_name}/batch-translate",
            json={"episode_names": ["S01E01", "S01E02"]}
        )
        assert resp1.status_code == 200
        job_id_1 = resp1.json()["job_id"]

        # Second dispatch with identical episodes in reversed order
        resp2 = client.post(
            f"/projects/{proj_name}/batch-translate",
            json={"episode_names": ["S01E02", "S01E01"]}
        )
        assert resp2.status_code == 200
        job_id_2 = resp2.json()["job_id"]

        # Must return the existing active batch job ID
        assert job_id_2 == job_id_1

    @patch("routers.pipeline._monitor_batch_queue_progress", new_callable=MagicMock)
    def test_batch_translate_different_episodes_spawns_new(self, mock_mon, client, project_with_episodes):
        proj_name = project_with_episodes

        resp1 = client.post(
            f"/projects/{proj_name}/batch-translate",
            json={"episode_names": ["S01E01", "S01E02"]}
        )
        job_id_1 = resp1.json()["job_id"]

        # Different episode subset: should create a new batch job
        resp2 = client.post(
            f"/projects/{proj_name}/batch-translate",
            json={"episode_names": ["S01E03"]}
        )
        assert resp2.status_code == 200
        job_id_2 = resp2.json()["job_id"]
        assert job_id_2 != job_id_1

    @patch("routers.pipeline._monitor_batch_queue_progress", new_callable=MagicMock)
    def test_batch_translate_after_completion(self, mock_mon, client, project_with_episodes):
        proj_name = project_with_episodes

        resp1 = client.post(
            f"/projects/{proj_name}/batch-translate",
            json={"episode_names": ["S01E01"]}
        )
        job_id_1 = resp1.json()["job_id"]

        # Mark first batch as completed
        update_job(job_id_1, status="completed")

        # Now re-running the same batch should spawn a new job
        resp2 = client.post(
            f"/projects/{proj_name}/batch-translate",
            json={"episode_names": ["S01E01"]}
        )
        job_id_2 = resp2.json()["job_id"]
        assert job_id_2 != job_id_1

    @patch("routers.pipeline._monitor_batch_queue_progress", new_callable=MagicMock)
    def test_batch_translate_returns_sub_jobs_and_embeds_parent_id(self, mock_mon, client, project_with_episodes):
        import json
        proj_name = project_with_episodes
        resp = client.post(
            f"/projects/{proj_name}/batch-translate",
            json={"episode_names": ["S01E01", "S01E02"]}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert "sub_jobs" in data
        assert "item_ids" in data
        assert len(data["sub_jobs"]) == 2
        assert data["sub_jobs"] == data["item_ids"]

        # Verify queue items carry parent_job_id in options
        queue = TranslationQueue()
        for item_id in data["sub_jobs"]:
            item = queue.get_item(item_id)
            assert item is not None
            opts = item.get("options") or {}
            if isinstance(opts, str):
                opts = json.loads(opts)
            assert opts.get("parent_job_id") == data["job_id"]

        # Verify dedup response also includes sub_jobs and item_ids
        resp_dedup = client.post(
            f"/projects/{proj_name}/batch-translate",
            json={"episode_names": ["S01E01", "S01E02"]}
        )
        assert resp_dedup.status_code == 200
        dedup_data = resp_dedup.json()
        assert dedup_data["job_id"] == data["job_id"]
        assert dedup_data["sub_jobs"] == data["sub_jobs"]
        assert dedup_data["item_ids"] == data["item_ids"]


class TestAutoTranslateDedup:
    """Test start_auto_translate deduplication against active auto_translate and simple_pipeline."""

    @patch("routers.pipeline._process_auto_translate", new_callable=MagicMock)
    def test_auto_translate_dedup_while_active(self, mock_process, client, project_with_episodes):
        proj_name = project_with_episodes

        resp1 = client.post(f"/projects/{proj_name}/auto-translate", json={})
        assert resp1.status_code == 200
        job_id_1 = resp1.json()["job_id"]

        # Second request while job_id_1 is pending
        resp2 = client.post(f"/projects/{proj_name}/auto-translate", json={})
        assert resp2.status_code == 200
        job_id_2 = resp2.json()["job_id"]
        assert job_id_2 == job_id_1

    def test_auto_translate_dedup_against_simple_pipeline(self, client, project_with_episodes):
        proj_name = project_with_episodes

        # Create active simple_pipeline in awaiting_review
        sp_id = create_job("simple_pipeline", project_name=proj_name)
        jobs[sp_id].pipeline_mode = "simple"
        jobs[sp_id].status = "awaiting_review"

        # Attempting auto_translate should return existing simple_pipeline job ID
        resp = client.post(f"/projects/{proj_name}/auto-translate", json={})
        assert resp.status_code == 200
        assert resp.json()["job_id"] == sp_id

        # Mark simple_pipeline completed
        update_job(sp_id, status="completed")

        # Now auto_translate can create a fresh auto_translate job
        with patch("routers.pipeline._process_auto_translate", new_callable=MagicMock):
            resp2 = client.post(f"/projects/{proj_name}/auto-translate", json={})
            assert resp2.status_code == 200
            new_id = resp2.json()["job_id"]
            assert new_id != sp_id
            assert new_id.startswith("auto_translate_")


class TestPipelineDedup:
    """Test start_pipeline deduplication against concurrent pipeline runs."""

    @patch("routers.pipeline._process_pipeline", new_callable=MagicMock)
    def test_pipeline_dedup_while_active(self, mock_proc, client, project_with_episodes):
        proj_name = project_with_episodes

        resp1 = client.post(f"/projects/{proj_name}/pipeline/start", json={"mode": "auto"})
        assert resp1.status_code == 200
        job_id_1 = resp1.json()["job_id"]

        # Second call while active returns identical job_id
        resp2 = client.post(f"/projects/{proj_name}/pipeline/start", json={"mode": "auto"})
        assert resp2.status_code == 200
        assert resp2.json()["job_id"] == job_id_1

        # Mark completed
        update_job(job_id_1, status="completed")

        # Third call creates a new pipeline job
        resp3 = client.post(f"/projects/{proj_name}/pipeline/start", json={"mode": "auto"})
        assert resp3.status_code == 200
        job_id_3 = resp3.json()["job_id"]
        assert job_id_3 != job_id_1


class TestBatchExtractEmbeddedDedupAndCancellation:
    """Test batch_extract_embedded deduplication and cooperative cancellation."""

    def test_batch_extract_endpoint_deduplication(self, client, project_with_episodes):
        proj_name = project_with_episodes

        with patch("routers.embedded._run_batch_extract", new_callable=MagicMock):
            resp1 = client.post(f"/projects/{proj_name}/embedded/extract-all", json={"force": True})
            assert resp1.status_code == 200
            job_id_1 = resp1.json()["job_id"]

            # Second call while active
            resp2 = client.post(f"/projects/{proj_name}/embedded/extract-all", json={"force": True})
            assert resp2.status_code == 200
            job_id_2 = resp2.json()["job_id"]
            assert job_id_2 == job_id_1

            # Complete job
            update_job(job_id_1, status="completed")

            # Third call creates fresh job
            resp3 = client.post(f"/projects/{proj_name}/embedded/extract-all", json={"force": True})
            assert resp3.status_code == 200
            job_id_3 = resp3.json()["job_id"]
            assert job_id_3 != job_id_1

    @pytest.mark.asyncio
    async def test_batch_extract_cooperative_cancellation(self, project_with_episodes, tmp_path, monkeypatch):
        """Verify _run_batch_extract aborts upon user cancellation and remains cancelled."""
        proj_name = project_with_episodes
        job_id = create_job("batch_extract_embedded", project_name=proj_name)

        # Create dummy media files
        episodes = ["S01E01", "S01E02", "S01E03"]
        for ep in episodes:
            media_file = tmp_path / f"Show.{ep}.mkv"
            media_file.write_bytes(b"dummy mkv")
            storage.save_episode(proj_name, ep, [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hi"}], metadata={"arr_media_path": str(media_file)})

        fake_ass = "[Script Info]\nTitle: Test\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Line\n"
        fake_tracks = [SubtitleTrack(index=2, codec="ass", language="eng", title="Sub", frames=1)]

        monkeypatch.setattr(embedded_subs, "resolve_tools", lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))
        monkeypatch.setattr(embedded_subs, "probe_subtitle_tracks", lambda *a, **k: fake_tracks)

        extract_count = 0
        async def fake_extract(*a, **k):
            nonlocal extract_count
            extract_count += 1
            # User cancels during first episode extraction
            jobs[job_id].cancelled = True
            return fake_ass

        monkeypatch.setattr(embedded_subs, "extract_track", fake_extract)

        # Run extraction task
        await _run_batch_extract(job_id, proj_name, episodes, force=True, migrate_srt=False)

        # Assert cancellation halted the batch immediately after episode 1
        assert extract_count == 1
        assert jobs[job_id].status == "cancelled"
        assert jobs[job_id].status != "completed"
        assert "cancelled by user" in jobs[job_id].message.lower()

    def test_cancelled_jobs_allow_fresh_submission(self, client, project_with_episodes):
        """Cancelled jobs must not block fresh dispatches."""
        proj_name = project_with_episodes

        with patch("routers.embedded._run_batch_extract", new_callable=MagicMock):
            resp1 = client.post(f"/projects/{proj_name}/embedded/extract-all", json={"force": True})
            job_id_1 = resp1.json()["job_id"]

            # Cancel job
            jobs[job_id_1].cancelled = True
            jobs[job_id_1].status = "cancelled"

            # Subsequent call must not reuse the cancelled job
            resp2 = client.post(f"/projects/{proj_name}/embedded/extract-all", json={"force": True})
            job_id_2 = resp2.json()["job_id"]
            assert job_id_2 != job_id_1
