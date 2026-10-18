"""
Empirical Adversarial Test Suite for Milestone 4 (R3):
Parent-Child Job Discovery, Options Injection, and Root Filtering Backend Contracts.

Author: Challenger M4-1
Focus:
- Multi-episode batch translate returns job_id, sub_jobs, and item_ids.
- Enqueued items in translation_queue strictly contain options.parent_job_id.
- Single-episode translation (standalone) does NOT inject parent_job_id.
- Database fallback in routers/jobs correctly reads parent_id from queue options.
- Dedup returns identical parent_job_id and sub_jobs.
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from main import app
from utils import storage
from utils.jobs_manager import jobs, create_job, update_job, JobStatus
from utils.translation_queue import TranslationQueue, KIND_TRANSLATION


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_test_env(isolate_test_data_dir, monkeypatch):
    """Ensure in-memory jobs and translation queue are isolated and clean."""
    test_db = isolate_test_data_dir / "omnisub_challenger_m4.db"
    monkeypatch.setattr("utils.translation_queue.DB_FILE", test_db)
    monkeypatch.setattr("utils.jobs_manager.DB_FILE", test_db)
    monkeypatch.setattr("utils.paths.DB_FILE", test_db)

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
    queue = TranslationQueue()
    with queue._conn() as conn:
        conn.execute("DELETE FROM translation_queue")
        conn.commit()

    yield

    with queue._conn() as conn:
        conn.execute("DELETE FROM translation_queue")
        conn.commit()
    jobs.clear()
    _batch_target_episodes.clear()


@pytest.fixture
def multi_ep_project(isolate_test_data_dir):
    """Create a test project with 5 episodes for stress testing batch translation."""
    import uuid
    proj_name = f"BatchParentTest_{uuid.uuid4().hex[:8]}"
    storage.create_project(proj_name, {
        "show_name": "Batch Parent Test Show",
        "target_language": "Greek",
        "settings": {
            "translation_model": "gemini-2.5-flash",
            "context_model": "gemini-2.5-flash",
            "glossary_model": "gemini-2.5-flash",
        }
    })
    for i in range(1, 6):
        ep = f"S01E{i:02d}"
        storage.save_episode(proj_name, ep, [
            {"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": f"Line for {ep}", "translated": ""}
        ])
    yield proj_name
    try:
        storage.delete_project(proj_name)
    except Exception:
        pass


class TestChallengerParentChildBackend:
    """Adversarial challenge on backend parent-child contracts."""

    @patch("routers.pipeline._monitor_batch_queue_progress", new_callable=MagicMock)
    def test_five_episode_batch_registers_parent_and_children_options(self, mock_mon, client, multi_ep_project):
        """Stress-test: 5-episode batch must return exactly 5 sub_jobs all linking to the 1 parent job."""
        proj_name = multi_ep_project
        episodes = [f"S01E{i:02d}" for i in range(1, 6)]

        resp = client.post(
            f"/projects/{proj_name}/batch-translate",
            json={"episode_names": episodes}
        )
        assert resp.status_code == 200, f"Batch translate failed: {resp.text}"
        data = resp.json()

        parent_job_id = data.get("job_id")
        sub_jobs = data.get("sub_jobs")
        item_ids = data.get("item_ids")

        assert parent_job_id is not None
        assert parent_job_id.startswith("batch_translate_")
        assert isinstance(sub_jobs, list)
        assert len(sub_jobs) == 5
        assert sub_jobs == item_ids

        # Verify parent job exists in jobs manager
        parent_job = jobs.get(parent_job_id)
        assert parent_job is not None
        assert parent_job.id.startswith("batch_translate_")
        assert parent_job.sub_jobs == sub_jobs

        # Verify translation_queue has 5 rows, each tagged with options.parent_job_id == parent_job_id
        queue = TranslationQueue()
        for item_id in sub_jobs:
            item = queue.get_item(item_id)
            assert item is not None, f"Item {item_id} not found in translation_queue"
            opts = item.get("options") or {}
            if isinstance(opts, str):
                opts = json.loads(opts)
            assert opts.get("parent_job_id") == parent_job_id, (
                f"Queue item {item_id} missing or mismatched parent_job_id: {opts}"
            )

    def test_standalone_single_episode_has_no_parent_job_id(self, client, multi_ep_project):
        """Standalone single episode translation must NOT carry parent_job_id."""
        proj_name = multi_ep_project
        ep = "S01E01"

        resp = client.post(f"/projects/{proj_name}/episodes/{ep}/translate", json={})
        assert resp.status_code == 200
        data = resp.json()
        item_id = data["item_id"]

        queue = TranslationQueue()
        item = queue.get_item(item_id)
        assert item is not None
        opts = item.get("options") or {}
        if isinstance(opts, str):
            opts = json.loads(opts)
        assert opts.get("parent_job_id") is None, f"Standalone job should not have parent_job_id: {opts}"

    @patch("routers.pipeline._monitor_batch_queue_progress", new_callable=MagicMock)
    def test_jobs_endpoint_database_fallback_resolves_parent_id(self, mock_mon, client, multi_ep_project):
        """If an in-memory job is evicted, /api/jobs/{id} fallback to DB must resolve parent_id from options."""
        proj_name = multi_ep_project
        episodes = ["S01E01"]

        resp = client.post(
            f"/projects/{proj_name}/batch-translate",
            json={"episode_names": episodes}
        )
        assert resp.status_code == 200
        data = resp.json()
        parent_job_id = data["job_id"]
        child_item_id = data["sub_jobs"][0]

        # Simulate eviction from in-memory jobs manager for the child worker job
        bg_child_id = f"bg_{child_item_id}"
        if bg_child_id in jobs:
            del jobs[bg_child_id]

        # Query /api/jobs/bg_{child_item_id}
        resp_child = client.get(f"/jobs/{bg_child_id}")
        assert resp_child.status_code == 200
        child_data = resp_child.json()

        assert child_data.get("parent_id") == parent_job_id, (
            f"DB fallback did not resolve parent_id: {child_data}"
        )

    def test_standalone_job_db_fallback_parent_id_is_none(self, client, multi_ep_project):
        """Standalone job DB fallback must return parent_id as None."""
        proj_name = multi_ep_project
        ep = "S01E02"

        resp = client.post(f"/projects/{proj_name}/episodes/{ep}/translate", json={})
        item_id = resp.json()["item_id"]
        bg_child_id = f"bg_{item_id}"

        if bg_child_id in jobs:
            del jobs[bg_child_id]

        resp_child = client.get(f"/jobs/{bg_child_id}")
        assert resp_child.status_code == 200
        child_data = resp_child.json()
        assert child_data.get("parent_id") is None
