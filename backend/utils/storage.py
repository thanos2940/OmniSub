"""
File-Based Storage Manager for Episode Data

Handles project and episode data persistence using JSON files.
Project metadata (glossary, context) is migrating to ADK Sessions.
"""

import json
import logging
import shutil
import tempfile
import os
import threading
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projects"

_arr_library_cache = None

# Per-project cache of the assembled episode-list-with-metadata (the project page).
# Reading N metadata files per project open is the main project-page cost on a slow
# disk; this caches the result and is invalidated whenever an episode is saved/deleted.
_episodes_list_cache: Dict[str, List[Dict]] = {}


def list_episodes_with_metadata(project_name: str) -> List[Dict]:
    """Assemble the episode list with metadata for a project, cached until an episode
    in the project changes. Used by GET /projects/{name}/episodes."""
    cached = _episodes_list_cache.get(project_name)
    if cached is not None:
        return cached
    result = []
    for ep_name in list_episodes(project_name):
        meta = load_episode_metadata(project_name, ep_name) or {}
        result.append({
            "name": ep_name,
            "season": meta.get("season"),
            "line_count": meta.get("line_count", 0),
            "translated": meta.get("translated", False),
            "metadata": meta,
        })
    _episodes_list_cache[project_name] = result
    return result


def _invalidate_episodes_list_cache(project_name: str) -> None:
    _episodes_list_cache.pop(project_name, None)


def get_project_setting(metadata: Dict, key: str, default_val=None) -> Any:
    """Resolve a project-specific setting, falling back to global configuration.
    
    If the project setting matches the old hardcoded default models, it falls back
    to the global configuration defaults.
    """
    project_settings = metadata.get("settings", {})
    global_config = load_global_config()
    
    global_key_map = {
        "translation_model": "default_translation_model",
        "scan_model": "default_scan_model",
        "context_model": "default_context_model",
        "glossary_model": "default_glossary_model",
        "review_model": "review_model",
        "ai_provider": "ai_provider"
    }
    
    val = project_settings.get(key)
    if val is not None and val != "":
        # Check if it's the old hardcoded default model name
        is_default_model = (
            (key == "translation_model" and val == "gemini-flash-lite-latest") or
            (key == "scan_model" and val == "gemini-flash-lite-latest") or
            (key == "context_model" and val == "gemini-flash-lite-latest") or
            (key == "glossary_model" and val == "gemini-flash-lite-latest") or
            (key == "review_model" and val == "gemini-flash-lite-latest")
        )
        if is_default_model:
            g_key = global_key_map.get(key)
            if g_key and g_key in global_config:
                return global_config[g_key]
        return val
        
    g_key = global_key_map.get(key, key)
    if g_key in global_config:
        return global_config[g_key]
        
    return default_val



_episode_locks = {}
_episode_locks_lock = threading.Lock()

def get_episode_lock(project_name: str, episode_name: str) -> threading.RLock:
    key = (project_name, episode_name)
    with _episode_locks_lock:
        if key not in _episode_locks:
            _episode_locks[key] = threading.RLock()
        return _episode_locks[key]


def write_json_atomic(path: Path, data: Any) -> None:
    """Write JSON data atomically to a file using tempfile + os.replace."""
    path = Path(path)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(dir=str(parent), prefix=path.name + ".tmp", text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path_str, path)
    except Exception:
        if os.path.exists(tmp_path_str):
            try:
                os.unlink(tmp_path_str)
            except Exception:
                pass
        raise


def invalidate_arr_library_cache():
    global _arr_library_cache
    _arr_library_cache = None


def episode_has_target(ep_meta: Optional[Dict], lang_code: Optional[str] = None) -> bool:
    """True if an episode already has a target subtitle.

    ``lang_code=None`` checks the primary language (flat ``arr_has_target`` / legacy
    ``bazarr_has_target``). A non-None ``lang_code`` checks the per-language
    ``arr_targets[code]`` record (Plan 15 — multiple target languages).
    """
    if not ep_meta:
        return False
    if lang_code:
        return bool((ep_meta.get("arr_targets") or {}).get(lang_code, {}).get("has_target"))
    return bool(ep_meta.get("arr_has_target") or ep_meta.get("bazarr_has_target"))


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


def project_target_languages(proj_meta: Optional[Dict]) -> list:
    """Return the list of target-language NAMES for a project (Plan 15).

    Defaults to ``[target_language]`` so single-language projects are unchanged.
    """
    if not proj_meta:
        return ["English"]
    langs = proj_meta.get("target_languages")
    primary = proj_meta.get("target_language", "English")
    if not langs:
        return [primary]
    # Ensure the primary is included and first.
    ordered = [primary] + [l for l in langs if l and l != primary]
    return list(dict.fromkeys(ordered))



def ensure_projects_dir() -> None:
    """Ensure projects directory exists."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def list_projects() -> List[str]:
    """List all project names in alphabetical order."""
    ensure_projects_dir()
    return sorted([d.name for d in PROJECTS_DIR.iterdir() if d.is_dir()])


def create_project(project_name: str, metadata: Optional[Dict] = None) -> Dict:
    """
    Create a new project with default or custom metadata.
    
    Args:
        project_name: Name of the project (becomes directory name)
        metadata: Optional initial metadata
        
    Returns:
        Complete project metadata
    """
    ensure_projects_dir()
    project_dir = PROJECTS_DIR / project_name
    project_dir.mkdir(exist_ok=True)
    (project_dir / "episodes").mkdir(exist_ok=True)
    
    global_config = load_global_config()
    default_metadata = {
        "show_name": project_name,
        "target_language": global_config.get("default_target_language", "English"),
        "glossary": {"terms": []},
        "context_guide": "",
        "parent_project": None,
        "type": "show",
        "settings": {
            "apply_subtitle_edit_fixes": global_config.get("apply_subtitle_edit_fixes", True),
            "inherit_glossary": True,
            "inherit_context": True,
            "inherit_characters": True
        }
    }
    
    if metadata:
        default_metadata.update(metadata)
    
    save_project_metadata(project_name, default_metadata)
    return default_metadata


def load_project_metadata(project_name: str) -> Optional[Dict]:
    """Load project metadata from JSON file."""
    project_file = PROJECTS_DIR / project_name / "project.json"
    if not project_file.exists():
        return None
    
    with open(project_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_resolved_project_metadata(project_name: str, _seen: Optional[set] = None) -> Dict:
    """Loads child project metadata merged with parent project glossary/context if configured."""
    meta = load_project_metadata(project_name)
    if not meta:
        return {}

    # Cycle guard: a self-parent or a parent loop (A→B→A) would otherwise recurse
    # forever. Once we revisit a project, stop inheriting and treat it as a root.
    if _seen is None:
        _seen = set()
    parent_name = meta.get("parent_project")
    if parent_name and parent_name in _seen:
        parent_name = None
    _seen.add(project_name)

    if not parent_name:
        # For non-child projects, mark terms as not inherited
        if "glossary" in meta and "terms" in meta["glossary"]:
            for term in meta["glossary"]["terms"]:
                if "inherited" not in term:
                    term["inherited"] = False
        return meta

    parent_meta = load_resolved_project_metadata(parent_name, _seen)
    if not parent_meta:
        if "glossary" in meta and "terms" in meta["glossary"]:
            for term in meta["glossary"]["terms"]:
                if "inherited" not in term:
                    term["inherited"] = False
        return meta
        
    settings = meta.get("settings", {})
    inherit_glossary = settings.get("inherit_glossary", True)
    inherit_context = settings.get("inherit_context", True)
    
    # Merge glossary: child terms override parent terms (case-insensitive term match)
    if inherit_glossary:
        parent_glossary = parent_meta.get("glossary", {})
        parent_terms = parent_glossary.get("terms", [])
        
        child_glossary = meta.get("glossary", {})
        child_terms = child_glossary.get("terms", [])
        
        child_term_map = {t.get("term", "").lower(): t for t in child_terms if t.get("term")}
        
        merged_terms = []
        for pt in parent_terms:
            term_name = pt.get("term", "")
            if not term_name:
                continue
            if term_name.lower() in child_term_map:
                continue
            pt_copy = dict(pt)
            pt_copy["inherited"] = True
            if "inherited_from" not in pt_copy:
                pt_copy["inherited_from"] = parent_name
            merged_terms.append(pt_copy)
            
        for ct in child_terms:
            ct_copy = dict(ct)
            ct_copy["inherited"] = False
            merged_terms.append(ct_copy)
            
        meta["glossary"] = {"terms": merged_terms}
    else:
        # If not inheriting, mark child terms as not inherited
        if "glossary" in meta and "terms" in meta["glossary"]:
            for term in meta["glossary"]["terms"]:
                term["inherited"] = False
        
    if inherit_context:
        parent_context = parent_meta.get("context_guide", "")
        child_context = meta.get("context_guide", "")
        if parent_context:
            if child_context:
                meta["context_guide"] = f"{child_context}{_UNIVERSE_CONTEXT_PREFIX}{parent_name}) ---\n{parent_context}"
            else:
                meta["context_guide"] = parent_context

    return meta


# The exact prefix load_resolved_project_metadata appends when merging a parent's
# context into a child. Single source of truth so the strip helper can't drift from
# the format above.
_UNIVERSE_CONTEXT_PREFIX = "\n\n--- Universe Context ("


def strip_inherited_context(context_guide: Optional[str]) -> str:
    """Remove the appended parent 'Universe Context' block(s) added at resolve time,
    leaving only the child's own context. Idempotent; self-heals compounded data."""
    if not context_guide:
        return context_guide or ""
    idx = context_guide.find(_UNIVERSE_CONTEXT_PREFIX)
    return context_guide[:idx].rstrip() if idx != -1 else context_guide


def save_project_metadata(project_name: str, metadata: Dict) -> None:
    """Save project metadata to JSON file."""
    invalidate_arr_library_cache()
    project_file = PROJECTS_DIR / project_name / "project.json"
    with open(project_file, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)


def delete_project(project_name: str) -> bool:
    """Delete a project and all its data."""
    invalidate_arr_library_cache()
    _invalidate_episodes_list_cache(project_name)
    project_dir = PROJECTS_DIR / project_name
    if project_dir.exists():
        shutil.rmtree(project_dir)
        try:
            from utils import metadata_index
            metadata_index.remove_project(project_name)
        except Exception:
            pass
        return True
    return False


def list_episodes(project_name: str) -> List[str]:
    """List all episode names for a project."""
    episodes_dir = PROJECTS_DIR / project_name / "episodes"
    if not episodes_dir.exists():
        return []
    return sorted([d.name for d in episodes_dir.iterdir() if d.is_dir()])


def save_episode(project_name: str, episode_name: str, data: List[Dict], metadata: Optional[Dict] = None, update_stats: bool = True) -> None:
    """
    Save episode data and optional metadata.
    """
    invalidate_arr_library_cache()
    _invalidate_episodes_list_cache(project_name)
    # Count needs_review lines so the review queue can find flagged episodes via the index
    # instead of scanning every episode's data.json (Plan 29 perf fix).
    review_count = sum(1 for l in data if l.get("needs_review")) if data else 0
    lock = get_episode_lock(project_name, episode_name)
    with lock:
        episode_dir = PROJECTS_DIR / project_name / "episodes" / episode_name
        episode_dir.mkdir(parents=True, exist_ok=True)

        write_json_atomic(episode_dir / "data.json", {"data": data})

        if metadata is not None:
            metadata["needs_review_count"] = review_count
            write_json_atomic(episode_dir / "metadata.json", metadata)

    # Index first, THEN recompute stats — so the indexed COUNT in
    # update_project_stats_on_change reflects this episode's new state.
    idx_meta = metadata if metadata is not None else (load_episode_metadata(project_name, episode_name) or {})
    idx_meta = {**idx_meta, "needs_review_count": review_count}
    _index_episode(project_name, episode_name, idx_meta)
    try:
        from utils import metadata_index
        metadata_index.set_review_count(project_name, episode_name, review_count)
    except Exception:
        pass

    if update_stats:
        update_project_stats_on_change(project_name)


def _index_episode(project_name: str, episode_name: str, ep_meta: Dict) -> None:
    """Best-effort upsert into the metadata index (Plan 03)."""
    try:
        from utils import metadata_index
        pmeta = load_project_metadata(project_name) or {}
        disabled = bool(pmeta.get("arr_disabled") or pmeta.get("bazarr_disabled"))
        orig = original_subtitle_exists(project_name, episode_name)
        metadata_index.upsert(project_name, episode_name, ep_meta or {}, orig, disabled)
    except Exception:
        pass


def save_episode_metadata(project_name: str, episode_name: str, metadata: Dict, update_stats: bool = True):
    """Save episode metadata only. See ``save_episode`` for ``update_stats``."""
    invalidate_arr_library_cache()
    _invalidate_episodes_list_cache(project_name)
    lock = get_episode_lock(project_name, episode_name)
    with lock:
        episode_dir = PROJECTS_DIR / project_name / "episodes" / episode_name
        episode_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(episode_dir / "metadata.json", metadata)

    _index_episode(project_name, episode_name, metadata)  # index first, then count
    if update_stats:
        update_project_stats_on_change(project_name)



def load_episode_metadata(project_name: str, episode_name: str) -> Optional[Dict]:
    """Load episode metadata only."""
    metadata_file = PROJECTS_DIR / project_name / "episodes" / episode_name / "metadata.json"
    if not metadata_file.exists():
        return None

    lock = get_episode_lock(project_name, episode_name)
    with lock:
        try:
            with open(metadata_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None


def load_episode(project_name: str, episode_name: str) -> Optional[Dict]:
    """Load episode data and metadata."""
    episode_dir = PROJECTS_DIR / project_name / "episodes" / episode_name
    data_file = episode_dir / "data.json"

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

        proj_meta = load_project_metadata(project_name)
        primary_lang = proj_meta.get("target_language", "Greek") if proj_meta else "Greek"
        from utils.language_codes import to_code
        primary_code = to_code(primary_lang)

        import hashlib
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


def delete_episode(project_name: str, episode_name: str) -> bool:
    """Delete an episode and its data."""
    invalidate_arr_library_cache()
    _invalidate_episodes_list_cache(project_name)
    episode_dir = PROJECTS_DIR / project_name / "episodes" / episode_name
    if episode_dir.exists():
        shutil.rmtree(episode_dir)
        update_project_stats_on_change(project_name)
        try:
            from utils import metadata_index
            metadata_index.remove(project_name, episode_name)
        except Exception:
            pass
        return True
    return False


def update_project_stats_on_change(project_name: str) -> None:
    """Recalculate project stats based on imported episodes and update project.json."""
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
    # This is called on every episode save, so the old per-episode scan made a
    # translation batch O(n^2) in file reads — the dominant disk cost during translation.
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
                or ep_meta.get("bazarr_has_target")
                or ep_meta.get("translated")
            ):
                translated += 1

    # Update relevant namespace
    is_arr = bool(meta.get("arr_source"))
    is_bazarr = bool(meta.get("bazarr_source"))
    # If neither is set, default to arr
    if not is_arr and not is_bazarr:
        is_arr = True

    if meta.get("type") == "movie" or meta.get("arr_media_type") == "movie" or meta.get("bazarr_media_type") == "movie":
        total_count = 1 if len(non_siblings) > 0 else 0
        translated_count = 1 if translated > 0 else 0
    else:
        # Keep existing or fallback to len(non_siblings)
        current_total = meta.get("arr_total_episodes") if is_arr else meta.get("bazarr_total_episodes")
        if current_total is None:
            current_total = len(non_siblings)
        else:
            current_total = max(current_total, len(non_siblings))
        
        total_count = current_total
        translated_count = translated

    if is_arr:
        meta["arr_total_episodes"] = total_count
        meta["arr_translated_episodes"] = translated_count
    if is_bazarr:
        meta["bazarr_total_episodes"] = total_count
        meta["bazarr_translated_episodes"] = translated_count

    save_project_metadata(project_name, meta)


# Global Configuration

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"

_config_defaults_cache: Optional[Dict] = None


def _config_defaults() -> Dict:
    """Canonical default values for every known setting — single source of truth
    (the SettingsRequest schema). Cached after first build."""
    global _config_defaults_cache
    if _config_defaults_cache is None:
        try:
            from routers.schemas import SettingsRequest
            _config_defaults_cache = SettingsRequest().model_dump()
        except Exception:
            _config_defaults_cache = {}
    return dict(_config_defaults_cache)


def _load_raw_config() -> Dict:
    """The on-disk config only (no defaults overlaid)."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def load_global_config() -> Dict:
    """Load global configuration: schema defaults overlaid with on-disk values.

    Guarantees every known setting is present with its canonical default, so callers
    don't need to repeat inline defaults (which previously drifted).
    """
    return {**_config_defaults(), **_load_raw_config()}


def save_global_config(config: Dict) -> None:
    """Merge ``config`` into the on-disk config (defaults are not persisted)."""
    current = _load_raw_config()
    current.update(config)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(current, f, indent=2, ensure_ascii=False)


def save_original_subtitle(project_name: str, episode_name: str, content: str, filename: Optional[str] = None) -> None:
    """Save the original subtitle using its true original file extension (e.g. original.ass).
    
    Check for its presence under any valid extension (.srt, .ass, .ssa),
    and save the format mapping in metadata (using original_extension and original_format keys).
    """
    episode_dir = PROJECTS_DIR / project_name / "episodes" / episode_name
    episode_dir.mkdir(parents=True, exist_ok=True)

    ext = "srt"
    if filename:
        lower_fn = filename.lower()
        if lower_fn.endswith(".srt"):
            ext = "srt"
        elif lower_fn.endswith(".ass"):
            ext = "ass"
        elif lower_fn.endswith(".ssa"):
            ext = "ssa"
        else:
            from utils.subtitle_io import detect_format
            ext = detect_format(content=content)
    else:
        # Content sniffing wins over any original.<ext> already on disk: re-saving
        # different content (e.g. an SRT-reconstructed source over a former .ass)
        # must land in the file matching the new content, not inherit the stale one.
        from utils.subtitle_io import detect_format
        ext = detect_format(content=content)

    with open(episode_dir / f"original.{ext}", 'w', encoding='utf-8') as f:
        f.write(content)

    # Clean up other extensions
    for possible_ext in ["srt", "ass", "ssa"]:
        if possible_ext != ext:
            try:
                (episode_dir / f"original.{possible_ext}").unlink(missing_ok=True)
            except Exception:
                pass

    # Save the format mapping in metadata
    meta = load_episode_metadata(project_name, episode_name) or {}
    meta["original_extension"] = ext
    meta["original_format"] = "ass" if ext in ["ass", "ssa"] else "srt"
    save_episode_metadata(project_name, episode_name, meta, update_stats=True)


def save_original_srt(project_name: str, episode_name: str, content: str, filename: Optional[str] = None) -> None:
    """Save original SRT file content (delegates to generalized helper)."""
    save_original_subtitle(project_name, episode_name, content, filename=filename)


def load_original_subtitle(project_name: str, episode_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Load original subtitle file content and its extension, returning (content, extension)."""
    meta = load_episode_metadata(project_name, episode_name)
    ext = None
    if meta:
        ext = meta.get("original_extension")

    episode_dir = PROJECTS_DIR / project_name / "episodes" / episode_name

    if ext in ["srt", "ass", "ssa"]:
        subtitle_file = episode_dir / f"original.{ext}"
        if subtitle_file.exists():
            with open(subtitle_file, 'r', encoding='utf-8') as f:
                return f.read(), ext

    # Fall back to checking files on disk
    for possible_ext in ["srt", "ass", "ssa"]:
        subtitle_file = episode_dir / f"original.{possible_ext}"
        if subtitle_file.exists():
            with open(subtitle_file, 'r', encoding='utf-8') as f:
                return f.read(), possible_ext

    return None, None


def load_original_srt(project_name: str, episode_name: str) -> Optional[str]:
    """Load original subtitle file content (supporting srt, ass, ssa)."""
    content, _ = load_original_subtitle(project_name, episode_name)
    return content


def original_subtitle_exists(project_name: str, episode_name: str) -> bool:
    """Check if an original subtitle file exists with any valid extension (srt, ass, ssa)."""
    episode_dir = PROJECTS_DIR / project_name / "episodes" / episode_name
    # Check metadata first
    meta = load_episode_metadata(project_name, episode_name)
    if meta:
        ext = meta.get("original_extension")
        if ext in ["srt", "ass", "ssa"] and (episode_dir / f"original.{ext}").exists():
            return True

    # Fallback to checking disk
    for possible_ext in ["srt", "ass", "ssa"]:
        if (episode_dir / f"original.{possible_ext}").exists():
            return True
    return False


def get_arr_library() -> Dict:
    """Return structured library data for Sonarr/Radarr-synced projects.

    Groups projects by type (series / movie) and disabled status.
    Reads from local project metadata — does NOT contact Sonarr/Radarr.

    Returns::

        {
            "series": [...],
            "movies": [...],
            "disabled": [...],
            "last_sync": "2026-05-10T...",
            "totals": { "series": 12, "movies": 8, "disabled": 2 }
        }
    """
    global _arr_library_cache
    if _arr_library_cache is not None:
        return _arr_library_cache

    all_names = list_projects()
    series = []
    movies = []
    disabled = []

    for p_name in all_names:
        meta = load_project_metadata(p_name)
        if not meta:
            continue
        # Include arr-sourced projects AND old bazarr projects (backward compat)
        if not (meta.get("arr_source") or meta.get("bazarr_source")):
            continue

        # Check for cached stats first
        meta_total = meta.get("arr_total_episodes")
        if meta_total is None:
            meta_total = meta.get("bazarr_total_episodes")
            
        meta_translated = meta.get("arr_translated_episodes")
        if meta_translated is None:
            meta_translated = meta.get("bazarr_translated_episodes")

        if meta_total is not None and meta_translated is not None:
            episode_count = meta_total
            translated = meta_translated
        else:
            episodes = list_episodes(p_name)
            non_siblings = []
            for ep_name in episodes:
                if ' [' in ep_name:
                    ep_meta = load_episode_metadata(p_name, ep_name)
                    if ep_meta and ep_meta.get("arr_secondary_of"):
                        continue
                non_siblings.append(ep_name)

            translated = 0
            for ep_name in non_siblings:
                ep_meta = load_episode_metadata(p_name, ep_name)
                if ep_meta and (ep_meta.get("arr_has_target") or ep_meta.get("bazarr_has_target")):
                    translated += 1
                    continue
                ep = load_episode(p_name, ep_name)
                if ep and any(entry.get("translated") for entry in ep.get("data", [])):
                    translated += 1
            episode_count = len(non_siblings)

        entry = {
            **meta,
            "name": p_name,
            "episode_count": episode_count,
            "translated_count": translated,
        }

        if meta.get("arr_disabled") or meta.get("bazarr_disabled"):
            disabled.append(entry)
        elif meta.get("arr_media_type") == "movie" or meta.get("bazarr_media_type") == "movie" or meta.get("type") == "movie":
            movies.append(entry)
        else:
            series.append(entry)

    last_sync = None
    for p_name in all_names:
        meta = load_project_metadata(p_name)
        if not meta:
            continue
        ts = meta.get("arr_last_sync") or meta.get("bazarr_last_sync")
        if ts and (last_sync is None or ts > last_sync):
            last_sync = ts

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


def get_project_line_frequencies(project_name: str) -> Dict[str, int]:
    """Scan all episode data.json files in a project and count frequencies of original lines."""
    from collections import Counter
    frequencies = Counter()
    episodes = list_episodes(project_name)
    for ep_name in episodes:
        episode_dir = PROJECTS_DIR / project_name / "episodes" / ep_name
        data_file = episode_dir / "data.json"
        if not data_file.exists():
            continue
        try:
            with open(data_file, 'r', encoding='utf-8') as f:
                result = json.load(f)
            # Handle list or dict format
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

