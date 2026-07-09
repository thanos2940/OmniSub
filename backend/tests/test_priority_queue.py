import os
import pytest
import shutil
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from utils.translation_queue import (
    TranslationQueue,
    PRIORITY_WEBHOOK,
    PRIORITY_MANUAL,
    PRIORITY_SYNC,
    PRIORITY_BACKLOG
)
from services.queue_service import enqueue_translation

@pytest.fixture
def temp_db():
    import gc
    import time
    # Setup temporary database
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    yield Path(db_path)
    # Teardown
    gc.collect()  # Force garbage collection to close any SQLite connections
    for _ in range(5):
        try:
            if os.path.exists(db_path):
                os.unlink(db_path)
            break
        except PermissionError:
            time.sleep(0.1)
    else:
        # If still locked, ignore to prevent failing the test on cleanup
        pass

def test_priority_constants():
    assert PRIORITY_WEBHOOK == 0
    assert PRIORITY_MANUAL == 1
    assert PRIORITY_SYNC == 2
    assert PRIORITY_BACKLOG == 3

def test_queue_ordering(temp_db):
    queue = TranslationQueue(temp_db)
    
    # Enqueue in arbitrary order
    id_backlog = queue.enqueue("ShowA", "Ep4", priority=PRIORITY_BACKLOG)
    id_webhook = queue.enqueue("ShowA", "Ep1", priority=PRIORITY_WEBHOOK)
    id_manual = queue.enqueue("ShowA", "Ep2", priority=PRIORITY_MANUAL)
    id_sync = queue.enqueue("ShowA", "Ep3", priority=PRIORITY_SYNC)
    
    # Retrieve pending tasks
    # claim_next should claim according to priority: Webhook (0) > Manual (1) > Sync (2) > Backlog (3)
    claimed_1 = queue.claim_next()
    assert claimed_1["id"] == id_webhook
    assert claimed_1["status"] == "running"
    
    claimed_2 = queue.claim_next()
    assert claimed_2["id"] == id_manual
    assert claimed_2["status"] == "running"
    
    claimed_3 = queue.claim_next()
    assert claimed_3["id"] == id_sync
    assert claimed_3["status"] == "running"
    
    claimed_4 = queue.claim_next()
    assert claimed_4["id"] == id_backlog
    assert claimed_4["status"] == "running"
    
    # Next claim should be None
    assert queue.claim_next() is None

@pytest.mark.asyncio
async def test_atomic_claim_concurrency(temp_db):
    queue = TranslationQueue(temp_db)
    
    # Enqueue multiple items
    item_ids = []
    for i in range(10):
        item_id = queue.enqueue("ShowConcurrency", f"Ep{i}", priority=PRIORITY_MANUAL)
        item_ids.append(item_id)
        
    claimed_items = []
    
    async def worker_task(worker_id):
        # Sleep slightly to stagger workers slightly if needed, or run completely concurrent
        await asyncio.sleep(0.01)
        item = queue.claim_next()
        if item:
            claimed_items.append((worker_id, item["id"]))
            
    # Launch 10 concurrent workers
    tasks = [worker_task(i) for i in range(10)]
    await asyncio.gather(*tasks)
    
    # Verify that:
    # 1. Exactly 10 items were claimed
    assert len(claimed_items) == 10
    
    # 2. Every claimed item is unique (no task double-processed)
    claimed_ids = [item_id for worker, item_id in claimed_items]
    assert len(set(claimed_ids)) == 10
    assert set(claimed_ids) == set(item_ids)

def test_clear_queue(temp_db):
    queue = TranslationQueue(temp_db)
    
    # Enqueue a few items
    id_1 = queue.enqueue("ShowClear", "Ep1", priority=PRIORITY_MANUAL)
    id_2 = queue.enqueue("ShowClear", "Ep2", priority=PRIORITY_MANUAL)
    id_3 = queue.enqueue("ShowClear", "Ep3", priority=PRIORITY_MANUAL)
    
    # Set status
    queue.update_status(id_1, "completed")
    queue.update_status(id_2, "failed")
    queue.update_status(id_3, "pending")
    
    # Clear completed/failed
    cleared = queue.clear()
    assert cleared == 2
    
    # Only pending should remain
    items = queue.get_all()
    assert len(items) == 1
    assert items[0]["id"] == id_3


def test_scan_directory_index(tmp_path):
    from integrations.subtitle_scanner import SubtitleScannerService
    
    # Create mock media files
    video1 = tmp_path / "ShowName.S01E01.mkv"
    video2 = tmp_path / "ShowName.S01E02.mp4"
    video1.touch()
    video2.touch()
    
    # Subtitle files
    sub1_en = tmp_path / "ShowName.S01E01.en.srt"
    sub1_el = tmp_path / "ShowName.S01E01.el.srt"
    sub2_en = tmp_path / "ShowName.S01E02.en.srt"
    sub1_en.touch()
    sub1_el.touch()
    sub2_en.touch()
    
    scanner = SubtitleScannerService(source_lang_code="en", target_lang_code="el")
    
    # Test scan_directory_index
    index = scanner.scan_directory_index(str(tmp_path))
    
    assert "ShowName.S01E01" in index
    assert "ShowName.S01E02" in index
    
    res1 = index["ShowName.S01E01"]
    assert res1.has_source_sub is True
    assert res1.has_target_sub is True
    assert Path(res1.source_sub_path).name == "ShowName.S01E01.en.srt"
    assert Path(res1.target_sub_path).name == "ShowName.S01E01.el.srt"
    
    res2 = index["ShowName.S01E02"]
    assert res2.has_source_sub is True
    assert res2.has_target_sub is False
    assert Path(res2.source_sub_path).name == "ShowName.S01E02.en.srt"
    assert res2.target_sub_path is None
    
    # Test scan_media_file using the index
    res_index = scanner.scan_media_file(str(video1), dir_index=index)
    assert res_index.has_source_sub is True
    assert res_index.has_target_sub is True


def test_arr_library_cached_stats(monkeypatch, tmp_path):
    import utils.storage as storage
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path)
    storage.invalidate_arr_library_cache()
    
    project_name = "TestShow"
    
    # Create the project with initial metadata and custom cached stats
    metadata = {
        "show_name": "Test Show",
        "target_language": "Greek",
        "type": "show",
        "arr_source": True,
        "arr_media_type": "series",
        "arr_total_episodes": 12,
        "arr_translated_episodes": 4,
    }
    storage.create_project(project_name, metadata)
    
    # Verify get_arr_library returns cached stats and doesn't do a manual scan
    library = storage.get_arr_library()
    assert len(library["series"]) == 1
    show = library["series"][0]
    assert show["name"] == project_name
    assert show["episode_count"] == 12
    assert show["translated_count"] == 4
    
    # Now save an episode (which triggers update_project_stats_on_change)
    episode_name = "S01E01"
    storage.save_episode(project_name, episode_name, [{"index": 1, "text": "Hello"}], {
        "arr_has_target": False
    })
    
    # Total episodes becomes max(12, 1) -> 12, translated becomes 0
    library = storage.get_arr_library()
    show = library["series"][0]
    assert show["episode_count"] == 12
    assert show["translated_count"] == 0
    
    # Now save episode metadata marking it as translated
    storage.save_episode_metadata(project_name, episode_name, {
        "arr_has_target": True
    })
    
    # Recalculated stats should update translated to 1
    library = storage.get_arr_library()
    show = library["series"][0]
    assert show["episode_count"] == 12
    assert show["translated_count"] == 1

