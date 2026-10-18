"""
Embedded Subtitle Extractor Service.

Provides standalone and batch extraction of embedded subtitle tracks (ASS/SRT)
from media video containers (MKV/MP4), used by routes, the background translation worker,
and the auto pipeline.
"""

import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone

from utils import storage, media_probe_cache, metadata_index
from utils.language_codes import to_code
from integrations import embedded_subs
from integrations.media_sync_engine import MediaSyncEngine, prefer_ass_for_project
from integrations.subtitle_scanner import SubtitleScannerService
from utils.source_clean import import_and_clean_srt

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


async def extract_embedded_for_episode(
    project_name: str,
    episode_name: str,
    stream_index: Optional[int] = None,
    force: bool = False,
    migrate_srt: bool = True
) -> Dict[str, Any]:
    """Extract embedded subtitle track for a single episode."""
    proj_meta = storage.load_project_metadata(project_name)
    if not proj_meta:
        raise ValueError(f"Project '{project_name}' not found.")

    ep_meta = storage.load_episode_metadata(project_name, episode_name)
    if ep_meta is None:
        if not storage.episode_dir(project_name, episode_name).exists():
            raise ValueError(f"Episode '{episode_name}' not found.")
        ep_meta = {}
    media_path = ep_meta.get("arr_media_path") or ep_meta.get("media_path")
    if media_path:
        from integrations.path_resolver import PathResolver
        from utils.storage import load_global_config
        cfg = load_global_config()
        resolver = PathResolver(cfg.get("arr_path_mappings", []))
        found = resolver.find_media_file(media_path, episode_name=episode_name)
        if found:
            media_path = found
            if ep_meta.get("arr_media_path") != media_path:
                ep_meta["arr_media_path"] = media_path
                storage.save_episode_metadata(project_name, episode_name, ep_meta, update_stats=False)
        else:
            resolved = resolver.resolve(media_path)
            if Path(resolved).exists():
                media_path = resolved
                ep_meta["arr_media_path"] = media_path
                storage.save_episode_metadata(project_name, episode_name, ep_meta, update_stats=False)

    if not media_path or not Path(media_path).exists():
        ep_dir = storage.episode_dir(project_name, episode_name)
        if ep_dir.exists():
            video_exts = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov"}
            for vf in ep_dir.iterdir():
                if vf.is_file() and vf.suffix.lower() in video_exts:
                    media_path = str(vf)
                    ep_meta["arr_media_path"] = media_path
                    storage.save_episode_metadata(project_name, episode_name, ep_meta, update_stats=False)
                    break
        if not media_path:
            # Fallback: search project directory for video files matching episode name
            proj_dir = storage.project_dir(project_name)
            if proj_dir.exists():
                video_exts = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov"}
                for vf in proj_dir.iterdir():
                    if vf.is_file() and vf.suffix.lower() in video_exts and episode_name.lower() in vf.stem.lower():
                        media_path = str(vf)
                        ep_meta["arr_media_path"] = media_path
                        storage.save_episode_metadata(project_name, episode_name, ep_meta, update_stats=False)
                        break

    if not media_path or not Path(media_path).exists():
        raise ValueError(f"No reachable media file linked for episode '{episode_name}'.")

    tools, global_config = _get_tools_and_config()
    if not tools:
        raise ValueError("ffmpeg / ffprobe not found. Configure ffmpeg_path in Settings.")

    loop = asyncio.get_running_loop()
    fingerprint = MediaSyncEngine._fingerprint(media_path)
    cached = None if force else media_probe_cache.get(media_path, fingerprint)
    if cached is not None:
        tracks = [embedded_subs.SubtitleTrack.from_dict(t) for t in cached]
    else:
        tracks = await loop.run_in_executor(None, embedded_subs.probe_subtitle_tracks, media_path, tools)
        media_probe_cache.put(media_path, fingerprint, embedded_subs.describe_candidates(tracks))

    if not tracks:
        raise ValueError(f"No subtitle tracks found in media file: {Path(media_path).name}")

    source_lang, keywords, prefer_ass = _get_project_embedded_settings(proj_meta, global_config)

    # Determine track
    chosen_track: Optional[embedded_subs.SubtitleTrack] = None
    if stream_index is not None:
        for t in tracks:
            if t.index == stream_index:
                chosen_track = t
                break
        if not chosen_track:
            raise ValueError(f"Stream index {stream_index} not found in media file.")
    else:
        chosen_track = embedded_subs.select_track(tracks, source_lang, keywords, prefer_ass=prefer_ass)
        if not chosen_track:
            raise ValueError(f"No usable subtitle track found for language '{source_lang}' in {Path(media_path).name}.")

    sidecar = embedded_subs.sidecar_path_for(media_path, source_lang, ext=chosen_track.output_format)

    # Extract or reuse existing sidecar
    content = ""
    if sidecar.exists() and not force:
        content = await loop.run_in_executor(None, MediaSyncEngine._read_file, str(sidecar))
        if not content:
            content = await embedded_subs.extract_track(media_path, chosen_track, tools=tools, config=global_config)
            await loop.run_in_executor(None, embedded_subs.write_sidecar, sidecar, content)
    else:
        content = await embedded_subs.extract_track(media_path, chosen_track, tools=tools, config=global_config)
        if not embedded_subs.looks_like_usable_sub(content, ext=chosen_track.output_format):
            raise ValueError(f"Extracted stream {chosen_track.index} [{chosen_track.output_format.upper()}] contains no dialogue events.")
        await loop.run_in_executor(None, embedded_subs.write_sidecar, sidecar, content)

    # Scan for target subs
    tgt_code = to_code(proj_meta.get("target_language", "Greek"))
    scanner = SubtitleScannerService(source_lang_code=source_lang, target_lang_code=tgt_code)
    scan = await loop.run_in_executor(None, scanner.scan_media_file, media_path)

    migrated_sibling = None
    if migrate_srt and ep_meta:
        migrated_sibling = await loop.run_in_executor(
            None, MediaSyncEngine._migrate_primary_format_flip,
            project_name, episode_name, scan, ep_meta,
        )

    target_content = None
    if scan.has_target_sub and scan.target_sub_path and not ep_meta.get("translated"):
        target_content = await loop.run_in_executor(None, MediaSyncEngine._read_file, scan.target_sub_path)

    sidecar_fingerprint = MediaSyncEngine._fingerprint(str(sidecar))
    extra_metadata = {
        "has_source_sub": True,
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
    storage._invalidate_episodes_list_cache(project_name)

    try:
        await loop.run_in_executor(None, metadata_index.update_episode_metadata, project_name, episode_name)
    except Exception:
        pass

    return {
        "status": "success",
        "episode_name": episode_name,
        "stream_index": chosen_track.index,
        "track_title": chosen_track.title or "untitled",
        "format": chosen_track.output_format,
        "line_count": res.get("line_count", 0),
        "sidecar_path": str(sidecar),
        "migrated_sibling": migrated_sibling,
    }


async def auto_extract_embedded_subs_for_episodes(
    project_name: str,
    episode_names: Optional[List[str]] = None,
    force: bool = False,
    migrate_srt: bool = True,
    progress_callback: Optional[Any] = None
) -> int:
    """Check video files for subtitles (external SRT/ASS, and embedded tracks),
    importing them for any episodes in project that lack subtitle cues.
    For anime projects (prefer_ass=True), prioritizes .ass subtitles.
    For standard projects, checks for .srt generally (and .ass if present).
    Returns the count of newly imported/extracted episodes.
    """
    proj_meta = storage.load_project_metadata(project_name)
    if not proj_meta:
        return 0

    all_eps = storage.list_episodes_with_metadata(project_name)
    if episode_names:
        target_set = set(episode_names)
        episodes_to_check = [e for e in all_eps if e.get("name") in target_set]
    else:
        episodes_to_check = all_eps

    extracted_count = 0
    loop = asyncio.get_running_loop()
    prefer_ass = prefer_ass_for_project(proj_meta)
    global_cfg = storage.load_global_config()
    source_lang = global_cfg.get("arr_source_language", "en")
    target_lang = proj_meta.get("target_language", "Greek")
    target_code = to_code(target_lang)

    for idx, ep_info in enumerate(episodes_to_check):
        ep_name = ep_info.get("name")
        meta = ep_info.get("metadata") or {}
        media_path = meta.get("arr_media_path") or ep_info.get("media_path")
        if not media_path:
            ep_dir = storage.episode_dir(project_name, ep_name)
            if ep_dir.exists():
                video_exts = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov"}
                for vf in ep_dir.iterdir():
                    if vf.is_file() and vf.suffix.lower() in video_exts:
                        media_path = str(vf)
                        meta["arr_media_path"] = media_path
                        storage.save_episode_metadata(project_name, ep_name, meta, update_stats=False)
                        break

        if not media_path or not Path(media_path).exists():
            continue

        has_sub = (
            meta.get("has_source_sub", True)
            and (meta.get("line_count", 0) > 0 or ep_info.get("line_count", 0) > 0)
        ) or storage.original_subtitle_exists(project_name, ep_name)

        has_ass = bool(meta.get("has_ass") or (meta.get("original_format") or "").lower() in ("ass", "ssa") or ep_info.get("has_ass"))

        # If it already has an ASS sub, or has sub and not anime, we're good
        if has_sub and (not prefer_ass or has_ass) and not force:
            continue

        scanner = SubtitleScannerService(source_lang_code=source_lang, target_lang_code=target_code, include_ass=True)
        scan = await loop.run_in_executor(None, scanner.scan_media_file, media_path)

        imported = False

        if prefer_ass:
            # For anime: check for external .ass first
            if scan.has_source_sub and scan.source_sub_path and Path(scan.source_sub_path).suffix.lower() in (".ass", ".ssa"):
                if progress_callback:
                    progress_callback(f"Found external ASS subtitle for {ep_name} ({Path(scan.source_sub_path).name}). Importing...")
                try:
                    content = await loop.run_in_executor(None, MediaSyncEngine._read_file, str(scan.source_sub_path))
                    if not content:
                        raise ValueError(f"External ASS file is empty or unreadable: {scan.source_sub_path}")
                    fp = MediaSyncEngine._fingerprint(scan.source_sub_path)
                    import_and_clean_srt(
                        project_name=project_name,
                        episode_name=ep_name,
                        raw_srt_content=content,
                        filename=Path(scan.source_sub_path).name,
                        fingerprint=fp,
                        extra_metadata={"has_source_sub": True, "arr_source": True, "arr_sub_path": scan.source_sub_path, "arr_media_path": media_path, "original_format": "ass"}
                    )
                    extracted_count += 1
                    imported = True
                    logger.info(f"Imported external ASS subtitle for {project_name}/{ep_name}")
                except Exception as e:
                    logger.warning(f"Failed to read external ASS subtitle for {ep_name}: {e}")

            # If no external ASS, try extracting embedded ASS from container
            if not imported:
                if progress_callback:
                    progress_callback(f"Checking video container for embedded ASS subtitle for {ep_name}...")
                try:
                    await extract_embedded_for_episode(
                        project_name=project_name,
                        episode_name=ep_name,
                        force=force,
                        migrate_srt=migrate_srt
                    )
                    extracted_count += 1
                    imported = True
                    logger.info(f"Auto-extracted embedded ASS subtitle for {project_name}/{ep_name}")
                except Exception as e:
                    logger.debug(f"Embedded ASS extraction skipped/failed for {ep_name}: {e}")

            # If still not imported, check external SRT
            if not imported and scan.has_source_sub and scan.source_sub_path and Path(scan.source_sub_path).suffix.lower() == ".srt":
                if progress_callback:
                    progress_callback(f"Found external SRT subtitle for {ep_name} ({Path(scan.source_sub_path).name}). Importing...")
                try:
                    content = await loop.run_in_executor(None, MediaSyncEngine._read_file, str(scan.source_sub_path))
                    if not content:
                        raise ValueError(f"External SRT file is empty or unreadable: {scan.source_sub_path}")
                    fp = MediaSyncEngine._fingerprint(scan.source_sub_path)
                    import_and_clean_srt(
                        project_name=project_name,
                        episode_name=ep_name,
                        raw_srt_content=content,
                        filename=Path(scan.source_sub_path).name,
                        fingerprint=fp,
                        extra_metadata={"has_source_sub": True, "arr_source": True, "arr_sub_path": scan.source_sub_path, "arr_media_path": media_path, "original_format": "srt"}
                    )
                    extracted_count += 1
                    imported = True
                    logger.info(f"Imported external SRT subtitle for {project_name}/{ep_name}")
                except Exception as e:
                    logger.warning(f"Failed to read external SRT subtitle for {ep_name}: {e}")

        else:
            # General / standard: check external subtitles first (SRT or ASS)
            if scan.has_source_sub and scan.source_sub_path and Path(scan.source_sub_path).exists():
                ext = Path(scan.source_sub_path).suffix.lower()
                if progress_callback:
                    progress_callback(f"Found external {ext.upper()} subtitle for {ep_name}. Importing...")
                try:
                    content = await loop.run_in_executor(None, MediaSyncEngine._read_file, str(scan.source_sub_path))
                    if not content:
                        raise ValueError(f"External subtitle file is empty or unreadable: {scan.source_sub_path}")
                    fp = MediaSyncEngine._fingerprint(scan.source_sub_path)
                    import_and_clean_srt(
                        project_name=project_name,
                        episode_name=ep_name,
                        raw_srt_content=content,
                        filename=Path(scan.source_sub_path).name,
                        fingerprint=fp,
                        extra_metadata={"has_source_sub": True, "arr_source": True, "arr_sub_path": scan.source_sub_path, "arr_media_path": media_path, "original_format": ext.lstrip(".")}
                    )
                    extracted_count += 1
                    imported = True
                    logger.info(f"Imported external subtitle for {project_name}/{ep_name}")
                except Exception as e:
                    logger.warning(f"Failed to read external subtitle for {ep_name}: {e}")

            # If no external subtitle found, probe container for embedded subtitles
            if not imported:
                if progress_callback:
                    progress_callback(f"Checking video container for embedded subtitles for {ep_name}...")
                try:
                    await extract_embedded_for_episode(
                        project_name=project_name,
                        episode_name=ep_name,
                        force=force,
                        migrate_srt=migrate_srt
                    )
                    extracted_count += 1
                    imported = True
                    logger.info(f"Auto-extracted embedded subtitle for {project_name}/{ep_name}")
                except Exception as e:
                    logger.warning(f"Could not auto-extract embedded subtitle for {project_name}/{ep_name}: {e}")

    if extracted_count > 0:
        storage._invalidate_episodes_list_cache(project_name)

    return extracted_count
