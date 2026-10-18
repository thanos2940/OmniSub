"""
Episode Persistence, Loading, Metadata Caching, and Query Helpers.
"""

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import List, Dict, Optional

from utils.storage_modules.atomic_io import (
    _safe_project_dir,
    _safe_episode_dir,
    get_episode_lock,
    write_json_atomic,
)
from utils.storage_modules.subtitle_store import original_subtitle_exists
import utils.paths as paths


PROJECTS_DIR = paths.PROJECTS_DIR

logger = logging.getLogger(__name__)

# Per-project cache of the assembled episode-list-with-metadata (the project page).
_episodes_list_cache: Dict[str, List[Dict]] = {}


def _invalidate_episodes_list_cache(project_name: str) -> None:
    _episodes_list_cache.pop(project_name, None)


def episode_has_target(ep_meta: Optional[Dict], lang_code: Optional[str] = None) -> bool:
    """True if an episode already has a target subtitle.

    ``lang_code=None`` checks the primary language (flat ``arr_has_target``).
    A non-None ``lang_code`` checks the per-language
    ``arr_targets[code]`` record (Plan 15 — multiple target languages).
    """
    if not ep_meta:
        return False
    if lang_code:
        return bool((ep_meta.get("arr_targets") or {}).get(lang_code, {}).get("has_target"))
    return bool(ep_meta.get("arr_has_target"))


def episode_translation_is_stale(ep_meta: Optional[Dict], lang_code: Optional[str] = None) -> bool:
    """True if the source changed since the target (for ``lang_code``) was produced.

    Compares the current source fingerprint against the fingerprint the existing
    target was translated from. ``lang_code=None`` uses the primary (flat) fields;
    a code uses ``arr_targets[code]``.
    """
    if not ep_meta:
        return False
    current_fp = ep_meta.get("arr_sub_fingerprint")
    if lang_code:
        translated_fp = (ep_meta.get("arr_targets") or {}).get(lang_code, {}).get("translated_from_fingerprint")
    else:
        translated_fp = ep_meta.get("arr_translated_from_fingerprint")
    return bool(translated_fp and current_fp and translated_fp != current_fp)


def list_episodes(project_name: str) -> List[str]:
    """List all episode names for a project."""
    episodes_dir = _safe_project_dir(project_name) / "episodes"
    if not episodes_dir.exists():
        return []
    return sorted([d.name for d in episodes_dir.iterdir() if d.is_dir()])


def list_episodes_with_metadata(project_name: str) -> List[Dict]:
    """Assemble the episode list with metadata for a project, cached until an episode
    in the project changes. Used by GET /projects/{name}/episodes."""
    cached = _episodes_list_cache.get(project_name)
    if cached is not None:
        return cached
    result = []
    for ep_name in list_episodes(project_name):
        meta = load_episode_metadata(project_name, ep_name) or {}
        has_source = meta.get("has_source_sub", True)
        orig_ex = original_subtitle_exists(project_name, ep_name)
        if meta.get("line_count", 0) > 0 or meta.get("arr_sub_path") or orig_ex:
            has_source = True
        elif meta.get("line_count", 0) == 0 and not meta.get("arr_sub_path") and not orig_ex:
            has_source = False

        media_path = meta.get("arr_media_path") or meta.get("media_path")
        if not media_path:
            ep_d = _safe_episode_dir(project_name, ep_name)
            if ep_d.exists():
                video_exts = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov"}
                for vf in ep_d.iterdir():
                    if vf.is_file() and vf.suffix.lower() in video_exts:
                        media_path = str(vf)
                        meta["arr_media_path"] = media_path
                        break
            if not media_path:
                proj_dir = _safe_project_dir(project_name)
                if proj_dir.exists():
                    video_exts = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov"}
                    for vf in proj_dir.iterdir():
                        if vf.is_file() and vf.suffix.lower() in video_exts and ep_name.lower() in vf.stem.lower():
                            media_path = str(vf)
                            meta["arr_media_path"] = media_path
                            break

        needs_review = meta.get("needs_review_count", 0)
        auto_repaired = meta.get("auto_repaired_count", 0)
        client_meta = dict(meta)
        client_meta.pop("clean_to_orig_map", None)
        result.append({
            "name": ep_name,
            "season": meta.get("season"),
            "line_count": meta.get("line_count", 0),
            "translated": meta.get("translated", False) or bool(meta.get("arr_has_target")),
            "has_source": has_source,
            "has_media": bool(media_path),
            "media_path": media_path,
            "has_ass": meta.get("has_ass", False) or (meta.get("original_format") or "").lower() in ("ass", "ssa"),
            "needs_review_count": needs_review,
            "auto_repaired_count": auto_repaired,
            "metadata": client_meta,
        })

    _episodes_list_cache[project_name] = result
    return result



def save_episode(
    project_name: str,
    episode_name: str,
    data: List[Dict],
    metadata: Optional[Dict] = None,
    update_stats: bool = True
) -> None:
    """Save episode data and optional metadata."""
    from utils.storage_modules.stats_and_index import (
        invalidate_arr_library_cache,
        update_project_stats_on_change,
        _index_episode,
    )
    invalidate_arr_library_cache()
    _invalidate_episodes_list_cache(project_name)
    review_count = sum(1 for l in data if l.get("needs_review")) if data else 0
    auto_repaired_count = sum(1 for l in data if l.get("auto_repaired") or l.get("repaired")) if data else 0
    lock = get_episode_lock(project_name, episode_name)
    with lock:
        ep_dir = _safe_episode_dir(project_name, episode_name)
        ep_dir.mkdir(parents=True, exist_ok=True)

        write_json_atomic(ep_dir / "data.json", {"data": data})

        if metadata is not None:
            metadata["needs_review_count"] = review_count
            if "auto_repaired_count" not in metadata or auto_repaired_count > 0:
                metadata["auto_repaired_count"] = max(metadata.get("auto_repaired_count", 0), auto_repaired_count)
            write_json_atomic(ep_dir / "metadata.json", metadata)

    # Index first, THEN recompute stats — so the indexed COUNT in
    # update_project_stats_on_change reflects this episode's new state.
    idx_meta = metadata if metadata is not None else (load_episode_metadata(project_name, episode_name) or {})
    idx_meta = {**idx_meta, "needs_review_count": review_count, "auto_repaired_count": auto_repaired_count}
    _index_episode(project_name, episode_name, idx_meta)
    try:
        from utils import metadata_index
        metadata_index.set_review_count(project_name, episode_name, review_count)
    except Exception:
        pass

    if update_stats:
        update_project_stats_on_change(project_name)


def save_episode_metadata(
    project_name: str,
    episode_name: str,
    metadata: Dict,
    update_stats: bool = True
) -> None:
    """Save episode metadata only. See ``save_episode`` for ``update_stats``."""
    from utils.storage_modules.stats_and_index import (
        invalidate_arr_library_cache,
        update_project_stats_on_change,
        _index_episode,
    )
    invalidate_arr_library_cache()
    _invalidate_episodes_list_cache(project_name)
    lock = get_episode_lock(project_name, episode_name)
    with lock:
        ep_dir = _safe_episode_dir(project_name, episode_name)
        ep_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(ep_dir / "metadata.json", metadata)

    _index_episode(project_name, episode_name, metadata)  # index first, then count
    if update_stats:
        update_project_stats_on_change(project_name)


_ORIGINAL_EXTENSIONS = ("srt", "ass", "ssa")


def _has_original_file(ep_dir: Path, meta: Dict) -> bool:
    """Whether the imported source (original.<ext>) is on disk, from metadata already in hand.

    load_episode_metadata used to ask original_subtitle_exists, which loads the
    metadata again — and for any episode with no cues yet (every subtitle-less
    placeholder) the two recursed into each other until Python's recursion limit
    tripped and a bare except swallowed it: ~500 frames and ~1000 path resolutions
    per metadata read, on every sync.
    """
    ext = meta.get("original_extension")
    if ext in _ORIGINAL_EXTENSIONS and (ep_dir / f"original.{ext}").exists():
        return True
    return any((ep_dir / f"original.{e}").exists() for e in _ORIGINAL_EXTENSIONS if e != ext)


def load_episode_metadata(project_name: str, episode_name: str) -> Optional[Dict]:
    """Load episode metadata only."""
    metadata_file = _safe_episode_dir(project_name, episode_name) / "metadata.json"
    if not metadata_file.exists():
        return None

    lock = get_episode_lock(project_name, episode_name)
    with lock:
        try:
            with open(metadata_file, 'r', encoding='utf-8') as f:
                meta = json.load(f)
                if meta:
                    if meta.get("line_count", 0) > 0 or _has_original_file(metadata_file.parent, meta):
                        meta["has_source_sub"] = True
                return meta
        except Exception:
            return None



def load_episode(project_name: str, episode_name: str) -> Optional[Dict]:
    """Load episode data and metadata."""
    from utils.storage_modules.project_store import load_project_metadata
    from utils.language_codes import to_code

    ep_dir = _safe_episode_dir(project_name, episode_name)
    data_file = ep_dir / "data.json"

    if not data_file.exists():
        return None

    lock = get_episode_lock(project_name, episode_name)
    with lock:
        with open(data_file, 'r', encoding='utf-8') as f:
            result = json.load(f)
            
        # Handle legacy list format
        if isinstance(result, list):
            logger.debug(f"Converted legacy list format for {episode_name}")
            result = {"data": result}
            write_json_atomic(data_file, result)

        # Lazy compaction on load (D-02): strip legacy drawing/karaoke bloat if original ASS exists
        has_ass_orig = (ep_dir / "original.ass").exists() or (ep_dir / "original.ssa").exists()
        if has_ass_orig and isinstance(result.get("data"), list):
            def _is_bloat(r: Dict) -> bool:
                ass = r.get("_ass")
                if not isinstance(ass, dict):
                    return False
                if not ass.get("translatable", True) and (ass.get("kind") or ass.get("reason")) in ("drawing", "karaoke", "empty"):
                    return True
                return False

            bloat_rows = [r for r in result["data"] if _is_bloat(r)]
            has_drawing = any((r.get("_ass") or {}).get("kind") == "drawing" or (r.get("_ass") or {}).get("reason") == "drawing" for r in bloat_rows)
            if has_drawing or len(bloat_rows) >= 2:
                result["data"] = [r for r in result["data"] if not _is_bloat(r)]
                for i, r in enumerate(result["data"], start=1):
                    r["id"] = str(i)
                write_json_atomic(data_file, {"data": result["data"]})
                meta_file = ep_dir / "metadata.json"
                if meta_file.exists():
                    try:
                        with open(meta_file, 'r', encoding='utf-8') as mf:
                            mdata = json.load(mf)
                        mdata["line_count"] = len(result["data"])
                        mdata.pop("clean_to_orig_map", None)
                        write_json_atomic(meta_file, mdata)
                    except Exception as e:
                        logger.warning(f"Failed to update metadata line_count on lazy compaction: {e}")

        proj_meta = load_project_metadata(project_name)
        primary_lang = proj_meta.get("target_language", "Greek") if proj_meta else "Greek"
        primary_code = to_code(primary_lang)

        modified = False
        for entry in result.get("data", []):
            if "translations" not in entry:
                entry["translations"] = {}
                if "translated" in entry and entry["translated"]:
                    entry["translations"][primary_code] = entry["translated"]
                modified = True
            if "src_hash" not in entry:
                orig_text = entry.get("original", "").strip()
                entry["src_hash"] = hashlib.sha256(orig_text.encode("utf-8")).hexdigest()[:16]
                modified = True
                
        if modified:
            write_json_atomic(data_file, result)

        metadata = load_episode_metadata(project_name, episode_name)
        if metadata:
            result["metadata"] = metadata

        return result


def load_episode_data(project_name: str, episode_name: str) -> Optional[List[Dict]]:
    """Load only the subtitle dialogue entries (data list) for an episode.
    
    Ensures compatibility with callers expecting item['translation'] or item['translated'].
    Returns a list of cue dictionaries, or None if the episode does not exist.
    """
    ep = load_episode(project_name, episode_name)
    if not ep or "data" not in ep:
        return None
    data = ep["data"]
    for item in data:
        if "translation" not in item:
            if "translated" in item and item["translated"]:
                item["translation"] = item["translated"]
            elif "translations" in item and isinstance(item["translations"], dict):
                # Pick primary language or first available translation
                for _code, text in item["translations"].items():
                    if text:
                        item["translation"] = text
                        break
    return data


def delete_episode(project_name: str, episode_name: str) -> bool:
    """Delete an episode and its data."""
    from utils.storage_modules.stats_and_index import (
        invalidate_arr_library_cache,
        update_project_stats_on_change,
    )
    invalidate_arr_library_cache()
    _invalidate_episodes_list_cache(project_name)
    ep_dir = _safe_episode_dir(project_name, episode_name)
    if ep_dir.exists():
        shutil.rmtree(ep_dir)
        update_project_stats_on_change(project_name)
        try:
            from utils import metadata_index
            metadata_index.remove(project_name, episode_name)
        except Exception:
            pass
        return True
    return False


def rename_episode(project_name: str, old_name: str, new_name: str) -> bool:
    """Rename an episode folder in place, keeping its data, translations and metadata.

    Used when an episode's primary source format changes (an ``.ass`` appears next to
    the ``.srt`` an episode was imported from): the existing episode is moved to its
    dual-format sibling name rather than being overwritten by the new format. Refuses
    to clobber an existing target.
    """
    from utils.storage_modules.stats_and_index import (
        invalidate_arr_library_cache,
        update_project_stats_on_change,
        _index_episode,
    )
    invalidate_arr_library_cache()
    _invalidate_episodes_list_cache(project_name)
    old_dir = _safe_episode_dir(project_name, old_name)
    new_dir = _safe_episode_dir(project_name, new_name)
    if not old_dir.exists() or new_dir.exists():
        return False
    try:
        old_dir.rename(new_dir)
    except OSError as e:
        logger.warning(f"Could not rename episode {project_name}/{old_name} -> {new_name}: {e}")
        return False

    # Move the index entry with it, or the old name lingers as a phantom episode
    # until the next full backfill.
    try:
        from utils import metadata_index
        metadata_index.remove(project_name, old_name)
    except Exception:
        pass
    _index_episode(project_name, new_name, load_episode_metadata(project_name, new_name) or {})
    update_project_stats_on_change(project_name)
    return True
