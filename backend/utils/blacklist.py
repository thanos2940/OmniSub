"""
Translation blacklist (Plan 14).

Records rejected translations so retries can avoid reproducing them. A line may have
several blacklisted target renderings.
"""

import sqlite3
from datetime import datetime, timezone
from typing import List, Dict, Optional

from utils.paths import DB_FILE


def _conn():
    c = sqlite3.connect(DB_FILE, timeout=10.0)
    c.row_factory = sqlite3.Row
    return c


def _init():
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS translation_blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                episode TEXT NOT NULL,
                line_index INTEGER NOT NULL,
                source_text TEXT,
                bad_target TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        c.commit()


_init()


def add(project: str, episode: str, line_index: int, source_text: str, bad_target: str, reason: str = "") -> None:
    if not (bad_target or "").strip():
        return
    with _conn() as c:
        c.execute(
            "INSERT INTO translation_blacklist (project, episode, line_index, source_text, bad_target, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project, episode, line_index, source_text or "", bad_target, reason or "", datetime.now(timezone.utc).isoformat()),
        )
        c.commit()


def for_lines(project: str, episode: str, indices: List[int]) -> Dict[int, List[str]]:
    """Map line_index -> [blacklisted targets] for the given indices."""
    if not indices:
        return {}
    placeholders = ",".join("?" * len(indices))
    with _conn() as c:
        cur = c.execute(
            f"SELECT line_index, bad_target FROM translation_blacklist "
            f"WHERE project = ? AND episode = ? AND line_index IN ({placeholders})",
            [project, episode] + list(indices),
        )
        out: Dict[int, List[str]] = {}
        for row in cur.fetchall():
            out.setdefault(row["line_index"], []).append(row["bad_target"])
        return out


def for_project(project: str) -> List[Dict]:
    with _conn() as c:
        cur = c.execute("SELECT * FROM translation_blacklist WHERE project = ? ORDER BY created_at DESC", (project,))
        return [dict(r) for r in cur.fetchall()]


def clear(project: str, episode: Optional[str] = None, line_index: Optional[int] = None) -> int:
    with _conn() as c:
        if episode is not None and line_index is not None:
            cur = c.execute("DELETE FROM translation_blacklist WHERE project = ? AND episode = ? AND line_index = ?",
                            (project, episode, line_index))
        elif episode is not None:
            cur = c.execute("DELETE FROM translation_blacklist WHERE project = ? AND episode = ?", (project, episode))
        else:
            cur = c.execute("DELETE FROM translation_blacklist WHERE project = ?", (project,))
        c.commit()
        return cur.rowcount


def build_negative_block(blacklist_map: Dict[int, List[str]], scene_local_to_global: Dict[int, int]) -> str:
    """Render an 'avoid these renderings' prompt block for the lines in a scene.

    ``scene_local_to_global`` maps the 1-based line number shown to the model to the
    episode-global line index, so the negatives reference the right line.
    """
    if not blacklist_map:
        return ""
    lines = []
    for local_num, global_idx in scene_local_to_global.items():
        bads = blacklist_map.get(global_idx)
        if bads:
            joined = " / ".join(sorted(set(bads))[:3])
            lines.append(f"{local_num}: do NOT translate as: {joined}")
    if not lines:
        return ""
    return "Avoid these previously-rejected renderings:\n" + "\n".join(lines)
