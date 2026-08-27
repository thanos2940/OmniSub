"""
Manual source-subtitle replace / search (Plan 17).

Phase A (implemented): upload a better source .srt for an episode, which replaces the
source, clears old translations, refreshes the fingerprint (so staleness forces a fresh
translation even if an old target is on disk), and enqueues a re-translation.

Phase B (stub): provider search — returns empty unless a provider is configured.
"""

import hashlib
from pathlib import Path
from fastapi import APIRouter, UploadFile, HTTPException

from utils import storage
from utils.subtitle_io import parse_subtitle

router = APIRouter()


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise HTTPException(status_code=400, detail="Could not decode subtitle file (unknown encoding)")


@router.post("/projects/{project}/episodes/{episode}/source/upload")
async def upload_source(project: str, episode: str, file: UploadFile):
    text = _decode(await file.read())
    from utils.source_clean import import_and_clean_srt
    try:
        res = import_and_clean_srt(project, episode, text, filename=file.filename)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to import/clean subtitle: {e}")

    ep_meta = res["metadata"]
    ep_meta["arr_source_origin"] = "manual"
    storage.save_episode_metadata(project, episode, ep_meta)

    cp = storage.episode_dir(project, episode) / "checkpoint.json"
    if cp.exists():
        try:
            cp.unlink()
        except Exception:
            pass

    from services.queue_service import enqueue_translation, PRIORITY_MANUAL
    item_id = enqueue_translation(project, episode, PRIORITY_MANUAL, {"force": True, "translation_type": "original"})
    return {"status": "success", "line_count": res["line_count"], "job_id": f"bg_{item_id}"}


@router.get("/projects/{project}/episodes/{episode}/source/search")
async def search_source(project: str, episode: str):
    """Phase B stub — returns empty until a source-subtitle provider is configured."""
    config = storage.load_global_config()
    if not config.get("opensubtitles_api_key"):
        return {"candidates": [], "configured": False,
                "message": "No source-subtitle provider configured. Add an API key in Settings."}
    # Provider implementations plug in here (see plan 17, Phase B).
    return {"candidates": [], "configured": True}
