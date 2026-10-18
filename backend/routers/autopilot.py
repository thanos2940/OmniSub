"""Autopilot API (docs/PLAN_autopilot.md 4.10): status, the item table and its row actions.

Protected by the global X-Api-Key middleware like every other /api/ route. The store is
SQLite (blocking), so handlers run it in a worker thread.
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from services.autopilot.service import get_service
from services.autopilot.store import ALL_STATES

router = APIRouter(prefix="/api/autopilot", tags=["autopilot"])
logger = logging.getLogger(__name__)


async def _action(fn, item_id: str):
    try:
        return await asyncio.to_thread(fn, item_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Autopilot item not found")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/status")
async def autopilot_status():
    return await asyncio.to_thread(get_service().status)


@router.get("/items")
async def autopilot_items(
    state: Optional[str] = Query(None, description="One state or a comma list"),
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    if state:
        unknown = [s for s in (p.strip() for p in state.split(",")) if s and s not in ALL_STATES]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown state(s): {', '.join(unknown)}")
    items, total = await asyncio.to_thread(get_service().list_items, state, q, limit, offset)
    return {"items": items, "total": total}


@router.get("/items/{item_id}")
async def autopilot_item(item_id: str):
    item = await asyncio.to_thread(get_service().store.get, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Autopilot item not found")
    return item


@router.post("/items/{item_id}/retry")
async def autopilot_retry(item_id: str):
    return await _action(get_service().retry_item, item_id)


@router.post("/items/{item_id}/skip")
async def autopilot_skip(item_id: str):
    return await _action(get_service().skip_item, item_id)


@router.post("/items/{item_id}/now")
async def autopilot_now(item_id: str):
    return await _action(get_service().check_now, item_id)


@router.post("/poll")
async def autopilot_poll():
    try:
        return {"success": True, "summary": await get_service().poll_now()}
    except Exception as e:
        logger.error(f"Manual autopilot poll failed: {e}", exc_info=True)
        return {"success": False, "summary": {"error": str(e)}}


@router.post("/reconcile")
async def autopilot_reconcile():
    try:
        return {"success": True, "summary": await get_service().reconcile_now()}
    except Exception as e:
        logger.error(f"Manual autopilot reconcile failed: {e}", exc_info=True)
        return {"success": False, "summary": {"error": str(e)}}
