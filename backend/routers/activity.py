"""
GET /api/activity — one snapshot of the work that is live right now.

The UI's "Active Tasks" panel is a view of this, not a ledger of its own: anything not in the snapshot is
not running. It is computed in a thread and cached for a moment so every open tab shares one computation,
and it never reads episode files from disk.
"""
import asyncio
import json
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

router = APIRouter()

CACHE_SECONDS = 1.5
LIVE_JOB_STATUSES = ("running", "paused", "awaiting_review")
PENDING_SHOWN_SECONDS = 600     # a pending job is shown this long; after that eviction closes it
LOG_TAIL = 50

_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "body": None}


def _link(project: Optional[str], episode: Optional[str]) -> Optional[str]:
    from urllib.parse import quote
    if not project:
        return None
    if not episode:
        return f"/project/{quote(project, safe='')}"
    return f"/project/{quote(project, safe='')}/episode/{quote(episode, safe='')}"


def _options(row: Dict[str, Any]) -> Dict[str, Any]:
    opts = row.get("options")
    if isinstance(opts, str):
        try:
            opts = json.loads(opts)
        except Exception:
            opts = {}
    return opts or {}


def _jobs_part(now: float) -> List[Dict[str, Any]]:
    from utils.jobs_manager import jobs
    out = []
    for jid, job in list(jobs.items()):
        if jid.startswith("bg_"):
            continue  # worker jobs are reported through their queue row below
        if job.status in LIVE_JOB_STATUSES or (job.status == "pending" and now - job.created_at < PENDING_SHOWN_SECONDS):
            out.append({
                "id": jid,
                "type": jid.rsplit("_", 1)[0] if jid[-1:].isdigit() else jid,
                "project": job.project_name,
                "episode": job.episode_name,
                "status": job.status,
                "progress": round(float(job.progress or 0.0), 1),
                "message": job.message or "",
                "stage": job.pipeline_stage,
                "eta_seconds": job.eta_seconds,
                "logs": list(job.logs[-LOG_TAIL:]),
                "started_at": job.created_at,
                "parent_id": job.parent_id,
                "link": _link(job.project_name, job.episode_name),
            })
    return out


def _queue_part(runner_info: Dict[str, Any], worker_alive: bool) -> List[Dict[str, Any]]:
    from utils.jobs_manager import jobs
    from utils.translation_queue import TranslationQueue
    from services.agent_task_service import agent_task_service

    q = TranslationQueue()
    with q._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, project_name, episode_name, kind, status, options, started_at FROM translation_queue "
            "WHERE status = 'running' ORDER BY started_at").fetchall()]
    if not rows:
        return []
    try:
        active_agent = {t["queue_id"]: t for t in agent_task_service.list_active_tasks()}
    except Exception:
        active_agent = {}
    lanes = {}
    for lane in runner_info.get("lanes") or []:
        if lane.get("task"):
            lanes[lane["task"]] = lane
    runner_running = bool(runner_info.get("running"))

    out = []
    for row in rows:
        opts = _options(row)
        engine = opts.get("engine") or "model"
        entry = {
            "id": row["id"], "project": row["project_name"], "episode": row["episode_name"],
            "kind": row.get("kind") or "translation", "status": "running", "engine": engine,
            "progress": 0.0, "units_done": None, "units_total": None, "lane": None, "message": "",
            "link": _link(row["project_name"], row["episode_name"]),
        }
        agent = active_agent.get(row["id"])
        job = jobs.get(f"bg_{row['id']}")
        if agent:
            lane = lanes.get(agent.get("task_id"))
            units, done = agent.get("units"), agent.get("units_done")
            entry.update(units_done=done, units_total=units,
                         progress=round(100.0 * (done or 0) / units, 1) if units else 0.0)
            if lane:
                entry.update(lane=lane.get("lane"), message=f"Translating with {lane.get('model') or 'the agent'}")
            elif runner_running:
                entry["message"] = "Claimed by the runner"
            else:
                entry.update(status="orphaned", message="Claimed by the agent runner, which is not running")
        elif job is not None and job.status in ("running", "pending", "paused"):
            entry.update(progress=round(float(job.progress or 0.0), 1), message=job.message or "",
                         units_done=job.cues_translated, units_total=job.cues_total)
            if not worker_alive and row.get("kind", "translation") == "translation":
                entry.update(status="orphaned", message="The worker that ran this is gone")
        else:
            entry.update(status="orphaned", message="Marked running but nothing is working on it")
        out.append(entry)
    return out


def _compute() -> Dict[str, Any]:
    from utils import storage
    from utils.translation_queue import TranslationQueue
    from services.agent_runner import get_runner
    now = time.time()
    config = storage.load_global_config() or {}
    try:
        runner_info = get_runner().info(log_since=10 ** 12)   # no log lines
    except Exception:
        runner_info = {"status": "stopped", "running": False, "paused": False, "message": "", "lanes": []}
    worker_alive = False
    try:
        from services.background_worker import worker
        task = getattr(worker, "running_task", None)
        worker_alive = bool(task is not None and not task.done())
    except Exception:
        pass
    summary = {}
    try:
        summary = TranslationQueue().get_summary()
    except Exception:
        pass
    return {
        "ts": now,
        "jobs": _jobs_part(now),
        "queue": _queue_part(runner_info, worker_alive),
        "runner": {k: runner_info.get(k) for k in ("status", "running", "paused", "message", "lanes", "config",
                                                   "pending", "throughput", "eta_minutes")},
        "worker": {"paused": bool(config.get("queue_worker_paused", False)),
                   "pending": summary.get("pending", 0), "running": summary.get("running", 0)},
        "summary": summary,
        "translation_engine": config.get("translation_engine", "agent"),
    }


def snapshot(force: bool = False) -> Dict[str, Any]:
    """The live-work snapshot, from the cache when it is fresh (callable from threads)."""
    with _lock:
        if not force and _cache["body"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
            return _cache["body"]
    body = _compute()
    with _lock:
        _cache.update(at=time.time(), body=body)
    return body


@router.get("/api/activity")
async def get_activity(force: bool = False):
    return await asyncio.to_thread(snapshot, force)
