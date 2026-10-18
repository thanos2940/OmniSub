"""Persistence for on-demand subtitle requests (MorpiceHub and similar front-ends).

Deliberately stores only what cannot be recomputed: who asked, for what, and the
disposition each episode got at request time (so "no subtitle to translate from"
survives, since it leaves no queue row behind). Live progress is always derived
from the translation queue and episode metadata when read, so there is no second
status field to drift out of sync with what the worker actually did.

Lives in the same SQLite file as the translation queue; the path is read per call
so tests that repoint ``utils.paths.DB_FILE`` are honoured.
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

from utils import paths

_init_lock = threading.Lock()
_initialized: set = set()


def _connect() -> sqlite3.Connection:
    db_path = str(paths.DB_FILE)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    if db_path not in _initialized:
        with _init_lock:
            if db_path not in _initialized:
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except Exception:
                    pass
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS on_demand_requests (
                        id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        requested_by TEXT,
                        client TEXT,
                        client_ref TEXT,
                        media_type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        year INTEGER,
                        project_name TEXT NOT NULL,
                        language TEXT NOT NULL,
                        lang_key TEXT NOT NULL DEFAULT '',
                        episodes TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_on_demand_created "
                    "ON on_demand_requests (created_at)"
                )
                conn.commit()
                _initialized.add(db_path)
    return conn


def _row_to_dict(row: sqlite3.Row) -> Dict:
    record = dict(row)
    try:
        record["episodes"] = json.loads(record.get("episodes") or "[]")
    except (TypeError, ValueError):
        record["episodes"] = []
    return record


def create(record: Dict) -> Dict:
    """Insert a request. ``record`` must carry an ``id``; ``created_at`` is filled in."""
    row = {
        "id": record["id"],
        "created_at": record.get("created_at") or datetime.now(timezone.utc).isoformat(),
        "requested_by": record.get("requested_by"),
        "client": record.get("client"),
        "client_ref": record.get("client_ref"),
        "media_type": record["media_type"],
        "title": record["title"],
        "year": record.get("year"),
        "project_name": record["project_name"],
        "language": record["language"],
        "lang_key": record.get("lang_key") or "",
        "episodes": json.dumps(record.get("episodes") or [], ensure_ascii=False),
    }
    conn = _connect()
    try:
        conn.execute(
            f"INSERT INTO on_demand_requests ({', '.join(row)}) "
            f"VALUES ({', '.join('?' for _ in row)})",
            list(row.values()),
        )
        conn.commit()
    finally:
        conn.close()
    return get(row["id"])


def get(request_id: str) -> Optional[Dict]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM on_demand_requests WHERE id = ?", (request_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row else None


def list_recent(limit: int = 50) -> List[Dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM on_demand_requests ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]
