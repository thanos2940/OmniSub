"""
Embedded Subtitle Extraction Queue Handler.
"""

import asyncio
import json
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable

from utils import storage
from utils.language_codes import to_code as get_lang_code
from integrations.subtitle_scanner import SubtitleScannerService

logger = logging.getLogger(__name__)


def dispatch_extractions(
    queue,
    extraction_tasks: set,
    global_config: dict,
    max_priority: Optional[int],
    trigger_fn: Callable[[], None]
) -> None:
    """Fill the embedded-extraction lane up to its own concurrency cap.

    Kept separate from the translation lane on purpose (plan D-F): extracting a
    muxed subtitle streams the *entire* container, so on a network share it is a
    multi-minute, full-file read.
    """
    if not global_config.get("embedded_extraction_enabled", False):
        return
    try:
        cap = max(1, int(global_config.get("embedded_extraction_concurrency", 1) or 1))
    except (TypeError, ValueError):
        cap = 1

    while len(extraction_tasks) < cap:
        item = queue.claim_next_extraction(max_priority=max_priority)
        if not item:
            return
        logger.info(
            f"Background worker claiming extraction {item['id']}: "
            f"{item['project_name']} - {item['episode_name']}"
        )
        task = asyncio.create_task(process_extraction_item(queue, item, global_config))
        extraction_tasks.add(task)

        def _extraction_done(t, _tasks=extraction_tasks):
            _tasks.discard(t)
            trigger_fn()

        task.add_done_callback(_extraction_done)


async def process_extraction_item(queue, item: dict, global_config: dict) -> None:
    """Extract one embedded ASS track to a sidecar and import it as an episode."""
    item_id = item["id"]
    project_name = item["project_name"]
    episode_name = item["episode_name"]
    attempts = item["attempts"]
    job_id = f"extract_{item_id}"

    from utils.jobs_manager import JobStatus, jobs, update_job
    from integrations import embedded_subs
    from integrations.media_sync_engine import MediaSyncEngine
    from utils import media_probe_cache

    jobs[job_id] = JobStatus(
        id=job_id, status="running", progress=0.0,
        message=f"Extracting embedded subtitle for {episode_name}...", logs=[],
        project_name=project_name, episode_name=episode_name,
    )

    loop = asyncio.get_running_loop()
    media_path = None
    try:
        options = {}
        if item.get("options"):
            try:
                options = json.loads(item["options"])
            except Exception:
                pass

        media_path = options.get("media_path")
        stream_index = options.get("stream_index")
        source_lang = options.get("source_lang_code") or "en"
        import_metadata = options.get("import_metadata") or {}
        track_info = options.get("track") or {}
        track_codec = track_info.get("codec") or "ass"
        output_fmt = options.get("output_format") or ("ass" if track_codec.lower() in ("ass", "ssa") else "srt")

        if not media_path or stream_index is None:
            raise ValueError("Extraction item is missing media_path or stream_index.")
        if not Path(media_path).exists():
            raise FileNotFoundError(f"Media file is not reachable: {media_path}")

        sidecar = embedded_subs.sidecar_path_for(media_path, source_lang, ext=output_fmt)

        content = None
        if sidecar.exists():
            content = await loop.run_in_executor(None, MediaSyncEngine._read_file, str(sidecar))
            if content and content.strip():
                update_job(job_id, log=f"Sidecar already present at {sidecar.name}; skipping extraction.")
            else:
                # An empty or unreadable file where the sidecar should be has nothing
                # worth keeping; the container still holds the track, so take it again.
                update_job(job_id, log=f"Existing {sidecar.name} is empty or unreadable; extracting it again.")
                content = None
        if content is None:
            update_job(
                job_id, progress=5.0,
                log=f"Extracting stream {stream_index} [{output_fmt.upper()}] "
                    f"({track_info.get('title') or 'untitled'}) from {Path(media_path).name}...",
            )
            track = embedded_subs.SubtitleTrack(
                index=int(stream_index),
                codec=track_codec,
                language=track_info.get("language") or "",
                title=track_info.get("title") or "",
            )
            content = await embedded_subs.extract_track(media_path, track, config=global_config)

            if not embedded_subs.looks_like_usable_sub(content, ext=output_fmt):
                raise RuntimeError(
                    f"Extracted stream {stream_index} [{output_fmt.upper()}] contains no dialogue events — "
                    "refusing to write an empty sidecar."
                )
            await loop.run_in_executor(None, embedded_subs.write_sidecar, sidecar, content)
            update_job(job_id, progress=60.0, log=f"Wrote {sidecar.name}.")

        if not storage.load_project_metadata(project_name) and options.get("project_metadata"):
            await loop.run_in_executor(
                None, storage.create_project, project_name, options["project_metadata"]
            )
            update_job(job_id, log=f"Created project '{project_name}'.")

        project_meta = storage.load_project_metadata(project_name) or {}
        tgt_code = get_lang_code(project_meta.get("target_language", "Greek"))
        scanner = SubtitleScannerService(source_lang_code=source_lang, target_lang_code=tgt_code)
        scan = await loop.run_in_executor(None, scanner.scan_media_file, media_path)

        existing_meta = storage.load_episode_metadata(project_name, episode_name)

        if existing_meta:
            migrated_sibling = await loop.run_in_executor(
                None, MediaSyncEngine._migrate_primary_format_flip,
                project_name, episode_name, scan, existing_meta,
            )
            if migrated_sibling:
                update_job(job_id, log=f"Moved the previous {Path(existing_meta.get('arr_sub_path') or '').suffix.lstrip('.') or 'srt'} "
                                       f"episode to '{migrated_sibling}' to keep its translations.")
                existing_meta = None

        target_content = None
        if scan.has_target_sub and scan.target_sub_path:
            already_translated = bool(
                existing_meta and (existing_meta.get("translated")
                                   or existing_meta.get("translation_status") == "completed")
            )
            if not already_translated:
                target_content = await loop.run_in_executor(
                    None, MediaSyncEngine._read_file, scan.target_sub_path
                )

        fingerprint = MediaSyncEngine._fingerprint(str(sidecar))
        extra_metadata = {
            "arr_source": True,
            "arr_sub_path": str(sidecar),
            "arr_media_path": media_path,
            "arr_has_target": scan.has_target_sub,
            "arr_target_path": scan.target_sub_path if scan.has_target_sub else None,
            "embedded_extracted": True,
            "embedded_track": track_info,
            "embedded_track_candidates": options.get("candidates") or [],
            **import_metadata,
        }

        from utils.source_clean import import_and_clean_srt

        def _do_import():
            import_and_clean_srt(
                project_name,
                episode_name,
                content,
                filename=sidecar.name,
                fingerprint=fingerprint,
                target_srt_content=target_content,
                extra_metadata=extra_metadata,
            )
        await loop.run_in_executor(None, _do_import)

        row = queue.get_item(item_id)
        if row and row.get("status") == "cancelled":
            # Cancelled while the track was being pulled out: the source stays imported (it is useful as it is),
            # but the row stays cancelled and no translation is chained after it.
            update_job(job_id, status="cancelled", progress=100.0,
                       message=f"Cancelled; {sidecar.name} was imported but is not translated.")
            logger.info(f"Extraction of {project_name}/{episode_name} was cancelled while it ran; not translating it.")
            return

        update_job(job_id, status="completed", progress=100.0,
                   message=f"Extracted and imported {sidecar.name}.")
        queue.update_status(
            item_id, status="completed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            logs=jobs[job_id].logs if job_id in jobs else None,
        )
        logger.info(f"Extracted embedded subtitle for {project_name}/{episode_name} -> {sidecar}")

        translate_after = options.get("translate_after")
        if isinstance(translate_after, dict):
            # An on-demand request: someone is waiting for this episode, so it goes
            # straight to translation with the options the request chose.
            from services.queue_service import enqueue_translation
            enqueue_translation(
                project_name, episode_name, item.get("priority", 1),
                translate_after.get("options") or {}, lang=translate_after.get("lang") or "",
            )

    except asyncio.CancelledError:
        queue.update_status(item_id, status="pending", started_at=None)
        raise

    except Exception as e:
        tb = traceback.format_exc()
        delays = [300, 1800, 7200]
        final_attempt = attempts >= len(delays)
        if final_attempt:
            logger.error(f"Error extracting embedded subtitle for item {item_id}: {e}\n{tb}")
        else:
            # Most failures here are the media share blinking (WinError 64, "Invalid
            # argument" from ffmpeg on a dropped UNC path) and the retry fixes them.
            logger.warning(
                f"Extraction for {project_name}/{episode_name} failed on attempt {attempts}; will retry: {e}"
            )
            logger.debug(tb)
        if job_id in jobs:
            update_job(job_id, log=f"Extraction error: {e}")
        job_logs = jobs[job_id].logs if job_id in jobs else None

        if media_path:
            try:
                media_probe_cache.invalidate(media_path)
            except Exception:
                pass

        if final_attempt:
            queue.update_status(item_id, status="failed", error=f"{e}\n\n{tb}", logs=job_logs)
            if job_id in jobs:
                update_job(job_id, status="failed", message=f"Extraction failed permanently: {e}")
        else:
            delay = delays[max(0, min(attempts - 1, len(delays) - 1))]
            next_retry = datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + delay, timezone.utc
            ).isoformat()
            queue.update_status(item_id, status="pending", error=f"{e}\n\n{tb}",
                                next_retry_at=next_retry, logs=job_logs)
            if job_id in jobs:
                update_job(job_id, status="pending", message=f"Extraction failed, retry at {next_retry}: {e}")
