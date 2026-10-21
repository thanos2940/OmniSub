import asyncio

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional

from utils.translation_queue import TranslationQueue
from utils.rate_limiter import per_model_rate_limiter

router = APIRouter()

class EnqueueRequest(BaseModel):
    project: str
    episodes: Optional[List[str]] = None
    priority: Optional[int] = None

class ClearQueueRequest(BaseModel):
    status: Optional[str] = None


_last_queue_reconcile_at = 0.0

@router.get("/api/queue")
@router.get("/queue")
async def get_queue_status(reconcile: bool = False, project: Optional[str] = None):
    """Queue items (100 at most) and summary. ``project`` narrows items and extractions to that project
    (case-insensitive) with a much higher cap, so a page about one show sees all its queued work."""
    global _last_queue_reconcile_at
    import time, asyncio
    from services.agent_task_service import agent_task_service
    now = time.monotonic()
    if reconcile or (now - _last_queue_reconcile_at >= 5.0):
        _last_queue_reconcile_at = now
        try:
            await asyncio.to_thread(agent_task_service.housekeeping)
            if reconcile:
                await asyncio.to_thread(agent_task_service.reconcile_queue_with_storage)
        except Exception:
            pass

    queue = TranslationQueue()
    from utils import storage
    from utils.concurrency_controller import concurrency_manager
    from utils.jobs_manager import jobs
    global_config = storage.load_global_config()

    # One project's rows sort active work first, so a wide cap still leaves nothing queued out.
    limit = 1000 if project else 100
    items = queue.get_all(limit=limit, include_all_kinds=True, project=project)
    # Embedded-subtitle extractions run on their own lane and are listed
    # separately so the translation queue keeps its existing shape.
    extractions = queue.get_extractions(limit=limit if project else 50, project=project)
    # Agent-routed items: tag them, and give claimed ones the agent's live progress
    # (measured from its work file, since no in-process job tracks an external agent).
    try:
        active_agent = {t["queue_id"]: t for t in await asyncio.to_thread(agent_task_service.list_active_tasks)}
    except Exception:
        active_agent = {}
    import json as _json
    for it in items:
        opts = it.get("options")
        if isinstance(opts, str):
            try:
                opts = _json.loads(opts)
            except Exception:
                opts = {}
        it["engine"] = (opts or {}).get("engine") or "model"
        agent = active_agent.get(it.get("id"))
        if agent:
            it["agent"] = {k: agent.get(k) for k in ("task_id", "units", "units_done", "last_activity", "workspace_dir")}
            if agent.get("units"):
                it["progress"] = round(100.0 * (agent.get("units_done") or 0) / agent["units"], 1)
    # Enrich running items with live progress from the in-memory job registry
    # (the worker tracks per-scene status under the "bg_<id>" job), so the queue UI
    # can show a real progress bar instead of just "running".
    for it in items:
        if it.get("status") == "running":
            job = jobs.get(f"bg_{it['id']}")
            if job is not None:
                scenes = job.scene_status or {}
                done = sum(1 for v in scenes.values() if v == "completed")
                it["progress"] = round(job.progress, 1)
                it["scenes_done"] = done
                it["scenes_total"] = len(scenes)
                it["cues_translated"] = job.cues_translated
                it["cues_total"] = job.cues_total
                it["eta_seconds"] = job.eta_seconds
        elif it.get("status") == "completed":
            ep_meta = storage.load_episode_metadata(it.get("project_name"), it.get("episode_name")) or {}
            it["auto_repaired_count"] = ep_meta.get("auto_repaired_count", 0)
            if not it.get("review_count"):
                it["review_count"] = ep_meta.get("needs_review_count", 0)

    return {
        "items": items,
        "summary": queue.get_summary(),
        "rate_limits": per_model_rate_limiter.get_all_stats(),
        "paused": global_config.get("queue_worker_paused", False),
        "translation_engine": global_config.get("translation_engine", "agent"),
        "concurrency": concurrency_manager.stats(),
        "extractions": extractions,
    }


@router.post("/api/queue/enqueue")
async def enqueue_episodes(request: EnqueueRequest):
    from services.queue_service import enqueue_translation, enqueue_missing_for_project, PRIORITY_MANUAL
    priority = request.priority if request.priority is not None else PRIORITY_MANUAL
    
    if request.episodes:
        enqueued_ids = []
        for ep_name in request.episodes:
            item_id = enqueue_translation(request.project, ep_name, priority)
            enqueued_ids.append(item_id)
        return {"status": "success", "enqueued_count": len(enqueued_ids), "item_ids": enqueued_ids}
    else:
        enqueued_ids = enqueue_missing_for_project(request.project, priority)
        return {"status": "success", "enqueued_count": len(enqueued_ids), "item_ids": enqueued_ids}


@router.post("/api/queue/enqueue-missing")
async def enqueue_all_missing(background_tasks: BackgroundTasks):
    """Queue everything the library still lacks. Episodes whose subtitle is imported are queued now
    (``enqueued_count``); finding the never-imported ones of every Sonarr/Radarr title (their subtitle is
    inside the video) takes longer and runs as job ``job_id`` (one at a time)."""
    from services import on_demand
    from services.queue_service import enqueue_missing_for_library, PRIORITY_BACKLOG
    enqueued = await asyncio.to_thread(enqueue_missing_for_library, PRIORITY_BACKLOG)
    total_enqueued = sum(len(ids) for ids in enqueued.values())
    already = on_demand.library_job_running()
    job_id = on_demand.start_library_job()
    if not already:
        background_tasks.add_task(on_demand.translate_missing_library, job_id)
    return {"status": "success", "enqueued_count": total_enqueued, "enqueued": enqueued, "job_id": job_id}


class CancelAllRequest(BaseModel):
    status: Optional[str] = "active"


@router.post("/api/queue/cancel-all")
async def cancel_all_queue_items(request: Optional[CancelAllRequest] = None):
    """Cancel all pending, running, or active queue items and terminate running worker jobs."""
    queue = TranslationQueue()
    target_status = (request.status if request and request.status else "active")
    
    # Cancel running background worker jobs
    from services.background_worker import worker
    worker.cancel_all_tasks()

    cancelled_count = queue.cancel_all(target_status)
    return {"status": "success", "cancelled_count": cancelled_count, "target_status": target_status}


@router.delete("/api/queue/clear")
async def clear_queue(status: Optional[str] = None, request: Optional[ClearQueueRequest] = None):
    """Clear queue items by status ('history', 'pending', 'cancelled', 'active', or 'all')."""
    queue = TranslationQueue()
    target_status = status
    if request and request.status:
        target_status = request.status
    if not target_status:
        target_status = "history"

    # If clearing all or active/pending, also abort running worker tasks
    if target_status in ("all", "active", "pending", "*"):
        from services.background_worker import worker
        worker.cancel_all_tasks()

    cleared_count = queue.clear(target_status)
    return {"status": "success", "cleared_count": cleared_count, "target_status": target_status}


@router.delete("/api/queue/{item_id}")
async def delete_queue_item(item_id: str):
    """Permanently delete a single queue item."""
    from services.background_worker import worker
    worker.cancel_task(item_id)
    queue = TranslationQueue()
    deleted = queue.delete_item(item_id)
    return {"status": "success", "deleted": deleted}


@router.post("/api/queue/pause")
async def pause_queue():
    from utils import storage
    storage.save_global_config({"queue_worker_paused": True})
    from services.background_worker import worker
    worker.trigger()
    return {"status": "success", "paused": True}


@router.post("/api/queue/resume")
async def resume_queue():
    from utils import storage
    storage.save_global_config({"queue_worker_paused": False})
    from services.background_worker import worker
    worker.trigger()
    return {"status": "success", "paused": False}


@router.post("/api/queue/check-daily-limit")
async def check_daily_limit():
    """Manually probe daily-exhausted models right now to see if the quota lifted.

    Bypasses the scheduled recheck interval (force=True). Any model whose quota is
    back has its latch cleared and its paused items returned to the queue, and the
    worker is woken to resume them.
    """
    from utils.daily_limit_probe import recheck_daily_limits

    outcome = await recheck_daily_limits(force=True)

    if outcome["cleared"]:
        # Return paused items to pending for the recovered models, then wake the worker.
        queue = TranslationQueue()
        queue.unpause_daily_limit_items(
            lambda m: per_model_rate_limiter.get_limiter(m)._daily_exhausted
        )
        from services.background_worker import worker
        worker.trigger()

    return {
        "status": "success",
        "checked": len(outcome["results"]),
        "cleared": outcome["cleared"],
        "results": outcome["results"],
    }


@router.post("/api/queue/{item_id}/retry")
async def retry_queue_item(item_id: str):
    queue = TranslationQueue()
    queue.retry_now(item_id)
    try:
        from services.background_worker import worker
        worker.trigger()
    except Exception:
        pass
    return {"status": "success"}


@router.post("/api/queue/{item_id}/cancel")
async def cancel_queue_item(item_id: str):
    from services.background_worker import worker
    worker.cancel_task(item_id)
    queue = TranslationQueue()
    queue.cancel(item_id)
    return {"status": "success"}


@router.post("/api/queue/{item_id}/prioritize")
async def prioritize_queue_item(item_id: str):
    queue = TranslationQueue()
    queue.prioritize(item_id)
    return {"status": "success"}


@router.get("/api/queue/{item_id}/logs")
@router.get("/queue/{item_id}/logs")
async def get_queue_item_logs(item_id: str):
    queue = TranslationQueue()
    logs = queue.get_logs(item_id)
    return {"logs": logs}


class ContinueRequest(BaseModel):
    boost_priority: Optional[bool] = True


@router.post("/api/queue/{item_id}/postpone")
@router.post("/queue/{item_id}/postpone")
async def postpone_queue_item(item_id: str):
    from services.queue_service import postpone_item
    success = postpone_item(item_id)
    return {"status": "success" if success else "not_found", "item_id": item_id}


@router.post("/api/queue/{item_id}/continue")
@router.post("/queue/{item_id}/continue")
async def continue_queue_item(item_id: str, request: Optional[ContinueRequest] = None, boost_priority: Optional[bool] = None):
    boost = True
    if request is not None and request.boost_priority is not None:
        boost = request.boost_priority
    elif boost_priority is not None:
        boost = boost_priority
    from services.queue_service import continue_item
    success = continue_item(item_id, boost_priority=boost)
    return {"status": "success" if success else "not_found", "item_id": item_id, "boost_priority": boost}


@router.post("/api/queue/{item_id}/stop")
@router.post("/queue/{item_id}/stop")
async def stop_queue_item(item_id: str):
    from services.queue_service import stop_item
    success = stop_item(item_id)
    return {"status": "success" if success else "not_found", "item_id": item_id}

