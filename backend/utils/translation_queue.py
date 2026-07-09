import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

# Priority constants
PRIORITY_WEBHOOK = 0   # new download just landed
PRIORITY_MANUAL  = 1   # user clicked Translate / Batch / Auto in the UI
PRIORITY_SYNC    = 2   # scheduled sonarr/radarr gap-fill
PRIORITY_BACKLOG = 3   # bulk "translate everything missing"

DB_FILE = Path(__file__).resolve().parent.parent / "omnisub.db"

class TranslationQueue:
    # Run the one-time legacy-pollution cleanup once per process, not on every
    # construction (TranslationQueue() is instantiated per request in routers).
    _migration_done = False

    def __init__(self, db_path: Path = DB_FILE):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        # Return connection with row factory for dict-like rows
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            # WAL lets readers and a writer proceed concurrently — important now
            # that the worker runs multiple episodes in parallel and the API
            # reads the queue while the worker writes. Persists at the DB level.
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
            except Exception:
                pass
            # Fresh DBs get the multi-language schema directly: the work unit is
            # (project, episode, lang) where lang='' means the project's primary language.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS translation_queue (
                    id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    episode_name TEXT NOT NULL,
                    priority INTEGER NOT NULL, -- 0=webhook, 1=manual, 2=sync, 3=backlog
                    status TEXT NOT NULL,      -- pending, running, completed, failed, paused_daily_limit, cancelled
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    next_retry_at TEXT,
                    options TEXT,
                    logs TEXT,
                    kind TEXT NOT NULL DEFAULT 'translation',
                    review_count INTEGER NOT NULL DEFAULT 0,
                    lang TEXT NOT NULL DEFAULT '',
                    UNIQUE(project_name, episode_name, lang)
                )
            """)

            # Dynamically add columns if they don't exist (older DBs)
            cursor = conn.execute("PRAGMA table_info(translation_queue)")
            columns = [row["name"] for row in cursor.fetchall()]
            if "options" not in columns:
                conn.execute("ALTER TABLE translation_queue ADD COLUMN options TEXT")
            if "logs" not in columns:
                conn.execute("ALTER TABLE translation_queue ADD COLUMN logs TEXT")
            if "kind" not in columns:
                conn.execute("ALTER TABLE translation_queue ADD COLUMN kind TEXT NOT NULL DEFAULT 'translation'")
            if "review_count" not in columns:
                # Lines flagged needs_review (gaps + quality flags) on the last completed
                # run, so the UI can distinguish a clean completion from a flagged one.
                conn.execute("ALTER TABLE translation_queue ADD COLUMN review_count INTEGER NOT NULL DEFAULT 0")
            if "lang" not in columns:
                # Rebuild to add the lang column AND widen UNIQUE to include it (Plan 15).
                # SQLite can't alter a UNIQUE constraint in place, so recreate + copy.
                conn.executescript(
                    """
                    CREATE TABLE translation_queue_new (
                        id TEXT PRIMARY KEY, project_name TEXT NOT NULL, episode_name TEXT NOT NULL,
                        priority INTEGER NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT, created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
                        next_retry_at TEXT, options TEXT, logs TEXT, kind TEXT NOT NULL DEFAULT 'translation',
                        review_count INTEGER NOT NULL DEFAULT 0,
                        lang TEXT NOT NULL DEFAULT '', UNIQUE(project_name, episode_name, lang)
                    );
                    INSERT INTO translation_queue_new
                        (id, project_name, episode_name, priority, status, attempts, last_error,
                         created_at, started_at, completed_at, next_retry_at, options, logs, kind, lang)
                        SELECT id, project_name, episode_name, priority, status, attempts, last_error,
                               created_at, started_at, completed_at, next_retry_at, options, logs, kind, ''
                        FROM translation_queue;
                    DROP TABLE translation_queue;
                    ALTER TABLE translation_queue_new RENAME TO translation_queue;
                    """
                )
                
            # Create job_history table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_history (
                    id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    episode_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    logs TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                )
            """)
            
            # One-time migration: clean up legacy job-tracker pollution that older
            # builds wrote into translation_queue. Runs once per process.
            if not TranslationQueue._migration_done:
                conn.execute("""
                    DELETE FROM translation_queue
                    WHERE kind = 'translation' AND (
                        episode_name LIKE 'Scan%' OR
                        episode_name LIKE 'Pipeline%' OR
                        episode_name LIKE 'Auto Translate%' OR
                        episode_name LIKE 'Simple Pipeline%' OR
                        episode_name LIKE 'Generate Summary%' OR
                        episode_name LIKE 'Create Glossary%' OR
                        episode_name LIKE 'Enhance Glossary%' OR
                        episode_name LIKE 'Create Context%' OR
                        episode_name LIKE 'Enhance Context%' OR
                        episode_name LIKE 'Generate Character Profiles%' OR
                        episode_name LIKE 'Sonarr Sync%' OR
                        episode_name LIKE 'Radarr Sync%' OR
                        episode_name LIKE 'Arr Sync%' OR
                        project_name IN ('Sonarr Integration', 'Radarr Integration', 'Arr Integration')
                    )
                """)
                TranslationQueue._migration_done = True

            conn.commit()

    def enqueue(self, project_name: str, episode_name: str, priority: int = 1, options: Optional[Dict] = None, lang: str = "") -> str:
        """Enqueue an episode (for target language ``lang``, '' = primary). Returns the item ID."""
        import json
        now_str = datetime.now(timezone.utc).isoformat()
        item_id = str(uuid.uuid4())
        options_str = json.dumps(options, ensure_ascii=False) if options else None
        lang = lang or ""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO translation_queue
                    (id, project_name, episode_name, priority, status, created_at, options, lang)
                    VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (item_id, project_name, episode_name, priority, now_str, options_str, lang)
                )
                conn.commit()
                return item_id
        except sqlite3.IntegrityError:
            # Already enqueued, check if failed/paused/cancelled to reset back to pending
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT id FROM translation_queue WHERE project_name = ? AND episode_name = ? AND lang = ?",
                    (project_name, episode_name, lang)
                )
                row = cursor.fetchone()
                existing_id = row["id"] if row else item_id

                conn.execute(
                    """
                    UPDATE translation_queue
                    SET status = 'pending',
                        priority = MIN(priority, ?),
                        next_retry_at = NULL,
                        attempts = 0,
                        options = COALESCE(?, options),
                        started_at = NULL,
                        completed_at = NULL,
                        last_error = NULL,
                        logs = NULL
                    WHERE project_name = ? AND episode_name = ? AND lang = ?
                      AND status IN ('completed', 'failed', 'cancelled', 'paused_daily_limit', 'pending')
                    """,
                    (priority, options_str, project_name, episode_name, lang)
                )
                conn.commit()
                return existing_id

    def claim_next(self, max_priority: Optional[int] = None) -> Optional[Dict]:
        """Claim the next pending task atomically (status -> 'running', attempts += 1).

        If ``max_priority`` is given, only items with ``priority <= max_priority`` are
        eligible (used by the off-peak scheduler to keep webhook/manual flowing while
        deferring sync/backlog).
        """
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Select the next pending item
                prio_clause = "AND priority <= ?" if max_priority is not None else ""
                params = [now_str] + ([max_priority] if max_priority is not None else [])
                cursor = conn.execute(
                    f"""
                    SELECT * FROM translation_queue
                    WHERE status = 'pending' AND kind = 'translation' AND (next_retry_at IS NULL OR next_retry_at <= ?)
                    {prio_clause}
                    ORDER BY priority ASC, created_at ASC
                    LIMIT 1
                    """,
                    params
                )
                row = cursor.fetchone()
                if row:
                    item = dict(row)
                    # Update status to running and increment attempts
                    conn.execute(
                        "UPDATE translation_queue SET status = 'running', started_at = ?, attempts = attempts + 1 WHERE id = ?",
                        (now_str, item["id"])
                    )
                    conn.commit()
                    # Return the updated item dict with updated status/started_at/attempts
                    item["status"] = "running"
                    item["started_at"] = now_str
                    item["attempts"] += 1
                    return item
                else:
                    conn.commit()
                    return None
            except Exception:
                conn.rollback()
                raise

    def claim_backlog_window(self, limit: int = 200) -> List[Dict]:
        """Atomically claim up to ``limit`` pending BACKLOG items (-> running) for batch
        processing (Plan 01). Returns the claimed rows."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    """
                    SELECT * FROM translation_queue
                    WHERE status = 'pending' AND kind = 'translation' AND priority = ?
                      AND (next_retry_at IS NULL OR next_retry_at <= ?)
                    ORDER BY created_at ASC LIMIT ?
                    """,
                    (PRIORITY_BACKLOG, now_str, limit),
                )
                rows = [dict(r) for r in cursor.fetchall()]
                for r in rows:
                    conn.execute(
                        "UPDATE translation_queue SET status='running', started_at=?, attempts=attempts+1 WHERE id=?",
                        (now_str, r["id"]),
                    )
                    r["status"] = "running"
                    r["attempts"] += 1
                conn.commit()
                return rows
            except Exception:
                conn.rollback()
                raise

    def repend(self, item_id: str) -> None:
        """Return a claimed item to 'pending' (e.g. batch submission failed)."""
        with self._get_connection() as conn:
            conn.execute("UPDATE translation_queue SET status='pending', started_at=NULL WHERE id=?", (item_id,))
            conn.commit()

    def update_status(self, item_id: str, status: str, error: Optional[str] = None,
                      started_at: Optional[str] = None, completed_at: Optional[str] = None,
                      next_retry_at: Optional[str] = None, attempts: Optional[int] = None,
                      logs: Optional[List[str]] = None, review_count: Optional[int] = None) -> None:
        """Update fields of a queue item."""
        import json
        updates = {"status": status}
        if error is not None:
            updates["last_error"] = error
        if review_count is not None:
            updates["review_count"] = review_count
        if started_at is not None:
            updates["started_at"] = started_at
        if completed_at is not None:
            updates["completed_at"] = completed_at
        if next_retry_at is not None:
            updates["next_retry_at"] = next_retry_at
        if attempts is not None:
            updates["attempts"] = attempts
        if logs is not None:
            updates["logs"] = json.dumps(logs, ensure_ascii=False)

        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [item_id]

        with self._get_connection() as conn:
            conn.execute(f"UPDATE translation_queue SET {set_clause} WHERE id = ?", values)
            conn.commit()

    def get_all(self, limit: int = 100) -> List[Dict]:
        """Retrieve all items from queue ordered by status, priority, and created time."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM translation_queue
                WHERE kind = 'translation'
                ORDER BY 
                    CASE status 
                        WHEN 'running' THEN 1
                        WHEN 'pending' THEN 2
                        WHEN 'paused_daily_limit' THEN 3
                        WHEN 'failed' THEN 4
                        WHEN 'completed' THEN 5
                        WHEN 'cancelled' THEN 6
                        ELSE 7
                    END,
                    priority ASC,
                    created_at DESC
                LIMIT ?
                """,
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_summary(self) -> Dict:
        """Get counts of tasks by status and limits consumed today."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT status, COUNT(*) as cnt FROM translation_queue WHERE kind = 'translation' GROUP BY status")
            counts = {row["status"]: row["cnt"] for row in cursor.fetchall()}
            
            # Count completed today (UTC)
            today_start = datetime.now(timezone.utc).date().isoformat()
            cursor_today = conn.execute(
                "SELECT COUNT(*) as cnt FROM translation_queue WHERE kind = 'translation' AND status = 'completed' AND completed_at >= ?",
                (today_start,)
            )
            completed_today = cursor_today.fetchone()["cnt"]

        return {
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "completed": counts.get("completed", 0),
            "completed_today": completed_today,
            "failed": counts.get("failed", 0),
            "paused_daily_limit": counts.get("paused_daily_limit", 0),
            "cancelled": counts.get("cancelled", 0),
        }

    def get_logs(self, item_id: str) -> List[str]:
        """Get the execution logs of a specific task."""
        import json
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT logs FROM translation_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if row and row["logs"]:
                try:
                    return json.loads(row["logs"])
                except Exception:
                    return []
            return []

    def clear(self, status: Optional[str] = None) -> int:
        """Clear completed, cancelled, or failed items from the queue."""
        with self._get_connection() as conn:
            if status:
                cursor = conn.execute("DELETE FROM translation_queue WHERE status = ?", (status,))
            else:
                cursor = conn.execute("DELETE FROM translation_queue WHERE status IN ('completed', 'cancelled', 'failed')")
            conn.commit()
            return cursor.rowcount

    def cancel(self, item_id: str) -> None:
        """Cancel a queue item."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE translation_queue SET status = 'cancelled' WHERE id = ? AND status IN ('pending', 'failed', 'paused_daily_limit')",
                (item_id,)
            )
            conn.commit()

    def retry_now(self, item_id: str) -> None:
        """Force retry of a failed or paused task immediately."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE translation_queue SET status = 'pending', next_retry_at = NULL, attempts = 0 WHERE id = ?",
                (item_id,)
            )
            conn.commit()

    def prioritize(self, item_id: str) -> None:
        """Bump priority of a task to high (0)."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE translation_queue SET priority = 0 WHERE id = ?",
                (item_id,)
            )
            conn.commit()

    def unpause_daily_limit_items(self, is_model_exhausted_fn=None) -> None:
        """Resume any tasks paused due to daily rate limit exhaustion, optionally checking if their model is unexhausted."""
        if is_model_exhausted_fn is None:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE translation_queue SET status = 'pending', next_retry_at = NULL WHERE status = 'paused_daily_limit'"
                )
                conn.commit()
            return

        import json
        from utils.storage import load_global_config

        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, options FROM translation_queue WHERE status = 'paused_daily_limit'"
            )
            rows = cursor.fetchall()
            if not rows:
                return

            global_config = load_global_config()
            default_model = global_config.get("default_translation_model", "gemini-flash-lite-latest")

            to_unpause = []
            for row in rows:
                item_id = row["id"]
                options_str = row["options"]
                model = None
                if options_str:
                    try:
                        options = json.loads(options_str)
                        model = options.get("model")
                    except Exception:
                        pass
                if not model:
                    model = default_model

                if not is_model_exhausted_fn(model):
                    to_unpause.append(item_id)

            if to_unpause:
                placeholders = ",".join(["?"] * len(to_unpause))
                conn.execute(
                    f"UPDATE translation_queue SET status = 'pending', next_retry_at = NULL WHERE id IN ({placeholders})",
                    to_unpause
                )
                conn.commit()


    def counts_by_priority(self) -> Dict[int, int]:
        """Active (pending/running/paused) item counts grouped by priority (Plan 08)."""
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT priority, COUNT(*) AS c FROM translation_queue
                WHERE kind = 'translation' AND status IN ('pending','running','paused_daily_limit')
                GROUP BY priority
                """
            )
            return {row["priority"]: row["c"] for row in cur.fetchall()}

    def completions_by_day(self, days: int = 14) -> List[Dict]:
        """Completed-translation counts per UTC day, most recent first (Plan 08)."""
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT substr(completed_at, 1, 10) AS d, COUNT(*) AS c
                FROM translation_queue
                WHERE kind = 'translation' AND status = 'completed' AND completed_at IS NOT NULL
                GROUP BY d ORDER BY d DESC LIMIT ?
                """,
                (days,)
            )
            return [{"date": row["d"], "count": row["c"]} for row in cur.fetchall()]

    def reset_orphaned_running(self) -> int:
        """Reset items stuck in 'running' (e.g. after a crash/restart) back to 'pending'.

        The worker is the only thing that sets 'running'; if the process died or a
        task was hard-cancelled, those rows would otherwise never be picked up again.
        Call once on startup before the worker begins claiming.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE translation_queue SET status = 'pending', started_at = NULL WHERE status = 'running'"
            )
            conn.commit()
            return cursor.rowcount
