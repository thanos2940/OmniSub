"""
Project Metadata, Universe Inheritance, and Hierarchy Resolution.
"""

import json
import logging
import shutil
import threading
from typing import List, Dict, Optional

from utils.storage_modules.atomic_io import (
    _safe_project_dir,
    ensure_projects_dir,
    _get_projects_dir,
    write_json_atomic as _atomic_write_json,
)
from utils.storage_modules.config_store import load_global_config
import utils.paths as paths

PROJECTS_DIR = paths.PROJECTS_DIR

logger = logging.getLogger(__name__)

_project_locks = {}
_project_locks_lock = threading.Lock()


def _get_project_lock(project_name: str) -> threading.RLock:
    """Return a per-project threading.RLock for thread-safe metadata operations."""
    with _project_locks_lock:
        if project_name not in _project_locks:
            _project_locks[project_name] = threading.RLock()
        return _project_locks[project_name]


get_project_lock = _get_project_lock

# The exact prefix load_resolved_project_metadata appends when merging a parent's
# context into a child. Single source of truth so the strip helper can't drift from
# the format above.
_UNIVERSE_CONTEXT_PREFIX = "\n\n--- Universe Context ("


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


def list_projects() -> List[str]:
    """List all project names in alphabetical order."""
    ensure_projects_dir()
    projects_dir = _get_projects_dir()
    return sorted([d.name for d in projects_dir.iterdir() if d.is_dir()])


VALID_PRIORITY_TIERS = {"high", "normal", "low", "ignored"}


def resolve_project_priority(project_meta: Optional[Dict]) -> str:
    """Resolve the effective priority tier ("high", "normal", "low", "ignored") for a project."""
    if not project_meta or not isinstance(project_meta, dict):
        return "normal"
    arr_disabled = bool(project_meta.get("arr_disabled"))
    tp = project_meta.get("translation_priority")
    if tp in VALID_PRIORITY_TIERS:
        if arr_disabled and tp == "normal":
            return "ignored"
        return str(tp)
    if arr_disabled:
        return "ignored"
    return "normal"


def create_project(project_name: str, metadata: Optional[Dict] = None) -> Dict:
    """Create a new project with default or custom metadata.
    
    Args:
        project_name: Name of the project (becomes directory name)
        metadata: Optional initial metadata
        
    Returns:
        Complete project metadata
    """
    ensure_projects_dir()
    project_dir = _safe_project_dir(project_name)
    project_dir.mkdir(exist_ok=True)
    (project_dir / "episodes").mkdir(exist_ok=True)
    
    global_config = load_global_config()
    default_settings = {
        "apply_subtitle_edit_fixes": global_config.get("apply_subtitle_edit_fixes", True),
        "inherit_glossary": True,
        "inherit_context": True,
        "inherit_characters": True,
        "temperature": global_config.get("temperature", 0.3),
        "top_k": global_config.get("top_k", 64),
        "top_p": global_config.get("top_p", 0.95),
        "translation_model": global_config.get("default_translation_model", "gemini-2.5-flash"),
        "fallback_translation_model": global_config.get("fallback_translation_model", "gemini-2.5-flash"),
        "scan_model": global_config.get("default_scan_model", "gemini-2.5-flash"),
        "context_model": global_config.get("default_context_model", "gemini-2.5-flash"),
        "glossary_model": global_config.get("default_glossary_model", "gemini-2.5-flash"),
        "review_model": global_config.get("review_model", "gemini-2.5-flash"),
    }
    
    default_metadata = {
        "show_name": project_name,
        "target_language": global_config.get("default_target_language", "English"),
        "glossary": {"terms": []},
        "context_guide": "",
        "parent_project": None,
        "type": "show",
        "translation_priority": "normal",
        "arr_disabled": False,
        "settings": default_settings,
    }
    
    if metadata:
        passed_settings = metadata.get("settings")
        default_metadata.update(metadata)
        if passed_settings and isinstance(passed_settings, dict):
            merged_settings = dict(default_settings)
            merged_settings.update(passed_settings)
            default_metadata["settings"] = merged_settings
    
    save_project_metadata(project_name, default_metadata)
    return default_metadata


def load_project_metadata(project_name: str) -> Optional[Dict]:
    """Load project metadata from JSON file."""
    project_file = _safe_project_dir(project_name) / "project.json"
    with _get_project_lock(project_name):
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
    suppressed_terms = {t.lower().strip() for t in meta.get("suppressed_terms", []) if t}
    if inherit_glossary:
        parent_glossary = parent_meta.get("glossary", {})
        parent_terms = parent_glossary.get("terms", [])
        parent_term_map = {t.get("term", "").lower().strip(): t for t in parent_terms if t.get("term")}
        
        child_glossary = meta.get("glossary", {})
        child_terms = child_glossary.get("terms", [])
        child_term_map = {t.get("term", "").lower().strip(): t for t in child_terms if t.get("term")}
        
        merged_terms = []
        for pt in parent_terms:
            term_name = pt.get("term", "")
            if not term_name:
                continue
            term_lower = term_name.lower().strip()
            if term_lower in suppressed_terms:
                continue
            if term_lower in child_term_map:
                continue
            pt_copy = dict(pt)
            pt_copy["inherited"] = True
            pt_copy["is_override"] = False
            pt_copy["upstream_modified"] = False
            if "inherited_from" not in pt_copy:
                pt_copy["inherited_from"] = parent_name
            merged_terms.append(pt_copy)
            
        for ct in child_terms:
            ct_copy = dict(ct)
            ct_copy["inherited"] = False
            term_lower = ct.get("term", "").lower().strip()
            if term_lower in parent_term_map:
                ct_copy["is_override"] = True
                ct_copy["inherited_from"] = parent_name
                parent_def = parent_term_map[term_lower]
                ct_copy["parent_term"] = dict(parent_def)
                is_diff = (
                    parent_def.get("translation", "") != ct.get("translation", "") or
                    parent_def.get("type", "") != ct.get("type", "") or
                    parent_def.get("gender", "") != ct.get("gender", "") or
                    parent_def.get("description", "") != ct.get("description", "") or
                    parent_def.get("keep_original", False) != ct.get("keep_original", False)
                )
                ct_copy["upstream_modified"] = is_diff
            else:
                ct_copy["is_override"] = False
                ct_copy["upstream_modified"] = False
            merged_terms.append(ct_copy)
            
        meta["glossary"] = {"terms": merged_terms}
    else:
        # If not inheriting, mark child terms as not inherited
        if "glossary" in meta and "terms" in meta["glossary"]:
            for term in meta["glossary"]["terms"]:
                term["inherited"] = False
                term["is_override"] = False
                term["upstream_modified"] = False
        
    if inherit_context:
        parent_context = parent_meta.get("context_guide", "")
        child_context = meta.get("context_guide", "")
        if parent_context:
            if child_context:
                meta["context_guide"] = f"{child_context}{_UNIVERSE_CONTEXT_PREFIX}{parent_name}) ---\n{parent_context}"
            else:
                meta["context_guide"] = parent_context

    return meta


def strip_inherited_context(context_guide: Optional[str]) -> str:
    """Remove the appended parent 'Universe Context' block(s) added at resolve time,
    leaving only the child's own context. Idempotent; self-heals compounded data."""
    if not context_guide:
        return context_guide or ""
    idx = context_guide.find(_UNIVERSE_CONTEXT_PREFIX)
    return context_guide[:idx].rstrip() if idx != -1 else context_guide


def get_descendant_projects(project_name: str) -> List[str]:
    """Find all projects that directly or indirectly inherit from project_name."""
    descendants = []
    direct_children = []
    for p in list_projects():
        if p == project_name:
            continue
        meta = load_project_metadata(p)
        if meta and meta.get("parent_project") == project_name:
            direct_children.append(p)
    
    for child in direct_children:
        descendants.append(child)
        descendants.extend(get_descendant_projects(child))
        
    return list(dict.fromkeys(descendants))


def save_project_metadata(project_name: str, metadata: Dict) -> None:
    """Save project metadata to JSON file under project lock atomically."""
    from utils.storage_modules.stats_and_index import invalidate_arr_library_cache
    invalidate_arr_library_cache()

    if "translation_priority" in metadata:
        tp = str(metadata.get("translation_priority") or "").lower().strip()
        if tp not in VALID_PRIORITY_TIERS:
            tp = "normal"
        if tp == "normal" and metadata.get("arr_disabled"):
            tp = "ignored"
        metadata["translation_priority"] = tp
        metadata["arr_disabled"] = (tp == "ignored")
    elif "arr_disabled" in metadata:
        if metadata["arr_disabled"]:
            metadata["translation_priority"] = "ignored"
        elif metadata.get("translation_priority") in ("ignored", None):
            metadata["translation_priority"] = "normal"

    try:
        from utils.metadata_index import get_metadata_index
        get_metadata_index().set_project_disabled(project_name, bool(metadata.get("arr_disabled")))
    except Exception:
        pass

    metadata_path = _safe_project_dir(project_name) / "project.json"
    with _get_project_lock(project_name):
        _atomic_write_json(metadata_path, metadata)


def delete_project(project_name: str) -> bool:
    """Delete a project and all its data. Safely unlinks any child projects pointing to it."""
    from utils.storage_modules.stats_and_index import invalidate_arr_library_cache
    from utils.storage_modules.episode_store import _invalidate_episodes_list_cache
    invalidate_arr_library_cache()
    _invalidate_episodes_list_cache(project_name)
    
    # Decouple any children referencing this project as their parent
    for p in list_projects():
        if p == project_name:
            continue
        try:
            meta = load_project_metadata(p)
            if meta and meta.get("parent_project") == project_name:
                meta["parent_project"] = None
                save_project_metadata(p, meta)
        except Exception:
            pass

    project_dir = _safe_project_dir(project_name)
    if project_dir.exists():
        shutil.rmtree(project_dir)
        try:
            from utils import metadata_index
            metadata_index.remove_project(project_name)
        except Exception:
            pass
        return True
    return False


try:
    import sys
    if "utils.storage" in sys.modules:
        setattr(sys.modules["utils.storage"], "resolve_project_priority", resolve_project_priority)
except Exception:
    pass
