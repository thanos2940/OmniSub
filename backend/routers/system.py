"""
System & Updates API Router
===========================
Endpoints for checking version status, updates (channel ``main`` = origin/main, or GitHub Releases),
runtime status, log files and server lifecycle management.
Backup & migration lives in routers/backup.py.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from services import system_info
from services.update_service import UPDATE_CHANNELS, update_service
from utils import storage

logger = logging.getLogger("omnisub.routers.system")

router = APIRouter(tags=["system"])


class ApplyUpdateRequest(BaseModel):
    force: bool = False
    target_tag: Optional[str] = None


class AutoUpdateConfigRequest(BaseModel):
    enabled: bool
    channel: Optional[str] = "auto"
    interval_hours: Optional[int] = 6


def _update_payload(status: dict) -> dict:
    """The update status the UI renders: the check result plus the installed version, release notes
    (the commit subjects on the main channel) and the update settings."""
    config = storage.load_global_config()
    installed = update_service.get_installed_release()
    latest_rel = status.get("latest_release") or {}
    commits = status.get("commits") or []
    notes = latest_rel.get("body") or "\n".join(f"- {c.get('message', '')}" for c in commits)
    return {
        **status,
        "tag_name": installed.get("tag_name") or status.get("current_commit"),
        "commit_sha": installed.get("commit_sha") or status.get("current_commit_full"),
        "installed_release": installed,
        "release_notes": notes,
        "published_at": latest_rel.get("published_at") or "",
        "html_url": latest_rel.get("html_url") or "",
        "auto_update_enabled": config.get("auto_update_enabled", False),
        "auto_update_interval_hours": config.get("auto_update_interval_hours", 6),
        "update_channel": config.get("update_channel", "auto"),
        "github_webhook_enabled": config.get("github_webhook_enabled", True),
        "has_webhook_secret": bool(config.get("github_webhook_secret")),
    }


# ---------------------------------------------------------------------------
# System Version & Updates Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/system/version")
@router.get("/api/system/updates/status")
async def get_version_and_update_status():
    """The last update check, served from the cache: this is called on every page load, so it never waits
    for the network (an old or missing result is refreshed in the background, at most every few minutes)."""
    status = await asyncio.to_thread(update_service.cached_status)
    update_service.schedule_refresh()
    return {**_update_payload(status), "stale": update_service.is_stale(status)}


@router.post("/api/system/updates/check")
async def check_updates_now():
    """Check now: `git fetch` on the main channel, GitHub Releases on the releases channel."""
    status = await update_service.check_for_updates(force=True)
    return {**_update_payload(status), "stale": False}


@router.post("/api/system/updates/apply")
async def apply_update_now(payload: Optional[ApplyUpdateRequest] = None):
    """Download latest release tag or commits, sync dependencies, and restart."""
    force = payload.force if payload else False
    target_tag = payload.target_tag if payload else None

    if target_tag is not None:
        result = await update_service.apply_update(force=force, target_tag=target_tag)
    else:
        result = await update_service.apply_update(force=force)
    if not result.get("success") and result.get("status") == "busy":
        raise HTTPException(
            status_code=409,
            detail="Cannot update while a translation job is running. Please wait for completion or force update."
        )
    if not result.get("success") and result.get("status") in ("local_changes", "local_commits", "wrong_branch"):
        raise HTTPException(status_code=409, detail=result.get("message"))
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=result.get("message", "Failed to apply update.")
        )
    return result


@router.get("/api/system/auto-update")
async def get_auto_update_config():
    """Get auto-update settings."""
    config = storage.load_global_config()
    return {
        "auto_update_enabled": config.get("auto_update_enabled", False),
        "auto_update_interval_hours": config.get("auto_update_interval_hours", 6),
        "update_channel": config.get("update_channel", "auto"),
    }


@router.post("/api/system/auto-update")
async def set_auto_update_config(payload: AutoUpdateConfigRequest):
    """Update auto-update settings."""
    channel = (payload.channel or "auto").strip().lower()
    if channel not in UPDATE_CHANNELS:
        raise HTTPException(status_code=422, detail=f"channel must be one of: {', '.join(UPDATE_CHANNELS)}")
    storage.save_global_config({
        "auto_update_enabled": payload.enabled,
        "update_channel": channel,
        "auto_update_interval_hours": payload.interval_hours or 6,
    })
    return {
        "success": True,
        "auto_update_enabled": payload.enabled,
        "update_channel": channel,
        "auto_update_interval_hours": payload.interval_hours or 6,
    }


@router.post("/api/system/restart")
async def restart_server(background_tasks: BackgroundTasks):
    """Manually restart the OmbiSub server (exit code 42 under the supervisor, which starts it again)."""
    background_tasks.add_task(update_service.trigger_restart, 1.0)
    return {"status": "restarting", "message": "Server restart initiated."}


# ---------------------------------------------------------------------------
# Runtime status & logs
# ---------------------------------------------------------------------------

@router.get("/api/system/status")
async def system_status(request: Request):
    """Version, uptime, port, URL base, data dir, supervisor, tools, agent runner and free disk space."""
    app = request.app
    return await asyncio.to_thread(
        system_info.collect_status, getattr(app, "version", ""), getattr(app.state, "url_base", None)
    )


@router.get("/api/system/logs/list")
async def list_log_files():
    return {"logs": system_info.list_logs()}


@router.get("/api/system/logs")
async def get_log(name: str = "ombisub", lines: int = system_info.DEFAULT_LINES):
    """The last ``lines`` (1..5000) of a whitelisted log file."""
    if name not in system_info.LOG_FILES:
        raise HTTPException(status_code=404, detail=f"Unknown log '{name}'. Available: {', '.join(system_info.LOG_FILES)}")
    try:
        return await asyncio.to_thread(system_info.read_log, name, lines)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"The log '{name}' has not been written yet.")
