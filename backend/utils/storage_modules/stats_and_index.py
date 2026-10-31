"""
Project Stats Calculation, Arr Library Cache, and Index Synchronization.
"""

import json
import logging
from collections import Counter
from typing import Dict

from utils.storage_modules.atomic_io import _safe_episode_dir

logger = logging.getLogger(__name__)

_arr_library_cache = None
_library_cache = None

# What a Library row shows of a project's metadata (get_library); the rest (glossary, guide, settings) stays out.
LIBRARY_FIELDS = ("show_name", "type", "arr_source", "arr_media_type", "target_language", "parent_project",
                  "arr_last_sync")


def invalidate_arr_library_cache() -> None:
    global _arr_library_cache, _library_cache
    _arr_library_cache = None
    _library_cache = None


def _index_episode(project_name: str, episode_name: str, ep_meta: Dict) -> None:
    """Best-effort upsert into the metadata index (Plan 03)."""
    try:
        from utils import metadata_index
        from utils.storage_modules.project_store import load_project_metadata, resolve_project_priority
        from utils.storage_modules.subtitle_store import original_subtitle_exists
        pmeta = load_project_metadata(project_name) or {}
        disabled = (resolve_project_priority(pmeta) == "ignored")
        orig = original_subtitle_exists(project_name, episode_name)
        metadata_index.upsert(project_name, episode_name, ep_meta or {}, orig, disabled)
    except Exception:
        pass


def update_project_stats_on_change(project_name: str) -> None:
    """Recalculate project stats based on imported episodes and update project.json."""
    from utils.storage_modules.project_store import (
        load_project_metadata,
        save_project_metadata,
        _get_project_lock,
    )
    from utils.storage_modules.episode_store import list_episodes, load_episode_metadata

    with _get_project_lock(project_name):
        meta = load_project_metadata(project_name)
        if not meta:
            return

        episodes = list_episodes(project_name)

        # Filter out sibling episodes
        non_siblings = []
        for ep_name in episodes:
            if ' [' in ep_name:
                ep_meta = load_episode_metadata(project_name, ep_name)
                if ep_meta and ep_meta.get("arr_secondary_of"):
                    continue
            non_siblings.append(ep_name)

        # Fast path: one indexed COUNT instead of reading every episode's metadata file.
        translated = None
        try:
            from utils import metadata_index
            translated = metadata_index.count_translated(project_name)
        except Exception:
            translated = None

        if translated is None:
            translated = 0
            for ep_name in non_siblings:
                ep_meta = load_episode_metadata(project_name, ep_name)
                if ep_meta and (
                    ep_meta.get("arr_has_target")
                    or ep_meta.get("translated")
                ):
                    translated += 1

        if meta.get("type") == "movie" or meta.get("arr_media_type") == "movie":
            total_count = 1 if len(non_siblings) > 0 else 0
            translated_count = 1 if translated > 0 else 0
        else:
            current_total = meta.get("arr_total_episodes")
            if current_total is None:
                current_total = len(non_siblings)
            else:
                current_total = max(current_total, len(non_siblings))
            
            total_count = current_total
            translated_count = translated

        meta["arr_total_episodes"] = total_count
        meta["arr_translated_episodes"] = translated_count

        save_project_metadata(project_name, meta)


def get_arr_library() -> Dict:
    """Return structured library data for Sonarr/Radarr-synced projects.

    Groups projects by type (series / movie) and disabled status.
    Reads from local project metadata — does NOT contact Sonarr/Radarr.
    """
    global _arr_library_cache
    if _arr_library_cache is not None:
        return _arr_library_cache

    from utils.storage_modules.project_store import list_projects, load_project_metadata, resolve_project_priority

    all_names = list_projects()
    series = []
    movies = []
    disabled = []
    last_sync = None

    for p_name in all_names:
        meta = load_project_metadata(p_name)
        if not meta:
            continue
        if not meta.get("arr_source"):
            continue
        ts = meta.get("arr_last_sync")
        if ts and (last_sync is None or ts > last_sync):
            last_sync = ts

        episode_count, translated = _title_counts(p_name, meta)

        prio = resolve_project_priority(meta)
        is_disabled = (prio == "ignored")

        entry = {
            **meta,
            "name": p_name,
            "translation_priority": prio,
            "arr_disabled": is_disabled,
            "episode_count": episode_count,
            "translated_count": translated,
        }

        if is_disabled:
            disabled.append(entry)
        elif meta.get("arr_media_type") == "movie" or meta.get("type") == "movie":
            movies.append(entry)
        else:
            series.append(entry)

    result_dict = {
        "series": sorted(series, key=lambda x: x.get("show_name", "")),
        "movies": sorted(movies, key=lambda x: x.get("show_name", "")),
        "disabled": sorted(disabled, key=lambda x: x.get("show_name", "")),
        "last_sync": last_sync,
        "totals": {
            "series": len(series),
            "movies": len(movies),
            "disabled": len(disabled),
        },
    }
    _arr_library_cache = result_dict
    return result_dict


def _title_counts(p_name: str, meta: Dict):
    """(episodes, translated episodes) of a project: the stats its metadata keeps
    (update_project_stats_on_change), else counted from its episodes."""
    meta_total = meta.get("arr_total_episodes")
    meta_translated = meta.get("arr_translated_episodes")
    if meta_total is not None and meta_translated is not None:
        return meta_total, meta_translated

    from utils.storage_modules.episode_store import list_episodes, load_episode_metadata, load_episode
    non_siblings = []
    for ep_name in list_episodes(p_name):
        if ' [' in ep_name:
            ep_meta = load_episode_metadata(p_name, ep_name)
            if ep_meta and ep_meta.get("arr_secondary_of"):
                continue
        non_siblings.append(ep_name)

    translated = 0
    for ep_name in non_siblings:
        ep_meta = load_episode_metadata(p_name, ep_name)
        if ep_meta and ep_meta.get("arr_has_target"):
            translated += 1
            continue
        ep = load_episode(p_name, ep_name)
        if ep and any(entry.get("translated") for entry in ep.get("data", [])):
            translated += 1
    return len(non_siblings), translated


def get_library() -> Dict:
    """Every project as the Library page lists it: ``series`` and ``movies`` (synced from Sonarr/Radarr), ``other``
    (projects of your own) and ``ignored``. An entry holds only what a row shows (``LIBRARY_FIELDS``, priority,
    episode and translated counts), not the glossary, guide and settings of the full metadata, so the page
    loads in one small answer however large the glossaries grow. One pass over the projects, cached until a
    project or an episode changes (``invalidate_arr_library_cache``)."""
    global _library_cache
    if _library_cache is not None:
        return _library_cache
    from utils.storage_modules.project_store import list_projects, load_project_metadata, resolve_project_priority

    groups: Dict[str, list] = {"series": [], "movies": [], "other": [], "ignored": []}
    last_sync = None
    for p_name in list_projects():
        meta = load_project_metadata(p_name)
        if not meta:
            continue
        prio = resolve_project_priority(meta)
        episode_count, translated = _title_counts(p_name, meta)
        entry = {"name": p_name, **{k: meta[k] for k in LIBRARY_FIELDS if meta.get(k) is not None},
                 "translation_priority": prio, "arr_disabled": prio == "ignored",
                 "episode_count": episode_count, "translated_count": translated}
        if meta.get("arr_source"):
            ts = meta.get("arr_last_sync")
            if ts and (last_sync is None or ts > last_sync):
                last_sync = ts
        if prio == "ignored":
            groups["ignored"].append(entry)
        elif not meta.get("arr_source"):
            groups["other"].append(entry)
        elif meta.get("arr_media_type") == "movie" or meta.get("type") == "movie":
            groups["movies"].append(entry)
        else:
            groups["series"].append(entry)
    for entries in groups.values():
        entries.sort(key=lambda e: (e.get("show_name") or e["name"]).lower())
    result = {**groups, "last_sync": last_sync, "totals": {k: len(v) for k, v in groups.items()}}
    _library_cache = result
    return result


def get_project_line_frequencies(project_name: str) -> Dict[str, int]:
    """Scan all episode data.json files in a project and count frequencies of original lines."""
    from utils.storage_modules.episode_store import list_episodes

    frequencies = Counter()
    episodes = list_episodes(project_name)
    for ep_name in episodes:
        ep_dir = _safe_episode_dir(project_name, ep_name)
        data_file = ep_dir / "data.json"
        if not data_file.exists():
            continue
        try:
            with open(data_file, 'r', encoding='utf-8') as f:
                result = json.load(f)
            data = result if isinstance(result, list) else result.get("data", [])
            for entry in data:
                orig = entry.get("original", "")
                if orig:
                    normalized = orig.strip().lower()
                    if normalized:
                        frequencies[normalized] += 1
        except Exception:
            pass
    return dict(frequencies)
