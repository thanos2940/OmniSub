"""
System & Updates API Router
===========================
Endpoints for checking version status, official GitHub Releases,
initiating updates and server lifecycle management.
Backup & migration lives in routers/backup.py.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from services.update_service import update_service
from utils import storage

logger = logging.getLogger("omnisub.routers.system")

router = APIRouter(tags=["system"])


class ApplyUpdateRequest(BaseModel):
    force: bool = False
    target_tag: Optional[str] = None


class AutoUpdateConfigRequest(BaseModel):
    enabled: bool
    channel: Optional[str] = "releases"
    interval_hours: Optional[int] = 6


# ---------------------------------------------------------------------------
# System Version & Updates Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/system/version")
@router.get("/api/system/updates/status")
async def get_version_and_update_status():
    """Get installed release metadata, GitHub release status, and update settings."""
    config = storage.load_global_config()
    status = await update_service.check_for_updates(force=False)
    installed = update_service.get_installed_release()

    latest_rel = status.get("latest_release") or {}
    return {
        **status,
        "tag_name": installed.get("tag_name") or status.get("current_commit"),
        "commit_sha": installed.get("commit_sha") or status.get("current_commit_full"),
        "installed_release": installed,
        "release_notes": latest_rel.get("body") or "",
        "published_at": latest_rel.get("published_at") or "",
        "html_url": latest_rel.get("html_url") or "",
        "auto_update_enabled": config.get("auto_update_enabled", False),
        "auto_update_interval_hours": config.get("auto_update_interval_hours", 6),
        "update_channel": config.get("update_channel", "releases"),
        "github_webhook_enabled": config.get("github_webhook_enabled", True),
        "has_webhook_secret": bool(config.get("github_webhook_secret")),
    }


@router.post("/api/system/check-update")
@router.post("/api/system/updates/check")
async def check_updates_now():
    """Force an immediate check against GitHub Releases / remote."""
    status = await update_service.check_for_updates(force=True)
    installed = update_service.get_installed_release()
    latest_rel = status.get("latest_release") or {}

    return {
        **status,
        "tag_name": installed.get("tag_name") or status.get("current_commit"),
        "installed_release": installed,
        "release_notes": latest_rel.get("body") or "",
        "published_at": latest_rel.get("published_at") or "",
        "html_url": latest_rel.get("html_url") or "",
    }


@router.post("/api/system/update")
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
    if not result.get("success") and result.get("status") in ("local_changes", "local_commits"):
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
        "update_channel": config.get("update_channel", "releases"),
    }


@router.post("/api/system/auto-update")
async def set_auto_update_config(payload: AutoUpdateConfigRequest):
    """Update auto-update settings."""
    storage.save_global_config({
        "auto_update_enabled": payload.enabled,
        "update_channel": payload.channel or "releases",
        "auto_update_interval_hours": payload.interval_hours or 6,
    })
    return {
        "success": True,
        "auto_update_enabled": payload.enabled,
        "update_channel": payload.channel or "releases",
        "auto_update_interval_hours": payload.interval_hours or 6,
    }


@router.post("/api/system/restart")
async def restart_server(background_tasks: BackgroundTasks):
    """Manually restart the OmbiSub server."""
    background_tasks.add_task(update_service.trigger_restart, 1.0)
    return {"status": "restarting", "message": "Server restart initiated."}

