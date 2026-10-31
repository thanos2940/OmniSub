"""
Export Retry Queue Handler.
Retries subtitle file export when network share or filesystem is temporarily unavailable.
"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable, Dict

from utils import storage
from utils.language_codes import output_filename_from_original
from utils.source_clean import reconstruct_cleaned_srt

logger = logging.getLogger(__name__)

EXPORT_BACKOFF_DELAYS = [30, 120, 600, 1800, 7200]  # Exponential backoff up to 2 hours


def dispatch_export_retries(
    queue,
    export_tasks: set,
    global_config: dict,
    trigger_fn: Callable[[], None],
) -> None:
    """Claim pending export retry items and process them up to concurrency cap."""
    cap = max(1, int(global_config.get("export_retry_concurrency", 2) or 2))
    while len(export_tasks) < cap:
        item = queue.claim_next_export_retry()
        if not item:
            return
        logger.info(f"Claiming export retry for {item['project_name']} - {item['episode_name']}")
        task = asyncio.create_task(process_export_retry_item(queue, item, global_config), name=f"export_{item['id']}")
        export_tasks.add(task)

        def _done(t, _tasks=export_tasks):
            _tasks.discard(t)
            trigger_fn()

        task.add_done_callback(_done)


async def process_export_retry_item(queue, item: dict, global_config: dict) -> None:
    """Attempt writing subtitle file to target destination."""
    item_id = item["id"]
    project_name = item["project_name"]
    episode_name = item["episode_name"]
    attempts = item["attempts"]

    import json
    options = {}
    if item.get("options"):
        try:
            options = json.loads(item["options"]) if isinstance(item["options"], str) else item["options"]
        except Exception:
            options = {}

    target_path_str = options.get("target_path")
    tgt_lang = options.get("lang") or "el"
    is_primary_lang = options.get("is_primary_lang", True)

    ep_data = storage.load_episode(project_name, episode_name)
    ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}

    if not ep_data or not ep_data.get("data"):
        logger.error(f"Cannot retry export: no episode data in storage for {project_name}/{episode_name}")
        queue.update_status(item_id, status="failed", error="Episode data not found in storage")
        return

    if not target_path_str:
        media_path = ep_meta.get("arr_media_path")
        if media_path:
            media_file = Path(media_path)
            orig_filename = ep_meta.get("original_filename") or ""
            ext = Path(orig_filename).suffix or ".srt"
            target_filename = output_filename_from_original(f"{media_file.stem}{ext}", tgt_lang)
            target_path_str = str(media_file.parent / target_filename)

    if not target_path_str:
        queue.update_status(item_id, status="failed", error="No target path configured")
        return

    target_path = Path(target_path_str)
    try:
        if not target_path.parent.exists():
            raise FileNotFoundError(f"Output directory does not exist or is unreachable: '{target_path.parent}'.")

        content = reconstruct_cleaned_srt(project_name, episode_name, ep_data["data"], tgt_lang)
        target_path.write_text(content, encoding="utf-8-sig")
        logger.info(f"Successfully exported subtitle on retry: {target_path}")
        from services.plex_refresh import request_refresh
        request_refresh(target_path)

        # Update episode metadata
        from services.worker.translation_item_worker import _record_target
        current_src_fp = ep_meta.get("source_fingerprint", "")
        _record_target(ep_meta, is_primary_lang, tgt_lang, target_path, current_src_fp)
        ep_meta.pop("translation_warning", None)
        storage.save_episode_metadata(project_name, episode_name, ep_meta)

        queue.update_status(
            item_id,
            status="completed",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
    except (OSError, FileNotFoundError) as e:
        logger.warning(f"Export retry failed for {project_name}/{episode_name} (attempt {attempts}): {e}")
        if attempts >= 5:
            queue.update_status(item_id, status="failed", error=f"Max retry attempts reached: {e}")
        else:
            delay = EXPORT_BACKOFF_DELAYS[min(attempts - 1, len(EXPORT_BACKOFF_DELAYS) - 1)]
            next_retry_ts = datetime.now(timezone.utc).timestamp() + delay
            next_retry_str = datetime.fromtimestamp(next_retry_ts, timezone.utc).isoformat()
            queue.update_status(item_id, status="pending", next_retry_at=next_retry_str, error=str(e))
