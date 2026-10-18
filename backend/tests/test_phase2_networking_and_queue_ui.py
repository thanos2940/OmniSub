import builtins
import typing
builtins.Any = typing.Any

import sys
import importlib.util
from pathlib import Path
import pytest

from utils.translation_queue import (
    TranslationQueue,
    QUEUE_STATUS_QUEUED,
    QUEUE_STATUS_POSTPONED,
    QUEUE_STATUS_CANCELLED,
    KIND_TRANSLATION,
    KIND_EXTRACTION,
    KIND_EXPORT_RETRY,
)
from utils import metadata_index
from services.agent_runner import (
    DEFAULT_API_URL,
    normalize_api_url,
    clean_config,
    DEFAULT_CONFIG,
    LIVE_KEYS,
)
from routers.queue import (
    postpone_queue_item,
    continue_queue_item,
    stop_queue_item,
    get_queue_status,
    ContinueRequest,
)


def _load_agent_task_module():
    """Load scripts/agent_task.py dynamically regardless of sys.path."""
    root_dir = Path(__file__).resolve().parent.parent.parent
    script_path = root_dir / "scripts" / "agent_task.py"
    spec = importlib.util.spec_from_file_location("agent_task", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    proj_dir = tmp_path / "projects"
    proj_dir.mkdir(parents=True, exist_ok=True)
    from utils import storage
    monkeypatch.setattr(storage, "PROJECTS_DIR", proj_dir)
    metadata_index._init()
    q = TranslationQueue(db_file)
    return q, tmp_path


def test_agent_task_networking_and_ipv6_normalization():
    """Verify scripts/agent_task.py default URL and IPv6 localhost normalization."""
    agent_task = _load_agent_task_module()

    assert agent_task.DEFAULT_URL == "http://127.0.0.1:8000"

    # localhost -> 127.0.0.1 mapping prevents 2-second Windows ::1 SYN timeout
    assert agent_task.normalize_url("http://localhost:8000") == "http://127.0.0.1:8000"
    assert agent_task.normalize_url("http://localhost:8000/") == "http://127.0.0.1:8000"
    assert agent_task.normalize_url("http://localhost") == "http://127.0.0.1"
    assert agent_task.normalize_url("http://[::1]:8000") == "http://127.0.0.1:8000"
    assert agent_task.normalize_url("http://192.168.1.50:8000") == "http://192.168.1.50:8000"
    assert agent_task.normalize_url("") == "http://127.0.0.1:8000"
    assert agent_task.normalize_url(None) == "http://127.0.0.1:8000"


def test_agent_runner_networking_and_config():
    """Verify backend agent_runner.py default URL, live keys, and config normalization."""
    assert DEFAULT_API_URL == "http://127.0.0.1:8000"
    assert DEFAULT_CONFIG["api_url"] == "http://127.0.0.1:8000"
    assert "api_url" in LIVE_KEYS

    assert normalize_api_url("http://localhost:8000") == "http://127.0.0.1:8000"
    assert normalize_api_url("http://localhost:8000/") == "http://127.0.0.1:8000"
    assert normalize_api_url("http://localhost:9000") == "http://127.0.0.1:9000"
    assert normalize_api_url("http://[::1]:8000") == "http://127.0.0.1:8000"

    # clean_config normalizes empty, whitespace, and localhost URLs
    cleaned1 = clean_config({"api_url": "http://localhost:8000"})
    assert cleaned1["api_url"] == "http://127.0.0.1:8000"

    cleaned2 = clean_config({"api_url": "   "})
    assert cleaned2["api_url"] == "http://127.0.0.1:8000"

    cleaned3 = clean_config({"api_url": "http://192.168.1.200:8000"})
    assert cleaned3["api_url"] == "http://192.168.1.200:8000"


@pytest.mark.asyncio
async def test_queue_endpoints_continue_postpone_stop(test_db):
    """Verify queue API endpoints for postpone, continue (with boost_priority), and stop."""
    q, tmp_path = test_db

    item_id = q.enqueue("ShowX", "Ep01", priority=2, kind=KIND_TRANSLATION)
    assert q.get_item(item_id)["status"] == "pending"

    # 1. Postpone
    res_post = await postpone_queue_item(item_id)
    assert res_post["status"] == "success"
    assert q.get_item(item_id)["status"] == QUEUE_STATUS_POSTPONED

    # 2. Continue with boost_priority=True (should set queued and priority=10)
    res_cont = await continue_queue_item(item_id, ContinueRequest(boost_priority=True))
    assert res_cont["status"] == "success"
    assert res_cont["boost_priority"] is True
    item_boosted = q.get_item(item_id)
    assert item_boosted["status"] == QUEUE_STATUS_QUEUED
    assert item_boosted["priority"] == 10

    # 3. Stop
    res_stop = await stop_queue_item(item_id)
    assert res_stop["status"] == "success"
    assert q.get_item(item_id)["status"] == QUEUE_STATUS_CANCELLED

    # 4. Continue with query parameter boost_priority=False
    res_cont2 = await continue_queue_item(item_id, request=None, boost_priority=False)
    assert res_cont2["status"] == "success"
    assert res_cont2["boost_priority"] is False
    item_unboosted = q.get_item(item_id)
    assert item_unboosted["status"] == QUEUE_STATUS_QUEUED


@pytest.mark.asyncio
async def test_queue_status_ui_data_and_counts(test_db):
    """Verify get_queue_status provides summary counts and work_type badges for UI."""
    q, tmp_path = test_db

    id_trans = q.enqueue("ShowY", "Ep01", priority=2, kind=KIND_TRANSLATION)
    id_ext = q.enqueue("ShowY", "Ep02", priority=1, kind=KIND_EXTRACTION)
    id_exp = q.enqueue("ShowY", "Ep03", priority=1, kind=KIND_EXPORT_RETRY)

    # Postpone one item
    q.postpone_item(id_trans)

    status = await get_queue_status(reconcile=False)
    summary = status["summary"]
    items = {it["id"]: it for it in status["items"]}

    assert summary["postponed_count"] == 1
    assert summary["extraction_count"] == 1
    assert summary["export_retry_count"] == 1

    # Verify work_type on each item for frontend badges
    assert items[id_trans]["work_type"] == KIND_TRANSLATION
    assert items[id_trans]["status"] == QUEUE_STATUS_POSTPONED

    assert items[id_ext]["work_type"] == KIND_EXTRACTION
    assert items[id_ext]["status"] == "pending"

    assert items[id_exp]["work_type"] == KIND_EXPORT_RETRY
    assert items[id_exp]["status"] == "pending"


@pytest.mark.asyncio
async def test_episodes_save_and_delete_lines_async_offload(test_db, monkeypatch):
    """Verify save_episode and delete_episode_lines offload blocking disk/export calls to asyncio.to_thread."""
    import asyncio
    from utils import storage
    from routers.episodes import save_episode, delete_episode_lines
    from routers.schemas import SaveEpisodeRequest, DeleteLinesRequest

    project_name = "OffloadProj"
    episode_name = "S01E01"
    storage.create_project(project_name, {"target_language": "Greek"})

    real_to_thread = asyncio.to_thread
    thread_calls = []

    async def spy_to_thread(func, *args, **kwargs):
        thread_calls.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spy_to_thread)

    # 1. Test save_episode offloads storage.save_episode and auto_export_translated_subtitle
    req = SaveEpisodeRequest(
        data=[
            {"index": 0, "start": "00:00:01,000", "end": "00:00:02,000", "text": "Hello", "translated": "Γεια"},
            {"index": 1, "start": "00:00:03,000", "end": "00:00:04,000", "text": "World", "translated": "Κόσμε"},
        ],
        lang="Greek",
    )
    res = await save_episode(project_name, episode_name, req)
    assert res.status_code == 200

    from services.translation.export_formatter import auto_export_translated_subtitle
    assert storage.save_episode in thread_calls
    assert auto_export_translated_subtitle in thread_calls

    # 2. Test delete_episode_lines offloads storage.save_episode through asyncio.to_thread
    thread_calls.clear()
    del_req = DeleteLinesRequest(indexes=[0])
    del_res = await delete_episode_lines(project_name, episode_name, del_req)
    assert del_res["status"] == "success"
    assert del_res["deleted"] == 1
    assert del_res["line_count"] == 1
    assert storage.save_episode in thread_calls


@pytest.mark.asyncio
async def test_episodes_delete_translation_async_offload(test_db, monkeypatch):
    """Verify delete_episode_translation offloads _delete_episode_translation to asyncio.to_thread."""
    import asyncio
    from utils import storage
    from routers.episodes import delete_episode_translation, _delete_episode_translation

    project_name = "DelProj"
    episode_name = "S01E01"
    storage.create_project(project_name, {"target_language": "Greek"})
    storage.save_episode(project_name, episode_name, [
        {"index": 0, "text": "Hi", "translated": "Γεια"}
    ])

    real_to_thread = asyncio.to_thread
    thread_calls = []

    async def spy_to_thread(func, *args, **kwargs):
        thread_calls.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spy_to_thread)

    res = await delete_episode_translation(project_name, episode_name)
    assert res["status"] == "translation-deleted"
    assert _delete_episode_translation in thread_calls


@pytest.mark.asyncio
async def test_translation_item_worker_auto_export_async_offload(test_db, monkeypatch):
    """Verify translation_item_worker offloads auto_export_translated_subtitle via asyncio.to_thread."""
    import asyncio
    from unittest.mock import MagicMock
    from utils import storage
    from services.worker.translation_item_worker import process_translation_item
    from services.translation.export_formatter import auto_export_translated_subtitle

    q, tmp_path = test_db
    project_name = "WorkerExportProj"
    episode_name = "S01E01"
    storage.create_project(project_name, {"target_language": "Greek"})

    storage.save_original_subtitle(project_name, episode_name, "1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    storage.save_episode(project_name, episode_name, [
        {"id": "1", "original": "Hello", "translated": "Γεια", "translations": {"el": "Γεια"}}
    ], {"arr_target_path": str(tmp_path / "target.srt")})

    # Prevent 0-token skip so saved translation branch is taken
    monkeypatch.setattr("utils.storage.target_subtitle_exists", lambda *args, **kwargs: False)

    target_file = tmp_path / "target.srt"
    target_file.touch()

    mock_export = MagicMock(return_value=str(target_file))
    monkeypatch.setattr("services.translation.export_formatter.auto_export_translated_subtitle", mock_export)

    real_to_thread = asyncio.to_thread
    thread_funcs = []

    async def spy_to_thread(func, *args, **kwargs):
        thread_funcs.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spy_to_thread)

    item = {
        "id": 99,
        "project_name": project_name,
        "episode_name": episode_name,
        "translation_type": "saved",
        "attempts": 0,
        "options": {"translation_type": "saved"},
    }

    await process_translation_item(
        item=item,
        queue=q,
        is_task_cancelled=lambda: False,
        jobs={},
        active_jobs={},
        update_job=lambda *args, **kwargs: None,
        job_id="job_test",
    )

    # auto_export_translated_subtitle must be offloaded to asyncio.to_thread
    assert auto_export_translated_subtitle in thread_funcs or mock_export in thread_funcs

