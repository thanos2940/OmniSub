"""Blacklist endpoints (Plan 14)."""

from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel

from utils import blacklist

router = APIRouter()


class BlacklistAdd(BaseModel):
    line_index: int
    bad_target: str
    reason: Optional[str] = ""
    source_text: Optional[str] = ""


@router.get("/projects/{project}/blacklist")
async def get_blacklist(project: str):
    return {"items": blacklist.for_project(project)}


@router.post("/projects/{project}/episodes/{episode}/blacklist")
async def add_blacklist(project: str, episode: str, body: BlacklistAdd):
    blacklist.add(project, episode, body.line_index, body.source_text, body.bad_target, body.reason)
    return {"status": "success"}


@router.post("/projects/{project}/episodes/{episode}/blacklist-retry")
async def blacklist_and_retry(project: str, episode: str, body: BlacklistAdd):
    """Record the bad target and force a fresh translation that avoids it."""
    blacklist.add(project, episode, body.line_index, body.source_text, body.bad_target, body.reason)
    from services.queue_service import enqueue_translation, PRIORITY_MANUAL
    item_id = enqueue_translation(project, episode, PRIORITY_MANUAL, {"force": True, "translation_type": "original"})
    return {"status": "success", "job_id": f"bg_{item_id}"}


@router.delete("/projects/{project}/blacklist")
async def clear_blacklist(project: str, episode: Optional[str] = None):
    removed = blacklist.clear(project, episode)
    return {"status": "success", "removed": removed}
