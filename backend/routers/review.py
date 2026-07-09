import asyncio
import logging
import time
from typing import Dict, List, Optional
from pathlib import Path
import re

from fastapi import APIRouter, HTTPException

from utils import storage
from utils.srt_parser import reconstruct_srt
from utils.language_codes import to_code

router = APIRouter()
logger = logging.getLogger(__name__)

# The review-queue scan walks every episode's data.json. That's filesystem-heavy on a
# large library, so it MUST run off the event loop (asyncio.to_thread) and the hot global
# endpoint (polled by the TopBar badge) is cached with a short TTL.
_GLOBAL_REVIEW_TTL = 120.0
_global_review_cache = {"ts": 0.0, "data": None}
# Single-flight: only ONE global scan runs at a time. Concurrent pollers await the
# in-flight scan and share its result instead of each launching a duplicate full-library
# scan (which would thrash the disk).
_global_review_lock = asyncio.Lock()


async def _refresh_global_review_cache(force: bool = False):
    import time
    now = time.time()
    cached = _global_review_cache["data"]
    if not force and cached is not None and (now - _global_review_cache["ts"]) < _GLOBAL_REVIEW_TTL:
        return cached
    async with _global_review_lock:
        # Re-check: another request may have refreshed while we waited for the lock.
        now = time.time()
        cached = _global_review_cache["data"]
        if not force and cached is not None and (now - _global_review_cache["ts"]) < _GLOBAL_REVIEW_TTL:
            return cached
        flagged = await asyncio.to_thread(_scan_review_queue, None)
        result = {"items": flagged, "count": len(flagged)}
        _global_review_cache["data"] = result
        _global_review_cache["ts"] = time.time()
        return result


async def warm_global_review_cache():
    """At startup, populate the global review cache from the index (fast). We deliberately
    do NOT bulk-scan every episode's data.json here — on a large library that bulk read
    saturates the disk and degrades the whole app. needs_review counts populate
    incrementally as episodes are saved; ``POST /projects/review-queue/reindex`` can seed
    historical counts on demand (gently, in the background)."""
    try:
        await _refresh_global_review_cache(force=True)
    except Exception as e:
        logger.warning(f"review cache warm failed (non-critical): {e}")


@router.post("/projects/review-queue/reindex")
async def reindex_review_counts():
    """Manually seed needs_review counts for pre-existing episodes (gentle background scan).
    Use this once if you want historical review flags to show up in the queue."""
    async def _run():
        try:
            n = await asyncio.to_thread(_seed_review_index)
            await _refresh_global_review_cache(force=True)
            logger.info(f"review reindex complete: {n} episode(s) scanned")
        except Exception as e:
            logger.warning(f"review reindex failed: {e}")
    asyncio.create_task(_run())
    return {"status": "started", "note": "Seeding needs_review counts in the background; counts will fill in gradually."}


def _scan_review_queue(project_name: Optional[str] = None) -> List[Dict]:
    """Collect needs_review lines. Uses the metadata index to open ONLY episodes that
    actually have flags (needs_review > 0), so cost scales with the number of flagged
    episodes, not the whole library. Run via asyncio.to_thread."""
    from utils import metadata_index
    try:
        pairs = metadata_index.list_with_review(project_name)
    except Exception:
        pairs = []

    flagged = []
    for proj, ep_name in pairs:
        data = storage.load_episode(proj, ep_name)
        if not data:
            continue
        for i, line in enumerate(data["data"]):
            if line.get("needs_review"):
                item = {
                    "episode": ep_name,
                    "index": i,
                    "original": line.get("original", ""),
                    "translated": line.get("translated", ""),
                    "timecode": line.get("timecode", ""),
                    "review_issues": line.get("review_issues", ""),
                    "review_scores": line.get("review_scores", {}),
                }
                if not project_name:
                    item["project_name"] = proj
                flagged.append(item)
    return flagged


def _seed_review_index() -> int:
    """One-time scan to seed needs_review counts for episodes that existed before the
    index tracked them. Loads each translated episode's data.json once and records its
    flag count. After this, the review scan never needs a full-library walk again.

    Throttled: it sleeps briefly between batches so the bulk read can't monopolize the
    disk and starve live request handlers (it runs in a worker thread)."""
    import time
    from utils import metadata_index
    seeded = 0
    for proj, ep_name in metadata_index.list_translated(None):
        try:
            data = storage.load_episode(proj, ep_name)
            if data:
                cnt = sum(1 for l in data["data"] if l.get("needs_review"))
                metadata_index.set_review_count(proj, ep_name, cnt)
        except Exception:
            pass
        seeded += 1
        # Gentle: pause frequently so the bulk read leaves disk headroom for live
        # request handlers. Makes the one-time seed take longer but never starves the app.
        if seeded % 3 == 0:
            time.sleep(0.1)
    return seeded


def _invalidate_global_review_cache():
    _global_review_cache["ts"] = 0.0
    _global_review_cache["data"] = None


@router.get("/projects/review-queue")
async def get_global_review_queue(force: bool = False):
    """Get all lines flagged for user review across all projects (cached, off-loop, single-flight)."""
    return await _refresh_global_review_cache(force=force)


@router.get("/projects/{project_name}/review-queue")
async def get_review_queue(project_name: str):
    """Get all lines flagged for user review across all episodes of a project (off-loop)."""
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    flagged = await asyncio.to_thread(_scan_review_queue, project_name)
    return {"items": flagged, "count": len(flagged)}


@router.post("/projects/{project_name}/review-queue/resolve")
async def resolve_review_item(
    project_name: str,
    episode_name: str,
    line_index: int,
    translated_text: Optional[str] = None,
):
    """Mark a review-flagged line as resolved (user has verified it).

    Clears the needs_review flag and review metadata for the line.
    Optionally saves the user-edited translated text at the same time.
    """
    data = storage.load_episode(project_name, episode_name)
    if not data:
        raise HTTPException(status_code=404, detail="Episode not found")

    if line_index < 0 or line_index >= len(data["data"]):
        raise HTTPException(status_code=400, detail="Invalid line index")

    line = data["data"][line_index]
    line.pop("needs_review", None)
    line.pop("review_issues", None)
    line.pop("review_scores", None)

    # Save the edited translation text if provided
    if translated_text is not None:
        line["translated"] = translated_text
        # Also update the translations dict for the primary language
        proj_meta = storage.load_project_metadata(project_name)
        if proj_meta:
            primary_lang = proj_meta.get("target_language", "Greek")
            lang_code = to_code(primary_lang)
            if "translations" not in line:
                line["translations"] = {}
            line["translations"][lang_code] = translated_text

    metadata = storage.load_episode_metadata(project_name, episode_name) or {}
    storage.save_episode(project_name, episode_name, data["data"], metadata)

    # Check if there are any remaining needs_review lines in this episode
    remaining_review = any(l.get("needs_review") for l in data["data"])
    delivered = False
    output_path = ""
    if not remaining_review:
        metadata["review_status"] = "approved"
        storage.save_episode(project_name, episode_name, data["data"], metadata)
        
        # Deliver if arr source
        media_path = metadata.get("arr_media_path") or metadata.get("bazarr_media_path")
        if media_path:
            try:
                proj_meta = storage.load_project_metadata(project_name)
                target_lang = proj_meta.get("target_language", "Greek") if proj_meta else "Greek"
                lang_code = to_code(target_lang)
                from utils.source_clean import reconstruct_cleaned_srt
                from utils.language_codes import output_filename_from_original
                srt_output = reconstruct_cleaned_srt(project_name, episode_name, data["data"], lang_code)
                
                # Write next to media file
                media = Path(media_path)
                orig_filename = metadata.get("original_filename") or ""
                ext = Path(orig_filename).suffix
                if not ext:
                    _, ext_sub = storage.load_original_subtitle(project_name, episode_name)
                    ext = f".{ext_sub}" if ext_sub else ".srt"
                target_filename = output_filename_from_original(f"{media.stem}{ext}", lang_code)
                out_path = media.parent / target_filename
                out_path.write_text(srt_output, encoding="utf-8-sig")
                output_path = str(out_path)
                delivered = True
            except Exception as e:
                logger.error(f"Failed to deliver approved subtitle: {e}")

    _invalidate_global_review_cache()
    return {
        "status": "resolved",
        "episode": episode_name,
        "index": line_index,
        "all_resolved": not remaining_review,
        "delivered": delivered,
        "output_path": output_path
    }


@router.post("/projects/{project_name}/review-queue/resolve-all")
async def resolve_all_review_items(project_name: str):
    """Mark all review-flagged lines as resolved across all episodes."""
    resolved_count = 0
    delivered_episodes = []
    
    for ep_name in storage.list_episodes(project_name):
        data = storage.load_episode(project_name, ep_name)
        if not data:
            continue

        modified = False
        for line in data["data"]:
            if line.get("needs_review"):
                line.pop("needs_review", None)
                line.pop("review_issues", None)
                line.pop("review_scores", None)
                resolved_count += 1
                modified = True

        if modified:
            metadata = storage.load_episode_metadata(project_name, ep_name) or {}
            metadata["review_status"] = "approved"
            storage.save_episode(project_name, ep_name, data["data"], metadata)
            
            # Deliver if arr source
            media_path = metadata.get("arr_media_path") or metadata.get("bazarr_media_path")
            if media_path:
                try:
                    proj_meta = storage.load_project_metadata(project_name)
                    target_lang = proj_meta.get("target_language", "Greek") if proj_meta else "Greek"
                    lang_code = to_code(target_lang)
                    from utils.source_clean import reconstruct_cleaned_srt
                    from utils.language_codes import output_filename_from_original
                    srt_output = reconstruct_cleaned_srt(project_name, ep_name, data["data"], lang_code)
                    
                    media = Path(media_path)
                    orig_filename = metadata.get("original_filename") or ""
                    ext = Path(orig_filename).suffix
                    if not ext:
                        _, ext_sub = storage.load_original_subtitle(project_name, ep_name)
                        ext = f".{ext_sub}" if ext_sub else ".srt"
                    target_filename = output_filename_from_original(f"{media.stem}{ext}", lang_code)
                    out_path = media.parent / target_filename
                    out_path.write_text(srt_output, encoding="utf-8-sig")
                    delivered_episodes.append(ep_name)
                except Exception as e:
                    logger.error(f"Failed to deliver approved subtitle {ep_name}: {e}")

    _invalidate_global_review_cache()
    return {"status": "resolved", "count": resolved_count, "delivered_episodes": delivered_episodes}


from pydantic import BaseModel


@router.post("/projects/{project_name}/review-queue/save-line")
async def save_review_line(
    project_name: str,
    episode_name: str,
    line_index: int,
    translated_text: str,
):
    """Save edited translation text for a review-flagged line without resolving it.

    The review flags remain in place so the user can continue editing.
    """
    data = storage.load_episode(project_name, episode_name)
    if not data:
        raise HTTPException(status_code=404, detail="Episode not found")

    if line_index < 0 or line_index >= len(data["data"]):
        raise HTTPException(status_code=400, detail="Invalid line index")

    line = data["data"][line_index]
    line["translated"] = translated_text

    # Also update the translations dict for the primary language
    proj_meta = storage.load_project_metadata(project_name)
    if proj_meta:
        primary_lang = proj_meta.get("target_language", "Greek")
        lang_code = to_code(primary_lang)
        if "translations" not in line:
            line["translations"] = {}
        line["translations"][lang_code] = translated_text

    metadata = storage.load_episode_metadata(project_name, episode_name) or {}
    storage.save_episode(project_name, episode_name, data["data"], metadata)

    return {"status": "saved", "episode": episode_name, "index": line_index}


class AcceptSuggestionRequest(BaseModel):
    term: str
    translation: str
    type: str = "other"
    gender: str = "n/a"


class MeasureConformanceRequest(BaseModel):
    text: str
    timecode: str


@router.get("/projects/{project_name}/feedback/suggestions")
async def get_feedback_suggestions(project_name: str, min_freq: int = 2):
    """Fetch glossary/reconciliation suggestions for the project."""
    from utils.review_feedback import get_glossary_suggestions
    db_suggestions = get_glossary_suggestions(project_name, min_freq=min_freq)
    
    file_suggestions = []
    suggestions_file = storage.PROJECTS_DIR / project_name / "suggestions.json"
    if suggestions_file.exists():
        try:
            import json
            with open(suggestions_file, 'r', encoding='utf-8') as f:
                file_suggestions = json.load(f)
        except Exception:
            file_suggestions = []
            
    combined = {s["term"].lower(): s for s in file_suggestions}
    for db_s in db_suggestions:
        term_lower = db_s["term"].lower()
        if term_lower in combined:
            combined[term_lower]["occurrences"] = max(combined[term_lower]["occurrences"], db_s["occurrences"])
        else:
            combined[term_lower] = {
                "term": db_s["term"],
                "suggested_translation": db_s["translation"],
                "occurrences": db_s["occurrences"],
                "accepted": False
            }
            
    proj_meta = storage.load_project_metadata(project_name) or {}
    glossary = proj_meta.get("glossary", {})
    existing_terms = {t.get("term", "").lower().strip() for t in glossary.get("terms", []) if t.get("term")}
    
    result = [s for s in combined.values() if s["term"].lower().strip() not in existing_terms]
    return {"suggestions": result, "count": len(result)}


@router.post("/projects/{project_name}/feedback/suggestions/accept")
async def accept_feedback_suggestion(project_name: str, req: AcceptSuggestionRequest):
    """Add an accepted suggestion to the project glossary."""
    proj_meta = storage.load_project_metadata(project_name)
    if not proj_meta:
        raise HTTPException(status_code=404, detail="Project not found")
        
    glossary = proj_meta.get("glossary", {"terms": []})
    
    exists = False
    for t in glossary["terms"]:
        if t.get("term", "").lower().strip() == req.term.lower().strip():
            t["translation"] = req.translation
            t["type"] = req.type
            t["gender"] = req.gender
            exists = True
            break
            
    if not exists:
        glossary["terms"].append({
            "term": req.term,
            "translation": req.translation,
            "type": req.type,
            "gender": req.gender,
            "keep_original": False,
            "case_sensitive": False
        })
        
    proj_meta["glossary"] = glossary
    storage.save_project_metadata(project_name, proj_meta)
    
    suggestions_file = storage.PROJECTS_DIR / project_name / "suggestions.json"
    if suggestions_file.exists():
        try:
            import json
            with open(suggestions_file, 'r', encoding='utf-8') as f:
                sugs = json.load(f)
            for s in sugs:
                if s["term"].lower().strip() == req.term.lower().strip():
                    s["accepted"] = True
            with open(suggestions_file, 'w', encoding='utf-8') as f:
                json.dump(sugs, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
            
    from utils.cache_manager import invalidate_cache
    invalidate_cache(project_name)
    
    return {"status": "success", "term": req.term}


@router.post("/projects/{project_name}/conformance/measure")
async def measure_conformance(project_name: str, req: MeasureConformanceRequest):
    """Measure a single translated subtitle line's conformance metrics and return issues."""
    global_config = storage.load_global_config()
    from utils.subtitle_conformance import measure_line, default_limits
    limits = default_limits(global_config)
    
    metrics = measure_line(req.text, req.timecode)
    
    issues = []
    if metrics["cps"] > limits["max_cps"]:
        issues.append(f"reading speed {metrics['cps']} CPS > {limits['max_cps']}")
    if metrics["max_line_len"] > limits["max_chars_per_line"]:
        issues.append(f"line {metrics['max_line_len']} > {limits['max_chars_per_line']} chars")
    if metrics["line_count"] > limits["max_lines"]:
        issues.append(f"{metrics['line_count']} > {limits['max_lines']} lines")
        
    return {
        "metrics": metrics,
        "limits": limits,
        "is_conformant": len(issues) == 0,
        "issues": issues
    }
