"""
File-Based Storage Manager for Episode Data

Handles project and episode data persistence using JSON files.
Project metadata (glossary, context) is migrating to ADK Sessions.
"""

import json
import shutil
from pathlib import Path
from typing import List, Dict, Optional

PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projects"


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
    
    default_metadata = {
        "show_name": project_name,
        "target_language": "English",
        "glossary": {"terms": []},
        "context_guide": "",
        "parent_project": None,
        "type": "show",
        "settings": {
            "scan_model": "gemini-flash-lite-latest",
            "translation_model": "gemini-flash-latest",
            "apply_subtitle_edit_fixes": True
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


def save_project_metadata(project_name: str, metadata: Dict) -> None:
    """Save project metadata to JSON file."""
    project_file = PROJECTS_DIR / project_name / "project.json"
    with open(project_file, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)


def delete_project(project_name: str) -> bool:
    """Delete a project and all its data."""
    project_dir = PROJECTS_DIR / project_name
    if project_dir.exists():
        shutil.rmtree(project_dir)
        return True
    return False


def list_episodes(project_name: str) -> List[str]:
    """List all episode names for a project."""
    episodes_dir = PROJECTS_DIR / project_name / "episodes"
    if not episodes_dir.exists():
        return []
    return sorted([d.name for d in episodes_dir.iterdir() if d.is_dir()])


def save_episode(project_name: str, episode_name: str, data: List[Dict], metadata: Optional[Dict] = None) -> None:
    """
    Save episode data and optional metadata.
    
    Args:
        project_name: Project name
        episode_name: Episode name
        data: Parsed SRT data
        metadata: Optional episode metadata
    """
    episode_dir = PROJECTS_DIR / project_name / "episodes" / episode_name
    episode_dir.mkdir(parents=True, exist_ok=True)
    
    with open(episode_dir / "data.json", 'w', encoding='utf-8') as f:
        json.dump({"data": data}, f, indent=2, ensure_ascii=False)
    
    if metadata:
        with open(episode_dir / "metadata.json", 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)


def load_episode_metadata(project_name: str, episode_name: str) -> Optional[Dict]:
    """Load episode metadata only."""
    metadata_file = PROJECTS_DIR / project_name / "episodes" / episode_name / "metadata.json"
    if not metadata_file.exists():
        return None

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

    with open(data_file, 'r', encoding='utf-8') as f:
        result = json.load(f)
        
    # Handle legacy list format
    if isinstance(result, list):
        print(f"DEBUG: Converted legacy list format for {episode_name}")
        result = {"data": result}

    metadata = load_episode_metadata(project_name, episode_name)
    if metadata:
        result["metadata"] = metadata

    return result


def delete_episode(project_name: str, episode_name: str) -> bool:
    """Delete an episode and its data."""
    episode_dir = PROJECTS_DIR / project_name / "episodes" / episode_name
    if episode_dir.exists():
        shutil.rmtree(episode_dir)
        return True
    return False


# Global Configuration

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"

def load_global_config() -> Dict:
    """Load global configuration from JSON file."""
    if not CONFIG_FILE.exists():
        return {
            "default_target_language": "English",
            "default_scan_model": "gemini-flash-lite-latest",
            "default_translation_model": "gemini-flash-latest",
            "default_context_model": "gemini-flash-latest",
            "default_glossary_model": "gemini-flash-latest"
        }
    
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_global_config(config: Dict) -> None:
    """Save global configuration to JSON file."""
    # Preserve existing keys (like api_key) if not in new config
    current = load_global_config()
    current.update(config)
    
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(current, f, indent=2, ensure_ascii=False)


def save_original_srt(project_name: str, episode_name: str, content: str) -> None:
    """Save original SRT file content."""
    episode_dir = PROJECTS_DIR / project_name / "episodes" / episode_name
    episode_dir.mkdir(parents=True, exist_ok=True)
    with open(episode_dir / "original.srt", 'w', encoding='utf-8') as f:
        f.write(content)


def load_original_srt(project_name: str, episode_name: str) -> Optional[str]:
    """Load original SRT file content."""
    srt_file = PROJECTS_DIR / project_name / "episodes" / episode_name / "original.srt"
    if not srt_file.exists():
        return None
    with open(srt_file, 'r', encoding='utf-8') as f:
        return f.read()


# ---------------------------------------------------------------------------
# Bazarr Library Queries
# ---------------------------------------------------------------------------

def list_bazarr_projects() -> List[Dict]:
    """Return metadata for all projects created by Bazarr sync.

    Includes both enabled and disabled projects.  Each dict contains
    the full project metadata plus:
      - ``name``: the project directory name
      - ``episode_count``: number of imported episodes
      - ``translated_count``: number of episodes with translations
    """
    results = []
    for p_name in list_projects():
        meta = load_project_metadata(p_name)
        if not meta or not meta.get("bazarr_source"):
            continue

        episodes = list_episodes(p_name)
        
        # Use pre-calculated stats from sync engine if available
        meta_total = meta.get("bazarr_total_episodes")
        meta_translated = meta.get("bazarr_translated_episodes")
        
        if meta_total is not None and meta_translated is not None:
            # Stats are already in project.json, use them for performance
            results.append({
                **meta,
                "name": p_name,
                "episode_count": meta_total,
                "translated_count": meta_translated,
            })
            continue

        # Fallback: calculate manually (expensive)
        translated = 0
        for ep_name in episodes:
            ep_meta = load_episode_metadata(p_name, ep_name)
            if ep_meta and ep_meta.get("bazarr_has_target"):
                translated += 1
                continue
                
            ep = load_episode(p_name, ep_name)
            if ep and any(
                entry.get("translated") for entry in ep.get("data", [])
            ):
                translated += 1

        results.append({
            **meta,
            "name": p_name,
            "episode_count": len(episodes),
            "translated_count": translated,
        })

    return results


def get_bazarr_library() -> Dict:
    """Return structured library data for the frontend.

    Groups Bazarr projects by media type (series / movie) and
    separates disabled entries into their own list.

    Returns::

        {
            "series": [...],
            "movies": [...],
            "disabled": [...],
            "last_sync": "2026-05-10T...",
            "totals": { "series": 12, "movies": 8, "disabled": 2 }
        }
    """
    all_projects = list_bazarr_projects()

    series = []
    movies = []
    disabled = []

    for proj in all_projects:
        if proj.get("bazarr_disabled"):
            disabled.append(proj)
        elif proj.get("bazarr_media_type") == "movie":
            movies.append(proj)
        else:
            series.append(proj)

    # Find last sync timestamp across all projects
    last_sync = None
    for proj in all_projects:
        ts = proj.get("bazarr_last_sync")
        if ts and (last_sync is None or ts > last_sync):
            last_sync = ts

    return {
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

