import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
import json
import sqlite3
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field

from utils.paths import DB_FILE

logger = logging.getLogger(__name__)

class JobStatus(BaseModel):
    id: str
    status: str = "pending"  # pending, running, completed, failed, paused, awaiting_review, cancelled
    progress: float = 0.0
    message: str = ""
    logs: List[str] = Field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    prompt: Optional[str] = None
    ai_response: Optional[str] = None
    pipeline_stage: Optional[str] = None  # context, glossary, translate
    pipeline_mode: Optional[str] = None   # auto, step, simple
    sub_jobs: Optional[List[str]] = None
    scene_status: Optional[Dict[str, str]] = None  # {scene_id: status}
    cancelled: bool = False
    completed_episodes: List[str] = Field(default_factory=list)
    failed_episodes: List[str] = Field(default_factory=list)
    rate_limit_hits: int = 0
    daily_limit_reached: bool = False
    project_name: Optional[str] = None
    episode_name: Optional[str] = None
    finished_at: Optional[float] = None  # epoch seconds when reaching a terminal state
    # Live telemetry fields
    eta_seconds: Optional[float] = None
    cues_translated: Optional[int] = None
    cues_total: Optional[int] = None
    scenes_done: Optional[int] = None
    scenes_total: Optional[int] = None
    parent_id: Optional[str] = None


# In-memory jobs registry
jobs: Dict[str, JobStatus] = {}

_TERMINAL = ("completed", "failed", "cancelled", "partial")
_MAX_LOG_LINES = 2000

_JOB_DB_DEBOUNCE_SECONDS = 3.0
_last_job_db_sync: Dict[str, float] = {}
_pending_job_db_timers: Dict[str, asyncio.TimerHandle] = {}
_job_sync_lock = threading.Lock()


def evict_old_jobs(max_age_seconds: int = 1800, max_jobs: int = 500) -> int:
    """Drop terminal jobs from the in-memory registry.

    Terminal jobs are still retrievable via the job_history / translation_queue
    fallback in routers/jobs.py, so eviction is transparent to clients.
    """
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


_JOB_HISTORY_DDL = """
    CREATE TABLE IF NOT EXISTS job_history (
        id TEXT PRIMARY KEY,
        job_type TEXT NOT NULL,
        project_name TEXT NOT NULL,
        episode_name TEXT NOT NULL,
        status TEXT NOT NULL,
        message TEXT,
        logs TEXT,
        created_at TEXT NOT NULL,
        completed_at TEXT
    )
"""


def _write_job_snapshot_to_sqlite(
    db_id: str,
    job_type: str,
    project_name: str,
    ep_name: str,
    status: str,
    message: Optional[str],
    logs_str: str,
    now_str: str,
    completed_at: Optional[str]
) -> None:
    conn = None
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=30000")
        except Exception:
            pass
        with conn:
            # TranslationQueue also creates this table, but a job can finish before
            # anything has constructed a queue against this database.
            conn.execute(_JOB_HISTORY_DDL)
            conn.execute(
                """
                INSERT INTO job_history
                (id, job_type, project_name, episode_name, status, message, logs, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = CASE 
                        WHEN job_history.completed_at IS NOT NULL AND excluded.completed_at IS NULL THEN job_history.status 
                        ELSE excluded.status 
                    END,
                    message = CASE 
                        WHEN job_history.completed_at IS NOT NULL AND excluded.completed_at IS NULL THEN job_history.message 
                        ELSE excluded.message 
                    END,
                    logs = CASE 
                        WHEN job_history.completed_at IS NOT NULL AND excluded.completed_at IS NULL THEN job_history.logs 
                        ELSE excluded.logs 
                    END,
                    completed_at = COALESCE(excluded.completed_at, job_history.completed_at)
                """,
                (db_id, job_type, project_name, ep_name, status, message if status == "failed" else None, logs_str, now_str, completed_at)
            )
    except Exception as e:
        logger.error(f"Failed to sync job {db_id} to job_history: {e}", exc_info=True)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _trigger_deferred_job_sync(job_id: str):
    with _job_sync_lock:
        _pending_job_db_timers.pop(job_id, None)
    if job_id in jobs:
        _sync_job_to_db(jobs[job_id], force=True)


def _sync_job_to_db(job: JobStatus, force: bool = False):
    if not job.project_name:
        return
    # Skip background translations as they are directly enqueued and tracked in translation_queue
    if job.id.startswith("bg_"):
        return

    is_terminal = (job.status in _TERMINAL)
    now = time.time()

    with _job_sync_lock:
        last = _last_job_db_sync.get(job.id, 0.0)
        if not force and not is_terminal and (now - last) < _JOB_DB_DEBOUNCE_SECONDS:
            try:
                loop = asyncio.get_running_loop()
                if job.id not in _pending_job_db_timers:
                    delay = max(0.1, _JOB_DB_DEBOUNCE_SECONDS - (now - last))
                    _pending_job_db_timers[job.id] = loop.call_later(
                        delay, _trigger_deferred_job_sync, job.id
                    )
            except RuntimeError:
                pass
            return

        timer = _pending_job_db_timers.pop(job.id, None)
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass

        _last_job_db_sync[job.id] = now

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
        completed_at = now_str if is_terminal else None
        logs_str = json.dumps(job.logs, ensure_ascii=False)
        job_type = job.id.split("_")[0] if "_" in job.id else "job"
        msg = job.message

    snapshot = (db_id, job_type, project_name, ep_name, status, msg, logs_str, now_str, completed_at)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_write_job_snapshot_off_loop(snapshot))
    except RuntimeError:
        _write_job_snapshot_to_sqlite(*snapshot)


async def _write_job_snapshot_off_loop(snapshot: tuple) -> None:
    try:
        await asyncio.to_thread(_write_job_snapshot_to_sqlite, *snapshot)
    except RuntimeError:
        # The default executor is already shut down (the app is stopping). Write
        # inline: this is usually a job's final state, the one worth keeping.
        _write_job_snapshot_to_sqlite(*snapshot)


def create_job(job_type: str, project_name: Optional[str] = None, episode_name: Optional[str] = None) -> str:
    job_id = f"{job_type}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    jobs[job_id] = JobStatus(
        id=job_id, status="pending", progress=0.0, 
        message="Initializing...", logs=[],
        project_name=project_name, episode_name=episode_name
    )
    _sync_job_to_db(jobs[job_id], force=True)
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
    episode_name: str = None,
    pipeline_stage: str = None,
    pipeline_mode: str = None,
    sub_jobs: List[str] = None,
    cancelled: bool = None,
    eta_seconds: float = None,
    cues_translated: int = None,
    cues_total: int = None,
    scenes_done: int = None,
    scenes_total: int = None,
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
    if pipeline_stage is not None:
        job.pipeline_stage = pipeline_stage
    if pipeline_mode is not None:
        job.pipeline_mode = pipeline_mode
    if sub_jobs is not None:
        job.sub_jobs = sub_jobs
    if cancelled is not None:
        job.cancelled = cancelled
    if eta_seconds is not None:
        job.eta_seconds = eta_seconds
    if cues_translated is not None:
        job.cues_translated = cues_translated
    if cues_total is not None:
        job.cues_total = cues_total
    if scenes_done is not None:
        job.scenes_done = scenes_done
    if scenes_total is not None:
        job.scenes_total = scenes_total
    
    _sync_job_to_db(job)


