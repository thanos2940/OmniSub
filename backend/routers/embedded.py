import os
import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, BackgroundTasks, Query

from utils import storage, media_probe_cache, metadata_index
from utils.jobs_manager import create_job, update_job, JobStatus, jobs
from utils.language_codes import to_code
from integrations import embedded_subs
from integrations.media_sync_engine import MediaSyncEngine, prefer_ass_for_project
from integrations.subtitle_scanner import SubtitleScannerService
from integrations.path_resolver import PathResolver
from utils.source_clean import import_and_clean_srt
from routers.schemas import ExtractEmbeddedRequest, BatchExtractEmbeddedRequest, ProbeMediaTestRequest

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_tools_and_config():
    global_config = storage.load_global_config()
    tools = embedded_subs.resolve_tools(global_config)
    return tools, global_config


def _get_project_embedded_settings(proj_meta: Dict, global_config: Dict):
    proj_settings = proj_meta.get("settings") or {}
    source_lang = global_config.get("arr_source_language", "en")
    
    keywords_raw = proj_settings.get("embedded_deprioritize_keywords") or global_config.get("embedded_deprioritize_keywords")
    keywords = embedded_subs.parse_keywords(keywords_raw)
    
    prefer_ass = prefer_ass_for_project(proj_meta)
    return source_lang, keywords, prefer_ass


def _resolve_and_verify_media_file_sync(resolver: PathResolver, raw_path: str, ep_name: str) -> Optional[str]:
    """Synchronous media file resolution with existence checks (INT-P1-01)."""
    try:
        found = resolver.find_media_file(raw_path, episode_name=ep_name)
        if found:
            return found
        resolved = resolver.resolve(raw_path)
        if Path(resolved).exists():
            return resolved
    except OSError:
        pass
    return None


@router.get("/projects/{project_name}/episodes/{episode_name}/embedded/probe")
async def probe_episode_embedded(project_name: str, episode_name: str, force_refresh: bool = False):
    """Probe the media container linked to an episode and return diagnostic analysis of all subtitle tracks."""
    proj_meta = storage.load_project_metadata(project_name)
    if not proj_meta:
        raise HTTPException(status_code=404, detail="Project not found")

    ep_meta = storage.load_episode_metadata(project_name, episode_name)
    if ep_meta is None:
        if not storage.episode_dir(project_name, episode_name).exists():
            raise HTTPException(status_code=404, detail="Episode not found")
        ep_meta = {}

    media_path = ep_meta.get("arr_media_path")
    if media_path:
        cfg = storage.load_global_config()
        resolver = PathResolver(cfg.get("arr_path_mappings", []))
        try:
            resolved_media = await asyncio.wait_for(
                asyncio.to_thread(_resolve_and_verify_media_file_sync, resolver, media_path, episode_name),
                timeout=5.0,
            )
            if resolved_media:
                media_path = resolved_media
                if ep_meta.get("arr_media_path") != media_path:
                    ep_meta["arr_media_path"] = media_path
                    storage.save_episode_metadata(project_name, episode_name, ep_meta, update_stats=False)
            else:
                media_path = None
        except asyncio.TimeoutError:
            logger.warning(f"Timeout probing media file for episode {episode_name} at {media_path} (SMB offline)")
            media_path = None

    if not media_path:
        return {
            "has_media": False,
            "media_path": media_path,
            "media_filename": Path(media_path).name if media_path else None,
            "error": "No media file linked to this episode or media file is unreachable on disk.",
            "tools_available": bool(embedded_subs.resolve_tools(storage.load_global_config())),
            "tracks": [],
            "current_format": (ep_meta.get("original_format") or ep_meta.get("original_extension") or "srt").lstrip(".").lower(),
        }

    tools, global_config = _get_tools_and_config()
    if not tools:
        return {
            "has_media": True,
            "media_path": media_path,
            "media_filename": Path(media_path).name,
            "tools_available": False,
            "error": "ffmpeg / ffprobe not found. Configure ffmpeg_path in Settings or install on PATH.",
            "tracks": [],
            "current_format": (ep_meta.get("original_format") or ep_meta.get("original_extension") or "srt").lstrip(".").lower(),
        }

    fingerprint = MediaSyncEngine._fingerprint(media_path)
    cached = None if force_refresh else media_probe_cache.get(media_path, fingerprint)

    if cached is not None:
        tracks = [embedded_subs.SubtitleTrack.from_dict(t) for t in cached]
    else:
        loop = asyncio.get_running_loop()
        tracks = await loop.run_in_executor(None, embedded_subs.probe_subtitle_tracks, media_path, tools)
        media_probe_cache.put(media_path, fingerprint, embedded_subs.describe_candidates(tracks))

    source_lang, keywords, prefer_ass = _get_project_embedded_settings(proj_meta, global_config)
    analysis = embedded_subs.analyze_tracks_for_ui(tracks, source_lang, keywords)

    sidecar = embedded_subs.sidecar_path_for(media_path, source_lang)
    sidecar_exists = sidecar.exists()

    current_format = (ep_meta.get("original_format") or ep_meta.get("original_extension") or "srt").lstrip(".").lower()
    is_embedded_extracted = bool(ep_meta.get("embedded_extracted"))
    extracted_track = ep_meta.get("embedded_track")

    return {
        "has_media": True,
        "media_path": media_path,
        "media_filename": Path(media_path).name,
        "tools_available": True,
        "ffmpeg_path": tools.ffmpeg,
        "ffprobe_path": tools.ffprobe,
        "sidecar_exists": sidecar_exists,
        "sidecar_path": str(sidecar),
        "current_format": current_format,
        "is_embedded_extracted": is_embedded_extracted,
        "extracted_track": extracted_track,
        "source_lang_code": source_lang,
        "prefer_ass": prefer_ass,
        "tracks": analysis["tracks"],
        "recommended_stream_index": analysis["recommended_stream_index"],
        "total_tracks": analysis["total_tracks"],
        "ass_tracks_count": analysis.get("ass_tracks_count", 0),
        "srt_tracks_count": analysis.get("srt_tracks_count", 0),
        "text_tracks_count": analysis.get("text_tracks_count", 0),
        "image_tracks_count": analysis.get("image_tracks_count", 0),
        "has_candidate": analysis["has_candidate"],
    }


@router.post("/projects/{project_name}/episodes/{episode_name}/embedded/extract")
async def extract_episode_embedded(project_name: str, episode_name: str, request: ExtractEmbeddedRequest):
    """Manually extract a chosen (or recommended) embedded subtitle track from the episode's media file."""
    from services.embedded_extractor import extract_embedded_for_episode
    try:
        res = await extract_embedded_for_episode(
            project_name=project_name,
            episode_name=episode_name,
            stream_index=request.stream_index,
            force=request.force,
            migrate_srt=request.migrate_srt,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to extract embedded subtitle for {project_name}/{episode_name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Extraction error: {str(e)}")

    if request.auto_translate:
        from services.queue_service import enqueue_translation, PRIORITY_MANUAL
        enqueue_translation(project_name, episode_name, PRIORITY_MANUAL)

    return res



@router.get("/projects/{project_name}/embedded/status")
async def get_project_embedded_status(project_name: str):
    """Aggregate embedded subtitle status across all episodes in a project."""
    proj_meta = storage.load_project_metadata(project_name)
    if not proj_meta:
        raise HTTPException(status_code=404, detail="Project not found")

    tools, global_config = _get_tools_and_config()
    source_lang, keywords, prefer_ass = _get_project_embedded_settings(proj_meta, global_config)

    episodes = storage.list_episodes_with_metadata(project_name)
    total = len(episodes)
    with_media = 0
    ass_episodes = 0
    srt_episodes = 0
    extracted_count = 0
    extractable_candidates = []

    for ep in episodes:
        meta = ep.get("metadata") or {}
        media_path = meta.get("arr_media_path")
        orig_fmt = (meta.get("original_format") or meta.get("original_extension") or "srt").lstrip(".").lower()
        if orig_fmt in ("ass", "ssa"):
            ass_episodes += 1
        else:
            srt_episodes += 1

        if meta.get("embedded_extracted"):
            extracted_count += 1

        if media_path and Path(media_path).exists():
            with_media += 1
            if orig_fmt not in ("ass", "ssa") or prefer_ass:
                extractable_candidates.append(ep.get("name"))

    return {
        "project_name": project_name,
        "tools_available": bool(tools),
        "prefer_ass": prefer_ass,
        "total_episodes": total,
        "episodes_with_media": with_media,
        "ass_episodes": ass_episodes,
        "srt_episodes": srt_episodes,
        "embedded_extracted_count": extracted_count,
        "extractable_episodes": extractable_candidates,
    }


async def _run_batch_extract(job_id: str, project_name: str, episode_names: Optional[List[str]], force: bool, migrate_srt: bool):
    """Background task to extract embedded ASS tracks for multiple episodes."""
    try:
        if job_id in jobs and getattr(jobs[job_id], "cancelled", False):
            update_job(
                job_id,
                status="cancelled",
                message="Batch extraction cancelled by user.",
                log="[CANCELLED] Batch extraction cancelled by user before start.",
            )
            return

        update_job(job_id, status="running", progress=0.0, message="Starting embedded ASS extraction batch...")
        proj_meta = storage.load_project_metadata(project_name)
        if not proj_meta:
            if not (job_id in jobs and getattr(jobs[job_id], "cancelled", False)):
                update_job(job_id, status="failed", message="Project not found")
            return

        tools, global_config = _get_tools_and_config()
        if not tools:
            if not (job_id in jobs and getattr(jobs[job_id], "cancelled", False)):
                update_job(job_id, status="failed", message="ffmpeg / ffprobe not found. Configure in Settings.")
            return


        source_lang, keywords, prefer_ass = _get_project_embedded_settings(proj_meta, global_config)

        all_episodes = storage.list_episodes_with_metadata(project_name)
        if episode_names is not None:
            target_list = [ep for ep in episode_names if ep]
        else:
            target_list = [e["name"] for e in all_episodes]
        total = len(target_list)

        update_job(job_id, log=f"Scanning {total} episode(s) for embedded ASS tracks...")

        extracted = 0
        skipped = 0
        failed = 0
        results = []

        loop = asyncio.get_running_loop()

        from integrations.path_resolver import PathResolver
        cfg = storage.load_global_config()
        path_resolver = PathResolver(cfg.get("arr_path_mappings", []))

        for idx, ep_name in enumerate(target_list):
            if job_id in jobs and getattr(jobs[job_id], "cancelled", False):
                update_job(
                    job_id,
                    status="cancelled",
                    message=f"Batch extraction cancelled by user after {extracted} episode(s).",
                    log="[CANCELLED] Batch extraction cancelled by user.",
                )
                break

            ep_meta = storage.load_episode_metadata(project_name, ep_name)
            if not ep_meta:
                skipped += 1
                continue

            media_path = ep_meta.get("arr_media_path")
            if media_path:
                found = path_resolver.find_media_file(media_path, episode_name=ep_name)
                if found:
                    media_path = found
                    if ep_meta.get("arr_media_path") != media_path:
                        ep_meta["arr_media_path"] = media_path
                        storage.save_episode_metadata(project_name, ep_name, ep_meta, update_stats=False)
                else:
                    resolved = path_resolver.resolve(media_path)
                    if Path(resolved).exists():
                        media_path = resolved
                        ep_meta["arr_media_path"] = media_path
                        storage.save_episode_metadata(project_name, ep_name, ep_meta, update_stats=False)

            if not media_path or not Path(media_path).exists():
                update_job(job_id, log=f"[{ep_name}] Skipped: no reachable media file.")
                skipped += 1
                continue

            has_actual_sub = (ep_meta.get("has_source_sub", True) and ep_meta.get("line_count", 0) > 0) or storage.original_subtitle_exists(project_name, ep_name)
            orig_fmt = (ep_meta.get("original_format") or ep_meta.get("original_extension") or "srt").lstrip(".").lower()
            if has_actual_sub and orig_fmt in ("ass", "ssa") and not force:
                update_job(job_id, log=f"[{ep_name}] Skipped: already has .ass source.")
                skipped += 1
                continue


            # Probe media
            try:
                fingerprint = MediaSyncEngine._fingerprint(media_path)
                cached = media_probe_cache.get(media_path, fingerprint)
                if cached is not None:
                    tracks = [embedded_subs.SubtitleTrack.from_dict(t) for t in cached]
                else:
                    tracks = await loop.run_in_executor(None, embedded_subs.probe_subtitle_tracks, media_path, tools)
                    media_probe_cache.put(media_path, fingerprint, embedded_subs.describe_candidates(tracks))

                chosen_track = embedded_subs.select_track(tracks, source_lang, keywords, prefer_ass=prefer_ass)
                if not chosen_track:
                    update_job(job_id, log=f"[{ep_name}] No usable subtitle track found in {Path(media_path).name}.")
                    skipped += 1
                    continue

                sidecar = embedded_subs.sidecar_path_for(media_path, source_lang, ext=chosen_track.output_format)
                update_job(
                    job_id,
                    log=f"[{ep_name}] Extracting stream #{chosen_track.index} [{chosen_track.output_format.upper()}] "
                        f"({chosen_track.title or 'untitled'}, {chosen_track.frames or '?'} cues) from {Path(media_path).name}...",
                )

                content = await embedded_subs.extract_track(media_path, chosen_track, tools=tools, config=global_config)
                if not embedded_subs.looks_like_usable_sub(content, ext=chosen_track.output_format):
                    update_job(job_id, log=f"[{ep_name}] Extracted track had no dialogue events — skipped.")
                    skipped += 1
                    continue

                await loop.run_in_executor(None, embedded_subs.write_sidecar, sidecar, content)

                tgt_code = to_code(proj_meta.get("target_language", "Greek"))
                scanner = SubtitleScannerService(source_lang_code=source_lang, target_lang_code=tgt_code)
                scan = await loop.run_in_executor(None, scanner.scan_media_file, media_path)

                migrated_sibling = None
                if migrate_srt and ep_meta:
                    migrated_sibling = await loop.run_in_executor(
                        None, MediaSyncEngine._migrate_primary_format_flip,
                        project_name, ep_name, scan, ep_meta,
                    )
                    if migrated_sibling:
                        update_job(job_id, log=f"[{ep_name}] Preserved existing SRT translation at '{migrated_sibling}'.")

                target_content = None
                if scan.has_target_sub and scan.target_sub_path and not ep_meta.get("translated"):
                    target_content = await loop.run_in_executor(None, MediaSyncEngine._read_file, scan.target_sub_path)

                sidecar_fingerprint = MediaSyncEngine._fingerprint(str(sidecar))
                extra_metadata = {
                    "arr_source": True,
                    "arr_sub_path": str(sidecar),
                    "arr_media_path": media_path,
                    "arr_has_target": scan.has_target_sub,
                    "arr_target_path": scan.target_sub_path if scan.has_target_sub else None,
                    "embedded_extracted": True,
                    "embedded_track": chosen_track.to_dict(),
                    "embedded_track_candidates": embedded_subs.describe_candidates(tracks),
                    "embedded_extracted_at": datetime.now(timezone.utc).isoformat(),
                }

                def _do_batch_import(pn=project_name, en=ep_name, cnt=content, fn=sidecar.name, fp=sidecar_fingerprint, tc=target_content, em=extra_metadata):
                    return import_and_clean_srt(
                        project_name=pn,
                        episode_name=en,
                        raw_srt_content=cnt,
                        filename=fn,
                        fingerprint=fp,
                        target_srt_content=tc,
                        extra_metadata=em,
                    )

                res = await loop.run_in_executor(None, _do_batch_import)

                extracted += 1
                results.append({"name": ep_name, "stream": chosen_track.index, "cues": res.get("line_count", 0)})
                update_job(job_id, log=f"[{ep_name}] ✓ Successfully extracted and imported {res.get('line_count', 0)} cues.")
            except Exception as e:
                failed += 1
                logger.error(f"Failed to extract embedded ASS for {project_name}/{ep_name}: {e}", exc_info=True)
                update_job(job_id, log=f"[{ep_name}] ✗ Error: {e}")

            progress_pct = ((idx + 1) / total) * 100.0
            update_job(job_id, progress=progress_pct, message=f"Processed {idx + 1}/{total} episodes ({extracted} extracted)")

            if job_id in jobs and getattr(jobs[job_id], "cancelled", False):
                update_job(
                    job_id,
                    status="cancelled",
                    message=f"Batch extraction cancelled by user after {extracted} episode(s).",
                    log="[CANCELLED] Batch extraction cancelled by user.",
                )
                break

        if job_id in jobs and getattr(jobs[job_id], "cancelled", False):
            update_job(
                job_id,
                status="cancelled",
                message=f"Batch extraction cancelled by user after {extracted} episode(s).",
                log="[CANCELLED] Batch extraction cancelled by user.",
            )
            return

        try:
            await loop.run_in_executor(None, metadata_index.backfill)
        except Exception:
            pass

        storage._invalidate_episodes_list_cache(project_name)

        if job_id in jobs and getattr(jobs[job_id], "cancelled", False):
            update_job(
                job_id,
                status="cancelled",
                message=f"Batch extraction cancelled by user after {extracted} episode(s).",
                log="[CANCELLED] Batch extraction cancelled by user.",
            )
            return

        update_job(
            job_id,
            status="completed",
            progress=100.0,
            message=f"Batch extraction complete: {extracted} extracted, {skipped} skipped, {failed} failed.",
            result={"extracted": extracted, "skipped": skipped, "failed": failed, "results": results},
        )

    except Exception as e:
        if job_id in jobs and getattr(jobs[job_id], "cancelled", False):
            update_job(
                job_id,
                status="cancelled",
                message="Batch extraction cancelled by user.",
                log="[CANCELLED] Batch extraction cancelled by user.",
            )
            return
        logger.error(f"Batch extraction failed for {project_name}: {e}", exc_info=True)
        update_job(job_id, status="failed", message=f"Batch extraction failed: {e}", log=str(e))


@router.post("/projects/{project_name}/embedded/extract-all")
async def batch_extract_embedded_endpoint(project_name: str, background_tasks: BackgroundTasks, request: BatchExtractEmbeddedRequest):
    """Trigger a batch extraction job for embedded ASS tracks across multiple episodes."""
    proj_meta = storage.load_project_metadata(project_name)
    if not proj_meta:
        raise HTTPException(status_code=404, detail="Project not found")

    # Deduplicate: return active job ID if an extraction is already running for the project
    for existing_id, existing_job in list(jobs.items()):
        if (
            (existing_job.project_name or "").casefold() == project_name.casefold()
            and existing_id.startswith("batch_extract_embedded_")
            and existing_job.status in ("pending", "running")
            and not getattr(existing_job, "cancelled", False)
        ):
            return {"job_id": existing_id}

    job_id = create_job("batch_extract_embedded", project_name=project_name)
    background_tasks.add_task(
        _run_batch_extract,
        job_id,
        project_name,
        request.episode_names,
        request.force,
        request.migrate_srt,
    )
    return {"job_id": job_id}


@router.post("/api/settings/test-media-probe")
async def test_media_probe_endpoint(request: ProbeMediaTestRequest):
    """Diagnostic tool endpoint to test ffprobe on any arbitrary video file path from the settings UI."""
    media_path = (request.media_path or "").strip()
    if not media_path:
        raise HTTPException(status_code=400, detail="media_path is required")

    p = Path(media_path)
    if not p.exists():
        raise HTTPException(status_code=400, detail=f"File does not exist: {media_path}")

    tools, global_config = _get_tools_and_config()
    if not tools:
        raise HTTPException(status_code=400, detail="ffmpeg / ffprobe not found on system or settings.")

    loop = asyncio.get_running_loop()
    tracks = await loop.run_in_executor(None, embedded_subs.probe_subtitle_tracks, media_path, tools)

    source_lang = global_config.get("arr_source_language", "en")
    keywords = embedded_subs.parse_keywords(global_config.get("embedded_deprioritize_keywords"))
    analysis = embedded_subs.analyze_tracks_for_ui(tracks, source_lang, keywords)

    return {
        "media_path": media_path,
        "media_filename": p.name,
        "file_size_mb": round(p.stat().st_size / (1024 * 1024), 2),
        "tools": {
            "ffmpeg": tools.ffmpeg,
            "ffprobe": tools.ffprobe,
        },
        **analysis,
    }
