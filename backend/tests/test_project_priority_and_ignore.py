import os
import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from utils import storage
from utils.storage_modules import project_store
from utils.metadata_index import MetadataIndex, get_metadata_index
from utils.translation_queue import (
    TranslationQueue,
    PRIORITY_HIGH,
    PRIORITY_WEBHOOK,
    PRIORITY_MANUAL,
    PRIORITY_SYNC,
    PRIORITY_BACKLOG,
    PRIORITY_LOW,
    PRIORITY_RUN_NOW,
    KIND_TRANSLATION,
)
from main import app


@pytest.fixture
def temp_project(monkeypatch, tmp_path):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(project_store, "PROJECTS_DIR", projects_dir)
    monkeypatch.setattr("utils.storage_modules.atomic_io.PROJECTS_DIR", projects_dir)
    monkeypatch.setattr("utils.paths.PROJECTS_DIR", projects_dir)

    p_name = "Test_Priority_Show"
    storage.create_project(p_name, {"show_name": p_name})
    return p_name


@pytest.fixture
def temp_queue(tmp_path):
    db_file = tmp_path / "test_queue.db"
    return TranslationQueue(db_path=db_file)


def test_4_tier_persistence_and_sync(temp_project):
    # Default priority is normal and arr_disabled is False
    meta = storage.load_project_metadata(temp_project)
    assert project_store.resolve_project_priority(meta) == "normal"
    assert meta.get("arr_disabled") is False

    # 1. Set translation_priority = "high" -> arr_disabled should be False
    meta["translation_priority"] = "high"
    storage.save_project_metadata(temp_project, meta)
    saved = storage.load_project_metadata(temp_project)
    assert saved["translation_priority"] == "high"
    assert saved["arr_disabled"] is False
    assert project_store.resolve_project_priority(saved) == "high"

    # 2. Set translation_priority = "ignored" -> arr_disabled should automatically become True
    saved["translation_priority"] = "ignored"
    storage.save_project_metadata(temp_project, saved)
    saved2 = storage.load_project_metadata(temp_project)
    assert saved2["translation_priority"] == "ignored"
    assert saved2["arr_disabled"] is True
    assert project_store.resolve_project_priority(saved2) == "ignored"

    # 3. Setting arr_disabled = False when ignored -> restores the tier it had before ("high")
    del saved2["translation_priority"]
    saved2["arr_disabled"] = False
    storage.save_project_metadata(temp_project, saved2)
    saved3 = storage.load_project_metadata(temp_project)
    assert saved3["translation_priority"] == "high"
    assert saved3["arr_disabled"] is False
    assert "priority_before_ignore" not in saved3 and "ignored_by_user" not in saved3

    # 4. Setting arr_disabled = True -> sets translation_priority = "ignored"
    saved3["arr_disabled"] = True
    storage.save_project_metadata(temp_project, saved3)
    saved4 = storage.load_project_metadata(temp_project)
    assert saved4["translation_priority"] == "ignored"
    assert saved4["arr_disabled"] is True

    # Test resolve_project_priority edge cases
    assert project_store.resolve_project_priority(None) == "normal"
    assert project_store.resolve_project_priority({}) == "normal"
    assert project_store.resolve_project_priority({"translation_priority": "low"}) == "low"
    assert project_store.resolve_project_priority({"arr_disabled": True}) == "ignored"
    assert project_store.resolve_project_priority({"translation_priority": "normal", "arr_disabled": True}) == "ignored"


def test_metadata_index_set_project_disabled(monkeypatch, tmp_path):
    test_db = tmp_path / "test_meta.db"
    monkeypatch.setattr("utils.metadata_index.DB_FILE", test_db)
    from utils import metadata_index
    metadata_index._init()

    idx = get_metadata_index()
    # Add an untranslated episode
    metadata_index.upsert(
        project="ShowX",
        episode="S01E01",
        meta={"arr_has_target": False, "translated": False},
        original_exists=True,
        disabled=False,
    )
    untranslated = idx.list_untranslated("ShowX")
    assert ("ShowX", "S01E01") in untranslated

    # Disable project via MetadataIndex
    rows = idx.set_project_disabled("ShowX", True)
    assert rows >= 1
    assert idx.list_untranslated("ShowX") == []

    # Re-enable project
    rows = idx.set_project_disabled("ShowX", False)
    assert rows >= 1
    assert ("ShowX", "S01E01") in idx.list_untranslated("ShowX")


def test_queue_claim_order(temp_queue):
    q = temp_queue

    # Enqueue items in arbitrary order with different priorities
    # low=5, backlog=3, sync=2, manual=1, webhook=0, high=-1, run_now=10
    q.enqueue("Proj", "Low_Ep", priority=PRIORITY_LOW)
    q.enqueue("Proj", "Backlog_Ep", priority=PRIORITY_BACKLOG)
    q.enqueue("Proj", "Sync_Ep", priority=PRIORITY_SYNC)
    q.enqueue("Proj", "Manual_Ep", priority=PRIORITY_MANUAL)
    q.enqueue("Proj", "Webhook_Ep", priority=PRIORITY_WEBHOOK)
    q.enqueue("Proj", "High_Ep", priority=PRIORITY_HIGH)
    q.enqueue("Proj", "RunNow_Ep", priority=PRIORITY_RUN_NOW)

    claimed = []
    for _ in range(7):
        item = q.claim_next()
        assert item is not None
        claimed.append((item["episode_name"], item["priority"]))

    expected_order = [
        ("RunNow_Ep", PRIORITY_RUN_NOW),
        ("High_Ep", PRIORITY_HIGH),
        ("Webhook_Ep", PRIORITY_WEBHOOK),
        ("Manual_Ep", PRIORITY_MANUAL),
        ("Sync_Ep", PRIORITY_SYNC),
        ("Backlog_Ep", PRIORITY_BACKLOG),
        ("Low_Ep", PRIORITY_LOW),
    ]
    assert claimed == expected_order


def test_dynamic_reordering_and_cancellation(temp_queue):
    q = temp_queue

    # Enqueue items for ShowA and ShowB
    id_a1 = q.enqueue("ShowA", "Ep1", priority=PRIORITY_MANUAL)
    id_a2 = q.enqueue("ShowA", "Ep2", priority=PRIORITY_BACKLOG)
    id_a_run = q.enqueue("ShowA", "Ep_Run", priority=PRIORITY_RUN_NOW)
    id_b = q.enqueue("ShowB", "Ep1", priority=PRIORITY_MANUAL)

    # 1. Update ShowA to high priority -> Ep1 and Ep2 become -1, Ep_Run stays 10
    res_high = q.update_project_priority("ShowA", "high")
    assert res_high["updated"] == 2
    assert q.get_item(id_a1)["priority"] == -1
    assert q.get_item(id_a2)["priority"] == -1
    assert q.get_item(id_a_run)["priority"] == 10
    assert q.get_item(id_b)["priority"] == PRIORITY_MANUAL

    # 2. Update ShowA to low priority -> Ep1 and Ep2 become 5
    res_low = q.update_project_priority("ShowA", "low")
    assert res_low["updated"] == 2
    assert q.get_item(id_a1)["priority"] == 5
    assert q.get_item(id_a2)["priority"] == 5

    # 3. Update ShowA to normal priority -> Ep1 and Ep2 reset to 2 (SYNC)
    res_normal = q.update_project_priority("ShowA", "normal")
    assert res_normal["updated"] == 2
    assert q.get_item(id_a1)["priority"] == 2
    assert q.get_item(id_a2)["priority"] == 2

    # 4. Update ShowA to ignored -> all pending items for ShowA cancelled
    res_ignore = q.update_project_priority("ShowA", "ignored")
    assert res_ignore["cancelled"] == 3
    item_a1 = q.get_item(id_a1)
    assert item_a1["status"] == "cancelled"
    assert "Ignored" in (item_a1["last_error"] or "")

    # ShowB is unaffected
    assert q.get_item(id_b)["status"] == "pending"


def test_priority_endpoints(temp_project, monkeypatch, tmp_path):
    # Route queue to temp DB
    test_db = tmp_path / "queue_api.db"
    q = TranslationQueue(db_path=test_db)
    monkeypatch.setattr("utils.translation_queue.DB_FILE", test_db)
    monkeypatch.setattr("services.queue_service.get_queue", lambda *args: q)

    client = TestClient(app)

    # 1. Test PUT priority endpoint
    res = client.put(f"/api/projects/{temp_project}/priority", json={"priority": "high"})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "success"
    assert data["translation_priority"] == "high"
    assert data["arr_disabled"] is False

    # Verify metadata updated
    meta = storage.load_project_metadata(temp_project)
    assert meta["translation_priority"] == "high"

    # 2. Test Invalid priority returns 400
    res_bad = client.put(f"/api/projects/{temp_project}/priority", json={"priority": "ultra"})
    assert res_bad.status_code == 400

    # 3. Test Bulk Priority update
    p2 = "Test_Project_Two"
    storage.create_project(p2, {"show_name": p2})

    res_bulk = client.post("/api/projects/bulk-priority", json={
        "projects": [temp_project, p2],
        "priority": "low"
    })
    assert res_bulk.status_code == 200
    assert res_bulk.json()["updated_projects"] == 2

    meta1 = storage.load_project_metadata(temp_project)
    meta2 = storage.load_project_metadata(p2)
    assert meta1["translation_priority"] == "low"
    assert meta2["translation_priority"] == "low"

    # 4. Test GET project includes translation_priority
    res_get = client.get(f"/api/projects/{temp_project}")
    assert res_get.status_code == 200
    assert res_get.json()["translation_priority"] == "low"


# ---------------------------------------------------------------------------
# Paths that only flip arr_disabled (Sonarr/Radarr sync, on-demand, legacy routes)
# ---------------------------------------------------------------------------

def _set(project, **fields):
    meta = storage.load_project_metadata(project)
    meta.update(fields)
    storage.save_project_metadata(project, meta)
    return storage.load_project_metadata(project)


def test_title_leaving_and_returning_to_the_library_keeps_its_tier(temp_project):
    _set(temp_project, translation_priority="high")

    meta = _set(temp_project, arr_disabled=True)  # sync: the title left Sonarr
    assert meta["translation_priority"] == "ignored" and meta["arr_disabled"] is True
    assert not meta.get("ignored_by_user")

    meta = _set(temp_project, arr_disabled=False)  # ... and came back
    assert meta["translation_priority"] == "high" and meta["arr_disabled"] is False


def test_a_users_ignore_survives_a_returning_title(temp_project):
    from services.on_demand import _reenable_project

    meta = _set(temp_project, translation_priority="ignored")
    assert meta["ignored_by_user"] is True and meta["arr_disabled"] is True

    _reenable_project(temp_project)  # someone asked for the title from MorpiceHub
    meta = storage.load_project_metadata(temp_project)
    assert meta["translation_priority"] == "ignored" and meta["arr_disabled"] is True

    meta = _set(temp_project, context_guide="unrelated edit")
    assert meta["translation_priority"] == "ignored" and meta["ignored_by_user"] is True


def test_legacy_enable_and_disable_routes_follow_the_tier(temp_project):
    _set(temp_project, arr_source="sonarr", translation_priority="low")
    client = TestClient(app)

    assert client.post(f"/integrations/arr/library/{temp_project}/disable").status_code == 200
    meta = storage.load_project_metadata(temp_project)
    assert meta["translation_priority"] == "ignored" and meta["ignored_by_user"] is True

    assert client.post(f"/integrations/arr/library/{temp_project}/enable").status_code == 200
    meta = storage.load_project_metadata(temp_project)
    assert meta["translation_priority"] == "low" and meta["arr_disabled"] is False


def test_ignored_project_takes_explicit_requests_only(temp_project):
    from services.queue_service import enqueue_translation

    _set(temp_project, translation_priority="ignored")
    q = TranslationQueue()
    try:
        assert enqueue_translation(temp_project, "S01E01", PRIORITY_SYNC) == ""
        assert enqueue_translation(temp_project, "S01E02", PRIORITY_WEBHOOK) == ""
        item_id = enqueue_translation(temp_project, "S01E03", PRIORITY_MANUAL)  # the user clicked Translate
        assert item_id and q.get_item(item_id)["status"] == "pending"
    finally:
        with q._conn() as conn:
            conn.execute("DELETE FROM translation_queue WHERE project_name = ?", (temp_project,))
            conn.commit()


def test_requeueing_a_finished_item_takes_the_new_priority(temp_queue):
    q = temp_queue
    item_id = q.enqueue("ShowP", "Ep1", priority=PRIORITY_HIGH)  # queued while the show was "high"
    q.update_status(item_id, status="completed")

    assert q.enqueue("ShowP", "Ep1", priority=PRIORITY_LOW) == item_id  # the show is "low" now
    assert q.get_item(item_id)["priority"] == PRIORITY_LOW

    assert q.enqueue("ShowP", "Ep1", priority=PRIORITY_MANUAL) == item_id  # still pending: the more urgent wins
    assert q.get_item(item_id)["priority"] == PRIORITY_MANUAL
