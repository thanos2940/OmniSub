import json
import sqlite3
from datetime import datetime
from fastapi import APIRouter, HTTPException
from utils.jobs_manager import jobs
from utils.translation_queue import DB_FILE, TranslationQueue

router = APIRouter()


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id in jobs:
        if job_id.startswith("bg_"):
            try:
                target_id = job_id[3:]
                q_item = TranslationQueue().get_item(target_id)
                if q_item and q_item["status"] in ("completed", "failed", "cancelled"):
                    if jobs[job_id].status != q_item["status"]:
                        jobs[job_id].status = q_item["status"]
                        if q_item["status"] == "completed":
                            jobs[job_id].progress = 100.0
                            jobs[job_id].message = "Translation completed."
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
                # Also cancel the queue item and cancel in-flight worker task
                q = None
                try:
                    from services.background_worker import worker
                    q = getattr(worker, "queue", None)
                    worker.cancel_task(db_id)
                except Exception:
                    pass
                if not q:
                    q = TranslationQueue()
                q.cancel(db_id)
            else:
                if job.sub_jobs:
                    q = None
                    try:
                        from services.background_worker import worker
                        q = getattr(worker, "queue", None)
                    except Exception:
                        pass
                    if not q:
                        q = TranslationQueue()
                    for child_id in job.sub_jobs:
                        q.cancel(child_id)
                        try:
                            from services.background_worker import worker
                            worker.cancel_task(child_id)
                        except Exception:
                            pass
                        child_job = jobs.get(f"bg_{child_id}")
                        if child_job:
                            child_job.cancelled = True
                            child_job.status = "cancelled"
                from utils.jobs_manager import _sync_job_to_db
                _sync_job_to_db(job)
        return {"status": "cancelled"}

    # Database/Queue fallback
    q = None
    try:
        from services.background_worker import worker
        q = getattr(worker, "queue", None)
        worker.cancel_task(db_id)
    except Exception:
        pass
    if not q:
        q = TranslationQueue()
    q.cancel(db_id)
    return {"status": "cancelled"}

