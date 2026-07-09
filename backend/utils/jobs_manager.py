import logging
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
from typing import Dict, List, Optional, Any
from pydantic import BaseModel

logger = logging.getLogger(__name__)

DB_FILE = Path(__file__).resolve().parent.parent / "omnisub.db"

class JobStatus(BaseModel):
    id: str
    status: str  # pending, running, completed, failed, paused, awaiting_review, cancelled
    progress: float
    message: str
    logs: List[str]
    result: Optional[Dict[str, Any]] = None
    prompt: Optional[str] = None
    ai_response: Optional[str] = None
    pipeline_stage: Optional[str] = None  # context, glossary, translate
    pipeline_mode: Optional[str] = None   # auto, step
    sub_jobs: Optional[List[str]] = None
    scene_status: Optional[Dict[str, str]] = None  # {scene_id: status}
    cancelled: bool = False
    completed_episodes: List[str] = []
    failed_episodes: List[str] = []
    rate_limit_hits: int = 0
    daily_limit_reached: bool = False
    project_name: Optional[str] = None
    episode_name: Optional[str] = None
    finished_at: Optional[float] = None  # epoch seconds when reaching a terminal state


# In-memory jobs registry
jobs: Dict[str, JobStatus] = {}

_TERMINAL = ("completed", "failed", "cancelled", "partial")
_MAX_LOG_LINES = 2000


def evict_old_jobs(max_age_seconds: int = 1800, max_jobs: int = 500) -> int:
    """Drop terminal jobs from the in-memory registry.

    Terminal jobs are still retrievable via the job_history / translation_queue
    fallback in routers/jobs.py, so eviction is transparent to clients.
    """
    import time
    now = time.time()
    removed = 0
    # 1. Age-based eviction of terminal jobs.
    for jid in [j for j, job in jobs.items()
                if job.status in _TERMINAL and job.finished_at and (now - job.finished_at) > max_age_seconds]:
        jobs.pop(jid, None)
        removed += 1
    # 2. Cap total size — drop oldest terminal jobs first.
    if len(jobs) > max_jobs:
        terminal = sorted(
            [(job.finished_at or 0.0, jid) for jid, job in jobs.items() if job.status in _TERMINAL]
        )
        for _, jid in terminal:
            if len(jobs) <= max_jobs:
                break
            jobs.pop(jid, None)
            removed += 1
    return removed


def _sync_job_to_db(job: JobStatus):
    if not job.project_name:
        return
    # Skip background translations as they are directly enqueued and tracked in translation_queue
    if job.id.startswith("bg_"):
        return

    db_id = job.id
    project_name = job.project_name
    
    ep_name = job.episode_name or ""
    if not ep_name:
        if job.id.startswith("scan_episode"):
            ep_name = "Scan"
        elif job.id.startswith("retranslate_compare"):
            ep_name = "Retranslate"
        elif job.id.startswith("auto_translate"):
            ep_name = "Auto Translate"
        elif job.id.startswith("pipeline"):
            ep_name = "Pipeline"
        elif job.id.startswith("simple_pipeline"):
            ep_name = "Simple Pipeline"
        elif job.id.startswith("generate_summary"):
            ep_name = "Generate Summary"
        elif job.id.startswith("create_glossary"):
            ep_name = "Create Glossary"
        elif job.id.startswith("enhance_glossary"):
            ep_name = "Enhance Glossary"
        elif job.id.startswith("create_context"):
            ep_name = "Create Context"
        elif job.id.startswith("enhance_context"):
            ep_name = "Enhance Context"
        elif job.id.startswith("generate_profiles"):
            ep_name = "Generate Character Profiles"
        elif job.id.startswith("sonarr_sync"):
            ep_name = "Sonarr Sync"
        elif job.id.startswith("radarr_sync"):
            ep_name = "Radarr Sync"
        elif job.id.startswith("arr_sync"):
            ep_name = "Arr Sync"
        else:
            ep_name = "Job"

    if job.id not in ep_name:
        ep_name = f"{ep_name} ({job.id})"

    status = job.status
    if status == "partial":
        status = "completed"
    elif status == "paused":
        status = "paused_daily_limit"

    now_str = datetime.now(timezone.utc).isoformat()
    completed_at = None
    if status in ("completed", "failed", "cancelled"):
        completed_at = now_str

    logs_str = json.dumps(job.logs, ensure_ascii=False)
    job_type = job.id.split("_")[0] if "_" in job.id else "job"
    
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        conn.row_factory = sqlite3.Row
        with conn:
            cursor = conn.execute("SELECT id FROM job_history WHERE id = ?", (db_id,))
            row = cursor.fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE job_history
                    SET status = ?, message = ?, logs = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (status, job.message if status == "failed" else None, logs_str, completed_at, db_id)
                )
            else:
                conn.execute(
                    """
                    INSERT INTO job_history
                    (id, job_type, project_name, episode_name, status, message, logs, created_at, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (db_id, job_type, project_name, ep_name, status, job.message if status == "failed" else None, logs_str, now_str, completed_at)
                )
    except Exception as e:
        logger.error(f"Failed to sync job {job.id} to job_history: {e}", exc_info=True)


def create_job(job_type: str, project_name: Optional[str] = None, episode_name: Optional[str] = None) -> str:
    job_id = f"{job_type}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    jobs[job_id] = JobStatus(
        id=job_id, status="pending", progress=0.0, 
        message="Initializing...", logs=[],
        project_name=project_name, episode_name=episode_name
    )
    _sync_job_to_db(jobs[job_id])
    return job_id


def update_job(
    job_id: str,
    status: str = None,
    progress: float = None,
    message: str = None,
    log: str = None,
    result: Dict = None,
    prompt: str = None,
    ai_response: str = None,
    scene_status: Dict[str, str] = None,
    completed_episodes: list = None,
    failed_episodes: list = None,
    rate_limit_hits: int = None,
    daily_limit_reached: bool = None,
    project_name: str = None,
    episode_name: str = None
):
    if job_id not in jobs:
        return
    job = jobs[job_id]
    if project_name:
        job.project_name = project_name
    if episode_name:
        job.episode_name = episode_name
    if status:
        job.status = status
        if status in _TERMINAL and job.finished_at is None:
            import time
            job.finished_at = time.time()
    if progress is not None:
        job.progress = progress
    if message:
        job.message = message
    if log:
        job.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {log}")
        if len(job.logs) > _MAX_LOG_LINES:
            # Keep a bounded tail so a single chatty job can't balloon memory.
            del job.logs[:len(job.logs) - _MAX_LOG_LINES]
    if result:
        job.result = result
    if prompt:
        job.prompt = prompt
    if ai_response:
        job.ai_response = ai_response
    if scene_status:
        if not job.scene_status:
            job.scene_status = {}
        job.scene_status.update(scene_status)
    if completed_episodes is not None:
        job.completed_episodes = completed_episodes
    if failed_episodes is not None:
        job.failed_episodes = failed_episodes
    if rate_limit_hits is not None:
        job.rate_limit_hits = rate_limit_hits
    if daily_limit_reached is not None:
        job.daily_limit_reached = daily_limit_reached
    
    _sync_job_to_db(job)

