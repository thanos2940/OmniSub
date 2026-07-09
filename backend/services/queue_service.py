import logging
from typing import List, Dict, Optional

from utils import storage
from utils.translation_queue import (
    TranslationQueue,
    PRIORITY_WEBHOOK,
    PRIORITY_MANUAL,
    PRIORITY_SYNC,
    PRIORITY_BACKLOG
)

logger = logging.getLogger(__name__)

def enqueue_translation(project_name: str, episode_name: str, priority: int, options: Optional[Dict] = None, lang: str = "") -> str:
    """Enqueue an episode for translation (target language ``lang``, '' = primary) and wake the worker."""
    queue = TranslationQueue()
    item_id = queue.enqueue(project_name, episode_name, priority, options, lang)

    # Trigger background worker to process new task
    try:
        from services.background_worker import worker
        worker.trigger()
    except ImportError as e:
        logger.warning(f"Could not trigger background worker (this is normal during startup or in tests): {e}")

    return item_id

from utils.model_resolver import resolve_model

def enqueue_missing_for_project(project_name: str, priority: int = PRIORITY_BACKLOG) -> List[str]:
    """Enqueue all missing episodes (original exists, target doesn't) for a project."""
    proj_meta = storage.load_project_metadata(project_name)
    if not proj_meta or proj_meta.get("arr_disabled", False) or proj_meta.get("bazarr_disabled", False):
        logger.info(f"Project {project_name} is disabled or missing; skipping bulk enqueue.")
        return []
        
    global_config = storage.load_global_config()
    model = resolve_model("translation", proj_meta)

    from utils.language_codes import to_code
    target_names = storage.project_target_languages(proj_meta)
    primary_code = to_code(proj_meta.get("target_language", "English"))

    # Single-language project: keep the fast index path (Plan 03).
    if len(target_names) <= 1:
        options = {"model": model}
        missing: List[str] = []
        try:
            from utils import metadata_index
            if metadata_index.is_populated():
                missing = [ep for (_p, ep) in metadata_index.list_untranslated(project_name)]
        except Exception:
            missing = []
        if not missing:
            for ep_name in storage.list_episodes(project_name):
                ep_meta = storage.load_episode_metadata(project_name, ep_name)
                original_srt_exists = storage.original_subtitle_exists(project_name, ep_name)
                if original_srt_exists and (not storage.episode_has_target(ep_meta) or storage.episode_translation_is_stale(ep_meta)):
                    missing.append(ep_name)
        return [enqueue_translation(project_name, ep_name, priority, options) for ep_name in missing]

    # Multi-language project (Plan 15): one queue item per (episode, language).
    enqueued_ids = []
    for ep_name in storage.list_episodes(project_name):
        original_srt_exists = storage.original_subtitle_exists(project_name, ep_name)
        if not original_srt_exists:
            continue
        ep_meta = storage.load_episode_metadata(project_name, ep_name)
        for name in target_names:
            code = to_code(name)
            is_primary = (code == primary_code)
            lang_code_for_check = None if is_primary else code
            if storage.episode_has_target(ep_meta, lang_code_for_check) and not storage.episode_translation_is_stale(ep_meta, lang_code_for_check):
                continue
            opts = {"model": model, "target_language_name": name}
            enqueued_ids.append(enqueue_translation(project_name, ep_name, priority, opts, "" if is_primary else code))
    return enqueued_ids

def enqueue_missing_for_library(priority: int = PRIORITY_BACKLOG) -> Dict[str, List[str]]:
    """Enqueue all missing episodes across all enabled projects in the library."""
    results = {}
    for project_name in storage.list_projects():
        enqueued = enqueue_missing_for_project(project_name, priority)
        if enqueued:
            results[project_name] = enqueued
    return results
