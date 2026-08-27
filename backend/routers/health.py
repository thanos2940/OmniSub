"""
Unified health/status endpoint (Plan 16).

Aggregates the checks that are otherwise scattered across the integration test
endpoints into one pass/fail view: API key, Sonarr, Radarr, path mappings + media
write access, the background worker, disk space, and webhook configuration.
"""

import os
import asyncio
import shutil
import logging
from pathlib import Path
from typing import Dict, Any

import httpx
from fastapi import APIRouter

from utils import storage
from routers.settings import has_api_key
from integrations.sonarr import SonarrConfig, test_connection as sonarr_test
from integrations.radarr import RadarrConfig, test_connection as radarr_test
from integrations.path_resolver import PathResolver

router = APIRouter()
logger = logging.getLogger("omnisub.health")


def _ok(passed, detail="", hint=""):
    return {"status": "pass" if passed else "fail", "detail": detail, "hint": hint}


def _sample_media_dir() -> str:
    """Find one resolved media directory from synced episodes (for a write test)."""
    for p in storage.list_projects():
        for ep in storage.list_episodes(p):
            meta = storage.load_episode_metadata(p, ep) or {}
            mp = meta.get("arr_media_path")
            if mp:
                parent = str(Path(mp).parent)
                if Path(parent).exists():
                    return parent
    return ""


def _check_paths(config) -> dict:
    mappings = config.get("arr_path_mappings", []) or []
    resolver = PathResolver(mappings)
    results = []
    for m in mappings:
        remote = m.get("remote", "")
        local = m.get("local", "")
        reachable = resolver.is_reachable(local) if local else False
        results.append({"remote": remote, "local": local,
                        "status": "pass" if reachable else "fail",
                        "hint": "" if reachable else "Local path unreachable — check the mapping/mount."})
    # Write test in a real media directory
    write = {"status": "skip", "detail": "no synced media directory found", "hint": ""}
    media_dir = _sample_media_dir()
    if media_dir:
        probe = Path(media_dir) / ".omnisub_write_test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            write = _ok(True, f"writable: {media_dir}")
        except Exception as e:
            write = _ok(False, f"cannot write to {media_dir}: {e}",
                        "The worker must write .srt next to media — fix permissions/mount.")
    return {"mappings": results, "write_test": write}


async def _check_local_llm(base_url: str) -> Dict[str, Any]:
    """Check reachability and loaded models of the local LLM server."""
    if not base_url:
        return {"status": "skip", "detail": "not configured", "hint": "Set local_llm_base_url in Settings."}
    
    # Ensure it ends with /v1
    from adk_agents.llm_factory import _resolve_local_base_url
    url = _resolve_local_base_url(base_url)
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{url}/models")
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("id") for m in data.get("data", [])]
                model_str = ", ".join(models[:3]) + (f" (+{len(models)-3})" if len(models) > 3 else "")
                return {
                    "status": "pass",
                    "detail": f"Online: {model_str or 'No models loaded'}",
                    "models": models,
                    "hint": ""
                }
            else:
                return {
                    "status": "fail",
                    "detail": f"HTTP {resp.status_code}",
                    "hint": "Local server returned an error. Check if /v1/models is supported."
                }
    except Exception as e:
        return {
            "status": "fail",
            "detail": f"Unreachable: {str(e)}",
            "hint": "Check if LM Studio, Ollama, or llama.cpp is running and the URL is correct."
        }


@router.get("/api/health/full")
async def full_health():
    config = storage.load_global_config()
    provider = config.get("ai_provider", "cloud")
    out = {}
    
    out["ai_provider"] = {"status": "pass", "detail": provider.capitalize()}

    # API Key check: fail if cloud/hybrid and missing; warn if local and missing
    key_ok = has_api_key()
    if key_ok:
        out["api_key"] = _ok(True, "configured")
    else:
        if provider == "local":
            out["api_key"] = {
                "status": "warn", 
                "detail": "missing (ok for Local provider)", 
                "hint": "Set Gemini API key if you want to use Cloud or Hybrid modes."
            }
        else:
            out["api_key"] = {
                "status": "fail", 
                "detail": "missing", 
                "hint": "Set the Gemini API key in Settings (Required for Cloud/Hybrid modes)."
            }

    # Local LLM check
    out["local_llm"] = await _check_local_llm(config.get("local_llm_base_url"))

    # Sonarr / Radarr (concurrent, guarded)
    async def _arr(test_fn, cfg_cls, url_key, key_key, enabled_key, default_url):
        if not config.get(key_key):
            return {"status": "skip", "detail": "not configured", "enabled": config.get(enabled_key, False)}
        try:
            res = await test_fn(cfg_cls(base_url=config.get(url_key, default_url), api_key=config.get(key_key, "")))
            return {"status": "pass" if res.get("connected") else "fail",
                    "detail": res.get("version") or res.get("error", ""),
                    "enabled": config.get(enabled_key, False),
                    "hint": "" if res.get("connected") else "Check URL/API key/reachability."}
        except Exception as e:
            return _ok(False, str(e))

    out["sonarr"], out["radarr"] = await asyncio.gather(
        _arr(sonarr_test, SonarrConfig, "sonarr_url", "sonarr_api_key", "sonarr_enabled", "http://localhost:8989"),
        _arr(radarr_test, RadarrConfig, "radarr_url", "radarr_api_key", "radarr_enabled", "http://localhost:7878"),
    )

    out["paths"] = await asyncio.to_thread(_check_paths, config)  # scans episodes for a sample media dir — keep off the event loop

    # Worker + queue
    try:
        from services.background_worker import worker
        from utils.translation_queue import TranslationQueue
        summary = TranslationQueue().get_summary()
        running = bool(worker.running_task and not worker.running_task.done())
        out["worker"] = {
            "status": "pass" if running else "fail",
            "running": running,
            "paused": config.get("queue_worker_paused", False),
            "queue": summary,
            "hint": "" if running else "Background worker is not running — restart the backend.",
        }
    except Exception as e:
        out["worker"] = _ok(False, str(e))

    # ffmpeg (embedded subtitle extraction)
    try:
        from integrations.embedded_subs import tools_status
        if not config.get("embedded_extraction_enabled", False):
            out["ffmpeg"] = {"status": "skip", "detail": "embedded extraction disabled",
                             "hint": "Enable it in Settings to extract subtitles muxed into media files."}
        else:
            status = tools_status(config)
            out["ffmpeg"] = {
                "status": "pass" if status["available"] else "fail",
                "detail": status["ffmpeg"] or "ffmpeg/ffprobe not found",
                "hint": "" if status["available"]
                        else "Install ffmpeg or set ffmpeg_path in Settings — extraction is skipped without it.",
            }
    except Exception as e:
        out["ffmpeg"] = _ok(False, str(e))

    # Disk
    try:
        usage = shutil.disk_usage(str(storage.PROJECTS_DIR))
        free_gb = round(usage.free / (1024 ** 3), 1)
        out["disk"] = _ok(free_gb > 1.0, f"{free_gb} GB free",
                          "" if free_gb > 1.0 else "Low disk space for translations/checkpoints.")
    except Exception as e:
        out["disk"] = _ok(False, str(e))

    # Webhooks
    secret = (config.get("webhook_secret") or "").strip()
    suffix = f"?token={secret}" if secret else ""
    out["webhooks"] = {
        "status": "pass" if secret else "warn",
        "secured": bool(secret),
        "sonarr_url": f"/api/webhook/sonarr{suffix}",
        "radarr_url": f"/api/webhook/radarr{suffix}",
        "hint": "" if secret else "No webhook_secret set — Sonarr/Radarr webhooks will be rejected until you set one in Settings.",
    }

    statuses = [v.get("status") for v in out.values() if isinstance(v, dict)]
    out["overall"] = "fail" if "fail" in statuses else ("warn" if "warn" in statuses else "pass")
    return out
