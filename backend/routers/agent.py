"""
Agent API Router
================
REST endpoints for external AI agents (Claude Code, Antigravity, CLI workers) that
translate in place of the model: list and claim queue items, check and submit work
files, release or cancel claims, and audit projects for untranslated lines.

``scripts/agent_task.py`` talks to these endpoints whenever the server is running,
so every claim and submission happens in the server process and reaches the UI as
a live event. Service calls do file and database I/O, so they run in a thread.
"""

import asyncio
from typing import Optional, Any, Dict

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services import library_audit_job
from services.agent_task_service import agent_task_service

router = APIRouter(prefix="/api/agent", tags=["agent"])


class SubmitTaskRequest(BaseModel):
    file_path: Optional[str] = None
    content: Optional[str] = None
    context_guide: Optional[str] = None
    new_terms: Optional[Any] = None
    episode_summary: Optional[str] = None
    force: bool = False


class CheckTaskRequest(BaseModel):
    file_path: Optional[str] = None
    content: Optional[str] = None


class TargetTaskRequest(BaseModel):
    project_name: str
    episode_name: Optional[str] = None
    lang: Optional[str] = None
    full: bool = False


class EnqueueProjectRequest(BaseModel):
    project_name: str
    episode_names: Optional[Any] = None
    priority: int = 1
    full: bool = False
    include_translated: bool = False


class AuditRequest(BaseModel):
    project_name: str
    requeue: bool = False
    full: bool = False
    misplaced: bool = True
    repair: bool = False


class LibraryAuditRequest(BaseModel):
    repair: bool = False
    requeue: bool = False
    misplaced: bool = True


class RunnerStartRequest(BaseModel):
    engine: Optional[str] = None
    model: Optional[str] = None
    fallback_models: Optional[Any] = None
    lanes: Optional[int] = None
    per_show: Optional[int] = None
    chunk_lines: Optional[int] = None
    fix_rounds: Optional[int] = None
    max_episodes: Optional[int] = None
    watch: Optional[bool] = None


def _result(result: Dict[str, Any], not_found_statuses=("not_found",)) -> Any:
    """Return the service result; failures keep their full body (the CLI shows the
    report) with a status code that matches the failure."""
    if result.get("success"):
        return result
    code = 404 if result.get("status") in not_found_statuses else 400
    return JSONResponse(status_code=code, content=result)


@router.get("/tasks/pending")
async def list_pending_agent_tasks():
    """Pending translation items in claim order."""
    tasks = await asyncio.to_thread(agent_task_service.list_pending_tasks)
    return {"tasks": tasks, "count": len(tasks)}


@router.get("/tasks/active")
async def list_active_agent_tasks():
    """Episodes currently claimed by agents, with work-file progress."""
    tasks = await asyncio.to_thread(agent_task_service.list_active_tasks)
    return {"tasks": tasks, "count": len(tasks)}


@router.post("/tasks/next")
async def claim_next_agent_task():
    """Claim the next pending item and build its workspace."""
    return _result(await asyncio.to_thread(agent_task_service.prepare_task_workspace, None), ("empty",))


@router.post("/tasks/target")
async def prepare_target_agent_task(payload: TargetTaskRequest):
    """Claim a workspace for a specific project/episode (enqueueing it if needed)."""
    return _result(await asyncio.to_thread(
        agent_task_service.prepare_target_workspace,
        payload.project_name, payload.episode_name, payload.lang, payload.full,
    ))


@router.post("/enqueue")
async def enqueue_project_for_agent(payload: EnqueueProjectRequest):
    """Queue episodes of a project for agent translation."""
    result = await asyncio.to_thread(
        agent_task_service.enqueue_target_project,
        payload.project_name, payload.episode_names, payload.priority, payload.full, payload.include_translated,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Failed to enqueue project for agent."))
    return result


@router.post("/audit")
async def audit_project_translations(payload: AuditRequest):
    """Find episodes with untranslated lines (copied English, untranslated signs), misplaced lines
    and broken text; ``repair`` fixes the broken text (mojibake, leaked prompt text)."""
    return _result(await asyncio.to_thread(
        agent_task_service.audit_project, payload.project_name, payload.requeue, payload.full, payload.misplaced,
        payload.repair,
    ))


@router.post("/library-audit")
async def start_library_audit(payload: Optional[LibraryAuditRequest] = None):
    """Audit every project in the background (one run at a time). Returns the run's state; poll
    ``GET /api/agent/library-audit`` for progress and the result. 409 (with the state of the run
    that is going) when one is already running."""
    p = payload or LibraryAuditRequest()
    started, state = library_audit_job.start(repair=p.repair, requeue=p.requeue, misplaced=p.misplaced)
    return state if started else JSONResponse(status_code=409, content=state)


@router.get("/library-audit")
async def library_audit_state():
    """The library audit: running with its progress, or the last finished run (kept across restarts)."""
    return await asyncio.to_thread(library_audit_job.get_state)


@router.post("/tasks/sync")
async def sync_agent_tasks():
    """Release abandoned claims and complete pending items whose episode is already translated."""
    released = await asyncio.to_thread(agent_task_service.housekeeping)
    reconciled = await asyncio.to_thread(agent_task_service.reconcile_queue_with_storage)
    return {
        "success": True,
        "released_claims": released,
        # Kept for older clients; nothing is auto-ingested any more.
        "finished_agent_tasks": [],
        "finished_count": 0,
        "reconciled_queue_items": reconciled,
    }


@router.post("/tasks/cancel-all")
async def cancel_all_agent_tasks(status: Optional[str] = Query("pending")):
    """Cancel every open agent workspace and its queue item."""
    cancelled = await asyncio.to_thread(agent_task_service.cancel_all_agent_tasks, status)
    return {"success": True, "cancelled": cancelled}


@router.post("/tasks/clear")
async def clear_agent_tasks(status: Optional[str] = Query("finished")):
    """Delete workspace folders (default: finished ones)."""
    cleared = await asyncio.to_thread(agent_task_service.clear_agent_tasks, status)
    return {"success": True, "cleared": cleared}


@router.post("/tasks/{queue_id}/prepare")
async def prepare_agent_task(queue_id: str):
    """Claim a specific queue item and build its workspace."""
    return _result(await asyncio.to_thread(agent_task_service.prepare_task_workspace, queue_id), ("empty",))


@router.post("/tasks/{task_id}/check")
async def check_agent_task(task_id: str, payload: Optional[CheckTaskRequest] = None):
    """Validate a work file without ingesting anything."""
    content = (payload.content or payload.file_path) if payload else None
    return _result(await asyncio.to_thread(agent_task_service.check_task, task_id, content))


@router.post("/tasks/{task_id}/submit")
async def submit_agent_task(task_id: str, payload: Optional[SubmitTaskRequest] = None):
    """Validate and ingest an agent's translation, then post-process and export."""
    p = payload or SubmitTaskRequest()
    return _result(await asyncio.to_thread(
        agent_task_service.submit_task_result,
        task_id, p.content or p.file_path, p.context_guide, p.new_terms, p.episode_summary, p.force,
    ))


@router.post("/tasks/{task_id}/release")
async def release_agent_task(task_id: str):
    """Give a claimed episode back to the queue."""
    if not await asyncio.to_thread(agent_task_service.release_agent_task, task_id):
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return {"success": True, "task_id": task_id}


@router.post("/tasks/{task_id}/cancel")
async def cancel_single_agent_task(task_id: str):
    """Cancel a task: close its workspace and cancel the queue item."""
    if not await asyncio.to_thread(agent_task_service.cancel_agent_task, task_id):
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return {"success": True, "task_id": task_id}


# ---------------------------------------------------------------- direct runner

@router.get("/runner")
async def runner_status(since: int = Query(0), quota: bool = Query(False)):
    """The direct runner: status, lanes, pauses, Antigravity quota and the log after ``since``."""
    from services.agent_runner import get_runner
    return await asyncio.to_thread(get_runner().info, since, quota)


@router.post("/runner/start")
async def runner_start(payload: Optional[RunnerStartRequest] = None):
    """Start the runner (or change the settings of a running one)."""
    from services.agent_runner import get_runner
    config = {k: v for k, v in (payload.model_dump() if payload else {}).items() if v is not None}
    try:
        return await asyncio.to_thread(get_runner().start, config)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/runner/stop")
async def runner_stop(now: bool = Query(False)):
    """Stop after the episodes in hand (``now``: cut off the requests in flight)."""
    from services.agent_runner import get_runner
    return await asyncio.to_thread(get_runner().stop, now)


@router.post("/runner/resume-now")
async def runner_resume_now():
    """Clear quota and rate-limit pauses so the lanes try again right away."""
    from services.agent_runner import get_runner
    return await asyncio.to_thread(get_runner().resume_now)
