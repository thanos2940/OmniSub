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
            "translation_model": "gemini-flash-latest"
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
