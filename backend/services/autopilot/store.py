"""SQLite store for autopilot items (the state machine of docs/PLAN_autopilot.md 4.2) and cursors."""

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from utils import paths

logger = logging.getLogger(__name__)

ACTIVE_STATES = ("waiting", "checking", "extracting", "translating")
TERMINAL_STATES = ("done", "has_greek", "skipped", "no_source", "failed", "superseded", "gone")
ALL_STATES = ACTIVE_STATES + TERMINAL_STATES
TRACKED_STATES = ("extracting", "translating")

MAX_EVENTS = 50

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Columns transition() may set besides state/reason/detail.
_FIELDS = {
    "arr_item_id", "arr_parent_id", "arr_file_id", "media_path", "project_name", "episode_name",
    "title", "season", "episode", "detected_at", "due_at", "attempts", "next_attempt_at",
    "source_kind", "routed_at", "outputs",
}
_TIME_FIELDS = {"detected_at", "due_at", "next_attempt_at", "routed_at"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  arr_item_id INTEGER,
  arr_parent_id INTEGER,
  arr_file_id INTEGER,
  media_path TEXT NOT NULL,
  project_name TEXT, episode_name TEXT, title TEXT,
  season INTEGER, episode INTEGER,
  state TEXT NOT NULL, reason TEXT, detail TEXT,
  detected_at TEXT NOT NULL, due_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT,
  source_kind TEXT,
  routed_at TEXT,
  outputs TEXT,
  events TEXT,
  UNIQUE(source, arr_file_id)
);
CREATE INDEX IF NOT EXISTS items_state_due ON items(state, due_at);
CREATE INDEX IF NOT EXISTS items_project ON items(project_name, episode_name);
CREATE TABLE IF NOT EXISTS cursors (source TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


def iso_utc(value: Union[datetime, str, None]) -> Optional[str]:
    """ISO-8601 UTC with a fixed width, so the strings sort like the instants they name."""
    if value is None:
        return None
    if isinstance(value, str):
        value = parse_iso(value)
        if value is None:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime(_TIME_FORMAT)


def parse_iso(value: Union[str, datetime, None]) -> Optional[datetime]:
    """Parse an ISO-8601 string (Z or offset, any fractional digits) to an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00").replace("z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AutopilotStore:
    def __init__(self, db_path: Optional[Path] = None, *, clock: Optional[Callable[[], datetime]] = None):
        self.db_path = Path(db_path) if db_path else Path(paths.DATA_DIR) / "autopilot.db"
        self._clock = clock or _utcnow
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.DatabaseError:
                pass
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def now(self) -> datetime:
        return self._clock()

    # ------------------------------------------------------------------
    # Row helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _decode(row: Optional[sqlite3.Row]) -> Optional[Dict]:
        if row is None:
            return None
        item = dict(row)
        for key in ("outputs", "events"):
            try:
                value = json.loads(item.get(key) or "[]")
            except (TypeError, ValueError):
                value = []
            item[key] = value if isinstance(value, list) else []
        return item

    def _event(self, events: List[Dict], state: str, message: Optional[str]) -> List[Dict]:
        events = list(events) + [{"t": iso_utc(self.now()), "state": state, "message": message or ""}]
        return events[-MAX_EVENTS:]

    def _fetch(self, item_id: str) -> Optional[Dict]:
        return self._decode(self._conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------

    def upsert_detection(self, *, source: str, kind: str, arr_item_id: Optional[int], arr_parent_id: Optional[int],
                         arr_file_id: Optional[int], media_path: str, project_name: Optional[str],
                         episode_name: Optional[str], title: Optional[str], season: Optional[int],
                         episode: Optional[int], detected_at: Union[datetime, str, None],
                         grace_seconds: float) -> Tuple[Dict, bool]:
        """Record a newly imported file. Returns ``(item, created)``.

        A file already known (same ``(source, arr_file_id)``) is returned untouched. A new
        file for an episode/movie that already has items is an upgrade: the older items are
        marked ``superseded``.
        """
        now = self.now()
        detected = parse_iso(detected_at) or now
        due = detected + timedelta(seconds=max(0.0, float(grace_seconds or 0)))
        with self._lock:
            if arr_file_id is not None:
                row = self._conn.execute(
                    "SELECT * FROM items WHERE source = ? AND arr_file_id = ?", (source, arr_file_id)).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM items WHERE source = ? AND arr_file_id IS NULL AND arr_item_id IS ? "
                    "AND media_path = ? AND state NOT IN ('superseded', 'gone')",
                    (source, arr_item_id, media_path)).fetchone()
            if row is not None:
                return self._decode(row), False

            item_id = uuid.uuid4().hex
            events = self._event([], "waiting", "detected")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if arr_item_id is not None:
                    older = self._conn.execute(
                        "SELECT * FROM items WHERE source = ? AND arr_item_id = ? "
                        "AND state NOT IN ('superseded', 'gone')", (source, arr_item_id)).fetchall()
                    for old in older:
                        old_events = self._event(self._decode(old)["events"], "superseded",
                                                 "a newer file replaced this one")
                        self._conn.execute(
                            "UPDATE items SET state = 'superseded', reason = 'newer_file', updated_at = ?, "
                            "events = ? WHERE id = ?", (iso_utc(now), json.dumps(old_events), old["id"]))
                self._conn.execute(
                    "INSERT INTO items (id, source, kind, arr_item_id, arr_parent_id, arr_file_id, media_path, "
                    "project_name, episode_name, title, season, episode, state, detected_at, due_at, updated_at, "
                    "attempts, events) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'waiting', ?,?,?, 0, ?)",
                    (item_id, source, kind, arr_item_id, arr_parent_id, arr_file_id, media_path, project_name,
                     episode_name, title, season, episode, iso_utc(detected), iso_utc(due), iso_utc(now),
                     json.dumps(events)))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            return self._fetch(item_id), True

    def due_items(self, now: datetime, limit: int = 20) -> List[Dict]:
        """Waiting items whose time has come, and failed items with a retry due, oldest first."""
        stamp = iso_utc(now)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM items WHERE (state = 'waiting' AND due_at <= ?) "
                "OR (state = 'failed' AND next_attempt_at IS NOT NULL AND next_attempt_at <= ?) "
                "ORDER BY CASE WHEN state = 'waiting' THEN due_at ELSE next_attempt_at END, detected_at "
                "LIMIT ?", (stamp, stamp, int(limit))).fetchall()
        return [self._decode(r) for r in rows]

    def active_items(self) -> List[Dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM items WHERE state IN ('extracting', 'translating') ORDER BY updated_at").fetchall()
        return [self._decode(r) for r in rows]

    def transition(self, item_id: str, state: str, *, reason: Optional[str] = None, detail: Optional[str] = None,
                   message: Optional[str] = None, **fields: Any) -> Dict:
        """Move an item to ``state`` and record an event. Extra keyword fields update columns.

        On a real state change ``reason``/``detail`` replace the old ones (None clears them);
        when the state stays the same only non-None values replace them.
        """
        if state not in ALL_STATES:
            raise ValueError(f"unknown autopilot state: {state}")
        unknown = set(fields) - _FIELDS
        if unknown:
            raise ValueError(f"unknown autopilot item field(s): {sorted(unknown)}")
        with self._lock:
            current = self._fetch(item_id)
            if current is None:
                raise KeyError(item_id)
            changed = current["state"] != state
            sets: Dict[str, Any] = {"state": state, "updated_at": iso_utc(self.now())}
            if changed or reason is not None:
                sets["reason"] = reason
            if changed or detail is not None:
                sets["detail"] = detail
            for key, value in fields.items():
                if key in _TIME_FIELDS:
                    value = iso_utc(value)
                elif key == "outputs":
                    value = json.dumps(list(value or []), ensure_ascii=False)
                sets[key] = value
            sets["events"] = json.dumps(self._event(current["events"], state, message or reason or detail))
            assignments = ", ".join(f"{k} = ?" for k in sets)
            self._conn.execute(f"UPDATE items SET {assignments} WHERE id = ?", (*sets.values(), item_id))
            return self._fetch(item_id)

    def schedule_retry(self, item_id: str, error: str, delay_seconds: float, max_attempts: int) -> Dict:
        """Count a failed attempt: back to ``waiting`` after the delay, or ``failed`` once spent."""
        with self._lock:
            current = self._fetch(item_id)
            if current is None:
                raise KeyError(item_id)
            attempts = int(current["attempts"] or 0) + 1
            error = (error or "")[:500]
            if attempts >= max_attempts:
                return self.transition(item_id, "failed", reason="max_attempts", detail=error,
                                       message=f"gave up after {attempts} attempts: {error}",
                                       attempts=attempts, next_attempt_at=None)
            due = self.now() + timedelta(seconds=max(0.0, float(delay_seconds)))
            return self.transition(item_id, "waiting", reason="retry", detail=error,
                                   message=f"attempt {attempts} failed, retrying at {iso_utc(due)}: {error}",
                                   attempts=attempts, due_at=due, next_attempt_at=due)

    def get(self, item_id: str) -> Optional[Dict]:
        with self._lock:
            return self._fetch(item_id)

    def list(self, state: Optional[str] = None, q: Optional[str] = None, limit: int = 100,
             offset: int = 0) -> Tuple[List[Dict], int]:
        """Items newest-updated first, optionally filtered by state (comma list allowed) and text."""
        where, params = [], []
        if state:
            states = [s.strip() for s in str(state).split(",") if s.strip()]
            where.append(f"state IN ({','.join('?' * len(states))})")
            params += states
        if q:
            like = f"%{q.strip()}%"
            where.append("(title LIKE ? OR project_name LIKE ? OR episode_name LIKE ? OR media_path LIKE ?)")
            params += [like] * 4
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with self._lock:
            total = self._conn.execute(f"SELECT COUNT(*) FROM items{clause}", params).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT * FROM items{clause} ORDER BY updated_at DESC, detected_at DESC LIMIT ? OFFSET ?",
                (*params, int(limit), int(offset))).fetchall()
        return [self._decode(r) for r in rows], int(total)

    def counts(self) -> Dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT state, COUNT(*) AS n FROM items GROUP BY state").fetchall()
        out = {s: 0 for s in ALL_STATES}
        out.update({r["state"]: r["n"] for r in rows})
        return out

    def find_for_media(self, source: str, arr_item_id: Optional[int]) -> List[Dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM items WHERE source = ? AND arr_item_id = ? ORDER BY detected_at DESC, rowid DESC",
                (source, arr_item_id)).fetchall()
        return [self._decode(r) for r in rows]

    def recover(self) -> int:
        """Startup: items interrupted mid-check go back to ``waiting``, due now."""
        with self._lock:
            rows = self._conn.execute("SELECT id FROM items WHERE state = 'checking'").fetchall()
            for row in rows:
                self.transition(row["id"], "waiting", message="recovered after restart", due_at=self.now())
            return len(rows)

    def prune(self, older_than_days: int = 90) -> int:
        """Delete terminal items last updated before the cutoff."""
        cutoff = iso_utc(self.now() - timedelta(days=older_than_days))
        marks = ",".join("?" * len(TERMINAL_STATES))
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM items WHERE state IN ({marks}) AND updated_at < ?", (*TERMINAL_STATES, cutoff))
            return cur.rowcount

    # ------------------------------------------------------------------
    # Cursors
    # ------------------------------------------------------------------

    def get_cursor(self, source: str) -> Optional[Dict]:
        with self._lock:
            row = self._conn.execute("SELECT value FROM cursors WHERE source = ?", (source,)).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["value"])
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def set_cursor(self, source: str, value: Dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO cursors (source, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(source) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (source, json.dumps(value, ensure_ascii=False), iso_utc(self.now())))
