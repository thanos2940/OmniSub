"""On-demand subtitle requests from external front-ends (MorpiceHub).

A narrow surface on purpose: look up what exists, request what is missing, and
poll the result. The routing into extraction/translation lives in
services/on_demand.py; these handlers only translate between HTTP and it.

Protected by the global X-Api-Key middleware like every other /api/ route.
"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from routers.schemas import OnDemandSubtitleRequest
from services import on_demand

router = APIRouter(prefix="/api/on-demand", tags=["on-demand"])
logger = logging.getLogger(__name__)

_SERIES_ALIASES = {"series", "show", "tv", "episode"}


def _to_query(body: OnDemandSubtitleRequest) -> on_demand.MediaQuery:
    media_type = (body.media_type or "").strip().lower()
    if media_type in _SERIES_ALIASES:
        media_type = "series"
    elif media_type != "movie":
        raise HTTPException(status_code=422, detail="media_type must be 'movie' or 'series'.")
    if not any([body.tmdb_id, body.tvdb_id, body.imdb_id, body.radarr_id, body.sonarr_id, body.title]):
        raise HTTPException(status_code=422, detail="Identify the title by an id or a title.")
    return on_demand.MediaQuery(
        media_type=media_type,
        tmdb_id=body.tmdb_id,
        tvdb_id=body.tvdb_id,
        imdb_id=body.imdb_id,
        radarr_id=body.radarr_id,
        sonarr_id=body.sonarr_id,
        title=body.title,
        year=body.year,
        season=body.season,
        episodes=sorted(set(body.episodes or [])),
        language=body.language,
    )


def _error(exc: on_demand.OnDemandError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "code": exc.code, **exc.extra},
    )


@router.get("/status")
async def on_demand_status():
    """What this instance can do — used by clients as a key-checking health probe."""
    return on_demand.capabilities()


@router.post("/subtitles/lookup")
async def lookup_subtitles(body: OnDemandSubtitleRequest):
    """Which subtitles exist for a title and what a request would do. No side effects."""
    try:
        return await on_demand.lookup(_to_query(body))
    except on_demand.OnDemandError as exc:
        return _error(exc)


@router.post("/subtitles")
async def request_subtitles(body: OnDemandSubtitleRequest):
    """Sync the requested episodes and route each into extraction/translation."""
    try:
        return await on_demand.create_request(
            _to_query(body),
            requested_by=body.requested_by,
            client=body.client,
            client_ref=body.client_ref,
        )
    except on_demand.OnDemandError as exc:
        return _error(exc)


@router.get("/subtitles")
async def list_subtitle_requests(limit: int = 50):
    return {"requests": await asyncio.to_thread(on_demand.list_requests, max(1, min(int(limit), 200)))}


@router.get("/subtitles/{request_id}")
async def get_subtitle_request(request_id: str):
    request = await asyncio.to_thread(on_demand.get_request, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Unknown request id.")
    return request


@router.post("/subtitles/{request_id}/cancel")
async def cancel_subtitle_request(request_id: str):
    """Stop what the request queued and has not finished (see ``on_demand.cancel_request``); answers the request
    as it stands afterwards."""
    request = await asyncio.to_thread(on_demand.cancel_request, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Unknown request id.")
    return request
