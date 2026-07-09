from io import BytesIO
from zipfile import ZipFile
from typing import List, Dict, Optional
from fastapi import APIRouter, HTTPException, UploadFile, BackgroundTasks
from fastapi.responses import StreamingResponse
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


@router.get("/projects/{project_name}/episodes/{episode_name}")
async def get_episode(project_name: str, episode_name: str):
    try:
        data = storage.load_episode(project_name, episode_name)
        if not data:
            raise HTTPException(status_code=404, detail="Episode not found")
        return data
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


@router.post("/projects/{project_name}/episodes/{episode_name}/save")
async def save_episode(project_name: str, episode_name: str, request: SaveEpisodeRequest):
    previous = storage.load_episode(project_name, episode_name)

    proj_meta = storage.load_project_metadata(project_name)
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

    storage.save_episode(project_name, episode_name, request.data)

    if previous and previous.get("data"):
        try:
            _record_user_edits(project_name, episode_name, previous["data"], request.data, lang=request.lang)
        except Exception:
            pass

    # Auto-export if this save includes translated lines
    exported_path = None
    if any(line.get("translated") or line.get("translations", {}).get(lang_code) for line in request.data):
        try:
            metadata = storage.load_episode_metadata(project_name, episode_name) or {}
            exported_path = auto_export_translated_subtitle(project_name, episode_name, request.data, metadata)
        except Exception:
            pass

    return {"status": "success", "exported_path": exported_path}


@router.post("/projects/{project_name}/episodes/{episode_name}/delete-lines")
async def delete_episode_lines(project_name: str, episode_name: str, request: DeleteLinesRequest):
    """Permanently remove subtitle lines from an episode.

    Keeps exports aligned by re-keying clean_to_orig_map for the surviving lines
    and recording the deleted original cue indexes in deleted_orig_indexes, which
    reconstruct_cleaned_srt skips entirely. Re-exports the on-disk subtitle so the
    deleted lines disappear there too.
    """
    ep = storage.load_episode(project_name, episode_name)
    if not ep:
        raise HTTPException(status_code=404, detail="Episode not found")

    data = ep.get("data", [])
    deleted = {i for i in request.indexes if 0 <= i < len(data)}
    if not deleted:
        raise HTTPException(status_code=400, detail="No valid line indexes to delete")

    metadata = storage.load_episode_metadata(project_name, episode_name) or {}

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

    storage.save_episode(project_name, episode_name, new_data, metadata)

    # Refresh the exported file on disk so the deleted lines disappear there too
    exported_path = None
    has_media = metadata.get("arr_media_path") or metadata.get("bazarr_media_path")
    if has_media and any(line.get("translated") for line in new_data):
        exported_path = auto_export_translated_subtitle(project_name, episode_name, new_data, metadata)

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
        cp = storage.PROJECTS_DIR / project_name / "episodes" / episode_name / "checkpoint.json"
        if cp.exists():
            cp.unlink()
    except Exception:
        pass

    storage.save_episode(project_name, episode_name, new_data, metadata)

    exported_path = None
    if any(e.get("translated") or (e.get("translations") or {}) for e in new_data):
        try:
            exported_path = auto_export_translated_subtitle(project_name, episode_name, new_data, metadata)
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

    return {"status": "success", "track": track, **res}


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


def _delete_episode_translation(project_name: str, episode_name: str) -> bool:
    """Delete only an episode's TRANSLATION — clears in-app translated text and deletes
    the exported target .srt — while leaving the original/source subtitle intact, both
    in-app and on disk. Returns False if the episode doesn't exist.

    This is what the dashboard's "delete" does: the app mirrors the source directory, so
    the original is never removed here (Sync reconciles originals against the directory).
    """
    import os
    import logging
    logger = logging.getLogger(__name__)

    data = storage.load_episode(project_name, episode_name)
    if not data:
        return False

    for line in data["data"]:
        line["translated"] = ""
        line["translations"] = {}

    metadata = storage.load_episode_metadata(project_name, episode_name) or {}
    metadata["translated"] = False
    metadata["translation_status"] = "pending"

    # Delete the exported translated .srt next to media (and any per-language targets) so
    # the output folder reflects that the translation is gone.
    target_paths = [metadata.get("arr_target_path")]
    for t in (metadata.get("arr_targets") or {}).values():
        if isinstance(t, dict) and t.get("target_path"):
            target_paths.append(t["target_path"])
    for target_path in target_paths:
        if target_path and os.path.exists(target_path):
            try:
                os.remove(target_path)
                logger.info(f"Deleted target subtitle file at {target_path}")
            except Exception as e:
                logger.error(f"Failed to delete target subtitle file {target_path}: {e}")

    # Remove target flags so background workers do not skip re-translation.
    metadata.pop("arr_has_target", None)
    metadata.pop("arr_target_path", None)
    metadata.pop("bazarr_has_target", None)
    metadata.pop("arr_targets", None)

    # Drop the checkpoint so a fresh translation starts clean.
    try:
        cp = storage.PROJECTS_DIR / project_name / "episodes" / episode_name / "checkpoint.json"
        if cp.exists():
            cp.unlink()
    except Exception:
        pass

    storage.save_episode(project_name, episode_name, data["data"], metadata)
    return True


@router.delete("/projects/{project_name}/episodes/{episode_name}")
async def delete_episode(project_name: str, episode_name: str):
    """Dashboard delete = translation-only. The original subtitle stays intact (in-app
    and on disk); only the translation and its exported file are removed."""
    if _delete_episode_translation(project_name, episode_name):
        return {"status": "translation-deleted"}
    raise HTTPException(status_code=404, detail="Episode not found")


@router.post("/projects/{project_name}/episodes/{episode_name}/clear")
async def clear_episode_translation(project_name: str, episode_name: str):
    if _delete_episode_translation(project_name, episode_name):
        return {"status": "cleared"}
    raise HTTPException(status_code=404, detail="Episode not found")


# ---------------------------------------------------------------------------
# Translate / Retranslate / Merge
# ---------------------------------------------------------------------------

@router.post("/projects/{project_name}/episodes/{episode_name}/retranslate")
async def retranslate_episode(
    project_name: str,
    episode_name: str,
    background_tasks: BackgroundTasks,
    request: TranslateRequest
):
    validate_api_key()
    job_id = create_job("retranslate_compare", project_name=project_name, episode_name=episode_name)
    background_tasks.add_task(
        _process_retranslation_for_comparison, job_id, project_name,
        episode_name, request.model
    )
    return {"job_id": job_id}


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
    auto_export_translated_subtitle(project_name, episode_name, data["data"], metadata)

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

    media_path = ep_meta.get("arr_media_path") or ep_meta.get("bazarr_media_path")
    if not media_path:
        raise HTTPException(status_code=400, detail="No media path recorded for this episode — cannot determine export location")

    try:
        auto_export_translated_subtitle(project_name, episode_name, ep_data["data"], ep_meta)
        target_path = ep_meta.get("arr_target_path", "")
        return {"status": "exported", "path": target_path}
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

            media_path = ep_meta.get("arr_media_path") or ep_meta.get("bazarr_media_path")
            if not media_path:
                failed.append({"name": ep_name, "reason": "no media path"})
                continue

            auto_export_translated_subtitle(project_name, ep_name, ep_data["data"], ep_meta)
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
