"""Registry of subtitle files OmbiSub wrote (``DATA_DIR/subtitle_ownership.db``).

A subtitle file is *ours* when the registry holds it with a matching content hash, or --
for exports made before the registry existed -- when it is the episode's recorded
``arr_target_path`` (or its ``.srt`` companion) and OmbiSub translated the episode.

Nothing here raises to callers: reads fall back to "not ours", writes become no-ops.
"""

import hashlib
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from utils import paths

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_initialised: set = set()


def _db_path() -> Path:
    return Path(paths.DATA_DIR) / "subtitle_ownership.db"


def norm_path(path) -> str:
    """Normalized absolute path (case-folded on Windows) used as the registry key."""
    return os.path.normcase(os.path.abspath(os.path.normpath(str(path))))


def _file_info(path) -> Optional[tuple]:
    """(sha1, size) of a file, or None when it cannot be read."""
    try:
        h = hashlib.sha1()
        size = 0
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
                size += len(chunk)
        return h.hexdigest(), size
    except OSError:
        return None


def _connect() -> sqlite3.Connection:
    db = _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=10.0)
    conn.row_factory = sqlite3.Row
    key = str(db)
    if key not in _initialised:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS owned_files ("
            " path TEXT PRIMARY KEY, display_path TEXT NOT NULL, sha1 TEXT NOT NULL,"
            " size INTEGER NOT NULL, project_name TEXT, episode_name TEXT, written_at TEXT NOT NULL)"
        )
        conn.commit()
        _initialised.add(key)
    return conn


def register_file(path, project_name: Optional[str] = None, episode_name: Optional[str] = None) -> None:
    """Record a file OmbiSub just wrote (hash of its current content)."""
    try:
        info = _file_info(path)
        if info is None:
            logger.warning("Ownership: cannot register unreadable file '%s'", path)
            return
        sha1, size = info
        with _lock:
            conn = _connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO owned_files"
                    " (path, display_path, sha1, size, project_name, episode_name, written_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (norm_path(path), os.path.abspath(str(path)), sha1, size, project_name, episode_name,
                     datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        logger.warning("Ownership: could not register '%s': %s", path, e)


def _legacy_owned(path, ep_meta: Optional[Dict]) -> bool:
    if not ep_meta:
        return False
    target = ep_meta.get("arr_target_path")
    if not target:
        return False
    translated_by_us = bool(
        ep_meta.get("translation_source")
        or ep_meta.get("last_translated_at")
        or ep_meta.get("last_translation_attempt")
    )
    if not translated_by_us:
        return False
    candidates = {norm_path(target), norm_path(Path(target).with_suffix(".srt"))}
    return norm_path(path) in candidates


def is_ours(path, ep_meta: Optional[Dict] = None) -> bool:
    """True when OmbiSub wrote this file (and it is unchanged), or the legacy rule matches."""
    try:
        if not os.path.isfile(path):
            return False
        with _lock:
            conn = _connect()
            try:
                row = conn.execute("SELECT sha1 FROM owned_files WHERE path = ?", (norm_path(path),)).fetchone()
            finally:
                conn.close()
        if row is not None:
            info = _file_info(path)
            if info is not None and info[0] == row["sha1"]:
                return True
            # Registered but changed since (someone edited or replaced it): not ours any more.
            return False
        return _legacy_owned(path, ep_meta)
    except Exception as e:
        logger.warning("Ownership: is_ours failed for '%s': %s", path, e)
        return False


def forget(path) -> None:
    try:
        with _lock:
            conn = _connect()
            try:
                conn.execute("DELETE FROM owned_files WHERE path = ?", (norm_path(path),))
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        logger.warning("Ownership: could not forget '%s': %s", path, e)


def owned_files(project_name: Optional[str] = None) -> List[dict]:
    try:
        with _lock:
            conn = _connect()
            try:
                if project_name:
                    rows = conn.execute(
                        "SELECT * FROM owned_files WHERE project_name = ? ORDER BY written_at", (project_name,)
                    ).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM owned_files ORDER BY written_at").fetchall()
            finally:
                conn.close()
        return [
            {"path": r["display_path"], "sha1": r["sha1"], "size": r["size"],
             "project_name": r["project_name"], "episode_name": r["episode_name"],
             "written_at": r["written_at"]}
            for r in rows
        ]
    except Exception as e:
        logger.warning("Ownership: owned_files failed: %s", e)
        return []
