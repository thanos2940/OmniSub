import logging
from pathlib import Path
from typing import List, Dict, Optional, Union

from utils import storage
from utils.translation_queue import (
    TranslationQueue,
    PRIORITY_HIGH,
    PRIORITY_WEBHOOK,
    PRIORITY_MANUAL,
    PRIORITY_SYNC,
    PRIORITY_BACKLOG,
    PRIORITY_LOW,
    PRIORITY_RUN_NOW,
    KIND_TRANSLATION,
    KIND_EXTRACTION,
    KIND_EXPORT_RETRY,
)

logger = logging.getLogger(__name__)


def get_queue(db_path: Optional[Union[str, Path]] = None) -> TranslationQueue:
    """Return a TranslationQueue instance pointing to db_path (or default DB_FILE)."""
    if db_path:
        return TranslationQueue(Path(db_path))
    return TranslationQueue()


def _wake_worker() -> None:
    try:
        from services.background_worker import worker
        worker.trigger()
    except ImportError as e:
        logger.warning(f"Could not trigger background worker (this is normal during startup or in tests): {e}")


def _wake_runner() -> None:
    """A watching agent runner takes the new translation now rather than at its next minute."""
    try:
        from services import agent_runner
        agent_runner.nudge()
    except Exception as e:  # never let a wake-up break an enqueue
        logger.debug(f"Could not nudge the agent runner: {e}")


# An explicit request for one episode: allowed even for a project set to Ignored.
EXPLICIT_PRIORITIES = (PRIORITY_MANUAL, PRIORITY_RUN_NOW)


def skips_ignored(project_meta: Optional[Dict], priority: int, options: Optional[Dict] = None) -> bool:
    """True when an Ignored project must not get this work.

    Ignored stops automatic work (webhook, sync, backlog); an explicit request for an
    episode (Translate, Run Now, an on-demand request, or ``force``) still goes through.
    """
    return (storage.resolve_project_priority(project_meta) == "ignored"
            and priority not in EXPLICIT_PRIORITIES
            and not (options or {}).get("force"))


def enqueue_translation(project_name: str, episode_name: str, priority: int = PRIORITY_MANUAL, options: Optional[Dict] = None,
                        lang: str = "", delay_seconds: int = 0, next_retry_at: Optional[str] = None) -> str:
    """Enqueue an episode for translation (target language ``lang``, '' = primary) and wake the worker."""
    if not project_name or not str(project_name).strip() or not episode_name or not str(episode_name).strip():
        logger.warning(f"Skipping enqueue_translation with empty project ({project_name!r}) or episode ({episode_name!r})")
        return ""

    proj_meta = storage.load_project_metadata(project_name) or {}
    if skips_ignored(proj_meta, priority, options):
        logger.info(f"Project '{project_name}' is ignored; not queueing {episode_name} (priority {priority}).")
        return ""

    tier = storage.resolve_project_priority(proj_meta)
    if priority != PRIORITY_RUN_NOW:
        if tier == "high":
            priority = PRIORITY_HIGH
        elif tier == "low":
            priority = PRIORITY_LOW

    queue = TranslationQueue()
    item_id = queue.enqueue(
        project_name.strip(), episode_name.strip(), priority, options, lang,
        KIND_TRANSLATION, next_retry_at=next_retry_at, delay_seconds=delay_seconds
    )
    _wake_worker()
    _wake_runner()
    return item_id


def enqueue_extraction(project_name: str, episode_name: str, priority: int, options: Dict) -> str:
    """Enqueue an embedded-subtitle extraction and wake the worker."""
    if not project_name or not str(project_name).strip() or not episode_name or not str(episode_name).strip():
        logger.warning(f"Skipping enqueue_extraction with empty project ({project_name!r}) or episode ({episode_name!r})")
        return ""
    queue = TranslationQueue()
    item_id = queue.enqueue(project_name.strip(), episode_name.strip(), priority, options, "", KIND_EXTRACTION)
    _wake_worker()
    return item_id


def enqueue_export_retry(project_name: str, episode_name: str, target_path: str, lang: str = "",
                         priority: int = PRIORITY_SYNC, error: Optional[str] = None,
                         queue: Optional[TranslationQueue] = None) -> str:
    """Enqueue an export retry task and wake the worker."""
    if not project_name or not str(project_name).strip() or not episode_name or not str(episode_name).strip():
        logger.warning(f"Skipping enqueue_export_retry with empty project ({project_name!r}) or episode ({episode_name!r})")
        return ""
    q = queue if queue is not None else TranslationQueue()
    options = {
        "target_path": target_path,
        "lang": lang,
        "error": error,
    }
    item_id = q.enqueue(project_name.strip(), episode_name.strip(), priority, options, lang, KIND_EXPORT_RETRY)
    _wake_worker()
    return item_id


from utils.model_resolver import resolve_model

def enqueue_missing_for_project(project_name: str, priority: int = PRIORITY_BACKLOG) -> List[str]:
    """Enqueue all missing episodes (original exists, target doesn't) for a project."""
    proj_meta = storage.load_project_metadata(project_name)
    if not proj_meta or proj_meta.get("arr_disabled", False):
        logger.info(f"Project {project_name} is disabled or missing; skipping bulk enqueue.")
        return []
        
    global_config = storage.load_global_config()
    model = resolve_model("translation", proj_meta)
    ai_provider = storage.get_project_setting(proj_meta, "ai_provider") or global_config.get("ai_provider", "cloud")

    from utils.language_codes import to_code
    target_names = storage.project_target_languages(proj_meta)
    primary_code = to_code(proj_meta.get("target_language", "English"))

    # Single-language project: keep the fast index path (Plan 03).
    if len(target_names) <= 1:
        options = {"model": model, "ai_provider": ai_provider}
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
            opts = {"model": model, "ai_provider": ai_provider, "target_language_name": name}
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


def translation_options_for(project_name: str) -> Dict:
    """Standard translation options for a project's episodes (model, provider, sampling,
    primary target language), as the Autopilot and "Translate missing" enqueue them."""
    proj_meta = storage.load_project_metadata(project_name) or {}
    global_config = storage.load_global_config()
    target_names = storage.project_target_languages(proj_meta)
    return {
        "model": resolve_model("translation", proj_meta),
        "ai_provider": storage.get_project_setting(proj_meta, "ai_provider") or global_config.get("ai_provider", "cloud"),
        "temperature": storage.get_project_setting(proj_meta, "temperature") or global_config.get("temperature"),
        "top_k": storage.get_project_setting(proj_meta, "top_k") or global_config.get("top_k"),
        "top_p": storage.get_project_setting(proj_meta, "top_p") or global_config.get("top_p"),
        "target_language_name": (
            target_names[0] if proj_meta and target_names
            else global_config.get("default_target_language") or "English"
        ),
    }


def postpone_item(item_id: Union[str, int]) -> bool:
    """Postpone a queued, pending, or failed queue item."""
    return get_queue().postpone_item(item_id)


def continue_item(item_id: Union[str, int], boost_priority: bool = True) -> bool:
    """Continue a postponed or queued item, optionally boosting its priority to 10."""
    res = get_queue().continue_item(item_id, boost_priority=boost_priority)
    if res:
        _wake_worker()
        _wake_runner()
    return res


def stop_item(item_id: Union[str, int]) -> bool:
    """Stop/cancel a queue item and abort any running worker job."""
    return get_queue().stop_item(item_id)


def list_queue_items(limit: int = 100, work_types: Optional[Union[tuple, list, str]] = None) -> List[Dict]:
    """Retrieve queue items with full visibility across work types."""
    return get_queue().get_all(limit=limit, work_types=work_types, include_all_kinds=True)

