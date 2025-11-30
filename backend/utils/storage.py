"""
MIGRATION NOTE: This storage module is being phased out in favor of ADK Sessions.
During migration, we maintain both systems:
- JSON files: Episode data (SRT content, translations)
- ADK Sessions: Project metadata (glossary, context, settings)

After full migration, only episode data will remain in JSON.
"""

import json
import os
import shutil
from pathlib import Path
from typing import List, Dict, Optional

# Base directory for all projects (backend/projects/)
PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projects"


# --- Hybrid Storage Accessors (ADK Migration) ---

async def get_project_glossary_adk(project_name: str, session_manager) -> Dict:
    """
    Get glossary from ADK session (new system).
    Falls back to JSON if session doesn't exist yet.
    """
    try:
        state = await session_manager.get_project_state(project_name)
        return state.get("glossary", {"terms": []})
    except:
        # Fallback to JSON
        metadata = load_project_metadata(project_name)
        return metadata.get("glossary", {"terms": []}) if metadata else {"terms": []}


def ensure_projects_dir() -> None:
    """
    Ensure the projects directory exists, creating it if necessary.
    
    This is called automatically by most functions to guarantee the directory structure.
    """
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def list_projects() -> List[str]:
    """
    List all available projects.
    
    Returns:
        List of project names (directory names) in alphabetical order
        
    Example:
        >>> list_projects()
        ['Dune', 'Frieren', 'MyAnime']
    """
    ensure_projects_dir()
    return sorted([d.name for d in PROJECTS_DIR.iterdir() if d.is_dir()])


def create_project(project_name: str, metadata: Optional[Dict] = None) -> Dict:
    """
    Create a new project with default or custom metadata.
    
    Args:
        project_name: Name of the project (will become directory name)
        metadata: Optional project metadata. If None, creates default structure with:
                 - show_name: Project name
                 - glossary: Empty terms list
                 - context_guide: Empty string
                 - type: Standard project (creates episodes directory)
                 
    Returns:
        The metadata dictionary that was saved
        
    Example:
        >>> create_project("MyShow", {"show_name": "My Show", "target_language": "Spanish"})
        
    Note:
        - Parent projects (type="parent") do NOT get an episodes directory
        - Project directory is created if it doesn't exist
    """
    ensure_projects_dir()
    project_path = PROJECTS_DIR / project_name
    project_path.mkdir(exist_ok=True)
    
    # Set default metadata if none provided
    if metadata is None:
        metadata = {
            "show_name": project_name,
            "glossary": {"terms": []},
            "context_guide": ""
        }
    
    # Only create episodes directory for non-parent projects
    # Parent projects are used for grouping seasons/series
    if metadata.get("type") != "parent":
        (project_path / "episodes").mkdir(exist_ok=True)
    
    save_project_metadata(project_name, metadata)
    return metadata


def save_project_metadata(project_name: str, metadata: Dict) -> None:
    """
    Save project metadata to project.json file.
    
    Args:
        project_name: Name of the project
        metadata: Dictionary containing project configuration and glossary
        
    Note:
        - File is saved with UTF-8 encoding to support international characters
        - JSON is formatted with 2-space indentation for readability
        - ensure_ascii=False preserves Unicode characters
    """
    project_path = PROJECTS_DIR / project_name
    with open(project_path / "project.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)


def load_project_metadata(project_name: str) -> Optional[Dict]:
    """
    Load project metadata from project.json file.
    
    Args:
        project_name: Name of the project
        
    Returns:
        Dictionary containing project metadata, or None if project doesn't exist
        
    Example:
        >>> metadata = load_project_metadata("MyShow")
        >>> if metadata:
        ...     print(metadata['show_name'])
    """
    project_path = PROJECTS_DIR / project_name
    meta_file = project_path / "project.json"
    
    if not meta_file.exists():
        return None
        
    with open(meta_file, "r", encoding="utf-8") as f:
        return json.load(f)


def save_episode(
    project_name: str, 
    episode_name: str, 
    data: List[Dict], 
    original_content: Optional[str] = None, 
    metadata: Optional[Dict] = None
) -> None:
    """
    Save episode data, original SRT content, and metadata.
    
    Args:
        project_name: Name of the parent project
        episode_name: Name/identifier of the episode (e.g., "S01E01", "Episode 1")
        data: List of subtitle entry dictionaries (from srt_parser.parse_srt)
        original_content: Optional original SRT file content to preserve
        metadata: Optional episode-specific metadata (translation status, notes, etc.)
        
    Note:
        - Creates episode directory structure if it doesn't exist
        - All files use UTF-8 encoding for international character support
        - Original SRT is preserved for reference and potential re-translation
    """
    episode_dir = PROJECTS_DIR / project_name / "episodes" / episode_name
    episode_dir.mkdir(parents=True, exist_ok=True)
    
    # Save parsed subtitle data
    with open(episode_dir / "data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    # Save original SRT file if provided
    if original_content:
        with open(episode_dir / "original.srt", "w", encoding="utf-8") as f:
            f.write(original_content)
    
    # Save episode metadata if provided
    if metadata:
        with open(episode_dir / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)


def load_episode(project_name: str, episode_name: str) -> Optional[Dict]:
    """
    Load all episode data including subtitle entries, original SRT, and metadata.
    
    Args:
        project_name: Name of the parent project
        episode_name: Name/identifier of the episode
        
    Returns:
        Dictionary containing:
            - data: List of subtitle entry dictionaries
            - original_content: Original SRT file content (empty string if not found)
            - metadata: Episode metadata (empty dict if not found)
        Returns None if episode doesn't exist
        
    Example:
        >>> episode = load_episode("MyShow", "S01E01")
        >>> if episode:
        ...     for entry in episode['data']:
        ...         print(entry['original'], "->", entry['translated'])
    """
    episode_dir = PROJECTS_DIR / project_name / "episodes" / episode_name
    data_file = episode_dir / "data.json"
    
    if not data_file.exists():
        return None
    
    # Load subtitle data (required)
    with open(data_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Load original SRT file if it exists
    original_content = ""
    orig_file = episode_dir / "original.srt"
    if orig_file.exists():
        with open(orig_file, "r", encoding="utf-8") as f:
            original_content = f.read()
    
    # Load metadata if it exists
    metadata = {}
    meta_file = episode_dir / "metadata.json"
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    
    return {
        "data": data, 
        "original_content": original_content, 
        "metadata": metadata
    }


def list_episodes(project_name: str) -> List[str]:
    """
    List all episodes in a project.
    
    Args:
        project_name: Name of the project
        
    Returns:
        List of episode names (directory names), or empty list if no episodes exist
        
    Example:
        >>> list_episodes("MyShow")
        ['S01E01', 'S01E02', 'S01E03']
    """
    episodes_dir = PROJECTS_DIR / project_name / "episodes"
    
    if not episodes_dir.exists():
        return []
        
    return sorted([d.name for d in episodes_dir.iterdir() if d.is_dir()])


def delete_episode(project_name: str, episode_name: str) -> bool:
    """
    Delete an episode and all its associated data.
    
    Args:
        project_name: Name of the parent project
        episode_name: Name/identifier of the episode to delete
        
    Returns:
        True if episode was deleted, False if it didn't exist
        
    Warning:
        This permanently deletes the episode directory and all files within it.
        This operation cannot be undone.
    """
    episode_dir = PROJECTS_DIR / project_name / "episodes" / episode_name
    
    if episode_dir.exists() and episode_dir.is_dir():
        shutil.rmtree(episode_dir)
        return True
    
    return False


def update_episode_metadata(
    project_name: str, 
    episode_name: str, 
    metadata_updates: Dict
) -> bool:
    """
    Update specific fields in episode metadata.
    
    This performs a partial update, merging new values with existing metadata
    rather than replacing the entire metadata file.
    
    Args:
        project_name: Name of the parent project
        episode_name: Name/identifier of the episode
        metadata_updates: Dictionary of metadata fields to update
        
    Returns:
        True if update was successful, False if episode doesn't exist
        
    Example:
        >>> update_episode_metadata("MyShow", "S01E01", {
        ...     "translation_status": "completed",
        ...     "review_notes": "Check line 42"
        ... })
    """
    episode_dir = PROJECTS_DIR / project_name / "episodes" / episode_name
    
    if not episode_dir.exists():
        return False
    
    meta_file = episode_dir / "metadata.json"
    
    # Load existing metadata or start with empty dict
    metadata = {}
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    
    # Merge updates into existing metadata
    metadata.update(metadata_updates)
    
    # Save updated metadata
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    return True
