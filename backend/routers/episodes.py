import asyncio
from datetime import datetime, timezone
from io import BytesIO
from zipfile import ZipFile
from typing import Any, List, Dict, Optional
from fastapi import APIRouter, HTTPException, UploadFile, BackgroundTasks, Request, Response
from fastapi.responses import StreamingResponse, JSONResponse
from utils import storage
from utils.srt_parser import parse_srt, reconstruct_srt
from utils.subtitle_fixer import fix_subtitles_with_se
from utils.jobs_manager import create_job
from services.translation_service import _record_user_edits, _process_retranslation_for_comparison, auto_export_translated_subtitle
from routers.schemas import SaveEpisodeRequest, TranslateRequest, MergeTranslationRequest, BatchDownloadRequest, DeleteLinesRequest, ApplyFixesRequest, BatchApplyFixesRequest
from routers.settings import validate_api_key

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Language-code helpers now live in utils.language_codes (single source of truth).
# Kept as module-level aliases for the download endpoints below.
from utils.language_codes import LANG_CODES, to_code as _lang_code, output_filename_from_original


def _output_filename(ep_name: str, ep_metadata: Dict, lang_code: str) -> str:
    """Build the output .srt filename from the original source filename.

    Strips the source language tag (e.g. .en) and appends the target tag.
    Falls back to ep_name if no original_filename is stored.
    """
    orig = (ep_metadata or {}).get("original_filename") or f"{ep_name}.srt"
    return output_filename_from_original(orig, lang_code)


def _prepare_srt_content(project_name: str, episode_name: str, ep_data: Dict, global_config: Dict, target_lang_code: str) -> str:
    """Apply SubtitleEdit fixes if enabled, then reconstruct SRT using reconstruct_cleaned_srt."""
    lines = ep_data.get("data", [])
    ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}
    orig_ext = (ep_meta.get("original_extension") or "").lstrip(".")
    if global_config.get("apply_subtitle_edit_fixes") and orig_ext not in ["ass", "ssa"]:
        try:
            lines = fix_subtitles_with_se(lines)
        except Exception:
            pass  # non-critical; fall back to unprocessed
    from utils.source_clean import reconstruct_cleaned_srt
    return reconstruct_cleaned_srt(project_name, episode_name, lines, target_lang_code)


# ---------------------------------------------------------------------------
# Episode Listing / Read
# ---------------------------------------------------------------------------

@router.get("/projects/{project_name}/episodes")
async def list_episodes(project_name: str):
    """List all episodes with metadata. Served from a per-project cache (invalidated on
    any episode save/delete); the cold build runs off the event loop."""
    import asyncio
    return await asyncio.to_thread(storage.list_episodes_with_metadata, project_name)


def _etag_for(stat) -> str:
    return f'W/"{stat.st_mtime_ns:x}-{stat.st_size:x}"'


@router.get("/projects/{project_name}/episodes/{episode_name}")
async def get_episode(project_name: str, episode_name: str, request: Request):
    """The live editor polls this every few seconds while a translation is
    running (EpisodeView.jsx), refetching the entire (potentially
    thousands-of-lines) payload each time. An ETag derived from the backing
    data.json's mtime/size — no read, no hashing — lets an unchanged poll
    short-circuit to 304 instead of re-serializing and re-transferring the
    whole episode."""
    try:
        episode_dir = storage.episode_dir(project_name, episode_name)
        data_file = episode_dir / "data.json"
        if not data_file.exists():
            raise HTTPException(status_code=404, detail="Episode not found")

        pre_load_etag = _etag_for(data_file.stat())
        if request.headers.get("if-none-match") == pre_load_etag:
            return Response(status_code=304)

        data = storage.load_episode(project_name, episode_name)
        if not data:
            raise HTTPException(status_code=404, detail="Episode not found")

        # load_episode() can self-heal legacy entries (missing translations/
        # src_hash) and rewrite data.json as a side effect — stat again so the
        # ETag we hand back reflects what's actually on disk now, not the
        # pre-load snapshot. Otherwise the very next request's If-None-Match
        # would never match and every poll would pay full cost forever.
        etag = _etag_for(data_file.stat())
        return JSONResponse(content=data, headers={"ETag": etag})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load episode: {str(e)})")


# ---------------------------------------------------------------------------
# Upload / Save / Delete / Clear
# ---------------------------------------------------------------------------

@router.post("/projects/{project_name}/episodes/{episode_name}/upload")
async def upload_episode(project_name: str, episode_name: str, file: UploadFile):
    validate_api_key()
    content = (await file.read()).decode('utf-8')
    from utils.source_clean import import_and_clean_srt
    try:
        res = import_and_clean_srt(project_name, episode_name, content, filename=file.filename)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to import/clean subtitle: {e}")

    return {"status": "success", "line_count": res["line_count"]}


@router.put("/projects/{project_name}/episodes/{episode_name}")
@router.post("/projects/{project_name}/episodes/{episode_name}/save")
async def save_episode(project_name: str, episode_name: str, request: SaveEpisodeRequest, request_raw: Request = None):
    data_file = storage.episode_dir(project_name, episode_name) / "data.json"
    if request_raw and data_file.exists():
        if_match = request_raw.headers.get("if-match")
        if if_match and if_match != "*":
            current_etag = _etag_for(data_file.stat())
            if if_match != current_etag:
                raise HTTPException(status_code=409, detail=f"Conflict: ETag mismatch. Current: {current_etag}, If-Match: {if_match}")

    previous = await asyncio.to_thread(storage.load_episode, project_name, episode_name)

    proj_meta = await asyncio.to_thread(storage.load_project_metadata, project_name)
    primary_lang = proj_meta.get("target_language", "Greek") if proj_meta else "Greek"
    from utils.language_codes import to_code
    primary_code = to_code(primary_lang)
    lang_code = to_code(request.lang or primary_lang)

    for line in request.data:
        if "translations" not in line:
            line["translations"] = {}
        if lang_code == primary_code and "translated" in line:
            line["translations"][primary_code] = line["translated"]
        elif "translated" in line and not line["translations"].get(primary_code):
            # fallback for older format
            line["translations"][primary_code] = line["translated"]
            
        # Keep translated as mirror of primary
        if primary_code in line["translations"]:
            line["translated"] = line["translations"][primary_code]

    await asyncio.to_thread(storage.save_episode, project_name, episode_name, request.data)

    if previous and previous.get("data"):
        try:
            await asyncio.to_thread(_record_user_edits, project_name, episode_name, previous["data"], request.data, lang=request.lang)
        except Exception:
            pass

    # Auto-export if this save includes translated lines
    exported_path = None
    has_translated = any(line.get("translated") or line.get("translations", {}).get(lang_code) for line in request.data)
    if has_translated:
        try:
            metadata = (await asyncio.to_thread(storage.load_episode_metadata, project_name, episode_name)) or {}
            metadata["translated"] = True
            metadata["translation_status"] = "completed"
            metadata["last_translated_at"] = datetime.now(timezone.utc).isoformat()
            await asyncio.to_thread(storage.save_episode_metadata, project_name, episode_name, metadata)
            exported_path = await asyncio.to_thread(auto_export_translated_subtitle, project_name, episode_name, request.data, metadata, force=True)
        except Exception:
            pass

        try:
            from services.agent_task_service import agent_task_service
            await asyncio.to_thread(agent_task_service.mark_task_completed, project_name, episode_name)
        except Exception:
            try:
                from utils.translation_queue import TranslationQueue
                q = TranslationQueue()
                now_str = datetime.now(timezone.utc).isoformat()
                def _update_q():
                    with q._conn() as conn:
                        conn.execute("""
                            UPDATE translation_queue
                            SET status = 'completed', completed_at = COALESCE(completed_at, ?)
                            WHERE project_name = ? AND episode_name = ? AND status IN ('pending', 'running')
                        """, (now_str, project_name, episode_name))
                        conn.commit()
                await asyncio.to_thread(_update_q)
            except Exception:
                pass

    new_etag = _etag_for(data_file.stat()) if data_file.exists() else None
    headers = {"ETag": new_etag} if new_etag else {}
    return JSONResponse(
        content={"status": "success", "exported_path": exported_path, "etag": new_etag},
        headers=headers
    )


@router.post("/projects/{project_name}/episodes/{episode_name}/delete-lines")
async def delete_episode_lines(project_name: str, episode_name: str, request: DeleteLinesRequest):
    """Permanently remove subtitle lines from an episode.

    Keeps exports aligned by re-keying clean_to_orig_map for the surviving lines
    and recording the deleted original cue indexes in deleted_orig_indexes, which
    reconstruct_cleaned_srt skips entirely. Re-exports the on-disk subtitle so the
    deleted lines disappear there too.
    """
    ep = await asyncio.to_thread(storage.load_episode, project_name, episode_name)
    if not ep:
        raise HTTPException(status_code=404, detail="Episode not found")

    data = ep.get("data", [])
    deleted = {i for i in request.indexes if 0 <= i < len(data)}
    if not deleted:
        raise HTTPException(status_code=400, detail="No valid line indexes to delete")

    metadata = (await asyncio.to_thread(storage.load_episode_metadata, project_name, episode_name)) or {}

    from utils.source_clean import normalize_orig_indexes
    old_map = {int(k): normalize_orig_indexes(v) for k, v in (metadata.get("clean_to_orig_map") or {}).items()}
    deleted_orig = {int(i) for i in metadata.get("deleted_orig_indexes") or []}

    new_data = []
    new_map = {}
    for idx, line in enumerate(data):
        if idx in deleted:
            if idx in old_map:
                deleted_orig.update(old_map[idx])
            continue
        if idx in old_map:
            new_map[str(len(new_data))] = old_map[idx]
        new_data.append(line)

    if old_map:
        metadata["clean_to_orig_map"] = new_map
    metadata["deleted_orig_indexes"] = sorted(deleted_orig)
    metadata["line_count"] = len(new_data)

    await asyncio.to_thread(storage.save_episode, project_name, episode_name, new_data, metadata)

    # Refresh the exported file on disk so the deleted lines disappear there too
    exported_path = None
    has_media = metadata.get("arr_media_path")
    if has_media and any(line.get("translated") for line in new_data):
        exported_path = await asyncio.to_thread(auto_export_translated_subtitle, project_name, episode_name, new_data, metadata, force=True)

    return {
        "status": "success",
        "deleted": len(deleted),
        "line_count": len(new_data),
        "exported_path": exported_path,
    }


def _apply_fixes_and_persist(project_name: str, episode_name: str, track: str,
                             lang_code: str, primary_code: str) -> Dict:
    """Run SubtitleEdit fixes on one track of one episode, persist the re-read cue list
    as authoritative, and re-export. Returns ``{"stats","line_count","exported_path"}``.

    Synchronous (subprocess + disk I/O) — call via ``asyncio.to_thread`` from handlers.
    Raises SubtitleEditUnavailable (SE not configured) or RuntimeError (bad output).
    """
    from utils.subtitle_fixer import apply_common_fixes
    ep = storage.load_episode(project_name, episode_name)
    if not ep or not ep.get("data"):
        raise RuntimeError("Episode not found or empty")

    # SubtitleEdit fixes are SRT-only; running them on an ASS/SSA episode would corrupt
    # styles/karaoke and desync the event mapping. Refuse rather than silently mangle.
    _fmt_meta = storage.load_episode_metadata(project_name, episode_name) or {}
    if (_fmt_meta.get("original_format") or _fmt_meta.get("original_extension") or "").lower() in ("ass", "ssa"):
        raise RuntimeError("SubtitleEdit fixes are not supported for ASS/SSA subtitles.")

    new_data, stats = apply_common_fixes(ep["data"], track, lang_code, primary_code)

    metadata = storage.load_episode_metadata(project_name, episode_name) or {}
    # Only take over the cue structure when SE actually re-segmented the episode or
    # when fixing the source track. apply_common_fixes already reports whether the cue
    # count changed (stats["changed"]) — trust that rather than re-deriving it. A
    # target-track fix that leaves the cue count unchanged must NOT wipe the
    # source-derived clean_to_orig_map / deleted_orig_indexes (that would destroy the
    # original raw mapping and any prior editor deletions).
    structure_changed = bool(stats.get("changed"))
    if track == "source" or structure_changed:
        # The re-segmented cue list is now the source of truth for this episode, so
        # export emits it directly (reconstruct_cleaned_srt honors
        # structure_authoritative) instead of rebuilding on the original raw file.
        metadata["structure_authoritative"] = True
        metadata["clean_to_orig_map"] = {str(i): [i] for i in range(len(new_data))}
        metadata["deleted_orig_indexes"] = []
    metadata["line_count"] = len(new_data)

    if track == "source":
        # The fixed source becomes the new pristine source file.
        new_source_rows = [
            {"id": str(i + 1), "timecode": e.get("timecode", ""), "original": e.get("original", ""), "translated": ""}
            for i, e in enumerate(new_data)
        ]
        storage.save_original_srt(project_name, episode_name, reconstruct_srt(new_source_rows))
        metadata["raw_line_count"] = len(new_data)
        if not any((e.get("translations") or {}) or e.get("translated") for e in new_data):
            metadata["translated"] = False
            metadata["translation_status"] = "pending"

    # The cue structure changed — a stale checkpoint would mis-map on resume.
    try:
        cp = storage.episode_dir(project_name, episode_name) / "checkpoint.json"
        if cp.exists():
            cp.unlink()
    except Exception:
        pass

    storage.save_episode(project_name, episode_name, new_data, metadata)

    exported_path = None
    if any(e.get("translated") or (e.get("translations") or {}) for e in new_data):
        try:
            exported_path = auto_export_translated_subtitle(project_name, episode_name, new_data, metadata, force=True)
        except Exception:
            pass

    return {"stats": stats, "line_count": len(new_data), "exported_path": exported_path}


@router.post("/projects/{project_name}/episodes/{episode_name}/apply-fixes")
async def apply_episode_fixes(project_name: str, episode_name: str, request: ApplyFixesRequest):
    """Run SubtitleEdit 'Fix common errors' + 'Split long lines' on one track and
    re-read the (possibly re-segmented) output.

    ``track="source"`` cleans the imported subtitles (run before translating);
    ``track="target"`` cleans the translation (run after, as a final polish). Because
    SE can split long lines into new cues, the re-read cue list becomes authoritative
    for this episode so the splits survive into exports.
    """
    import asyncio

    track = (request.track or "source").lower()
    if track not in ("source", "target"):
        raise HTTPException(status_code=400, detail="track must be 'source' or 'target'")

    ep = storage.load_episode(project_name, episode_name)
    if not ep or not ep.get("data"):
        raise HTTPException(status_code=404, detail="Episode not found or empty")

    proj_meta = storage.load_project_metadata(project_name) or {}
    primary_code = _lang_code(proj_meta.get("target_language", "Greek"))
    lang_code = _lang_code(request.lang) if request.lang else primary_code

    from utils.subtitle_fixer import SubtitleEditUnavailable
    try:
        res = await asyncio.to_thread(
            _apply_fixes_and_persist, project_name, episode_name, track, lang_code, primary_code
        )
    except SubtitleEditUnavailable as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SubtitleEdit fixes failed: {e}")

    data_file = storage.episode_dir(project_name, episode_name) / "data.json"
    etag = _etag_for(data_file.stat()) if data_file.exists() else None
    headers = {"ETag": etag} if etag else {}
    return JSONResponse(content={"status": "success", "track": track, "etag": etag, **res}, headers=headers)


async def _process_batch_apply_fixes(job_id: str, project_name: str,
                                     episode_names: List[str], track: str, lang: Optional[str]):
    """Background job: apply SubtitleEdit fixes to many episodes' source, translation,
    or both. SE is a per-episode subprocess, so this runs off the request path."""
    import asyncio
    from utils.jobs_manager import update_job
    from utils.subtitle_fixer import SubtitleEditUnavailable

    proj_meta = storage.load_project_metadata(project_name) or {}
    primary_code = _lang_code(proj_meta.get("target_language", "Greek"))
    lang_code = _lang_code(lang) if lang else primary_code
    tracks = ["source", "target"] if track == "both" else [track]

    total = len(episode_names)
    update_job(job_id, status="running", progress=0.0,
               message=f"Applying SubtitleEdit fixes to {total} episode(s)...")

    results = []
    for i, ep_name in enumerate(episode_names):
        entry = {"name": ep_name, "tracks": {}, "skipped": [], "status": "ok"}
        try:
            for tr in tracks:
                if tr == "target":
                    ep = storage.load_episode(project_name, ep_name)
                    rows = (ep or {}).get("data", [])
                    has_target = any(
                        ((l.get("translations") or {}).get(lang_code))
                        or (lang_code == primary_code and l.get("translated"))
                        for l in rows
                    )
                    if not has_target:
                        entry["skipped"].append("translation (none yet)")
                        continue
                res = await asyncio.to_thread(
                    _apply_fixes_and_persist, project_name, ep_name, tr, lang_code, primary_code
                )
                s = res["stats"]
                entry["tracks"][tr] = {"before": s["before"], "after": s["after"]}
            applied = ", ".join(f"{t} {v['before']}→{v['after']}" for t, v in entry["tracks"].items()) or "nothing"
            skip = f" (skipped {', '.join(entry['skipped'])})" if entry["skipped"] else ""
            update_job(job_id, log=f"{ep_name}: {applied}{skip}")
        except SubtitleEditUnavailable as e:
            update_job(job_id, status="failed", message=str(e), log=str(e))
            return
        except Exception as e:
            entry["status"] = f"failed: {e}"
            update_job(job_id, log=f"{ep_name}: failed — {e}")
        results.append(entry)
        update_job(job_id, progress=((i + 1) / total) * 100.0, message=f"Fixed {i + 1}/{total} episode(s)")

    ok = sum(1 for r in results if r.get("status") == "ok")
    update_job(job_id, status="completed", progress=100.0,
               message=f"Applied SubtitleEdit fixes to {ok}/{total} episode(s)",
               result={"results": results, "ok": ok, "total": total})


@router.post("/projects/{project_name}/batch-apply-fixes")
async def batch_apply_fixes(project_name: str, background_tasks: BackgroundTasks, request: BatchApplyFixesRequest):
    """Apply SubtitleEdit common fixes to many episodes' source, translation, or both,
    as a background job. ``request.track`` may be 'source', 'target', or 'both'."""
    track = (request.track or "source").lower()
    if track not in ("source", "target", "both"):
        raise HTTPException(status_code=400, detail="track must be 'source', 'target', or 'both'")
    episode_names = request.episode_names or []
    if not episode_names:
        raise HTTPException(status_code=400, detail="No episodes selected")

    from utils.subtitle_fixer import _se_executable
    if not _se_executable():
        raise HTTPException(status_code=400, detail="SubtitleEdit is not configured. Set its executable path in Settings.")

    job_id = create_job("apply_fixes", project_name=project_name)
    background_tasks.add_task(_process_batch_apply_fixes, job_id, project_name, episode_names, track, request.lang)
    return {"job_id": job_id}


def _delete_episode_translation(project_name: str, episode_name: str, delete_disk_file: Optional[bool] = None) -> Any:
    """Delete an episode's translation — clears in-app translated text and optionally deletes
    the exported target subtitles on disk. Returns False/None if episode not found.
    """
    import os
    import logging
    from pathlib import Path
    logger = logging.getLogger(__name__)

    data = storage.load_episode(project_name, episode_name)
    if not data:
        return False if delete_disk_file is None else None

    for line in data.get("data", []):
        line["translated"] = ""
        line["translations"] = {}
        line.pop("from_tm", None)
        line.pop("needs_review", None)
        line.pop("review_issues", None)
        line.pop("review_scores", None)

    metadata = storage.load_episode_metadata(project_name, episode_name) or {}
    metadata["translated"] = False
    metadata["translation_status"] = "pending"
    metadata.pop("translation_failed", None)
    metadata.pop("translation_error", None)
    metadata.pop("translation_warning", None)

    # Detect existing target subtitle files on disk
    proj_meta = storage.load_project_metadata(project_name) or {}
    target_lang_name = proj_meta.get("target_language", "Greek")
    tgt_code = _lang_code(target_lang_name) or "el"

    candidate_paths = []
    if metadata.get("arr_target_path"):
        candidate_paths.append(metadata.get("arr_target_path"))
    for t in (metadata.get("arr_targets") or {}).values():
        if isinstance(t, dict) and t.get("target_path"):
            candidate_paths.append(t["target_path"])

    media_path = metadata.get("arr_media_path")
    if media_path:
        try:
            from services.path_resolver import path_resolver
            resolved_media = path_resolver.resolve(media_path)
            media_file = Path(resolved_media) if resolved_media else Path(media_path)
            candidate_paths.append(str(media_file.parent / f"{media_file.stem}.{tgt_code}.srt"))
            candidate_paths.append(str(media_file.parent / f"{media_file.stem}.{tgt_code}.ass"))
            candidate_paths.append(str(media_file.parent / f"{media_file.stem}.{tgt_code}.default.srt"))
            candidate_paths.append(str(media_file.parent / f"{media_file.stem}.{tgt_code}.forced.srt"))
            candidate_paths.append(str(media_file.parent / f"{media_file.stem}.{tgt_code}.forced.ass"))
        except Exception:
            pass

    for cp in list(candidate_paths):
        if str(cp).endswith(".ass"):
            candidate_paths.append(str(Path(cp).with_suffix(".srt")))

    candidate_paths.append(str(storage.episode_dir(project_name, episode_name) / f"{episode_name}.{tgt_code}.srt"))

    detected_disk_files = []
    seen = set()
    for cp in candidate_paths:
        if not cp:
            continue
        try:
            norm = os.path.normcase(os.path.abspath(str(cp)))
            if norm in seen:
                continue
            seen.add(norm)
            if os.path.exists(str(cp)):
                detected_disk_files.append(str(cp))
        except Exception:
            pass

    disk_file_ownership = {}
    try:
        from services.autopilot import ownership
        ledger = getattr(ownership, "OwnershipLedger", None)
        ledger_inst = ledger() if ledger else None
    except Exception:
        ownership = None
        ledger_inst = None

    for path_str in detected_disk_files:
        is_ours = False
        if ledger_inst and hasattr(ledger_inst, "is_ours"):
            try:
                is_ours = bool(ledger_inst.is_ours(path_str))
            except Exception:
                is_ours = False
        elif ownership and hasattr(ownership, "is_ours"):
            try:
                is_ours = bool(ownership.is_ours(path_str, metadata))
            except Exception:
                is_ours = False
        disk_file_ownership[path_str] = is_ours

    deleted_disk_files = []
    should_delete = bool(delete_disk_file) if delete_disk_file is not None else True
    if should_delete:
        for path_str in detected_disk_files:
            try:
                if os.path.exists(path_str):
                    os.remove(path_str)
                    deleted_disk_files.append(path_str)
                    if ownership and hasattr(ownership, "forget"):
                        try:
                            ownership.forget(path_str)
                        except Exception:
                            pass
                    logger.info(f"Deleted target subtitle file at {path_str}")
            except Exception as e:
                logger.error(f"Failed to delete target subtitle file {path_str}: {e}")

    # Remove target flags so background workers do not skip re-translation.
    metadata.pop("arr_has_target", None)
    metadata.pop("arr_target_path", None)
    metadata.pop("arr_targets", None)

    # Drop the checkpoint so a fresh translation starts clean.
    try:
        cp = storage.episode_dir(project_name, episode_name) / "checkpoint.json"
        if cp.exists():
            cp.unlink()
    except Exception:
        pass

    storage.save_episode(project_name, episode_name, data["data"], metadata)

    try:
        from utils import metadata_index
        metadata_index.set_review_count(project_name, episode_name, 0)
        metadata_index.upsert(project_name, episode_name, metadata)
    except Exception:
        pass

    if delete_disk_file is None:
        return True

    return {
        "status": "translation-deleted",
        "detected_disk_files": detected_disk_files,
        "deleted_disk_files": deleted_disk_files,
        "disk_file_ownership": disk_file_ownership,
    }


@router.delete("/projects/{project_name}/episodes/{episode_name}/translation")
@router.delete("/{project_name}/episodes/{episode_name}/translation")
async def delete_episode_translation(project_name: str, episode_name: str, delete_disk_file: bool = False):
    res = await asyncio.to_thread(_delete_episode_translation, project_name, episode_name, delete_disk_file=delete_disk_file)
    if res is None or res is False:
        raise HTTPException(status_code=404, detail="Episode not found")
    if isinstance(res, dict):
        return res
    return {
        "status": "translation-deleted",
        "detected_disk_files": [],
        "deleted_disk_files": [],
        "disk_file_ownership": {},
    }


@router.delete("/projects/{project_name}/episodes/{episode_name}")
async def delete_episode(project_name: str, episode_name: str, delete_disk_file: bool = False):
    """Dashboard delete = translation-only. The original subtitle stays intact (in-app
    and on disk); only the translation and its exported file are removed."""
    res = await asyncio.to_thread(_delete_episode_translation, project_name, episode_name, delete_disk_file=delete_disk_file)
    if res is None or res is False:
        raise HTTPException(status_code=404, detail="Episode not found")
    if isinstance(res, dict):
        return res
    return {"status": "translation-deleted"}


@router.post("/projects/{project_name}/episodes/{episode_name}/clear")
async def clear_episode_translation(project_name: str, episode_name: str, delete_disk_file: bool = False):
    res = await asyncio.to_thread(_delete_episode_translation, project_name, episode_name, delete_disk_file=delete_disk_file)
    if res is None or res is False:
        raise HTTPException(status_code=404, detail="Episode not found")
    if isinstance(res, dict):
        return res
    return {"status": "cleared"}


# ---------------------------------------------------------------------------
# Translate / Retranslate / Merge
# ---------------------------------------------------------------------------

@router.post("/projects/{project_name}/episodes/{episode_name}/retranslate")
async def retranslate_episode(
    project_name: str,
    episode_name: str,
    request: TranslateRequest
):
    validate_api_key()
    metadata = storage.load_project_metadata(project_name) or {}
    global_config = storage.load_global_config()
    from utils.model_resolver import resolve_model
    model = (
        request.model
        or resolve_model("translation", metadata)
    )
    
    timestamp = int(datetime.now(timezone.utc).timestamp())
    safe_model_tag = model.replace("/", "_").replace(":", "_").replace(".", "_")
    comparison_key = f"compare_{timestamp}_{safe_model_tag}"
    target_lang_name = request.target_language_name or metadata.get("target_language", "Greek")

    options = {
        "model": model,
        "comparison_mode": True,
        "comparison_key": comparison_key,
        "translation_type": "comparison",
        "target_language_name": target_lang_name,
        "enhance_glossary": request.enhance_glossary,
        "temperature": storage.get_project_setting(metadata, "temperature") or global_config.get("temperature"),
        "top_k": storage.get_project_setting(metadata, "top_k") or global_config.get("top_k"),
        "top_p": storage.get_project_setting(metadata, "top_p") or global_config.get("top_p")
    }
    
    from routers.pipeline import _pipeline_thread_lock
    from utils.translation_queue import TranslationQueue
    from services.queue_service import enqueue_translation, PRIORITY_MANUAL

    tq = TranslationQueue()
    with _pipeline_thread_lock:
        active_items = tq.get_all(limit=500)
        for item in active_items:
            if (
                str(item.get("project_name") or "").casefold() == project_name.casefold()
                and str(item.get("episode_name") or "").casefold() == episode_name.casefold()
            ):
                active_id = item["id"]
                return {
                    "job_id": f"bg_{active_id}",
                    "item_id": active_id,
                    "comparison_key": (item.get("options") or {}).get("comparison_key") or comparison_key,
                    "already_active": True
                }

        item_id = enqueue_translation(project_name, episode_name, PRIORITY_MANUAL, options)
    
    return {
        "job_id": f"bg_{item_id}",
        "item_id": item_id,
        "comparison_key": comparison_key
    }


@router.post("/projects/{project_name}/episodes/{episode_name}/retranslate-line")
async def retranslate_single_line(
    project_name: str,
    episode_name: str,
    request: Dict[str, Any]
):
    validate_api_key()
    line_index = int(request.get("line_index", 0))
    new_text = request.get("new_text")
    model = request.get("model")

    ep = storage.load_episode(project_name, episode_name)
    if not ep or not ep.get("data"):
        raise HTTPException(status_code=404, detail="Episode not found")

    data = ep["data"]
    if line_index < 0 or line_index >= len(data):
        raise HTTPException(status_code=400, detail="Invalid line_index")

    entry = data[line_index]
    proj_meta = storage.load_project_metadata(project_name) or {}
    primary_code = _lang_code(proj_meta.get("target_language", "Greek"))
    lang_code = _lang_code(request.get("lang")) if request.get("lang") else primary_code

    if new_text is None:
        from utils.model_resolver import resolve_model
        effective_model = model or resolve_model("translation", proj_meta)
        target_lang_name = proj_meta.get("target_language", "Greek")

        from adk_agents.translator_agent import build_translation_prompt
        from adk_agents import create_translation_pipeline
        from adk_config import get_ephemeral_session_service
        from google.adk.runners import types as adk_types, Runner as _Runner
        from utils.llm_utils import strip_reasoning_blocks
        from utils.structured_output import parse_structured
        from uuid import uuid4

        glossary = proj_meta.get("glossary", {"terms": []})
        context_guide = proj_meta.get("context_guide", "")

        prompt = build_translation_prompt(
            [entry.get("original", "")],
            target_lang_name,
            context_hint="",
            few_shot_context="",
            priority_glossary="",
            character_context="",
            episode_context="",
            conformance_hint="",
            negative_examples="",
            lookahead_line="",
        )

        shared_agent = create_translation_pipeline(
            project_name=project_name,
            target_language=target_lang_name,
            glossary=glossary,
            context_guide=context_guide,
            cartographer_model=effective_model,
            translator_model=effective_model,
            skip_glossary_step=True,
        )
        session_id = f"single_{uuid4().hex[:8]}"
        eph_svc = get_ephemeral_session_service()
        runner = _Runner(agent=shared_agent, app_name=f"Omnisub_{session_id}", session_service=eph_svc)
        await eph_svc.create_session(session_id=session_id, user_id="default_user", app_name=f"Omnisub_{session_id}")

        resp_text = ""
        async for event in runner.run_async(
            user_id="default_user",
            session_id=session_id,
            new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=prompt)])
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        resp_text += part.text

        clean_resp = strip_reasoning_blocks(resp_text)
        parsed = parse_structured(clean_resp, expected_count=1)
        if parsed and len(parsed) > 0:
            new_text = parsed[0]["text"]
        else:
            new_text = clean_resp.strip()

    old_translated = entry.get("translated", "")
    if old_translated and old_translated != new_text:
        entry["previous_translated"] = old_translated
    entry["translated"] = new_text
    entry["retranslated_text"] = new_text

    if "translations" not in entry:
        entry["translations"] = {}
    entry["translations"][lang_code] = new_text
    if lang_code == primary_code:
        entry["translations"][primary_code] = new_text

    ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}
    storage.save_episode(project_name, episode_name, data, ep_meta)

    data_file = storage.episode_dir(project_name, episode_name) / "data.json"
    etag = _etag_for(data_file.stat()) if data_file.exists() else None

    try:
        from services.translation_service import auto_export_translated_subtitle
        auto_export_translated_subtitle(project_name, episode_name, data, ep_meta, force=True)
    except Exception:
        pass

    return JSONResponse(
        content={
            "status": "success",
            "line_index": line_index,
            "translated": new_text,
            "previous_translated": entry.get("previous_translated"),
            "etag": etag
        },
        headers={"ETag": etag} if etag else {}
    )


@router.post("/projects/{project_name}/episodes/{episode_name}/merge")
async def merge_episode_translation(project_name: str, episode_name: str, request: MergeTranslationRequest):
    data = storage.load_episode(project_name, episode_name)
    if not data:
        raise HTTPException(status_code=404, detail="Episode not found")

    for idx_str, text in request.selected_lines.items():
        try:
            idx = int(idx_str)
            if 0 <= idx < len(data["data"]):
                data["data"][idx]["translated"] = text
        except (ValueError, TypeError):
            continue

    metadata = storage.load_episode_metadata(project_name, episode_name) or {}
    metadata["translated"] = True

    storage.save_episode(project_name, episode_name, data["data"], metadata)
    auto_export_translated_subtitle(project_name, episode_name, data["data"], metadata, force=True)

    try:
        from services.agent_task_service import agent_task_service
        agent_task_service.mark_task_completed(project_name, episode_name)
    except Exception:
        pass

    return {"status": "merged"}


# ---------------------------------------------------------------------------
# Export to Original Path (manual trigger)
# ---------------------------------------------------------------------------

@router.post("/projects/{project_name}/episodes/{episode_name}/export-to-path")
async def export_episode_to_path(project_name: str, episode_name: str):
    """Manually trigger export of the translated subtitle next to its source media file."""
    ep_data = storage.load_episode(project_name, episode_name)
    if not ep_data:
        raise HTTPException(status_code=404, detail="Episode not found")

    ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}
    if not any(line.get("translated") for line in ep_data.get("data", [])):
        raise HTTPException(status_code=400, detail="Episode has no translated content")

    media_path = ep_meta.get("arr_media_path")
    if not media_path:
        raise HTTPException(status_code=400, detail="No media path recorded for this episode — cannot determine export location")

    try:
        exported_path = auto_export_translated_subtitle(project_name, episode_name, ep_data["data"], ep_meta, force=True)
        if not exported_path:
            # The exporter logs the cause and swallows it (background paths must not die on
            # one bad episode) — but a manual export must never report a success that
            # didn't happen. The usual cause is a stale/unreachable arr_media_path.
            raise HTTPException(
                status_code=500,
                detail=f"Export failed — could not write next to {media_path}. "
                       "The media path may be stale or unreachable; re-sync the project.",
            )
        return {"status": "exported", "path": exported_path}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")


@router.post("/projects/{project_name}/batch-export-to-path")
async def batch_export_to_path(project_name: str, request: BatchDownloadRequest):
    """Manually trigger export for multiple episodes to their original media paths."""
    episode_names = request.episodes or storage.list_episodes(project_name)
    exported = []
    failed = []

    for ep_name in episode_names:
        try:
            ep_data = storage.load_episode(project_name, ep_name)
            if not ep_data:
                failed.append({"name": ep_name, "reason": "not found"})
                continue

            ep_meta = storage.load_episode_metadata(project_name, ep_name) or {}
            if not any(line.get("translated") for line in ep_data.get("data", [])):
                failed.append({"name": ep_name, "reason": "no translation"})
                continue

            media_path = ep_meta.get("arr_media_path")
            if not media_path:
                failed.append({"name": ep_name, "reason": "no media path"})
                continue

            exported_path = auto_export_translated_subtitle(project_name, ep_name, ep_data["data"], ep_meta, force=True)
            if not exported_path:
                failed.append({"name": ep_name, "reason": f"could not write next to {media_path} (stale/unreachable media path)"})
                continue
            exported.append(ep_name)
        except Exception as e:
            failed.append({"name": ep_name, "reason": str(e)})

    return {
        "exported": exported,
        "failed": failed,
        "total": len(episode_names)
    }


# ---------------------------------------------------------------------------
# Browser Download (single + batch ZIP)
# ---------------------------------------------------------------------------

@router.get("/projects/{project_name}/episodes/{episode_name}/download")
async def download_single_episode(project_name: str, episode_name: str):
    """Download a single translated episode as an SRT file.

    Uses the original source filename (adjusted language tag).
    Applies SubtitleEdit fixes if enabled.
    """
    proj_meta = storage.load_project_metadata(project_name)
    ep_data = storage.load_episode(project_name, episode_name)
    if not proj_meta or not ep_data:
        raise HTTPException(status_code=404, detail="Project or episode not found")

    ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}
    global_config = storage.load_global_config()

    lang_code = _lang_code(proj_meta.get("target_language", "English"))
    filename = _output_filename(episode_name, ep_meta, lang_code)
    srt_content = _prepare_srt_content(project_name, episode_name, ep_data, global_config, lang_code)

    return StreamingResponse(
        BytesIO(srt_content.encode('utf-8-sig')),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.post("/projects/{project_name}/batch-download")
async def batch_download(project_name: str, request: BatchDownloadRequest):
    """Download multiple translated episodes as a ZIP archive.

    Each file inside the ZIP uses the original source filename with the target
    language tag.  SubtitleEdit fixes are applied per-file if enabled.
    """
    proj_meta = storage.load_project_metadata(project_name)
    if not proj_meta:
        raise HTTPException(status_code=404, detail="Project not found")

    episode_names = request.episodes or storage.list_episodes(project_name)
    lang_code = _lang_code(proj_meta.get("target_language", "English"))
    global_config = storage.load_global_config()

    buffer = BytesIO()
    with ZipFile(buffer, 'w') as zf:
        for ep_name in episode_names:
            ep_data = storage.load_episode(project_name, ep_name)
            if not ep_data:
                continue
            ep_meta = storage.load_episode_metadata(project_name, ep_name) or {}
            filename = _output_filename(ep_name, ep_meta, lang_code)
            srt_content = _prepare_srt_content(project_name, ep_name, ep_data, global_config, lang_code)
            zf.writestr(filename, srt_content.encode('utf-8-sig'))

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{project_name}_export.zip"'}
    )
