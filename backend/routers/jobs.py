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
        return jobs[job_id]
        
    # Database fallback for older or finished tasks (e.g. after server restart)
    db_id = job_id
    row = None
    is_queue_job = False
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        conn.row_factory = sqlite3.Row
        with conn:
            # First, check translation_queue if it starts with bg_
            if db_id.startswith("bg_"):
                cursor = conn.execute("SELECT * FROM translation_queue WHERE id = ?", (db_id[3:],))
                row = cursor.fetchone()
                is_queue_job = True
            else:
                # Try finding in job_history
                cursor = conn.execute("SELECT * FROM job_history WHERE id = ?", (db_id,))
                row = cursor.fetchone()
                if not row:
                    # Check translation_queue as direct fallback
                    cursor = conn.execute("SELECT * FROM translation_queue WHERE id = ?", (db_id,))
                    row = cursor.fetchone()
                    if row:
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
        "daily_limit_reached": status == "paused_daily_limit"
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
                # Also cancel the queue item
                queue = TranslationQueue()
                queue.cancel(db_id)
            else:
                from utils.jobs_manager import _sync_job_to_db
                _sync_job_to_db(job)
        return {"status": "cancelled"}

    # Database/Queue fallback
    queue = TranslationQueue()
    queue.cancel(db_id)
    return {"status": "cancelled"}

