"""
Fingerprint-keyed cache of container subtitle-stream probes.

Without this, an enabled embedded-extraction feature runs one ffprobe per media
file on *every* sync, forever, for every file that turns out to have no embedded
ASS track at all — the common case in a mixed library. The result is keyed by the
media file's ``mtime_ns_size`` fingerprint (the same scheme
``MediaSyncEngine._fingerprint`` uses), so a replaced or re-muxed file invalidates
its own entry and nothing else has to remember to.

Lives in the existing SQLite DB rather than a JSON file: syncs probe many
directories concurrently, and a shared dict written from several threads is how
you get a truncated cache.

See docs/PLAN_embedded_ass_extraction.md §6.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from utils.paths import DB_FILE

logger = logging.getLogger(__name__)

_initialized = False


def _connect(db_path: Path = DB_FILE):
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn) -> None:
    global _initialized
    if _initialized:
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_probe_cache (
            media_path TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            tracks TEXT NOT NULL,
            probed_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    _initialized = True


def get(media_path: str, fingerprint: Optional[str], db_path: Path = DB_FILE) -> Optional[List[Dict]]:
    """Return the cached track dicts for ``media_path``, or None on a miss.

    A missing fingerprint (unstattable file) is always a miss: we cannot prove the
    cached entry still describes the file.
    """
    if not fingerprint:
        return None
    try:
        with _connect(db_path) as conn:
            _ensure_table(conn)
            row = conn.execute(
                "SELECT fingerprint, tracks FROM media_probe_cache WHERE media_path = ?",
                (str(media_path),),
            ).fetchone()
            if not row or row["fingerprint"] != fingerprint:
                return None
            return json.loads(row["tracks"])
    except (sqlite3.Error, json.JSONDecodeError) as e:
        logger.debug(f"Probe cache read failed for {media_path}: {e}")
        return None


def put(media_path: str, fingerprint: Optional[str], tracks: List[Dict],
        db_path: Path = DB_FILE) -> None:
    """Store the probe result. Failures are swallowed — the cache is an optimisation,
    never a correctness dependency."""
    if not fingerprint:
        return
    try:
        with _connect(db_path) as conn:
            _ensure_table(conn)
            conn.execute(
                """
                INSERT INTO media_probe_cache (media_path, fingerprint, tracks, probed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(media_path) DO UPDATE SET
                    fingerprint = excluded.fingerprint,
                    tracks = excluded.tracks,
                    probed_at = excluded.probed_at
                """,
                (str(media_path), fingerprint, json.dumps(tracks, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
    except (sqlite3.Error, TypeError, ValueError) as e:
        logger.debug(f"Probe cache write failed for {media_path}: {e}")


def invalidate(media_path: str, db_path: Path = DB_FILE) -> None:
    """Drop the cached probe for one media file (e.g. after a failed extraction, so
    the next sync re-reads the container rather than trusting a stale track list)."""
    try:
        with _connect(db_path) as conn:
            _ensure_table(conn)
            conn.execute("DELETE FROM media_probe_cache WHERE media_path = ?", (str(media_path),))
            conn.commit()
    except sqlite3.Error as e:
        logger.debug(f"Probe cache invalidate failed for {media_path}: {e}")


def clear(db_path: Path = DB_FILE) -> int:
    """Wipe the cache (settings changed, ffmpeg newly installed, manual reset)."""
    try:
        with _connect(db_path) as conn:
            _ensure_table(conn)
            cursor = conn.execute("DELETE FROM media_probe_cache")
            conn.commit()
            return cursor.rowcount
    except sqlite3.Error as e:
        logger.warning(f"Probe cache clear failed: {e}")
        return 0
