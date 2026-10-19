"""'Translate all missing' for the whole library: imported episodes now, never-imported ones in a job."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from services import on_demand
from utils import storage
from utils.jobs_manager import jobs


@pytest.fixture(autouse=True)
def no_library_job_yet(monkeypatch):
    monkeypatch.setattr(on_demand, "_library_job_id", None)


@pytest.fixture
def library():
    storage.create_project("Sonarr Show", {"show_name": "Sonarr Show", "arr_source": "sonarr", "arr_sonarr_id": 1})
    storage.create_project("Radarr Movie", {"show_name": "Radarr Movie", "arr_source": "radarr", "type": "movie",
                                            "arr_media_type": "movie", "arr_radarr_id": 2})
    storage.create_project("Gone Show", {"show_name": "Gone Show", "arr_source": "sonarr", "arr_sonarr_id": 3})
    meta = storage.load_project_metadata("Gone Show")
    meta["arr_disabled"] = True
    storage.save_project_metadata("Gone Show", meta)
    storage.create_project("Manual Project", {"show_name": "Manual Project"})
    yield


def test_job_walks_the_sonarr_radarr_titles(library, monkeypatch):
    calls = []

    async def fake_translate_missing(name, priority, include_imported=True):
        calls.append((name, include_imported))
        if name == "Radarr Movie":
            raise RuntimeError("Radarr is down")
        return {"queued_for_translation": 1, "queued_for_extraction": 4, "no_usable_source": 2}

    monkeypatch.setattr(on_demand, "translate_missing", fake_translate_missing)
    job_id = on_demand.start_library_job()
    totals = asyncio.run(on_demand.translate_missing_library(job_id))

    assert sorted(calls) == [("Radarr Movie", False), ("Sonarr Show", False)]  # no manual, no disabled title
    assert totals == {"titles": 2, "queued_for_translation": 1, "queued_for_extraction": 4,
                      "no_usable_source": 2, "errors": 1}
    job = jobs[job_id]
    assert job.status == "completed" and job.progress == 100.0
    assert any("Radarr is down" in line for line in job.logs)


def test_one_library_job_at_a_time(library):
    first = on_demand.start_library_job()
    jobs[first].status = "running"
    assert on_demand.start_library_job() == first
    jobs[first].status = "completed"
    assert on_demand.start_library_job() != first


def test_endpoint_queues_imported_episodes_and_starts_the_job(library, monkeypatch):
    import main
    from services import queue_service

    monkeypatch.setattr(queue_service, "enqueue_missing_for_library", lambda priority: {"Sonarr Show": ["a", "b"]})
    ran = []

    async def fake_job(job_id, priority=None):
        ran.append(job_id)

    monkeypatch.setattr(on_demand, "translate_missing_library", fake_job)
    res = TestClient(main.app).post("/api/queue/enqueue-missing")
    assert res.status_code == 200
    body = res.json()
    assert body["enqueued_count"] == 2 and body["job_id"]
    assert ran == [body["job_id"]]


def test_jobs_created_in_the_same_tick_get_their_own_ids():
    from utils.jobs_manager import create_job
    ids = [create_job("burst") for _ in range(50)]
    assert len(set(ids)) == 50
