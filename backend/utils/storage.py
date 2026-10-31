"""
File-Based Storage Manager for Episode Data (Modular Facade).

Handles project and episode data persistence using JSON files.
Subsystems are decomposed into single-responsibility modules under ``utils.storage_modules``.
"""

import logging

from utils.paths import PROJECTS_DIR, CONFIG_FILE

# 1. Atomic I/O, Safe Paths, and Locking
from utils.storage_modules.atomic_io import (
    _INVALID_NAME_CHARS,
    _RESERVED_WINDOWS_NAMES,
    safe_name,
    _safe_project_dir,
    _safe_episode_dir,
    project_dir,
    episode_dir,
    ensure_projects_dir,
    get_episode_lock,
    write_json_atomic,
    _episode_locks,
    _episode_locks_lock,
)

# 2. Global & Project Configuration
from utils.storage_modules.config_store import (
    load_global_config,
    save_global_config,
    get_project_setting,
    _config_defaults,
    _load_raw_config,
    _config_defaults_cache,
)

# 3. Project Metadata & Hierarchies
from utils.storage_modules.project_store import (
    _UNIVERSE_CONTEXT_PREFIX,
    VALID_PRIORITY_TIERS,
    resolve_project_priority,
    project_target_languages,
    list_projects,
    create_project,
    load_project_metadata,
    load_resolved_project_metadata,
    strip_inherited_context,
    get_descendant_projects,
    save_project_metadata,
    delete_project,
)

# 4. Episode Persistence, Loading, and Caching
from utils.storage_modules.episode_store import (
    episode_has_target,
    episode_translation_is_stale,
    list_episodes,
    list_episodes_with_metadata,
    save_episode,
    save_episode_metadata,
    load_episode_metadata,
    load_episode,
    load_episode_data,
    delete_episode,
    rename_episode,
    _episodes_list_cache,
    _invalidate_episodes_list_cache,
)

# 5. Original Subtitle File Operations
from utils.storage_modules.subtitle_store import (
    save_original_subtitle,
    save_original_srt,
    load_original_subtitle,
    load_original_srt,
    original_subtitle_exists,
    target_subtitle_exists,
)

# 6. Stats, Library Aggregation, and Index Sync
from utils.storage_modules.stats_and_index import (
    invalidate_arr_library_cache,
    _index_episode,
    update_project_stats_on_change,
    get_arr_library,
    get_library,
    get_project_line_frequencies,
    _arr_library_cache,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PROJECTS_DIR",
    "CONFIG_FILE",
    "safe_name",
    "project_dir",
    "episode_dir",
    "ensure_projects_dir",
    "get_episode_lock",
    "write_json_atomic",
    "load_global_config",
    "save_global_config",
    "get_project_setting",
    "project_target_languages",
    "list_projects",
    "create_project",
    "load_project_metadata",
    "load_resolved_project_metadata",
    "strip_inherited_context",
    "get_descendant_projects",
    "save_project_metadata",
    "delete_project",
    "episode_has_target",
    "episode_translation_is_stale",
    "list_episodes",
    "list_episodes_with_metadata",
    "save_episode",
    "save_episode_metadata",
    "load_episode_metadata",
    "load_episode",
    "load_episode_data",
    "delete_episode",
    "rename_episode",
    "save_original_subtitle",
    "save_original_srt",
    "load_original_subtitle",
    "load_original_srt",
    "original_subtitle_exists",
    "target_subtitle_exists",
    "invalidate_arr_library_cache",
    "update_project_stats_on_change",
    "get_arr_library",
    "get_library",
    "get_project_line_frequencies",
]
