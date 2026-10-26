"""
Live-work snapshot and job lifecycle
====================================
The "Active Tasks" panel is a view of ``GET /api/activity``: only what runs now is listed, a queue row
nobody owns is ``orphaned``, jobs that never started or whose queue row ended are closed by the
maintenance eviction, a worker job whose row is gone answers a final status instead of 404, and queue
changes reach the websocket as one coalesced ``queue_updated`` per burst.
"""
import time

import pytest
from fastapi.testclient import TestClient

from utils import event_broadcaster, jobs_manager
from utils.jobs_manager import JobStatus, create_job, jobs, time_out_stale_jobs, update_job
from utils.translation_queue import TranslationQueue, KIND_TRANSLATION, PRIORITY_MANUAL
from routers import activity


@pytest.fixture(autouse=True)
def clean():
    jobs.clear()
    activity._cache.update(at=0.0, body=None)

    def wipe():
        with TranslationQueue()._conn() as conn:
            conn.execute("DELETE FROM translation_queue")
            conn.commit()
    wipe()
    yield
    wipe()
    jobs.clear()


@pytest.fixture
def client():
    from main import app
    return TestClient(app)


def _row(project="Snap Show", episode="S01E01", status="running"):
    q = TranslationQueue()
    item_id = q.enqueue(project_name=project, episode_name=episode, priority=PRIORITY_MANUAL, kind=KIND_TRANSLATION)
    if status != "pending":
        q.update_status(item_id, status=status)
    return item_id


class TestSnapshot:

    def test_lists_live_jobs_and_classifies_queue_rows(self, client):
        jid = create_job("create_context", "Snap Show")
        update_job(jid, status="running", progress=40.0, message="Reading the show")
        done = create_job("create_glossary", "Snap Show")
        update_job(done, status="completed")
        orphan = _row()                       # running, but no worker job and no agent claim
        _row(episode="S01E02", status="pending")
        jobs[f"bg_{orphan}"] = None  # never happens, but a None must not break the snapshot
        del jobs[f"bg_{orphan}"]

        body = client.get("/api/activity").json()
        assert [j["id"] for j in body["jobs"]] == [jid]
        job = body["jobs"][0]
        assert job["status"] == "running" and job["progress"] == 40.0 and job["message"] == "Reading the show"
        assert job["link"] == "/project/Snap%20Show"
        assert [q["id"] for q in body["queue"]] == [orphan]
        assert body["queue"][0]["status"] == "orphaned"
        assert body["queue"][0]["link"] == "/project/Snap%20Show/episode/S01E01"
        assert body["summary"]["running"] == 1 and body["summary"]["pending"] == 1
        assert body["worker"]["paused"] is False
        assert body["runner"]["running"] is False
        assert set(body) >= {"ts", "jobs", "queue", "runner", "worker", "summary", "translation_engine"}

    def test_a_worker_job_makes_its_row_live(self, client):
        item = _row()
        jobs[f"bg_{item}"] = JobStatus(id=f"bg_{item}", status="running", progress=12.5, message="Scene 2",
                                       project_name="Snap Show", episode_name="S01E01", cues_translated=5, cues_total=40)
        body = client.get("/api/activity?force=true").json()
        assert body["jobs"] == []                       # bg_ jobs are reported through their row
        (entry,) = body["queue"]
        # The worker loop is not running under TestClient, so the row has no live owner.
        assert entry["status"] == "orphaned"
        assert entry["progress"] == 12.5 and entry["units_done"] == 5 and entry["units_total"] == 40

    def test_old_pending_jobs_are_not_shown(self, client):
        jid = create_job("episode_summary", "Snap Show", "S01E01")
        jobs[jid].created_at = time.time() - 2 * activity.PENDING_SHOWN_SECONDS
        assert client.get("/api/activity?force=true").json()["jobs"] == []

    def test_snapshot_is_cached_briefly(self, client):
        first = client.get("/api/activity").json()
        create_job("create_context", "Snap Show")
        assert client.get("/api/activity").json()["ts"] == first["ts"]
        assert client.get("/api/activity?force=true").json()["jobs"]


class TestEviction:

    def test_a_pending_job_that_never_started_times_out(self):
        jid = create_job("create_context", "Snap Show")
        jobs[jid].created_at = time.time() - jobs_manager.PENDING_TIMEOUT_SECONDS - 1
        fresh = create_job("create_context", "Snap Show")
        assert time_out_stale_jobs() == 1
        assert jobs[jid].status == "failed" and jobs[jid].finished_at
        assert jobs[fresh].status == "pending"

    def test_a_worker_job_follows_its_queue_row(self):
        done = _row(episode="S01E01", status="completed")
        gone = "no-such-row"
        live = _row(episode="S01E03", status="running")
        for item in (done, gone, live):
            jobs[f"bg_{item}"] = JobStatus(id=f"bg_{item}", status="running", project_name="Snap Show")
        assert time_out_stale_jobs() == 2
        assert jobs[f"bg_{done}"].status == "completed"
        assert jobs[f"bg_{gone}"].status == "cancelled"
        assert jobs[f"bg_{live}"].status == "running"

    def test_evict_old_jobs_closes_stale_jobs_first(self):
        jid = create_job("create_context", "Snap Show")
        jobs[jid].created_at = time.time() - jobs_manager.PENDING_TIMEOUT_SECONDS - 1
        jobs_manager.evict_old_jobs(max_age_seconds=10 ** 6)
        assert jobs[jid].status == "failed"

    def test_stale_history_rows_are_closed_at_startup(self):
        jid = create_job("create_context", "Snap Show")   # written to job_history as pending, synchronously
        jobs.clear()
        assert jobs_manager.mark_stale_history() >= 1
        with TranslationQueue()._conn() as conn:
            row = conn.execute("SELECT status, message FROM job_history WHERE id = ?", (jid,)).fetchone()
        assert row["status"] == "failed" and "restarted" in row["message"]


class TestJobLookup:

    def test_a_worker_job_whose_row_is_gone_is_a_final_answer(self, client):
        res = client.get("/jobs/bg_does-not-exist")
        assert res.status_code == 200
        assert res.json()["status"] == "cancelled"
        assert client.get("/jobs/does-not-exist").status_code == 404

    def test_the_queue_row_wins_over_a_stale_in_memory_job(self, client):
        item = _row(status="completed")
        jobs[f"bg_{item}"] = JobStatus(id=f"bg_{item}", status="running", project_name="Snap Show")
        body = client.get(f"/jobs/bg_{item}").json()
        assert body["status"] == "completed" and body["progress"] == 100.0
        TranslationQueue().delete_item(item)
        assert client.get(f"/jobs/bg_{item}").json()["status"] == "completed"   # final states are kept
        live = _row(episode="S01E09", status="running")
        jobs[f"bg_{live}"] = JobStatus(id=f"bg_{live}", status="running", project_name="Snap Show")
        TranslationQueue().delete_item(live)
        assert client.get(f"/jobs/bg_{live}").json()["status"] == "cancelled"   # a live job whose row vanished


class TestEvents:

    def test_queue_changes_are_broadcast_once_per_burst(self, monkeypatch):
        events = []
        monkeypatch.setattr(event_broadcaster, "broadcast_event", lambda kind, data=None: events.append((kind, data)))
        monkeypatch.setattr(event_broadcaster, "_queue_last", 0.0)
        monkeypatch.setattr(event_broadcaster, "_queue_timer", None)
        q = TranslationQueue()
        item = q.enqueue(project_name="Snap Show", episode_name="S01E01", priority=PRIORITY_MANUAL, kind=KIND_TRANSLATION)
        q.update_status(item, status="running")   # first change: at once
        q.cancel(item)                            # inside the gap: coalesced
        q.delete_item(item)                       # inside the gap: coalesced
        assert [k for k, _ in events] == ["queue_updated"]
        deadline = time.time() + 2
        while len(events) < 2 and time.time() < deadline:
            time.sleep(0.05)
        assert [k for k, _ in events] == ["queue_updated", "queue_updated"]
        assert events[-1][1]["reason"] == "delete_item"   # the trailing event carries the last reason

    def test_job_updates_are_broadcast(self, monkeypatch):
        events = []
        monkeypatch.setattr(event_broadcaster, "broadcast_event", lambda kind, data=None: events.append((kind, data)))
        jid = create_job("create_context", "Snap Show")
        update_job(jid, progress=10.0)
        update_job(jid, progress=20.0)                # within a second of the last: dropped
        update_job(jid, status="completed")           # a status change always goes out
        kinds = [(k, d["status"], d["progress"]) for k, d in events if k == "job_updated"]
        assert kinds == [("job_updated", "pending", 10.0), ("job_updated", "completed", 20.0)]
