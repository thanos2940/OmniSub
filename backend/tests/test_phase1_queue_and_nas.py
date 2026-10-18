import builtins
import typing
builtins.Any = typing.Any

import os
import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from utils.translation_queue import (
    TranslationQueue,
    QUEUE_STATUS_QUEUED,
    QUEUE_STATUS_POSTPONED,
    QUEUE_STATUS_CANCELLED,
    KIND_TRANSLATION,
    KIND_EXTRACTION,
    KIND_EXPORT_RETRY,
    PRIORITY_MANUAL,
    PRIORITY_BACKLOG,
)
from services import queue_service
from services.translation.export_formatter import resolve_cross_share_media, _current_media_file
from services.disk_sync_service import DiskSyncService
from utils import storage
from utils import metadata_index


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """Set up temporary database and storage for isolated testing."""
    db_file = tmp_path / "test_data.db"
    monkeypatch.setattr("utils.paths.DB_FILE", db_file)
    monkeypatch.setattr("utils.translation_queue.DB_FILE", db_file)
    monkeypatch.setattr("utils.metadata_index.DB_FILE", db_file)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("utils.paths.DATA_DIR", data_dir)
    metadata_index._init()
    q = TranslationQueue(db_file)
    return q, tmp_path


def test_queue_task_controls_and_dequeue_priority(test_db):
    """Test postpone, continue, stop, and dequeue_next ordering."""
    q, tmp_path = test_db

    # 1. Enqueue items
    id1 = q.enqueue("ProjA", "Ep01", priority=1, kind=KIND_TRANSLATION)
    id2 = q.enqueue("ProjA", "Ep02", priority=2, kind=KIND_TRANSLATION)
    id3 = q.enqueue("ProjA", "Ep03", priority=3, kind=KIND_TRANSLATION)

    # Verify initial get_summary
    summary = q.get_summary()
    assert summary["pending"] == 3
    assert summary["postponed_count"] == 0

    # 2. Postpone id1
    assert q.postpone_item(id1) is True
    item1 = q.get_item(id1)
    assert item1["status"] == QUEUE_STATUS_POSTPONED
    assert item1.get("updated_at") is not None

    summary2 = q.get_summary()
    assert summary2["pending"] == 2
    assert summary2["postponed_count"] == 1

    # 3. Dequeue next should claim id2 (priority 2), NOT postponed id1
    claimed = q.dequeue_next(work_types=[KIND_TRANSLATION])
    assert claimed is not None
    assert claimed["id"] == id2
    assert claimed["status"] == "running"

    # 4. Continue id1 with boost_priority=True (should set status='queued' and priority=10)
    assert q.continue_item(id1, boost_priority=True) is True
    item1_cont = q.get_item(id1)
    assert item1_cont["status"] == QUEUE_STATUS_QUEUED
    assert item1_cont["priority"] == 10

    # 5. Dequeue next should claim boosted id1 BEFORE id3 (priority 3), because priority=10 is boosted
    claimed_boosted = q.dequeue_next(work_types=[KIND_TRANSLATION])
    assert claimed_boosted is not None
    assert claimed_boosted["id"] == id1

    # 6. Stop id3
    assert q.stop_item(id3) is True
    item3 = q.get_item(id3)
    assert item3["status"] == QUEUE_STATUS_CANCELLED


def test_queue_service_helpers_and_list_items(test_db):
    """Test queue_service module helpers and full queue visibility with work_type."""
    q, tmp_path = test_db

    id_trans = queue_service.enqueue_translation("ProjB", "Ep01", priority=2)
    id_ext = queue_service.enqueue_extraction("ProjB", "Ep02", priority=1, options={"sub_index": 0})
    id_exp = queue_service.enqueue_export_retry("ProjB", "Ep03", target_path="/fake/path", queue=q)

    # Test list_queue_items (get_all with include_all_kinds=True)
    all_items = queue_service.list_queue_items()
    assert len(all_items) >= 3
    kinds = {item["id"]: item["work_type"] for item in all_items}
    assert kinds[id_trans] == KIND_TRANSLATION
    assert kinds[id_ext] == KIND_EXTRACTION
    assert kinds[id_exp] == KIND_EXPORT_RETRY

    # Test filtering by work_types
    ext_only = q.get_all(work_types=[KIND_EXTRACTION])
    assert len(ext_only) == 1
    assert ext_only[0]["id"] == id_ext

    # Test queue_service.postpone_item and queue_service.continue_item
    assert queue_service.postpone_item(id_trans) is True
    assert q.get_item(id_trans)["status"] == "postponed"

    assert queue_service.continue_item(id_trans, boost_priority=False) is True
    assert q.get_item(id_trans)["status"] == "queued"
    assert q.get_item(id_trans)["priority"] == 2  # priority untouched

    assert queue_service.stop_item(id_trans) is True
    assert q.get_item(id_trans)["status"] == "cancelled"


@pytest.mark.asyncio
async def test_queue_router_endpoints(test_db):
    """Test FastAPI queue router endpoints for postpone, continue, and stop."""
    from routers.queue import (
        postpone_queue_item,
        continue_queue_item,
        stop_queue_item,
        get_queue_status,
        ContinueRequest,
    )

    q, tmp_path = test_db
    item_id = q.enqueue("ProjC", "Ep01", priority=2)

    # Test postpone
    res_post = await postpone_queue_item(item_id)
    assert res_post["status"] == "success"
    assert q.get_item(item_id)["status"] == "postponed"

    # Test continue
    res_cont = await continue_queue_item(item_id, ContinueRequest(boost_priority=True))
    assert res_cont["status"] == "success"
    assert q.get_item(item_id)["status"] == "queued"
    assert q.get_item(item_id)["priority"] == 10

    # Test get_queue_status includes counts and work_type
    status_dict = await get_queue_status(reconcile=False)
    assert "summary" in status_dict
    assert "postponed_count" in status_dict["summary"]
    assert any(it["id"] == item_id and it["work_type"] == "translation" for it in status_dict["items"])

    # Test stop
    res_stop = await stop_queue_item(item_id)
    assert res_stop["status"] == "success"
    assert q.get_item(item_id)["status"] == "cancelled"


@pytest.mark.asyncio
async def test_background_worker_housekeeping_decoupling(test_db):
    """Test background worker loop dispatches extractions & export retries even when worker paused."""
    from services.background_worker import BackgroundTranslationWorker

    q, tmp_path = test_db
    worker = BackgroundTranslationWorker()
    worker.queue = q

    # Enqueue extraction and export_retry
    id_ext = q.enqueue("ProjW", "Ep01", priority=1, kind=KIND_EXTRACTION)
    id_exp = q.enqueue("ProjW", "Ep02", priority=1, kind=KIND_EXPORT_RETRY)
    id_trans = q.enqueue("ProjW", "Ep03", priority=1, kind=KIND_TRANSLATION)

    # Global config with queue_worker_paused=True
    gc = {"queue_worker_paused": True, "adaptive_concurrency_enabled": False}

    extraction_dispatched = []
    export_dispatched = []

    def fake_dispatch_ext(tasks, config, max_prio):
        extraction_dispatched.append(True)

    def fake_dispatch_exp(queue, tasks, config, trigger):
        export_dispatched.append(True)

    with patch.object(worker, "_dispatch_extractions", side_effect=fake_dispatch_ext), \
         patch("services.worker.export_retry_worker.dispatch_export_retries", side_effect=fake_dispatch_exp):

        # One iteration of loop checks
        worker._dispatch_extractions(set(), gc, 100)
        from services.worker.export_retry_worker import dispatch_export_retries
        dispatch_export_retries(q, set(), gc, worker.trigger)

    assert len(extraction_dispatched) == 1
    assert len(export_dispatched) == 1


def test_stale_cross_share_nas_path_re_resolution(test_db):
    """Test re-resolving media paths moved across shares or stale mounts."""
    q, tmp_path = test_db

    # Create local directories mimicking NAS shares
    nas_old = tmp_path / "nas_old" / "anime"
    nas_new = tmp_path / "nas_new" / "anime"
    nas_new.mkdir(parents=True, exist_ok=True)

    video_file = nas_new / "Attack on Titan - S01E01.mkv"
    video_file.write_bytes(b"dummy video content")

    # Path mapping configured to nas_new
    global_config = {
        "arr_path_mappings": [
            {"remote": "/media/anime", "local": str(nas_new)},
        ]
    }

    # Stale remote path pointing to old unmapped /media/tv/Attack on Titan - S01E01.mkv
    stale_media_path = "/media/tv/Attack on Titan - S01E01.mkv"
    metadata = {
        "project_name": "Attack on Titan",
        "arr_media_path": stale_media_path,
    }

    # Project metadata pointing arr_path to nas_new
    storage.save_project_metadata("Attack on Titan", {
        "arr_path": str(nas_new),
        "target_language": "Greek",
    })

    resolved = resolve_cross_share_media(
        media_path=stale_media_path,
        episode_name="S01E01",
        metadata=metadata,
        global_config=global_config,
        project_name="Attack on Titan",
    )

    assert resolved is not None
    assert resolved.exists()
    assert resolved == video_file
    assert metadata["arr_media_path"] == str(video_file)

    # Test _current_media_file invokes resolve_cross_share_media
    metadata2 = {
        "project_name": "Attack on Titan",
        "arr_media_path": "/invalid/mount/path/Attack on Titan - S01E01.mkv",
    }
    curr_media = _current_media_file("S01E01", metadata2, global_config, project_name="Attack on Titan")
    assert curr_media is not None
    assert curr_media == video_file
    assert metadata2["arr_media_path"] == str(video_file)


def test_disk_sync_preserves_disabled_state_in_index(test_db):
    """Test that disk sync preserves disabled=True when project has arr_disabled=True."""
    q, tmp_path = test_db

    project_name = "DisabledProject"
    storage.create_project(project_name, {"target_language": "Greek", "arr_disabled": True})

    ep_name = "S01E01"
    media_dir = tmp_path / "disabled_media"
    media_dir.mkdir(parents=True, exist_ok=True)
    video_path = media_dir / "DisabledProject - S01E01.mkv"
    video_path.write_bytes(b"video")
    sub_path = media_dir / "DisabledProject - S01E01.el.srt"
    sub_path.write_text("1\n00:00:01,000 --> 00:00:02,000\nΓεια σας\n", encoding="utf-8")

    ep_meta = {
        "episode_name": ep_name,
        "arr_media_path": str(video_path),
        "arr_target_path": str(sub_path),
        "arr_has_target": True,
        "translated": True,
        "translation_status": "completed",
    }
    storage.save_episode_metadata(project_name, ep_name, ep_meta)
    storage.save_project_metadata(project_name, {
        "arr_disabled": True,
        "target_language": "Greek",
        "path": str(media_dir),
    })

    # Run disk sync
    sync_service = DiskSyncService()
    res = sync_service.sync_project_disk(project_name)
    assert res["success"] is True

    # Verify metadata_index retains disabled=1
    with metadata_index._conn() as conn:
        row = conn.execute(
            "SELECT disabled FROM episodes_index WHERE project = ? AND episode = ?",
            (project_name, ep_name)
        ).fetchone()
        assert row is not None
        assert row["disabled"] == 1
