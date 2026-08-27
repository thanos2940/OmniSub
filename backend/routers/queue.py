from fastapi import APIRouter
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


@router.get("/api/queue")
async def get_queue_status():
    queue = TranslationQueue()
    from utils import storage
    from utils.concurrency_controller import concurrency_manager
    from utils.jobs_manager import jobs
    global_config = storage.load_global_config()

    items = queue.get_all(limit=100)
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

    return {
        "items": items,
        "summary": queue.get_summary(),
        "rate_limits": per_model_rate_limiter.get_all_stats(),
        "paused": global_config.get("queue_worker_paused", False),
        "concurrency": concurrency_manager.stats(),
        # Embedded-subtitle extractions run on their own lane and are listed
        # separately so the translation queue keeps its existing shape.
        "extractions": queue.get_extractions(limit=50),
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
async def enqueue_all_missing():
    from services.queue_service import enqueue_missing_for_library, PRIORITY_BACKLOG
    enqueued = enqueue_missing_for_library(PRIORITY_BACKLOG)
    total_enqueued = sum(len(ids) for ids in enqueued.values())
    return {"status": "success", "enqueued_count": total_enqueued, "enqueued": enqueued}


@router.delete("/api/queue/clear")
async def clear_queue(status: Optional[str] = None, request: Optional[ClearQueueRequest] = None):
    queue = TranslationQueue()
    target_status = status
    if request and request.status:
        target_status = request.status
    cleared_count = queue.clear(target_status)
    return {"status": "success", "cleared_count": cleared_count}


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
    return {"status": "success"}


@router.post("/api/queue/{item_id}/cancel")
async def cancel_queue_item(item_id: str):
    queue = TranslationQueue()
    queue.cancel(item_id)
    return {"status": "success"}


@router.post("/api/queue/{item_id}/prioritize")
async def prioritize_queue_item(item_id: str):
    queue = TranslationQueue()
    queue.prioritize(item_id)
    return {"status": "success"}


@router.get("/api/queue/{item_id}/logs")
async def get_queue_item_logs(item_id: str):
    queue = TranslationQueue()
    logs = queue.get_logs(item_id)
    return {"logs": logs}
