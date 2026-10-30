import json
import sqlite3
from datetime import datetime
from fastapi import APIRouter, HTTPException
from utils.jobs_manager import jobs, update_job
from utils.translation_queue import DB_FILE, TranslationQueue

router = APIRouter()


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id in jobs:
        if job_id.startswith("bg_"):
            # The queue row is the truth for a worker job: when the two disagree, follow the row.
            try:
                target_id = job_id[3:]
                q_item = TranslationQueue().get_item(target_id)
                job = jobs[job_id]
                if q_item is None and job.status not in ("completed", "failed", "cancelled"):
                    update_job(job_id, status="cancelled", message="Removed from the queue.")
                elif q_item and q_item["status"] in ("completed", "failed", "cancelled") and job.status != q_item["status"]:
                    final = q_item["status"]
                    message = {"completed": "Translation completed.",
                               "failed": f"Failed: {q_item.get('last_error') or 'see the queue'}",
                               "cancelled": "Cancelled."}[final]
                    update_job(job_id, status=final, progress=100.0 if final == "completed" else None, message=message)
            except Exception:
                pass
        return jobs[job_id]
        
    # Database fallback for older or finished tasks (e.g. after server restart)
    db_id = job_id
    row = None
    is_queue_job = False
    try:
        tq = TranslationQueue()
        target_id = db_id[3:] if db_id.startswith("bg_") else db_id
        q_item = tq.get_item(target_id)
        if q_item:
            row = q_item
            is_queue_job = True
        else:
            with tq._conn() as conn:
                cursor = conn.execute("SELECT * FROM job_history WHERE id = ?", (db_id,))
                hist_row = cursor.fetchone()
                if hist_row:
                    row = dict(hist_row)
                    is_queue_job = False
                elif not db_id.startswith("bg_"):
                    direct_item = tq.get_item(db_id)
                    if direct_item:
                        row = direct_item
                        is_queue_job = True
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database lookup failed: {e}")

    if not row:
        if job_id.startswith("bg_"):
            # The row is gone (cleared or deleted): a final answer, so a client stops asking.
            return {"id": job_id, "status": "cancelled", "progress": 0.0, "message": "Removed from the queue.",
                    "logs": [], "result": None, "scene_status": {}, "cancelled": True, "completed_episodes": [],
                    "failed_episodes": [], "rate_limit_hits": 0, "daily_limit_reached": False, "parent_id": None}
        raise HTTPException(status_code=404, detail="Job not found")

    status = row["status"]
    # Map DB status back to frontend job status
    job_status = status
    if status == "paused_daily_limit":
        job_status = "paused"
        
    progress = 0.0
    if status == "completed":
        progress = 100.0
    elif status == "failed":
        progress = 100.0

    logs = []
    if row["logs"]:
        try:
            logs = json.loads(row["logs"])
        except Exception:
            pass

    if is_queue_job:
        message = f"Background task: {row['episode_name']}"
        if status == "completed":
            message = "Translation completed."
        elif status == "failed":
            message = f"Failed: {row['last_error'] or 'Unknown error'}"
        elif status == "paused_daily_limit":
            message = "Paused: Daily API limit reached."
        elif status == "pending":
            message = "Pending in queue..."
    else:
        message = row["message"] or ""
        if status == "completed" and not message:
            message = "Job completed."
        elif status == "failed" and not message:
            message = f"Failed: {message}"
        elif status == "cancelled" and not message:
            message = "Job cancelled."

    parent_id = None
    if is_queue_job:
        try:
            raw_opts = row["options"] if "options" in row.keys() else None
            if raw_opts:
                parsed_opts = json.loads(raw_opts) if isinstance(raw_opts, str) else raw_opts
                parent_id = parsed_opts.get("parent_job_id")
        except Exception:
            pass

    return {
        "id": job_id,
        "status": job_status,
        "progress": progress,
        "message": message,
        "logs": logs,
        "result": None,
        "scene_status": {},
        "cancelled": status == "cancelled",
        "completed_episodes": [row["episode_name"]] if status == "completed" else [],
        "failed_episodes": [row["episode_name"]] if status == "failed" else [],
        "rate_limit_hits": 0,
        "daily_limit_reached": status == "paused_daily_limit",
        "parent_id": parent_id
    }


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    from services.background_worker import worker
    from services.queue_service import cancel_item as _cancel_item

    def cancel_item(item_id: str) -> bool:   # the worker's queue: the one its jobs live in
        return _cancel_item(item_id, queue=getattr(worker, "queue", None))

    db_id = job_id
    if db_id.startswith("bg_"):
        db_id = db_id[3:]
        
    if job_id in jobs:
        job = jobs[job_id]
        job.cancelled = True
        if job.status in ("running", "pending", "paused", "awaiting_review"):
            job.status = "cancelled"
            job.message = "Cancelled by user"
            job.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] Cancelled by user")
            
            if job.id.startswith("bg_"):
                # The queue item too, wherever its work is (worker task, agent runner lane).
                cancel_item(db_id)
            else:
                if job.sub_jobs:
                    for child_id in job.sub_jobs:
                        cancel_item(child_id)
                        child_job = jobs.get(f"bg_{child_id}")
                        if child_job:
                            child_job.cancelled = True
                            child_job.status = "cancelled"
                from utils.jobs_manager import _sync_job_to_db
                _sync_job_to_db(job)
        return {"status": "cancelled"}

    # Database/Queue fallback
    cancel_item(db_id)
    return {"status": "cancelled"}

