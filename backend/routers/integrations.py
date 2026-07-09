import os
import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Optional, Any
from pathlib import Path

from fastapi import APIRouter, HTTPException, BackgroundTasks, Header, Query

from utils import storage
from utils.jobs_manager import create_job, update_job
from utils.language_codes import to_code
from integrations.sonarr import SonarrConfig, test_connection as sonarr_test_connection
from integrations.radarr import RadarrConfig, test_connection as radarr_test_connection
from integrations.media_sync_engine import MediaSyncEngine
from integrations.path_resolver import PathResolver

from routers.schemas import ArrTestRequest, PathTestRequest

router = APIRouter()
logger = logging.getLogger(__name__)


def _build_arr_engine(config: Dict) -> MediaSyncEngine:
    """Build MediaSyncEngine from global settings."""
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
    return MediaSyncEngine(
        sonarr_config=sonarr_cfg,
        radarr_config=radarr_cfg,
        resolver=resolver,
        source_lang_code=source_code,
        target_lang_code=target_code,
        target_language=target_lang,
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


@router.post("/integrations/sonarr/test")
async def sonarr_test(request: Optional[ArrTestRequest] = None):
    """Test Sonarr connection with saved or provided settings."""
    config = storage.load_global_config()
    cfg = SonarrConfig(
        base_url=(request and request.url) or config.get("sonarr_url", "http://localhost:8989"),
        api_key=(request and request.api_key) or config.get("sonarr_api_key", ""),
    )
    if not cfg.api_key:
        return {"connected": False, "error": "Sonarr API key not configured"}
    return await sonarr_test_connection(cfg)


@router.post("/integrations/radarr/test")
async def radarr_test(request: Optional[ArrTestRequest] = None):
    """Test Radarr connection with saved or provided settings."""
    config = storage.load_global_config()
    cfg = RadarrConfig(
        base_url=(request and request.url) or config.get("radarr_url", "http://localhost:7878"),
        api_key=(request and request.api_key) or config.get("radarr_api_key", ""),
    )
    if not cfg.api_key:
        return {"connected": False, "error": "Radarr API key not configured"}
    return await radarr_test_connection(cfg)


@router.post("/integrations/arr/test-path")
async def arr_test_path(request: PathTestRequest):
    """Test path mapping: resolve a remote path and check local accessibility."""
    resolver = PathResolver(request.path_mappings)
    resolved_path = resolver.resolve(request.remote_path)
    exists = resolver.is_reachable(resolved_path)
    is_file = False
    readable = False
    if exists:
        try:
            p_obj = Path(resolved_path)
            is_file = p_obj.is_file()
            readable = os.access(resolved_path, os.R_OK)
        except Exception:
            pass
    return {
        "remote_path": request.remote_path,
        "resolved_path": resolved_path,
        "exists": exists,
        "is_file": is_file,
        "readable": readable,
        "error": None if exists else f"Path does not exist or is unreachable: {resolved_path}",
    }


@router.post("/integrations/sonarr/sync")
async def sonarr_sync(background_tasks: BackgroundTasks):
    """Trigger a full Sonarr -> Omnisub sync as a background job."""
    config = storage.load_global_config()
    if not config.get("sonarr_api_key"):
        raise HTTPException(status_code=400, detail="Sonarr API key not configured")
    job_id = create_job("sonarr_sync", project_name="Sonarr Integration")
    background_tasks.add_task(_run_arr_sync, job_id, "sonarr")
    return {"job_id": job_id}


@router.post("/integrations/radarr/sync")
async def radarr_sync(background_tasks: BackgroundTasks):
    """Trigger a full Radarr -> Omnisub sync as a background job."""
    config = storage.load_global_config()
    if not config.get("radarr_api_key"):
        raise HTTPException(status_code=400, detail="Radarr API key not configured")
    job_id = create_job("radarr_sync", project_name="Radarr Integration")
    background_tasks.add_task(_run_arr_sync, job_id, "radarr")
    return {"job_id": job_id}


@router.post("/integrations/arr/sync")
async def arr_sync_all(background_tasks: BackgroundTasks):
    """Trigger a full sync against both Sonarr and Radarr."""
    job_id = create_job("arr_sync", project_name="Arr Integration")
    background_tasks.add_task(_run_arr_sync, job_id, "both")
    return {"job_id": job_id}


async def _run_arr_sync(job_id: str, source: str = "both"):
    """Background task: run MediaSyncEngine.full_sync()."""
    try:
        update_job(job_id, status="running", progress=5.0, message=f"Starting {source} sync...")
        config = storage.load_global_config()
        engine = _build_arr_engine(config)

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

    Reads locally-stored project metadata — does NOT contact Sonarr/Radarr.
    """
    return storage.get_arr_library()


@router.post("/integrations/arr/library/{project_name}/disable")
async def arr_disable_entry(project_name: str):
    """Manually disable a synced library entry."""
    meta = storage.load_project_metadata(project_name)
    if not meta:
        raise HTTPException(status_code=404, detail="Project not found")
    if not meta.get("arr_source"):
        raise HTTPException(status_code=400, detail="Not an arr-synced project")
    meta["arr_disabled"] = True
    meta["arr_disabled_at"] = datetime.now().isoformat()
    storage.save_project_metadata(project_name, meta)
    try:
        from utils import metadata_index
        metadata_index.set_project_disabled(project_name, True)
    except Exception:
        pass
    return {"status": "disabled", "project": project_name}


@router.post("/integrations/arr/library/{project_name}/enable")
async def arr_enable_entry(project_name: str):
    """Re-enable a previously disabled library entry."""
    meta = storage.load_project_metadata(project_name)
    if not meta:
        raise HTTPException(status_code=404, detail="Project not found")
    meta["arr_disabled"] = False
    meta.pop("arr_disabled_at", None)
    storage.save_project_metadata(project_name, meta)
    try:
        from utils import metadata_index
        metadata_index.set_project_disabled(project_name, False)
    except Exception:
        pass
    return {"status": "enabled", "project": project_name}


@router.post("/integrations/arr/library/{project_name}/enqueue-missing")
async def enqueue_project_missing(project_name: str):
    """Enqueue all missing episodes of a show/movie to the backlog queue."""
    from services.queue_service import enqueue_missing_for_project, PRIORITY_BACKLOG
    enqueued = enqueue_missing_for_project(project_name, PRIORITY_BACKLOG)
    return {"status": "success", "enqueued_count": len(enqueued)}


_webhook_warned = False


def _verify_webhook_token(token: Optional[str], header_token: Optional[str]) -> None:
    """Enforce the configured webhook secret. If unset, allow (back-compat) but warn once."""
    global _webhook_warned
    secret = (storage.load_global_config().get("webhook_secret") or "").strip()
    if not secret:
        if not _webhook_warned:
            logger.warning("Webhook endpoints are OPEN — set 'webhook_secret' in Settings to require a token.")
            _webhook_warned = True
        return
    supplied = (token or header_token or "").strip()
    if supplied != secret:
        raise HTTPException(status_code=401, detail="Invalid or missing webhook token")


@router.post("/api/webhook/sonarr")
async def sonarr_webhook(
    payload: Dict,
    background_tasks: BackgroundTasks,
    token: Optional[str] = Query(None),
    x_omnisub_token: Optional[str] = Header(None),
):
    """Receive Sonarr 'Download' events and import the new subtitle."""
    _verify_webhook_token(token, x_omnisub_token)
    event_type = payload.get("eventType", "")
    logger.info(f"Sonarr webhook: {event_type}")
    if event_type == "Test":
        return {"status": "success", "message": "Webhook connection verified"}
    if event_type not in ("Download", "EpisodeFileRenamed"):
        return {"status": "ignored", "eventType": event_type}

    background_tasks.add_task(_process_sonarr_webhook, payload)
    return {"status": "queued"}


@router.post("/api/webhook/radarr")
async def radarr_webhook(
    payload: Dict,
    background_tasks: BackgroundTasks,
    token: Optional[str] = Query(None),
    x_omnisub_token: Optional[str] = Header(None),
):
    """Receive Radarr 'Download' events and import the new subtitle."""
    _verify_webhook_token(token, x_omnisub_token)
    event_type = payload.get("eventType", "")
    logger.info(f"Radarr webhook: {event_type}")
    if event_type == "Test":
        return {"status": "success", "message": "Webhook connection verified"}
    if event_type not in ("Download", "MovieFileRenamed"):
        return {"status": "ignored", "eventType": event_type}

    background_tasks.add_task(_process_radarr_webhook, payload)
    return {"status": "queued"}


async def _process_sonarr_webhook(payload: Dict):
    """Background: import episode from Sonarr webhook, optionally auto-translate."""
    try:
        from utils.srt_parser import parse_srt
        config = storage.load_global_config()
        engine = _build_arr_engine(config)
        location = await engine.process_sonarr_webhook(payload, storage, parse_srt)
        if location and config.get("arr_auto_translate"):
            project_name, episode_name = location.split("/", 1)
            from services.queue_service import enqueue_translation, PRIORITY_WEBHOOK
            enqueue_translation(project_name, episode_name, PRIORITY_WEBHOOK)
    except Exception as e:
        logger.error(f"Sonarr webhook processing failed: {e}", exc_info=True)


async def _process_radarr_webhook(payload: Dict):
    """Background: import movie from Radarr webhook, optionally auto-translate."""
    try:
        from utils.srt_parser import parse_srt
        config = storage.load_global_config()
        engine = _build_arr_engine(config)
        location = await engine.process_radarr_webhook(payload, storage, parse_srt)
        if location and config.get("arr_auto_translate"):
            project_name, episode_name = location.split("/", 1)
            from services.queue_service import enqueue_translation, PRIORITY_WEBHOOK
            enqueue_translation(project_name, episode_name, PRIORITY_WEBHOOK)
    except Exception as e:
        logger.error(f"Radarr webhook processing failed: {e}", exc_info=True)
