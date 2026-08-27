"""
Omnisub API - Subtitle Translation Platform

FastAPI backend providing endpoints for project management, glossary creation,
and AI-powered subtitle translation using ADK agents.
"""

import os
import asyncio
import logging
import secrets as _secrets
from contextlib import asynccontextmanager
from typing import Dict, Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env')

from utils import storage
from utils.rate_limiter import per_model_rate_limiter
from utils.auth import generate_api_key, generate_webhook_secret
from routers.settings import has_api_key, validate_api_key

from routers import (
    auth,
    projects,
    episodes,
    settings,
    glossary_context,
    pipeline,
    integrations,
    review,
    translation_memory,
    characters,
    summaries,
    queue,
    jobs,
    health,
    dashboard,
    blacklist,
    consistency,
    source_subs,
    setup,
    embedded,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("omnisub.main")

_scheduled_sync_task: Optional[asyncio.Task] = None
_maintenance_task: Optional[asyncio.Task] = None
_batch_task: Optional[asyncio.Task] = None


async def _batch_loop():
    """Drain BACKLOG items via the Gemini Batch API when enabled (Plan 01)."""
    from services.batch_translator import batch_translator
    while True:
        try:
            config = storage.load_global_config()
            if config.get("batch_api_enabled", False):
                await batch_translator.run_once(config)
                await asyncio.sleep(30)
            else:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Batch loop error (non-critical): {e}", exc_info=True)
            await asyncio.sleep(120)


async def _maintenance_loop():
    """Periodic housekeeping: evict old in-memory jobs."""
    from utils.jobs_manager import evict_old_jobs
    while True:
        try:
            await asyncio.sleep(180)
            config = storage.load_global_config()
            removed = evict_old_jobs(
                max_age_seconds=config.get("job_retention_seconds", 1800),
                max_jobs=config.get("job_registry_max", 500),
            )
            if removed:
                logger.info(f"Maintenance: evicted {removed} old in-memory job(s)")
            # Prune old telemetry rows (v2 D8) — cheap, bounded.
            try:
                from utils import telemetry
                telemetry.prune(max_age_days=90)
            except Exception:
                pass
            # Flush any debounced rate-limiter state to disk.
            try:
                per_model_rate_limiter.flush()
            except Exception:
                pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Maintenance loop error (non-critical): {e}")


async def _scheduled_arr_sync_loop():
    """Background loop that runs Sonarr/Radarr sync at the configured interval."""
    from utils.translation_queue import TranslationQueue, PRIORITY_SYNC
    from routers.integrations import _build_arr_engine

    while True:
        try:
            config = storage.load_global_config()
            interval_minutes = config.get("arr_sync_interval", 0)
            sonarr_ok = config.get("sonarr_enabled") and config.get("sonarr_api_key")
            radarr_ok = config.get("radarr_enabled") and config.get("radarr_api_key")

            if interval_minutes <= 0 or (not sonarr_ok and not radarr_ok):
                await asyncio.sleep(60)
                continue

            await asyncio.sleep(interval_minutes * 60)

            config = storage.load_global_config()
            engine = _build_arr_engine(config)
            result = await engine.full_sync()
            logger.info(
                f"Scheduled arr sync complete: "
                f"{result.new_projects} new, {result.new_episodes} episodes"
            )

            # Refresh the metadata index after a sync (Plan 03) — off the event loop.
            try:
                from utils import metadata_index
                await asyncio.to_thread(metadata_index.backfill)
            except Exception:
                pass

            # Queue missing subtitles for translation
            queue = TranslationQueue()
            
            for project_name in storage.list_projects():
                proj_meta = storage.load_project_metadata(project_name) or {}
                if proj_meta.get("arr_disabled", False):
                    continue
                
                episodes_list = storage.list_episodes(project_name)
                for ep_name in episodes_list:
                    ep_meta = storage.load_episode_metadata(project_name, ep_name)
                    if ep_meta:
                        original_srt_exists = storage.original_subtitle_exists(project_name, ep_name)
                        needs_translation = (
                            not storage.episode_has_target(ep_meta)
                            or storage.episode_translation_is_stale(ep_meta)
                        )
                        if original_srt_exists and needs_translation:
                            queue.enqueue(project_name, ep_name, priority=PRIORITY_SYNC)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Scheduled arr sync error: {e}", exc_info=True)
            await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup tasks
    # Load API key from config.json if not present in environment
    if not os.environ.get("GOOGLE_API_KEY"):
        try:
            from routers.settings import get_api_key
            key = get_api_key()
            if key:
                os.environ["GOOGLE_API_KEY"] = key
                logger.info("Loaded GOOGLE_API_KEY from config.json")
        except Exception as e:
            logger.warning(f"Failed to load GOOGLE_API_KEY from config.json: {e}")

    # Generate the auth API key and webhook secret on first boot (idempotent).
    # These exist unconditionally — even with auth_enabled=false — so enabling
    # auth later, or configuring a webhook, never needs a restart.
    try:
        _cfg = storage.load_global_config()
        _updates = {}
        if not _cfg.get("api_key"):
            _updates["api_key"] = generate_api_key()
        if not _cfg.get("webhook_secret"):
            _updates["webhook_secret"] = generate_webhook_secret()
        if _updates:
            storage.save_global_config(_updates)
            logger.info(f"Generated missing secret(s) on first boot: {sorted(_updates.keys())}")
        if not _cfg.get("auth_enabled"):
            logger.warning(
                "Authentication is DISABLED. Anyone who can reach this server can "
                "read your settings and control translations. Set credentials in "
                "Settings -> Security (or the setup wizard) to secure it."
            )
    except Exception as e:
        logger.warning(f"Auth secret bootstrap skipped (non-critical): {e}")

    # Setup-wizard migration: an install that already has a real config (a
    # Gemini key, or Sonarr/Radarr credentials) predates the wizard and should
    # never see it — only a genuinely empty config.json goes through first-run.
    try:
        raw = storage._load_raw_config()
        if not raw.get("setup_completed"):
            already_configured = bool(
                raw.get("api_key_obfuscated")
                or raw.get("sonarr_api_key")
                or raw.get("radarr_api_key")
            )
            if already_configured:
                storage.save_global_config({"setup_completed": True})
                logger.info("Setup wizard: existing install detected, marking setup_completed")
    except Exception as e:
        logger.warning(f"Setup-wizard migration skipped (non-critical): {e}")

    # Recover any queue items orphaned in 'running' by a previous crash/restart
    try:
        from utils.translation_queue import TranslationQueue
        recovered = TranslationQueue().reset_orphaned_running()
        if recovered:
            logger.info(f"Recovered {recovered} orphaned 'running' queue item(s) → pending")
    except Exception as e:
        logger.warning(f"Orphaned-running recovery skipped (non-critical): {e}")

    # Backfill the metadata index from files if empty (Plan 03).
    # Runs in a thread — backfill is filesystem-heavy and must never block the event loop.
    try:
        from utils import metadata_index
        if not metadata_index.is_populated():
            n = await asyncio.to_thread(metadata_index.backfill)
            if n:
                logger.info(f"metadata_index: backfilled {n} episode(s)")
    except Exception as e:
        logger.warning(f"metadata_index backfill skipped (non-critical): {e}")

    global _scheduled_sync_task, _maintenance_task, _batch_task
    _scheduled_sync_task = asyncio.create_task(_scheduled_arr_sync_loop())
    _maintenance_task = asyncio.create_task(_maintenance_loop())
    _batch_task = asyncio.create_task(_batch_loop())

    # Warm the global review-queue cache in the background (off the event loop) so the
    # TopBar badge / Review page don't trigger a cold full-library scan on first poll.
    try:
        from routers.review import warm_global_review_cache
        asyncio.create_task(warm_global_review_cache())
    except Exception as e:
        logger.warning(f"review cache warm skipped (non-critical): {e}")

    # Start background translation worker
    from services.background_worker import worker
    worker.start()

    # Run migration for messy Bazarr movie episode names on first boot
    try:
        from integrations.media_sync_engine import MediaSyncEngine
        fixed = MediaSyncEngine.migrate_old_episode_names(storage)
        if fixed:
            logger.info(f"Migration: renamed episodes in {len(fixed)} projects: {fixed}")
    except Exception as e:
        logger.warning(f"Migration skipped (non-critical): {e}")

    yield

    # Shutdown tasks
    if _scheduled_sync_task:
        _scheduled_sync_task.cancel()
        _scheduled_sync_task = None
    if _maintenance_task:
        _maintenance_task.cancel()
        _maintenance_task = None
    if _batch_task:
        _batch_task.cancel()
        _batch_task = None

    # Stop background translation worker
    from services.background_worker import worker
    worker.stop()

    # Persist any debounced rate-limiter state before exit.
    try:
        per_model_rate_limiter.flush()
    except Exception:
        pass

    # Close shared HTTPX clients for Sonarr/Radarr
    try:
        from integrations.sonarr import close_client as close_sonarr_client
        await close_sonarr_client()
    except Exception:
        pass
    try:
        from integrations.radarr import close_client as close_radarr_client
        await close_radarr_client()
    except Exception:
        pass


app = FastAPI(title="Omnisub API", version="5.0", lifespan=lifespan)

# CORS: drive allowed origins from config. A non-empty list is spec-correct with
# credentials; an empty/unset list falls back to wildcard WITHOUT credentials.
_cors_origins = storage.load_global_config().get("cors_allow_origins") or []
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Auth gate: when auth_enabled, every request under a PROTECTED prefix needs a
# matching X-Api-Key header, except the small allowlist below. Everything else
# (the built SPA's index.html/JS/CSS, and — in dev — any path Vite would have
# served) stays public: the frontend shell has to be reachable before a user
# has logged in, the same way any self-hosted app's own login page is public.
# Preflight OPTIONS always passes through — it never carries custom headers,
# so gating it would break CORS entirely.
#
# _PROTECTED_PREFIXES is every top-level path segment actually registered by a
# router (verify with: grep -rhoE '@router\.(get|post|put|delete|patch)\("[^"]*"'
# backend/routers/*.py) — keep it in sync when a router adds a new top-level
# prefix; anything NOT in this list is implicitly public (fails safe toward
# "reachable", not toward "protected", so a forgotten entry here is a privacy
# bug, not an RCE — the executable-path/settings/webhook endpoints themselves
# are still individually hardened regardless of this gate).
_PROTECTED_PREFIXES = ("/api/", "/projects", "/jobs", "/integrations", "/rate-limit", "/settings")
_AUTH_EXEMPT_PATHS = {"/api/health", "/docs", "/openapi.json", "/redoc"}
_AUTH_EXEMPT_PREFIXES = ("/api/auth/login", "/api/auth/status", "/api/webhook/")


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    config = storage.load_global_config()
    if config.get("auth_enabled"):
        path = request.url.path
        is_protected = path.startswith(_PROTECTED_PREFIXES)
        if is_protected and path not in _AUTH_EXEMPT_PATHS and not path.startswith(_AUTH_EXEMPT_PREFIXES):
            stored = config.get("api_key") or ""
            supplied = request.headers.get("X-Api-Key", "")
            if not stored or not _secrets.compare_digest(supplied, stored):
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    return await call_next(request)


@app.exception_handler(ValueError)
async def _value_error_handler(request: Request, exc: ValueError):
    """Storage-layer path validation (utils.storage.safe_name / project_dir /
    episode_dir) raises ValueError on unsafe project/episode names; surface that
    as 400 Bad Request instead of an unhandled 500."""
    return JSONResponse({"detail": str(exc)}, status_code=400)

# Register routers
app.include_router(auth.router)
app.include_router(settings.router)
app.include_router(review.router)
app.include_router(projects.router)
app.include_router(episodes.router)
app.include_router(glossary_context.router)
app.include_router(pipeline.router)
app.include_router(integrations.router)
app.include_router(translation_memory.router)
app.include_router(characters.router)
app.include_router(summaries.router)
app.include_router(queue.router)
app.include_router(jobs.router)
app.include_router(health.router)
app.include_router(dashboard.router)
app.include_router(blacklist.router)
app.include_router(consistency.router)
app.include_router(source_subs.router)
app.include_router(setup.router)
app.include_router(embedded.router)


@app.get("/api/health")
async def health_check():
    """Liveness check for monitoring and container orchestration. Deliberately
    under /api/ (not bare /health) so the SPA fallback below can own that path
    for the frontend's own Health page instead of losing the route collision."""
    return {
        "status": "healthy",
        "service": "Omnisub API",
        "version": "5.0",
        "adk_enabled": True,
        "api_key_configured": has_api_key()
    }


# Serve the built frontend (backend/static/, populated by the Dockerfile's
# frontend-builder stage) and hand any non-API GET request to it, so React
# Router's client-side routes (e.g. /settings, /health, /project/X) resolve
# correctly on a hard refresh or direct link. In dev, this directory doesn't
# exist and Vite serves the frontend separately on :5173 — nothing here runs.
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.is_dir():
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    _assets_dir = _STATIC_DIR / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="spa-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (_STATIC_DIR / full_path).resolve()
        # Serve a real static file (favicon, manifest, etc.) if the path
        # matches one that exists directly under static/; otherwise fall
        # through to index.html so React Router owns the route client-side.
        if full_path and candidate.is_file() and candidate.is_relative_to(_STATIC_DIR.resolve()):
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    
    # Sync rate limiter with config on startup
    conf = storage.load_global_config()
    per_model_rate_limiter.load_config_and_state()
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
