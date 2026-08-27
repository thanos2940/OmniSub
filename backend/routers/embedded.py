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


@router.get("/projects/{project_name}/episodes/{episode_name}/embedded/probe")
async def probe_episode_embedded(project_name: str, episode_name: str, force_refresh: bool = False):
    """Probe the media container linked to an episode and return diagnostic analysis of all subtitle tracks."""
    proj_meta = storage.load_project_metadata(project_name)
    if not proj_meta:
        raise HTTPException(status_code=404, detail="Project not found")

    ep_meta = storage.load_episode_metadata(project_name, episode_name)
    if ep_meta is None:
        if not storage.episode_exists(project_name, episode_name) if hasattr(storage, "episode_exists") else not (storage.episode_dir(project_name, episode_name) / "data.json").exists():
            raise HTTPException(status_code=404, detail="Episode not found")
        ep_meta = {}

    media_path = ep_meta.get("arr_media_path") or ep_meta.get("bazarr_media_path")
    if not media_path or not Path(media_path).exists():
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
        "ass_tracks_count": analysis["ass_tracks_count"],
        "image_tracks_count": analysis["image_tracks_count"],
        "has_candidate": analysis["has_candidate"],
    }


@router.post("/projects/{project_name}/episodes/{episode_name}/embedded/extract")
async def extract_episode_embedded(project_name: str, episode_name: str, request: ExtractEmbeddedRequest):
    """Manually extract a chosen (or recommended) embedded subtitle track from the episode's media file."""
    proj_meta = storage.load_project_metadata(project_name)
    if not proj_meta:
        raise HTTPException(status_code=404, detail="Project not found")

    ep_meta = storage.load_episode_metadata(project_name, episode_name)
    if ep_meta is None:
        if not storage.episode_exists(project_name, episode_name) if hasattr(storage, "episode_exists") else not (storage.episode_dir(project_name, episode_name) / "data.json").exists():
            raise HTTPException(status_code=404, detail="Episode not found")
        ep_meta = {}

    media_path = ep_meta.get("arr_media_path") or ep_meta.get("bazarr_media_path")
    if not media_path or not Path(media_path).exists():
        raise HTTPException(status_code=400, detail=f"No media file linked or reachable for {episode_name}.")

    tools, global_config = _get_tools_and_config()
    if not tools:
        raise HTTPException(status_code=400, detail="ffmpeg / ffprobe not found. Configure ffmpeg_path in Settings.")

    loop = asyncio.get_running_loop()
    fingerprint = MediaSyncEngine._fingerprint(media_path)
    cached = media_probe_cache.get(media_path, fingerprint)
    if cached is not None:
        tracks = [embedded_subs.SubtitleTrack.from_dict(t) for t in cached]
    else:
        tracks = await loop.run_in_executor(None, embedded_subs.probe_subtitle_tracks, media_path, tools)
        media_probe_cache.put(media_path, fingerprint, embedded_subs.describe_candidates(tracks))

    if not tracks:
        raise HTTPException(status_code=400, detail="No subtitle tracks found in media file.")

    source_lang, keywords, _ = _get_project_embedded_settings(proj_meta, global_config)

    # Determine track to extract
    chosen_track: Optional[embedded_subs.SubtitleTrack] = None
    if request.stream_index is not None:
        for t in tracks:
            if t.index == request.stream_index:
                chosen_track = t
                break
        if not chosen_track:
            raise HTTPException(status_code=400, detail=f"Stream index {request.stream_index} not found in media file.")
    else:
        chosen_track = embedded_subs.select_track(tracks, source_lang, keywords)
        if not chosen_track:
            image_count = sum(1 for t in tracks if t.is_image)
            detail = f"No usable subtitle track found in media file. ({image_count} image track(s) found requiring OCR)."
            raise HTTPException(status_code=400, detail=detail)

    sidecar = embedded_subs.sidecar_path_for(media_path, source_lang, ext=chosen_track.output_format)

    # Extract or reuse existing sidecar if not forced
    content = ""
    if sidecar.exists() and not request.force:
        content = await loop.run_in_executor(None, MediaSyncEngine._read_file, str(sidecar))
        if not content:
            # Re-extract if unreadable
            content = await embedded_subs.extract_track(media_path, chosen_track, tools=tools, config=global_config)
            await loop.run_in_executor(None, embedded_subs.write_sidecar, sidecar, content)
    else:
        content = await embedded_subs.extract_track(media_path, chosen_track, tools=tools, config=global_config)
        if not embedded_subs.looks_like_usable_sub(content, ext=chosen_track.output_format):
            raise HTTPException(status_code=400, detail=f"Extracted stream {chosen_track.index} [{chosen_track.output_format.upper()}] contains no dialogue events.")
        await loop.run_in_executor(None, embedded_subs.write_sidecar, sidecar, content)

    # Scan for target subs
    tgt_code = to_code(proj_meta.get("target_language", "Greek"))
    scanner = SubtitleScannerService(source_lang_code=source_lang, target_lang_code=tgt_code)
    scan = await loop.run_in_executor(None, scanner.scan_media_file, media_path)

    # Migrate SRT to [srt] sibling if requested and format flipped
    migrated_sibling = None
    if request.migrate_srt and ep_meta:
        migrated_sibling = await loop.run_in_executor(
            None, MediaSyncEngine._migrate_primary_format_flip,
            project_name, episode_name, scan, ep_meta,
        )

    target_content = None
    if scan.has_target_sub and scan.target_sub_path and not (ep_meta and ep_meta.get("translated")):
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

    def _do_import():
        return import_and_clean_srt(
            project_name=project_name,
            episode_name=episode_name,
            raw_srt_content=content,
            filename=sidecar.name,
            fingerprint=sidecar_fingerprint,
            target_srt_content=target_content,
            extra_metadata=extra_metadata,
        )

    res = await loop.run_in_executor(None, _do_import)

    try:
        await loop.run_in_executor(None, metadata_index.update_episode_metadata, project_name, episode_name)
    except Exception:
        pass

    if request.auto_translate:
        from services.queue_service import enqueue_translation, PRIORITY_MANUAL
        enqueue_translation(project_name, episode_name, PRIORITY_MANUAL)

    logger.info(f"Successfully extracted stream {chosen_track.index} for {project_name}/{episode_name} -> {sidecar}")

    return {
        "status": "success",
        "episode_name": episode_name,
        "stream_index": chosen_track.index,
        "track_title": chosen_track.title or "untitled",
        "line_count": res.get("line_count", 0),
        "sidecar_path": str(sidecar),
        "migrated_sibling": migrated_sibling,
    }


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
        media_path = meta.get("arr_media_path") or meta.get("bazarr_media_path")
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
        update_job(job_id, status="running", progress=0.0, message="Starting embedded ASS extraction batch...")
        proj_meta = storage.load_project_metadata(project_name)
        if not proj_meta:
            update_job(job_id, status="failed", message="Project not found")
            return

        tools, global_config = _get_tools_and_config()
        if not tools:
            update_job(job_id, status="failed", message="ffmpeg / ffprobe not found. Configure in Settings.")
            return

        source_lang, keywords, prefer_ass = _get_project_embedded_settings(proj_meta, global_config)

        all_episodes = storage.list_episodes_with_metadata(project_name)
        target_list = episode_names or [e["name"] for e in all_episodes]
        total = len(target_list)

        update_job(job_id, log=f"Scanning {total} episode(s) for embedded ASS tracks...")

        extracted = 0
        skipped = 0
        failed = 0
        results = []

        loop = asyncio.get_running_loop()

        for idx, ep_name in enumerate(target_list):
            ep_meta = storage.load_episode_metadata(project_name, ep_name)
            if not ep_meta:
                skipped += 1
                continue

            media_path = ep_meta.get("arr_media_path") or ep_meta.get("bazarr_media_path")
            if not media_path or not Path(media_path).exists():
                update_job(job_id, log=f"[{ep_name}] Skipped: no reachable media file.")
                skipped += 1
                continue

            orig_fmt = (ep_meta.get("original_format") or ep_meta.get("original_extension") or "srt").lstrip(".").lower()
            if orig_fmt in ("ass", "ssa") and not force:
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

        try:
            await loop.run_in_executor(None, metadata_index.backfill)
        except Exception:
            pass

        update_job(
            job_id,
            status="completed",
            progress=100.0,
            message=f"Batch extraction complete: {extracted} extracted, {skipped} skipped, {failed} failed.",
            result={"extracted": extracted, "skipped": skipped, "failed": failed, "results": results},
        )
    except Exception as e:
        logger.error(f"Batch extraction failed for {project_name}: {e}", exc_info=True)
        update_job(job_id, status="failed", message=f"Batch extraction failed: {e}", log=str(e))


@router.post("/projects/{project_name}/embedded/extract-all")
async def batch_extract_embedded_endpoint(project_name: str, background_tasks: BackgroundTasks, request: BatchExtractEmbeddedRequest):
    """Trigger a batch extraction job for embedded ASS tracks across multiple episodes."""
    proj_meta = storage.load_project_metadata(project_name)
    if not proj_meta:
        raise HTTPException(status_code=404, detail="Project not found")

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
