"""
System & Updates API Router
===========================
Endpoints for checking version status, detecting updates from GitHub,
initiating updates, and managing server lifecycle.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel

from services.update_service import update_service
from utils import storage

router = APIRouter(prefix="/api/system", tags=["system"])


class ApplyUpdateRequest(BaseModel):
    force: bool = False


@router.get("/updates/status")
async def get_update_status():
    """Get current Git version, remote status, changelog, and update settings."""
    config = storage.load_global_config()
    status = await update_service.check_for_updates(force=False)

    return {
        **status,
        "auto_update_enabled": config.get("auto_update_enabled", True),
        "auto_update_interval_hours": config.get("auto_update_interval_hours", 6),
        "github_webhook_enabled": config.get("github_webhook_enabled", True),
        "has_webhook_secret": bool(config.get("github_webhook_secret")),
    }


@router.post("/updates/check")
async def check_updates_now():
    """Force an immediate check against GitHub remote."""
    status = await update_service.check_for_updates(force=True)
    return status


@router.post("/updates/apply")
async def apply_update_now(payload: Optional[ApplyUpdateRequest] = None):
    """Download latest commits, sync dependencies, and trigger a graceful restart."""
    force = payload.force if payload else False
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


@router.post("/restart")
async def restart_server(background_tasks: BackgroundTasks):
    """Manually restart the OmbiSub server."""
    background_tasks.add_task(update_service.trigger_restart, 1.0)
    return {"status": "restarting", "message": "Server restart initiated."}
