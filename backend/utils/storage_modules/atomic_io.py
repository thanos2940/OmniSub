"""
Atomic File I/O, Safe Path Resolution, and Episode Locking.
"""

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import utils.paths as paths

logger = logging.getLogger(__name__)

# Characters/values that must never appear in a project or episode name once it's
# used as a filesystem path segment. Blocks both traversal (path separators, "..")
# and Windows-specific gotchas (reserved device names, trailing dots/spaces).
_INVALID_NAME_CHARS = set('<>:"/\\|?*\x00')
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


PROJECTS_DIR = paths.PROJECTS_DIR


def _get_projects_dir() -> Path:
    """Dynamic resolution of PROJECTS_DIR supporting test monkeypatching."""
    try:
        import utils.storage as storage_mod
        p = getattr(storage_mod, "PROJECTS_DIR", None)
        if p is not None and p != paths.PROJECTS_DIR:
            return Path(p)
    except Exception:
        pass
    if PROJECTS_DIR != paths.PROJECTS_DIR:
        return Path(PROJECTS_DIR)
    return paths.PROJECTS_DIR


def safe_name(name: str) -> str:
    """Validate a project/episode name for safe use as a single filesystem path
    segment. Raises ValueError on anything that could traverse out of PROJECTS_DIR
    or collide with a reserved Windows device name. Returns the name unchanged
    (never mutates it) so call sites can use it as a drop-in guard."""
    if not name or name != name.strip() or name in (".", ".."):
        raise ValueError(f"Invalid name: {name!r}")
    if set(name) & _INVALID_NAME_CHARS:
        raise ValueError(f"Invalid name: {name!r}")
    if name.split(".")[0].upper() in _RESERVED_WINDOWS_NAMES:
        raise ValueError(f"Invalid name: {name!r}")
    return name


def _safe_project_dir(project_name: str) -> Path:
    """Resolve a project's directory, guaranteed to stay inside PROJECTS_DIR."""
    root_dir = _get_projects_dir()
    candidate = (root_dir / safe_name(project_name)).resolve()
    root = root_dir.resolve()
    if candidate != root and not candidate.is_relative_to(root):
        raise ValueError(f"Path escapes projects directory: {project_name!r}")
    return candidate


def _safe_episode_dir(project_name: str, episode_name: str) -> Path:
    """Resolve an episode's directory, guaranteed to stay inside PROJECTS_DIR."""
    candidate = (_safe_project_dir(project_name) / "episodes" / safe_name(episode_name)).resolve()
    root = _get_projects_dir().resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"Path escapes projects directory: {episode_name!r}")
    return candidate


# Public aliases for other modules (``storage.project_dir(name)`` /
# ``storage.episode_dir(project, episode)``)
project_dir = _safe_project_dir
episode_dir = _safe_episode_dir


def ensure_projects_dir() -> None:
    """Ensure projects directory exists."""
    _get_projects_dir().mkdir(parents=True, exist_ok=True)


_episode_locks = {}
_episode_locks_lock = threading.Lock()


def get_episode_lock(project_name: str, episode_name: str) -> threading.RLock:
    """Return a per-(project, episode) threading.RLock for thread-safe file operations."""
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
            if isinstance(data, dict) and len(data.get("data", [])) > 2000:
                json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
            else:
                json.dump(data, f, indent=2, ensure_ascii=False)
        for attempt in range(20):
            try:
                os.replace(tmp_path_str, path)
                break
            except (PermissionError, OSError) as pe:
                if attempt == 19:
                    raise pe
                import time
                time.sleep(0.005 * (1.3 ** attempt))
    except Exception:
        if os.path.exists(tmp_path_str):
            try:
                os.unlink(tmp_path_str)
            except Exception:
                pass
        raise
