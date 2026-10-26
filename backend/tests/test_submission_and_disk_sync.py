"""
Unit and Integration Tests for Real-time Submissions and Disk Sync
==================================================================
Tests:
1. EventBroadcaster: Event buffering, history polling, event dispatch.
2. In-Memory Job Sync: /jobs/{job_id} syncs SQLite completion into memory.
3. Agent Task Submission Event Broadcast: submit_task_result emits agent_submission and queue_updated.
4. DiskSyncService:
   - Subtitle presence detection (.el.ass/.el.srt) marks episode completed, imports cues, marks queue completed.
   - Subtitle removal detection: deleting target subtitle on disk resets episode to untranslated and re-queues item.
   - Project disk sync and library disk sync endpoints (/projects/{name}/sync-disk and /api/sync/disk).
"""

import json
import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from main import app
from utils import storage
from utils.paths import DB_FILE
from utils.translation_queue import TranslationQueue, KIND_TRANSLATION
from utils.event_broadcaster import broadcaster, broadcast_event
from utils.jobs_manager import jobs, JobStatus, update_job
from services.agent_task_service import AgentTaskService
from services.disk_sync_service import DiskSyncService


@pytest.fixture
def client():
    return TestClient(app)


class TestEventBroadcaster:
    """Test WebSocket and polling event broadcast system."""

    def test_broadcast_event_buffering_and_history(self):
        initial_events = broadcaster.get_events(since_id=0)
        start_id = initial_events[-1]["id"] if initial_events else 0

        ev1 = broadcast_event("test_submission", {"project_name": "TestShow", "episode_name": "S01E01"})
        assert ev1["type"] == "test_submission"
        assert ev1["id"] > start_id
        assert ev1["project_name"] == "TestShow"

        new_events = broadcaster.get_events(since_id=start_id)
        assert any(e["type"] == "test_submission" and e["id"] == ev1["id"] for e in new_events)

    def test_events_poll_endpoint(self, client):
        ev = broadcast_event("agent_submission", {"task_id": "task_poll_123", "status": "completed"})
        res = client.get(f"/api/events/poll?since={ev['id'] - 1}")
        assert res.status_code == 200
        data = res.json()
        assert "events" in data
        assert any(e["id"] == ev["id"] for e in data["events"])


class TestJobSyncWithSQLite:
    """Test that /jobs/{job_id} reflects SQLite translation_queue updates."""

    def test_job_syncs_sqlite_completion(self, client, monkeypatch):
        q = TranslationQueue()
        with q._conn() as conn:
            conn.execute("""
                INSERT INTO translation_queue (id, project_name, episode_name, status, kind, priority, created_at)
                VALUES ('sync_test_001', 'TestShow', 'S01E01', 'completed', 'translation', 1, '2026-09-28T00:00:00Z')
                ON CONFLICT(id) DO UPDATE SET status = 'completed'
            """)
            conn.commit()

        # In-memory job was still marked "running"
        bg_id = "bg_sync_test_001"
        jobs[bg_id] = JobStatus(
            id=bg_id,
            status="running",
            progress=50.0,
            message="Translating...",
            project_name="TestShow",
            episode_name="S01E01",
        )

        res = client.get(f"/jobs/{bg_id}")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "completed"
        assert data["progress"] == 100.0
        assert "completed" in data["message"].lower()


class TestDiskSyncService:
    """Test disk sync scanning, target subtitle discovery, cue import, and deletion re-queue."""

    @pytest.fixture(autouse=True)
    def setup_test_project(self, tmp_path, monkeypatch):
        self.tmp_dir = tmp_path
        self.projects_dir = tmp_path / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(storage, "PROJECTS_DIR", self.projects_dir)
        monkeypatch.setattr("utils.storage_modules.atomic_io.PROJECTS_DIR", self.projects_dir)
        monkeypatch.setattr("utils.storage_modules.project_store.PROJECTS_DIR", self.projects_dir)
        monkeypatch.setattr("utils.storage_modules.episode_store.PROJECTS_DIR", self.projects_dir)

        # Setup test project
        self.project_name = "DiskSyncShow"
        storage.create_project(self.project_name, {"show_name": self.project_name, "target_language": "Greek"})

        # Media directory on disk
        self.media_dir = tmp_path / "Media" / "DiskSyncShow" / "Season 1"
        self.media_dir.mkdir(parents=True, exist_ok=True)

        proj_meta = storage.load_project_metadata(self.project_name)
        proj_meta["media_path"] = str(tmp_path / "Media" / "DiskSyncShow")
        storage.save_project_metadata(self.project_name, proj_meta)

        # Initial source episode
        self.ep_name = "S01E01"
        self.cues = [
            {"id": 1, "start": "00:00:01,000", "end": "00:00:04,000", "original": "Hello World", "translated": ""},
            {"id": 2, "start": "00:00:05,000", "end": "00:00:08,000", "original": "How are you?", "translated": ""},
        ]
        storage.save_episode(self.project_name, self.ep_name, self.cues)
        storage.save_episode_metadata(self.project_name, self.ep_name, {
            "arr_media_path": str(self.media_dir / "DiskSyncShow - S01E01.mkv"),
            "arr_sub_path": str(self.media_dir / "DiskSyncShow - S01E01.en.srt"),
            "arr_has_target": False,
            "arr_target_path": None,
            "translated": False,
            "translation_status": "untranslated",
        })

    def test_disk_sync_finds_new_target_sub_and_completes_queue(self):
        # Create an .el.ass file on disk
        el_ass = self.media_dir / "DiskSyncShow - S01E01.el.ass"
        el_ass.write_text("""[Script Info]
Title: DiskSyncShow S01E01
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:04.00,Default,,0,0,0,,Γεια σου Κόσμε
Dialogue: 0,0:00:05.00,0:00:08.00,Default,,0,0,0,,Πώς είσαι;
""", encoding="utf-8")

        # Put item in queue
        q = TranslationQueue()
        with q._conn() as conn:
            conn.execute("""
                INSERT INTO translation_queue (id, project_name, episode_name, status, kind, priority, created_at)
                VALUES ('disk_q_1', 'DiskSyncShow', 'S01E01', 'pending', 'translation', 1, '2026-09-28T00:00:00Z')
                ON CONFLICT(id) DO UPDATE SET status = 'pending'
            """)
            conn.commit()

        service = DiskSyncService()
        res = service.sync_project_disk(self.project_name, re_queue_removed=True, import_cues=True)

        assert res["success"] is True
        assert res["translations_found"] == 1

        # Verify episode metadata updated
        meta = storage.load_episode_metadata(self.project_name, self.ep_name)
        assert meta["arr_has_target"] is True
        assert meta["translated"] is True
        assert meta["translation_status"] == "completed"
        assert meta["arr_target_path"] == str(el_ass.resolve())

        # Verify translated cues imported
        loaded_cues = storage.load_episode_data(self.project_name, self.ep_name)
        assert loaded_cues[0].get("translated") == "Γεια σου Κόσμε"
        assert loaded_cues[1].get("translated") == "Πώς είσαι;"

        # Verify queue marked completed
        item = q.get_item("disk_q_1")
        assert item["status"] == "completed"

    def test_disk_sync_detects_removed_target_sub_and_requeues(self):
        # 1. Start with an existing target file
        el_srt = self.media_dir / "DiskSyncShow - S01E01.el.srt"
        el_srt.write_text("1\n00:00:01,000 --> 00:00:04,000\nΓεια σου\n", encoding="utf-8")

        service = DiskSyncService()
        service.sync_project_disk(self.project_name)

        meta = storage.load_episode_metadata(self.project_name, self.ep_name)
        assert meta["translated"] is True

        # Put queue item as completed
        q = TranslationQueue()
        with q._conn() as conn:
            conn.execute("DELETE FROM translation_queue WHERE project_name = ? AND episode_name = ?", (self.project_name, self.ep_name))
            conn.execute("""
                INSERT INTO translation_queue (id, project_name, episode_name, status, kind, priority, created_at)
                VALUES ('disk_q_rem', 'DiskSyncShow', 'S01E01', 'completed', 'translation', 1, '2026-09-28T00:00:00Z')
            """)
            conn.commit()

        # 2. User removes the file from disk
        el_srt.unlink()

        # 3. Run disk sync
        res = service.sync_project_disk(self.project_name, re_queue_removed=True)
        assert res["success"] is True
        assert res["translations_removed"] == 1
        assert res["queue_requeued"] >= 1

        # Check metadata updated to untranslated
        meta = storage.load_episode_metadata(self.project_name, self.ep_name)
        assert meta["arr_has_target"] is False
        assert meta["translated"] is False
        assert meta["translation_status"] == "untranslated"

        # Check queue item re-queued to pending
        item = q.get_item("disk_q_rem")
        assert item["status"] == "pending"

    def test_a_sync_that_nobody_asked_to_requeue_leaves_the_queue_alone(self):
        el_srt = self.media_dir / "DiskSyncShow - S01E01.el.srt"
        el_srt.write_text("1\n00:00:01,000 --> 00:00:04,000\nΓεια σου\n", encoding="utf-8")
        service = DiskSyncService()
        service.sync_project_disk(self.project_name)
        el_srt.unlink()

        res = service.sync_project_disk(self.project_name)  # the default: what the project sync job runs
        assert res["translations_removed"] == 1 and res["queue_requeued"] == 0
        item = TranslationQueue().find_item(self.project_name, self.ep_name, "", "translation")
        assert item is None or item["status"] != "pending"

    def test_sync_project_disk_api_endpoint(self, client):
        res = client.post(f"/projects/{self.project_name}/sync-disk", json={"re_queue_removed": True})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "scanned_episodes" in data

    def test_sync_all_disk_api_endpoint(self, client):
        res = client.post("/api/sync/disk", params={"re_queue_removed": True})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "total_projects" in data

    def test_disk_sync_language_variants_and_season_structures(self):
        # Create a S01 directory (compact season naming)
        s01_dir = self.tmp_dir / "Media" / "DiskSyncShow" / "S01"
        s01_dir.mkdir(parents=True, exist_ok=True)

        ep2_name = "S01E02"
        storage.save_episode(self.project_name, ep2_name, [
            {"id": 1, "original": "English original line", "translated": ""}
        ])
        storage.save_episode_metadata(self.project_name, ep2_name, {
            "arr_media_path": str(s01_dir / "DiskSyncShow.S01E02.mkv"),
            "translated": False,
            "translation_status": "untranslated",
        })

        # Put file named .greek.ass
        greek_sub = s01_dir / "DiskSyncShow.S01E02.greek.ass"
        greek_sub.write_text("""[Script Info]
Title: DiskSyncShow S01E02
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:04.00,Default,,0,0,0,,Ελληνική γραμμή
""", encoding="utf-8")

        service = DiskSyncService()
        res = service.sync_project_disk(self.project_name)
        assert res["success"] is True
        assert res["translations_found"] >= 1

        meta = storage.load_episode_metadata(self.project_name, ep2_name)
        assert meta["translated"] is True
        assert meta["translation_status"] == "completed"
        assert "greek.ass" in str(meta["arr_target_path"])

    def test_disk_sync_preserves_source_english_cues(self):
        # S01E03: Brand new episode with both .en.srt and .el.ass on disk
        ep3_name = "S01E03"
        storage.create_project(self.project_name, {"target_language": "Greek"})
        
        en_srt = self.media_dir / "DiskSyncShow - S01E03.en.srt"
        en_srt.write_text("1\n00:00:01,000 --> 00:00:04,000\nThis is pure English.\n", encoding="utf-8")

        el_ass = self.media_dir / "DiskSyncShow - S01E03.el.ass"
        el_ass.write_text("""[Script Info]
Title: DiskSyncShow S01E03
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:04.00,Default,,0,0,0,,Αυτό είναι καθαρά Ελληνικά.
""", encoding="utf-8")

        # Episode metadata pointing to media & subs
        storage.save_episode_metadata(self.project_name, ep3_name, {
            "arr_media_path": str(self.media_dir / "DiskSyncShow - S01E03.mkv"),
            "arr_sub_path": str(en_srt),
            "translated": False,
        })

        service = DiskSyncService()
        res = service.sync_project_disk(self.project_name, import_cues=True)
        assert res["success"] is True

        cues = storage.load_episode_data(self.project_name, ep3_name)
        assert len(cues) > 0
        # Original MUST remain English, not overwritten by Greek
        assert "pure English" in cues[0].get("original", "")
        # Translated MUST contain the Greek text
        assert "Ελληνικά" in (cues[0].get("translated") or cues[0].get("translations", {}).get("el", ""))

    def test_disk_sync_auto_adds_new_untracked_episodes(self):
        # Create an untracked video file and subtitle for S01E04
        s01_dir = self.media_dir
        mkv_file = s01_dir / "DiskSyncShow - S01E04.mkv"
        mkv_file.write_text("fake video content", encoding="utf-8")
        srt_file = s01_dir / "DiskSyncShow - S01E04.en.srt"
        srt_file.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello episode four.\n", encoding="utf-8")

        service = DiskSyncService()
        res = service.sync_project_disk(self.project_name, auto_add_new_episodes=True, import_cues=True)
        assert res["success"] is True
        assert res["new_episodes_added"] >= 1

        eps = storage.list_episodes(self.project_name)
        assert "S01E04" in eps

        meta = storage.load_episode_metadata(self.project_name, "S01E04")
        assert meta["has_source_sub"] is True
        assert meta["translated"] is False


class TestWebSocketBroadcasterThreads:
    """Test thread-safe broadcasting to connected WebSockets."""

    def test_broadcast_from_background_thread_to_websocket(self, client):
        import threading
        import time

        with client.websocket_connect("/api/ws") as ws:
            ws.send_text("ping")
            pong = ws.receive_text()
            assert pong == "pong"

            # Broadcast from a completely separate OS thread without an event loop
            def worker_thread():
                time.sleep(0.05)
                broadcast_event("bg_thread_event", {"message": "hello from background thread"})

            t = threading.Thread(target=worker_thread)
            t.start()
            t.join()

            # The WebSocket should receive the message scheduled onto the main event loop
            msg = ws.receive_json()
            assert msg["type"] == "bg_thread_event"
            assert msg["message"] == "hello from background thread"


class TestAgentTaskSubmissionAPI:
    """Test POST /api/agent/tasks/{task_id}/submit API with WebSocket broadcasting."""

    def test_api_submit_broadcasts_agent_submission_event(self, client):
        from services.agent_task_service import agent_task_service
        project_name = "APIShow"
        storage.create_project(project_name, {"show_name": project_name, "target_language": "Greek"})
        storage.save_episode(project_name, "S01E01", [
            {"id": 1, "original": "Hello world", "translated": ""}
        ])

        # Prepare a mock workspace
        ws_info = agent_task_service.prepare_target_workspace(project_name, "S01E01", lang="Greek")
        assert ws_info["success"] is True
        task_id = ws_info["task_id"]

        with client.websocket_connect("/api/ws") as ws:
            ws.send_text("ping")
            assert ws.receive_text() == "pong"

            # Submit via the HTTP endpoint
            sub_res = client.post(
                f"/api/agent/tasks/{task_id}/submit",
                json={
                    "content": "1\n00:00:01,000 --> 00:00:04,000\nΓεια σου κόσμε\n"
                }
            )
            assert sub_res.status_code == 200
            assert sub_res.json()["status"] == "completed"

            # WebSocket client must receive agent_submission event immediately (the queue's own
            # coalesced ``queue_updated`` for the row's status change may come first).
            for _ in range(5):
                ev = ws.receive_json()
                if ev["type"] == "agent_submission":
                    break
            assert ev["type"] == "agent_submission"
            assert ev["task_id"] == task_id
            assert ev["project_name"] == project_name
            assert ev["episode_name"] == "S01E01"


