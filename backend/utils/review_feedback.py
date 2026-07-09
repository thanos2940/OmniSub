"""
Reviewer and User Correction Feedback Loop (Plan 28).
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Tuple, Optional
import logging

from utils import storage
from utils import blacklist
from utils.translation_memory import TranslationMemory

logger = logging.getLogger(__name__)
DB_FILE = Path(__file__).resolve().parent.parent / "omnisub.db"


def _conn():
    c = sqlite3.connect(DB_FILE, timeout=10.0)
    c.row_factory = sqlite3.Row
    return c


def _init():
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS reviewer_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                episode TEXT NOT NULL,
                line_index INTEGER NOT NULL,
                source TEXT NOT NULL,
                bad_target TEXT NOT NULL,
                corrected_target TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        c.commit()
        # Migration to add lang_code column
        try:
            cursor = c.execute("PRAGMA table_info(reviewer_feedback)")
            columns = [row["name"] for row in cursor.fetchall()]
            if "lang_code" not in columns:
                c.execute("ALTER TABLE reviewer_feedback ADD COLUMN lang_code TEXT")
                c.commit()
        except Exception as e:
            logger.warning(f"Could not perform migration for reviewer_feedback: {e}")


_init()


def record_feedback(
    project: str,
    episode: str,
    line_index: int,
    source: str,
    bad_target: str,
    corrected_target: str,
    reason: str = "",
    lang_code: Optional[str] = None
) -> None:
    """Record reviewer or human correction and route to blacklist/TM/suggestions."""
    source = (source or "").strip()
    bad_target = (bad_target or "").strip()
    corrected_target = (corrected_target or "").strip()
    
    if not source or not corrected_target or bad_target == corrected_target:
        return
        
    # 1. Insert into reviewer_feedback
    with _conn() as c:
        c.execute(
            "INSERT INTO reviewer_feedback (project, episode, line_index, source, bad_target, corrected_target, reason, created_at, lang_code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project, episode, line_index, source, bad_target, corrected_target, reason or "", datetime.now(timezone.utc).isoformat(), lang_code)
        )
        c.commit()
        
    # 2. Route negative -> Add to blacklist
    try:
        blacklist.add(project, episode, line_index, source, bad_target, reason=f"Feedback: {reason}")
    except Exception as e:
        logger.error(f"Failed to blacklist bad target: {e}")
        
    # 3. Route positive -> Add to Translation Memory as gold example (is_user_edited=True)
    try:
        global_config = storage.load_global_config()
        if global_config.get("tm_enabled", True):
            tm = TranslationMemory(project, lang_code=lang_code)
            tm.add_translations(
                source_lines=[source],
                target_lines=[corrected_target],
                episode_name=episode,
                is_user_edited=True
            )
    except Exception as e:
        logger.error(f"Failed to add corrected target to TM: {e}")


def get_glossary_suggestions(project: str, min_freq: int = 3) -> List[Dict]:
    """Query frequent source->corrected pairings and filter out existing glossary terms."""
    meta = storage.load_project_metadata(project) or {}
    glossary = meta.get("glossary", {})
    existing_terms = {t.get("term", "").lower().strip() for t in glossary.get("terms", []) if t.get("term")}
    
    with _conn() as c:
        cur = c.execute(
            "SELECT source, corrected_target, COUNT(*) as freq "
            "FROM reviewer_feedback "
            "WHERE project = ? "
            "GROUP BY source, corrected_target "
            "HAVING freq >= ? "
            "ORDER BY freq DESC",
            (project, min_freq)
        )
        suggestions = []
        for row in cur.fetchall():
            src = row["source"]
            tgt = row["corrected_target"]
            if src.lower().strip() not in existing_terms:
                suggestions.append({
                    "term": src,
                    "translation": tgt,
                    "occurrences": row["freq"]
                })
        return suggestions
