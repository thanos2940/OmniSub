"""
Episode metadata index (Plan 03) — an additive SQLite acceleration layer.

The JSON files remain the source of truth; this index is kept in sync on writes so
that "what's untranslated / stale across the library" is a single indexed query
instead of walking the filesystem and opening thousands of small JSON files.

All operations are best-effort: any error is swallowed so indexing can never break a
save, and callers fall back to a filesystem scan when the index is empty/unavailable.
"""

import sqlite3
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DB_FILE = Path(__file__).resolve().parent.parent / "omnisub.db"


def _conn():
    c = sqlite3.connect(DB_FILE, timeout=30.0)
    c.row_factory = sqlite3.Row
    try:
        # busy_timeout is per-connection and cheap. WAL/synchronous are DB-level and
        # persistent — set once in _init, NOT here (PRAGMA journal_mode takes a write
        # lock, so running it on every connection causes contention with the live server).
        c.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    return c


def _init():
    try:
        with _conn() as c:
            try:
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("PRAGMA synchronous=NORMAL")
            except Exception:
                pass
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS episodes_index (
                    project TEXT NOT NULL,
                    episode TEXT NOT NULL,
                    has_target INTEGER DEFAULT 0,
                    translated INTEGER DEFAULT 0,
                    src_fp TEXT,
                    translated_fp TEXT,
                    original_exists INTEGER DEFAULT 0,
                    disabled INTEGER DEFAULT 0,
                    arr_secondary_of TEXT,
                    PRIMARY KEY (project, episode)
                )
                """
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_ei_untranslated ON episodes_index(disabled, has_target, original_exists)")
            # needs_review count column (added later) — ALTER for existing DBs.
            cols = [r[1] for r in c.execute("PRAGMA table_info(episodes_index)")]
            if "needs_review" not in cols:
                c.execute("ALTER TABLE episodes_index ADD COLUMN needs_review INTEGER DEFAULT 0")
            if "arr_secondary_of" not in cols:
                c.execute("ALTER TABLE episodes_index ADD COLUMN arr_secondary_of TEXT")
            c.commit()
    except Exception as e:
        logger.warning(f"metadata_index init failed: {e}")


_init()


def set_review_count(project: str, episode: str, count: int) -> None:
    """Update just the needs_review count for an episode (used by the one-time seed scan)."""
    try:
        with _conn() as c:
            c.execute("UPDATE episodes_index SET needs_review=? WHERE project=? AND episode=?",
                      (int(count or 0), project, episode))
            c.commit()
    except Exception:
        pass


def list_with_review(project: Optional[str] = None) -> List[Tuple[str, str]]:
    """Episodes that have at least one needs_review line — the only ones the review scan
    must open. Returns [] when nothing is flagged (instant)."""
    try:
        with _conn() as c:
            sql = "SELECT project, episode FROM episodes_index WHERE needs_review > 0 AND disabled = 0"
            params: list = []
            if project is not None:
                sql += " AND project = ?"
                params.append(project)
            return [(r["project"], r["episode"]) for r in c.execute(sql, params).fetchall()]
    except Exception as e:
        logger.warning(f"metadata_index list_with_review failed: {e}")
        return []


def upsert(project: str, episode: str, meta: Dict, original_exists: bool, disabled: bool) -> None:
    try:
        with _conn() as c:
            # needs_review is managed separately by set_review_count() (it depends on line
            # data, not metadata) so it is intentionally NOT in the UPDATE SET — otherwise a
            # metadata-only save would clobber the real count back to 0.
            c.execute(
                """
                INSERT INTO episodes_index (project, episode, has_target, translated, src_fp, translated_fp, original_exists, disabled, needs_review, arr_secondary_of)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 0), ?)
                ON CONFLICT(project, episode) DO UPDATE SET
                    has_target=excluded.has_target, translated=excluded.translated,
                    src_fp=excluded.src_fp, translated_fp=excluded.translated_fp,
                    original_exists=excluded.original_exists, disabled=excluded.disabled,
                    arr_secondary_of=excluded.arr_secondary_of
                """,
                (
                    project, episode,
                    1 if (meta.get("arr_has_target") or meta.get("bazarr_has_target")) else 0,
                    1 if meta.get("translated") else 0,
                    meta.get("arr_sub_fingerprint"),
                    meta.get("arr_translated_from_fingerprint"),
                    1 if original_exists else 0,
                    1 if disabled else 0,
                    meta.get("needs_review_count"),
                    meta.get("arr_secondary_of"),
                ),
            )
            c.commit()
    except Exception as e:
        logger.debug(f"metadata_index upsert failed for {project}/{episode}: {e}")


def remove(project: str, episode: str) -> None:
    try:
        with _conn() as c:
            c.execute("DELETE FROM episodes_index WHERE project=? AND episode=?", (project, episode))
            c.commit()
    except Exception:
        pass


def remove_project(project: str) -> None:
    try:
        with _conn() as c:
            c.execute("DELETE FROM episodes_index WHERE project=?", (project,))
            c.commit()
    except Exception:
        pass


def set_project_disabled(project: str, disabled: bool) -> None:
    try:
        with _conn() as c:
            c.execute("UPDATE episodes_index SET disabled=? WHERE project=?", (1 if disabled else 0, project))
            c.commit()
    except Exception:
        pass


def is_populated() -> bool:
    try:
        with _conn() as c:
            return c.execute("SELECT 1 FROM episodes_index LIMIT 1").fetchone() is not None
    except Exception:
        return False


def list_untranslated(project: Optional[str] = None) -> List[Tuple[str, str]]:
    """Episodes needing translation: original exists, not disabled, and either no target
    or stale (source fingerprint != translated-from fingerprint)."""
    try:
        with _conn() as c:
            sql = (
                "SELECT project, episode FROM episodes_index "
                "WHERE original_exists=1 AND disabled=0 AND ("
                "  has_target=0 OR (translated_fp IS NOT NULL AND src_fp IS NOT NULL AND translated_fp <> src_fp)"
                ")"
            )
            params: list = []
            if project is not None:
                sql += " AND project=?"
                params.append(project)
            return [(r["project"], r["episode"]) for r in c.execute(sql, params).fetchall()]
    except Exception as e:
        logger.warning(f"metadata_index list_untranslated failed: {e}")
        return []


def count_translated(project: str) -> Optional[int]:
    """Count translated/has-target episodes for a project in ONE query (replaces an
    O(n) per-episode metadata file scan). Returns None if the index has no rows for the
    project, so the caller can fall back to the filesystem."""
    try:
        with _conn() as c:
            total = c.execute("SELECT COUNT(*) FROM episodes_index WHERE project = ?", (project,)).fetchone()[0]
            if not total:
                return None
            row = c.execute(
                "SELECT COUNT(*) FROM episodes_index WHERE project = ? AND arr_secondary_of IS NULL AND (has_target = 1 OR translated = 1)",
                (project,),
            ).fetchone()
            return int(row[0])
    except Exception as e:
        logger.warning(f"metadata_index count_translated failed: {e}")
        return None


def list_translated(project: Optional[str] = None) -> List[Tuple[str, str]]:
    """Episodes marked translated and not disabled — used to scope the review-queue scan
    so it only opens data.json for episodes that could possibly have flags."""
    try:
        with _conn() as c:
            sql = "SELECT project, episode FROM episodes_index WHERE translated = 1 AND disabled = 0"
            params: list = []
            if project is not None:
                sql += " AND project = ?"
                params.append(project)
            return [(r["project"], r["episode"]) for r in c.execute(sql, params).fetchall()]
    except Exception as e:
        logger.warning(f"metadata_index list_translated failed: {e}")
        return []


def backfill() -> int:
    """Populate the index from the JSON files. Blocking (filesystem-heavy) — callers MUST
    run it off the event loop via ``asyncio.to_thread``. Uses one connection and batched
    commits so it doesn't open a fresh connection per row."""
    from utils import storage
    count = 0
    try:
        with _conn() as c:
            for p in storage.list_projects():
                pmeta = storage.load_project_metadata(p) or {}
                disabled = bool(pmeta.get("arr_disabled") or pmeta.get("bazarr_disabled"))
                for ep in storage.list_episodes(p):
                    meta = storage.load_episode_metadata(p, ep) or {}
                    original_exists = storage.original_subtitle_exists(p, ep)
                    c.execute(
                        """
                        INSERT INTO episodes_index (project, episode, has_target, translated, src_fp, translated_fp, original_exists, disabled, arr_secondary_of)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(project, episode) DO UPDATE SET
                            has_target=excluded.has_target, translated=excluded.translated,
                            src_fp=excluded.src_fp, translated_fp=excluded.translated_fp,
                            original_exists=excluded.original_exists, disabled=excluded.disabled,
                            arr_secondary_of=excluded.arr_secondary_of
                        """,
                        (
                            p, ep,
                            1 if (meta.get("arr_has_target") or meta.get("bazarr_has_target")) else 0,
                            1 if meta.get("translated") else 0,
                            meta.get("arr_sub_fingerprint"),
                            meta.get("arr_translated_from_fingerprint"),
                            1 if original_exists else 0,
                            1 if disabled else 0,
                            meta.get("arr_secondary_of"),
                        ),
                    )
                    count += 1
                    if count % 500 == 0:
                        c.commit()  # release the write lock periodically so request-path upserts aren't starved
            c.commit()
    except Exception as e:
        logger.warning(f"metadata_index backfill failed: {e}")
    return count
