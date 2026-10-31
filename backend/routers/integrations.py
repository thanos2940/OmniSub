import os
import asyncio
import hmac
import logging
import time
import threading
from datetime import datetime
from typing import List, Dict, Optional, Any, Tuple
from pathlib import Path

from fastapi import APIRouter, HTTPException, BackgroundTasks, Header, Query

from utils import storage
from utils.jobs_manager import create_job, update_job
from utils.language_codes import to_code
from integrations.sonarr import SonarrConfig, test_connection as sonarr_test_connection
from integrations.radarr import RadarrConfig, test_connection as radarr_test_connection
from integrations.media_sync_engine import MediaSyncEngine
from integrations.path_resolver import PathResolver

from routers.schemas import ArrTestRequest, PathTestRequest, PlexTestRequest, ArrSyncRequest

router = APIRouter()
logger = logging.getLogger(__name__)


def _build_arr_engine(
    config: Dict,
    scan_ass: bool = True,
    extract_embedded_ass: Optional[bool] = None,
) -> MediaSyncEngine:
    """Build MediaSyncEngine from global settings with optional per-scan overrides."""
    resolver = PathResolver(config.get("arr_path_mappings", []))
    target_lang = config.get("default_target_language", "Greek")
    target_code = to_code(target_lang)
    source_code = config.get("arr_source_language", "en")

    sonarr_cfg = SonarrConfig(
        base_url=config.get("sonarr_url", "http://localhost:8989"),
        api_key=config.get("sonarr_api_key", ""),
        enabled=config.get("sonarr_enabled", False),
    )
    radarr_cfg = RadarrConfig(
        base_url=config.get("radarr_url", "http://localhost:7878"),
        api_key=config.get("radarr_api_key", ""),
        enabled=config.get("radarr_enabled", False),
    )

    embedded_enabled = (
        extract_embedded_ass
        if extract_embedded_ass is not None
        else config.get("embedded_extraction_enabled", True)
    )

    return MediaSyncEngine(
        sonarr_config=sonarr_cfg,
        radarr_config=radarr_cfg,
        resolver=resolver,
        source_lang_code=source_code,
        target_lang_code=target_code,
        target_language=target_lang,
        embedded_extraction=embedded_enabled,
        embedded_keywords=config.get("embedded_deprioritize_keywords"),
        scan_ass=scan_ass,
    )


@router.get("/integrations/arr/status")
async def arr_status():
    """Return connection status for Sonarr and Radarr."""
    config = storage.load_global_config()
    return {
        "sonarr": {
            "enabled": config.get("sonarr_enabled", False),
            "configured": bool(config.get("sonarr_api_key")),
            "url": config.get("sonarr_url", "http://localhost:8989"),
        },
        "radarr": {
            "enabled": config.get("radarr_enabled", False),
            "configured": bool(config.get("radarr_api_key")),
            "url": config.get("radarr_url", "http://localhost:7878"),
        },
    }


@router.get("/integrations/webhooks")
async def webhook_urls():
    """The Sonarr/Radarr webhook paths with the stored token, for Settings to show and copy.

    Settings masks ``webhook_secret``, so the page cannot build these itself. This route sits under
    ``/integrations`` and so behind the API key whenever login is on. Paths are relative; the page adds
    its own origin and URL base (only the browser knows how the server is reached)."""
    from urllib.parse import quote
    secret = (storage.load_global_config().get("webhook_secret") or "").strip()
    query = f"?token={quote(secret, safe='')}" if secret else ""
    return {
        "configured": bool(secret),
        "sonarr": f"/api/webhook/sonarr{query}",
        "radarr": f"/api/webhook/radarr{query}",
    }


@router.post("/integrations/sonarr/test")
async def sonarr_test(request: Optional[ArrTestRequest] = None):
    """Test Sonarr connection with saved or provided settings."""
    config = storage.load_global_config()
    target_url = (request and (request.url or request.sonarr_url)) or config.get("sonarr_url", "http://localhost:8989")
    target_key = (request and (request.api_key or request.sonarr_api_key)) or config.get("sonarr_api_key", "")
    cfg = SonarrConfig(base_url=target_url, api_key=target_key)
    if not cfg.api_key:
        return {"connected": False, "success": False, "error": "Sonarr API key not configured"}
    res = await sonarr_test_connection(cfg)
    res["success"] = res.get("connected", False)
    return res


@router.post("/integrations/radarr/test")
async def radarr_test(request: Optional[ArrTestRequest] = None):
    """Test Radarr connection with saved or provided settings."""
    config = storage.load_global_config()
    target_url = (request and (request.url or request.radarr_url)) or config.get("radarr_url", "http://localhost:7878")
    target_key = (request and (request.api_key or request.radarr_api_key)) or config.get("radarr_api_key", "")
    cfg = RadarrConfig(base_url=target_url, api_key=target_key)
    if not cfg.api_key:
        return {"connected": False, "success": False, "error": "Radarr API key not configured"}
    res = await radarr_test_connection(cfg)
    res["success"] = res.get("connected", False)
    return res


@router.post("/integrations/plex/test")
async def plex_test(request: Optional[PlexTestRequest] = None):
    """Test the Plex connection with saved or provided settings; lists the server's libraries."""
    from routers.settings import SECRET_SENTINEL
    from services.autopilot.plex import check_connection
    config = storage.load_global_config()
    url = (request and (request.url or request.plex_url)) or config.get("plex_url") or "http://localhost:32400"
    token = (request and (request.token or request.plex_token)) or ""
    if not token or token == SECRET_SENTINEL:
        token = config.get("plex_token") or ""
    res = await asyncio.to_thread(check_connection, url, token)
    res["success"] = res["connected"]
    return res


def _probe_path_sync(resolver: PathResolver, remote_path: str):
    """Synchronous filesystem probing offloaded to worker thread (INT-P1-01)."""
    resolved = resolver.resolve(remote_path)
    exists = resolver.is_reachable(resolved)
    is_file = False
    readable = False
    if exists:
        try:
            p_obj = Path(resolved)
            is_file = p_obj.is_file()
            readable = os.access(resolved, os.R_OK)
        except Exception:
            pass
    return resolved, exists, is_file, readable


@router.post("/integrations/arr/test-path")
async def arr_test_path(request: PathTestRequest):
    """Test path mapping: resolve a remote path and check local accessibility."""
    resolver = PathResolver(request.path_mappings)
    try:
        resolved_path, exists, is_file, readable = await asyncio.wait_for(
            asyncio.to_thread(_probe_path_sync, resolver, request.remote_path),
            timeout=5.0,
        )
        return {
            "remote_path": request.remote_path,
            "resolved_path": resolved_path,
            "exists": exists,
            "is_file": is_file,
            "readable": readable,
            "error": None if exists else f"Path does not exist or is unreachable: {resolved_path}",
        }
    except asyncio.TimeoutError:
        resolved_path = resolver.resolve(request.remote_path)
        return {
            "remote_path": request.remote_path,
            "resolved_path": resolved_path,
            "exists": False,
            "is_file": False,
            "readable": False,
            "error": f"Path probe timed out after 5.0s (SMB share or network host may be offline): {resolved_path}",
        }


@router.post("/integrations/sonarr/sync")
async def sonarr_sync(
    background_tasks: BackgroundTasks,
    request: Optional[ArrSyncRequest] = None,
):
    """Trigger a full Sonarr -> Omnisub sync as a background job."""
    config = storage.load_global_config()
    if not config.get("sonarr_api_key"):
        raise HTTPException(status_code=400, detail="Sonarr API key not configured")
    job_id = create_job("sonarr_sync", project_name="Sonarr Integration")
    background_tasks.add_task(_run_arr_sync, job_id, "sonarr", request)
    return {"job_id": job_id}


@router.post("/integrations/radarr/sync")
async def radarr_sync(
    background_tasks: BackgroundTasks,
    request: Optional[ArrSyncRequest] = None,
):
    """Trigger a full Radarr -> Omnisub sync as a background job."""
    config = storage.load_global_config()
    if not config.get("radarr_api_key"):
        raise HTTPException(status_code=400, detail="Radarr API key not configured")
    job_id = create_job("radarr_sync", project_name="Radarr Integration")
    background_tasks.add_task(_run_arr_sync, job_id, "radarr", request)
    return {"job_id": job_id}


@router.post("/integrations/arr/sync")
async def arr_sync_all(
    background_tasks: BackgroundTasks,
    request: Optional[ArrSyncRequest] = None,
):
    """Trigger a full sync against both Sonarr and Radarr."""
    job_id = create_job("arr_sync", project_name="Arr Integration")
    source = (request and request.source) or "both"
    background_tasks.add_task(_run_arr_sync, job_id, source, request)
    return {"job_id": job_id}


async def _run_arr_sync(job_id: str, source: str = "both", sync_req: Optional[ArrSyncRequest] = None):
    """Background task: run MediaSyncEngine.full_sync()."""
    try:
        scan_ass = sync_req.scan_ass if sync_req is not None else True
        extract_embedded = sync_req.extract_embedded_ass if sync_req is not None else None

        update_job(
            job_id,
            status="running",
            progress=5.0,
            message=f"Starting {source} sync (ASS: {'enabled' if scan_ass else 'skipped'}, Embedded probe: {'enabled' if extract_embedded else 'disabled'})...",
        )
        config = storage.load_global_config()
        engine = _build_arr_engine(config, scan_ass=scan_ass, extract_embedded_ass=extract_embedded)

        # Override enabled flags based on requested source
        if source == "sonarr":
            engine.radarr = None
        elif source == "radarr":
            engine.sonarr = None

        def progress_cb(progress=None, message=None):
            update_job(job_id, progress=progress, message=message, log=message)

        result = await engine.full_sync(progress_callback=progress_cb)

        if result.errors:
            for err in result.errors:
                update_job(job_id, log=f"ERROR: {err}")

        update_job(
            job_id,
            status="completed",
            progress=100.0,
            message=(
                f"Sync complete: {result.new_projects} new projects, "
                f"{result.new_episodes} new episodes, "
                f"{result.updated_episodes} updated, "
                f"{result.disabled_projects} disabled"
            ),
            result=result.to_dict(),
        )
        try:
            import asyncio
            from utils import metadata_index
            await asyncio.to_thread(metadata_index.backfill)
        except Exception:
            pass
    except Exception as e:
        update_job(job_id, status="failed", message=f"Sync failed: {str(e)}", log=str(e))


@router.get("/integrations/arr/library")
async def arr_library():
    """Return the full media library grouped by series/movies/disabled.

    Reads locally-stored project metadata — does NOT contact Sonarr/Radarr. In a thread: a cold cache reads
    every project's metadata.
    """
    return await asyncio.to_thread(storage.get_arr_library)


@router.post("/integrations/arr/library/{project_name}/disable")
async def arr_disable_entry(project_name: str):
    """Manually disable a synced library entry."""
    meta = storage.load_project_metadata(project_name)
    if not meta:
        raise HTTPException(status_code=404, detail="Project not found")
    if not meta.get("arr_source"):
        raise HTTPException(status_code=400, detail="Not an arr-synced project")
    meta["arr_disabled"] = True
    meta["ignored_by_user"] = True
    meta["arr_disabled_at"] = datetime.now().isoformat()
    storage.save_project_metadata(project_name, meta)
    from utils.translation_queue import get_translation_queue
    get_translation_queue().update_project_priority(project_name, "ignored")
    return {"status": "disabled", "project": project_name}


@router.post("/integrations/arr/library/{project_name}/enable")
async def arr_enable_entry(project_name: str):
    """Re-enable a previously disabled library entry."""
    meta = storage.load_project_metadata(project_name)
    if not meta:
        raise HTTPException(status_code=404, detail="Project not found")
    meta["arr_disabled"] = False
    meta.pop("arr_disabled_at", None)
    storage.save_project_metadata(project_name, meta)  # restores the tier it had before
    from utils.translation_queue import get_translation_queue
    get_translation_queue().update_project_priority(project_name, meta["translation_priority"])
    return {"status": "enabled", "project": project_name}


@router.post("/integrations/arr/library/{project_name}/enqueue-missing")
async def enqueue_project_missing(project_name: str):
    """Queue everything a show/movie still lacks, at backlog priority.

    Episodes OmbiSub has never imported are created from their media file and sent to
    extraction and translation (``services.on_demand.translate_missing``); the counts say
    what went where and which episodes have no usable source.
    """
    from services import on_demand
    try:
        return await on_demand.translate_missing(project_name)
    except on_demand.OnDemandError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


def _verify_webhook_token(token: Optional[str], header_token: Optional[str]) -> None:
    """Enforce the configured webhook secret.

    main.py's lifespan generates a secret on first boot, so this should always
    be set on a running instance; if it's ever empty (e.g. manually cleared),
    reject rather than fall open — an unauthenticated webhook is a way to make
    this server enqueue translation work or import media for anyone who finds
    the URL.
    """
    secret = (storage.load_global_config().get("webhook_secret") or "").strip()
    if not secret:
        raise HTTPException(
            status_code=401,
            detail="No webhook secret configured — set one in Settings before using webhooks.",
        )
    supplied = (token or header_token or "").strip()
    if not hmac.compare_digest(supplied.encode("utf-8"), secret.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid or missing webhook token")


class WebhookDeduplicator:
    """In-memory sliding window deduplicator for rapid-fire *arr webhooks (INT-P1-02)."""

    def __init__(self, window_seconds: float = 60.0):
        self.window_seconds = window_seconds
        self._seen: Dict[Tuple[str, str, str], float] = {}
        self._lock = threading.Lock()

    def is_duplicate(self, service: str, entity_id: Any, file_id: Any) -> bool:
        if entity_id is None or file_id is None:
            return False
        key = (service, str(entity_id), str(file_id))
        now = time.monotonic()
        with self._lock:
            # Evict entries older than sliding window
            expired = [k for k, ts in self._seen.items() if now - ts > self.window_seconds]
            for k in expired:
                del self._seen[k]

            if key in self._seen:
                return True
            self._seen[key] = now
            return False

    def clear(self):
        with self._lock:
            self._seen.clear()


_webhook_deduplicator = WebhookDeduplicator(window_seconds=60.0)

_AUTOPILOT_EXTRA_EVENTS = {"sonarr": ("EpisodeFileDelete",), "radarr": ("MovieFileDelete",)}


def _autopilot_enabled() -> bool:
    return bool(storage.load_global_config().get("autopilot_enabled", False))


async def _autopilot_ingest(source: str, payload: Dict) -> Dict:
    """Hand a validated webhook to the autopilot's watcher (it deduplicates by arr file id)."""
    from services.autopilot.service import get_service
    item = await asyncio.to_thread(get_service().watcher.ingest_webhook, source, payload)
    return {"status": "queued", "autopilot": True, "item_id": item["id"] if item else None}


@router.post("/api/webhook/sonarr")
async def sonarr_webhook(
    payload: Dict,
    background_tasks: BackgroundTasks,
    token: Optional[str] = Query(None),
    x_omnisub_token: Optional[str] = Header(None),
):
    """Receive Sonarr events: the Autopilot takes them when it is on, otherwise the library entry is refreshed."""
    _verify_webhook_token(token, x_omnisub_token)

    # INT-P1-06: Synchronous webhook payload validation
    val_err = MediaSyncEngine.validate_sonarr_payload(payload)
    if val_err:
        logger.warning(f"Sonarr webhook validation rejected: {val_err}")
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid Sonarr webhook payload", "details": val_err},
        )

    event_type = payload.get("eventType", "")
    logger.info(f"Sonarr webhook: {event_type}")
    if event_type == "Test":
        return {"status": "success", "message": "Webhook connection verified"}
    autopilot = _autopilot_enabled()
    if event_type not in ("Download", "EpisodeFileRenamed")             and not (autopilot and event_type in _AUTOPILOT_EXTRA_EVENTS["sonarr"]):
        return {"status": "ignored", "eventType": event_type}

    # INT-P1-02: Extract deduplication keys (series_id, episode_file_id)
    series = payload.get("series") or {}
    episode_file = payload.get("episodeFile") or {}
    series_id = series.get("id")
    episode_file_id = episode_file.get("id")
    if not episode_file_id:
        episodes = payload.get("episodes") or []
        if episodes and isinstance(episodes, list) and isinstance(episodes[0], dict):
            episode_file_id = episodes[0].get("episodeFileId") or episodes[0].get("id")

    # Autopilot: only the import event is a duplicate worth dropping here (its store deduplicates
    # by file id anyway); a rename or delete of that file seconds later must still get through.
    if series_id is not None and episode_file_id is not None and not (autopilot and event_type != "Download"):
        if _webhook_deduplicator.is_duplicate("sonarr", series_id, episode_file_id):
            logger.info(
                f"Sonarr webhook deduplicated (sliding window 60s): "
                f"series_id={series_id}, episode_file_id={episode_file_id}, eventType={event_type}"
            )
            return {
                "status": "ignored",
                "reason": "duplicate",
                "message": f"Deduplicated event within 60s window for series {series_id}, episode file {episode_file_id}",
                "eventType": event_type,
            }

    if autopilot:
        return await _autopilot_ingest("sonarr", payload)

    background_tasks.add_task(_process_sonarr_webhook, payload)
    return {"status": "queued"}


@router.post("/api/webhook/radarr")
async def radarr_webhook(
    payload: Dict,
    background_tasks: BackgroundTasks,
    token: Optional[str] = Query(None),
    x_omnisub_token: Optional[str] = Header(None),
):
    """Receive Radarr events: the Autopilot takes them when it is on, otherwise the library entry is refreshed."""
    _verify_webhook_token(token, x_omnisub_token)

    # INT-P1-06: Synchronous webhook payload validation
    val_err = MediaSyncEngine.validate_radarr_payload(payload)
    if val_err:
        logger.warning(f"Radarr webhook validation rejected: {val_err}")
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid Radarr webhook payload", "details": val_err},
        )

    event_type = payload.get("eventType", "")
    logger.info(f"Radarr webhook: {event_type}")
    if event_type == "Test":
        return {"status": "success", "message": "Webhook connection verified"}
    autopilot = _autopilot_enabled()
    if event_type not in ("Download", "MovieFileRenamed")             and not (autopilot and event_type in _AUTOPILOT_EXTRA_EVENTS["radarr"]):
        return {"status": "ignored", "eventType": event_type}

    # INT-P1-02: Extract deduplication keys (movie_id, movie_file_id)
    movie = payload.get("movie") or {}
    movie_file = payload.get("movieFile") or {}
    movie_id = movie.get("id")
    movie_file_id = movie_file.get("id")

    if movie_id is not None and movie_file_id is not None and not (autopilot and event_type != "Download"):
        if _webhook_deduplicator.is_duplicate("radarr", movie_id, movie_file_id):
            logger.info(
                f"Radarr webhook deduplicated (sliding window 60s): "
                f"movie_id={movie_id}, movie_file_id={movie_file_id}, eventType={event_type}"
            )
            return {
                "status": "ignored",
                "reason": "duplicate",
                "message": f"Deduplicated event within 60s window for movie {movie_id}, movie file {movie_file_id}",
                "eventType": event_type,
            }

    if autopilot:
        return await _autopilot_ingest("radarr", payload)

    background_tasks.add_task(_process_radarr_webhook, payload)
    return {"status": "queued"}


async def _process_sonarr_webhook(payload: Dict):
    """Background, Autopilot off: import the downloaded episode into the library. Never translates."""
    try:
        from utils.srt_parser import parse_srt
        engine = _build_arr_engine(storage.load_global_config())
        await engine.process_sonarr_webhook(payload, storage, parse_srt)
    except Exception as e:
        logger.error(f"Sonarr webhook processing failed: {e}", exc_info=True)


async def _process_radarr_webhook(payload: Dict):
    """Background, Autopilot off: import the downloaded movie into the library. Never translates."""
    try:
        from utils.srt_parser import parse_srt
        engine = _build_arr_engine(storage.load_global_config())
        await engine.process_radarr_webhook(payload, storage, parse_srt)
    except Exception as e:
        logger.error(f"Radarr webhook processing failed: {e}", exc_info=True)
