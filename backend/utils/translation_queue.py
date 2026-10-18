import sqlite3
import threading
import uuid
import time
import random
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Union, Any

from utils.paths import DB_FILE

# Priority constants
PRIORITY_HIGH    = -1  # High-priority project
PRIORITY_WEBHOOK = 0   # new download just landed
PRIORITY_MANUAL  = 1   # user clicked Translate / Batch / Auto in the UI
PRIORITY_SYNC    = 2   # scheduled sonarr/radarr gap-fill
PRIORITY_BACKLOG = 3   # bulk "translate everything missing"
PRIORITY_LOW     = 5   # Low-priority / deprioritized project
PRIORITY_RUN_NOW = 10  # Explicit single-episode Run Now

# Queue Status constants
QUEUE_STATUS_QUEUED = "queued"
QUEUE_STATUS_PENDING = "pending"
QUEUE_STATUS_RUNNING = "running"
QUEUE_STATUS_COMPLETED = "completed"
QUEUE_STATUS_FAILED = "failed"
QUEUE_STATUS_CANCELLED = "cancelled"
QUEUE_STATUS_POSTPONED = "postponed"

# Work kinds. Each is drained by its own claim method so the lanes never steal
# each other's items — an embedded-subtitle extraction can take minutes of full-file
# I/O and must not occupy a translation slot (docs/PLAN_embedded_ass_extraction.md).
KIND_TRANSLATION = "translation"
KIND_EXTRACTION  = "extraction"
KIND_EXPORT_RETRY = "export_retry"


def _json_opt(key: str) -> str:
    """SQL for ``options.<key>`` as text ('' when options is NULL/invalid or lacks the key)."""
    return f"COALESCE(json_extract(CASE WHEN json_valid(options) THEN options END, '$.{key}'), '')"


# Items routed to external AI agents carry options.engine == "agent".
NOT_AGENT_SQL = f"{_json_opt('engine')} != 'agent'"

_UNSET = object()


def _unique_index_columns(conn) -> set:
    """Every column covered by a UNIQUE index on translation_queue.

    Used to detect whether the schema predates the ``kind``-aware UNIQUE constraint,
    so the migration below is idempotent without a version table.
    """
    columns = set()
    for index in conn.execute("PRAGMA index_list(translation_queue)").fetchall():
        if not index["unique"]:
            continue
        for info in conn.execute(f'PRAGMA index_info("{index["name"]}")').fetchall():
            if info["name"]:
                columns.add(info["name"])
    return columns


_INITIAL_DB_FILE = DB_FILE


class TranslationQueue:
    # Run the one-time legacy-pollution cleanup once per process, not on every
    # construction (TranslationQueue() is instantiated per request in routers).
    _migration_done = False
    _initialized_dbs = set()

    def __init__(self, db_path: Path = DB_FILE):
        self.db_path = DB_FILE if db_path == _INITIAL_DB_FILE else (db_path or DB_FILE)
        db_key = str(Path(self.db_path).resolve())
        if db_key not in TranslationQueue._initialized_dbs:
            self._init_db()
            TranslationQueue._initialized_dbs.add(db_key)

    _thread_local = threading.local()

    def _get_connection(self):
        conns = getattr(self._thread_local, "conns", None)
        if conns is None:
            conns = {}
            self._thread_local.conns = conns
        key = str(Path(self.db_path).resolve())
        conn = conns.get(key)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA busy_timeout=30000")
            except Exception:
                pass
            conns[key] = conn
        return conn

    @contextmanager
    def _conn(self):
        conn = self._get_connection()
        yield conn

    def _init_db(self):
        with self._conn() as conn:
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
                    UNIQUE(project_name, episode_name, lang, kind)
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
            if "updated_at" not in columns:
                conn.execute("ALTER TABLE translation_queue ADD COLUMN updated_at TEXT")
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

            # Widen UNIQUE to include `kind`. Without it an extraction row and a
            # translation row for the same episode collide, and enqueue()'s
            # IntegrityError fallback would silently reuse the extraction row for a
            # translation request — leaving it stuck with kind='extraction' so the
            # translation lane could never claim it. SQLite can't alter a UNIQUE
            # constraint in place, so recreate + copy (same pattern as the lang
            # migration above). Idempotent via the index inspection.
            if "kind" not in _unique_index_columns(conn):
                conn.executescript(
                    """
                    CREATE TABLE translation_queue_kindmig (
                        id TEXT PRIMARY KEY, project_name TEXT NOT NULL, episode_name TEXT NOT NULL,
                        priority INTEGER NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT, created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
                        next_retry_at TEXT, options TEXT, logs TEXT, kind TEXT NOT NULL DEFAULT 'translation',
                        review_count INTEGER NOT NULL DEFAULT 0, lang TEXT NOT NULL DEFAULT '',
                        UNIQUE(project_name, episode_name, lang, kind)
                    );
                    INSERT INTO translation_queue_kindmig
                        (id, project_name, episode_name, priority, status, attempts, last_error,
                         created_at, started_at, completed_at, next_retry_at, options, logs, kind,
                         review_count, lang)
                        SELECT id, project_name, episode_name, priority, status, attempts, last_error,
                               created_at, started_at, completed_at, next_retry_at, options, logs, kind,
                               review_count, lang
                        FROM translation_queue;
                    DROP TABLE translation_queue;
                    ALTER TABLE translation_queue_kindmig RENAME TO translation_queue;
                    """
                )

            # Ensure case-insensitive unique index across project and episode names (Plan BE-P1-02)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_translation_queue_nocase "
                "ON translation_queue (project_name COLLATE NOCASE, episode_name COLLATE NOCASE, lang, kind)"
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

    def enqueue(self, project_name: Union[str, Dict], episode_name: str = "", priority: int = 1, options: Optional[Dict] = None,
                lang: str = "", kind: str = KIND_TRANSLATION, next_retry_at: Optional[str] = None, delay_seconds: int = 0) -> str:
        """Enqueue an episode (for target language ``lang``, '' = primary). Returns the item ID."""
        if isinstance(project_name, dict):
            d = project_name
            project_name = d.get("project_name", "")
            episode_name = d.get("episode_name", "")
            priority = d.get("priority", priority)
            options = d.get("options", options)
            lang = d.get("lang", lang)
            kind = d.get("kind", kind)
            next_retry_at = d.get("next_retry_at", next_retry_at)
            delay_seconds = d.get("delay_seconds", delay_seconds)
        import json
        from datetime import timedelta
        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.isoformat()
        if delay_seconds > 0 and not next_retry_at:
            next_retry_at = (now_dt + timedelta(seconds=delay_seconds)).isoformat()
        item_id = str(uuid.uuid4())
        options_str = json.dumps(options, ensure_ascii=False) if options else None
        lang = lang or ""
        kind = kind or KIND_TRANSLATION
        max_retries = 5
        for attempt in range(max_retries):
            try:
                try:
                    with self._conn() as conn:
                        conn.execute(
                            """
                            INSERT INTO translation_queue
                            (id, project_name, episode_name, priority, status, created_at, options, lang, kind, next_retry_at)
                            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
                            """,
                            (item_id, project_name, episode_name, priority, now_str, options_str, lang, kind, next_retry_at)
                        )
                        conn.commit()
                        return item_id
                except sqlite3.IntegrityError:
                    # Already enqueued, check if failed/paused/cancelled to reset back to pending
                    with self._conn() as conn:
                        cursor = conn.execute(
                            "SELECT id FROM translation_queue WHERE project_name = ? COLLATE NOCASE AND episode_name = ? COLLATE NOCASE AND lang = ? AND kind = ?",
                            (project_name, episode_name, lang, kind)
                        )
                        row = cursor.fetchone()
                        existing_id = row["id"] if row else item_id

                        conn.execute(
                            """
                            UPDATE translation_queue
                            SET priority = CASE WHEN status = 'pending' THEN MIN(priority, ?) ELSE ? END,
                                status = 'pending',
                                next_retry_at = ?,
                                attempts = 0,
                                options = COALESCE(?, options),
                                started_at = NULL,
                                completed_at = NULL,
                                last_error = NULL,
                                logs = NULL
                            WHERE project_name = ? COLLATE NOCASE AND episode_name = ? COLLATE NOCASE AND lang = ? AND kind = ?
                              AND status IN ('completed', 'failed', 'cancelled', 'paused_daily_limit', 'pending')
                            """,
                            (priority, priority, next_retry_at, options_str, project_name, episode_name, lang, kind)
                        )
                        conn.commit()
                        return existing_id
            except sqlite3.OperationalError as e:
                if ("locked" in str(e).lower() or "busy" in str(e).lower()) and attempt < max_retries - 1:
                    time.sleep(0.1 * (2 ** attempt) + random.uniform(0.02, 0.1))
                    continue
                raise

    def claim_next(self, max_priority: Optional[int] = None, exclude_agent: bool = False) -> Optional[Dict]:
        """Claim the next pending translation atomically (status -> 'running', attempts += 1).

        If ``max_priority`` is given, only items with ``priority <= max_priority`` are
        eligible (used by the off-peak scheduler to keep webhook/manual flowing while
        deferring sync/backlog). ``exclude_agent`` skips items routed to external AI
        agents (``options.engine == "agent"``) — the background worker must never take those.
        """
        return self._claim(KIND_TRANSLATION, max_priority, exclude_agent=exclude_agent)

    def claim_next_for_agent(self, max_priority: Optional[int] = None) -> Optional[Dict]:
        """Claim the next pending translation for an external agent (any engine)."""
        return self._claim(KIND_TRANSLATION, max_priority)

    def claim_next_extraction(self, max_priority: Optional[int] = None) -> Optional[Dict]:
        """Claim the next pending embedded-subtitle extraction.

        Separate from ``claim_next`` so the extraction lane has its own concurrency
        budget: extracting a muxed track streams the whole container, and letting one
        occupy a translation slot would stall the queue for minutes.
        """
        return self._claim(KIND_EXTRACTION, max_priority)

    def claim_next_export_retry(self) -> Optional[Dict]:
        """Claim the next pending export retry item atomically."""
        return self._claim(KIND_EXPORT_RETRY)

    def dequeue_next(
        self,
        work_types: Optional[Union[tuple, list, str]] = None,
        max_priority: Optional[int] = None,
        exclude_agent: bool = False,
    ) -> Optional[Dict]:
        """Claim and dequeue the next active item for the specified work_types.

        Only dequeues items with status in ('queued', 'pending'). Never dequeues 'postponed'.
        """
        now_str = datetime.now(timezone.utc).isoformat()
        max_retries = 5
        if isinstance(work_types, str):
            types_list = [work_types]
        elif work_types is not None:
            types_list = list(work_types)
        else:
            types_list = None

        for attempt in range(max_retries):
            try:
                with self._conn() as conn:
                    if conn.in_transaction:
                        conn.rollback()
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        prio_clause = "AND priority <= ?" if max_priority is not None else ""
                        agent_clause = f"AND {NOT_AGENT_SQL}" if exclude_agent else ""

                        kind_clause = ""
                        params = [now_str]
                        if types_list:
                            placeholders = ",".join("?" for _ in types_list)
                            kind_clause = f"AND kind IN ({placeholders})"
                            params.extend(types_list)
                        if max_priority is not None:
                            params.append(max_priority)

                        cursor = conn.execute(
                            f"""
                            SELECT * FROM translation_queue
                            WHERE status IN ('queued', 'pending')
                              AND (next_retry_at IS NULL OR next_retry_at <= ?)
                              {kind_clause}
                              {prio_clause}
                              {agent_clause}
                            ORDER BY CASE WHEN priority = 10 THEN -2 ELSE priority END ASC, created_at ASC
                            LIMIT 1
                            """,
                            params
                        )
                        row = cursor.fetchone()
                        if row:
                            item = dict(row)
                            conn.execute(
                                "UPDATE translation_queue SET status = 'running', started_at = ?, attempts = attempts + 1 WHERE id = ?",
                                (now_str, item["id"])
                            )
                            conn.commit()
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
            except sqlite3.OperationalError as e:
                if ("locked" in str(e).lower() or "busy" in str(e).lower()) and attempt < max_retries - 1:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise
        return None

    def _claim(self, kind: str, max_priority: Optional[int] = None, exclude_agent: bool = False) -> Optional[Dict]:
        return self.dequeue_next(work_types=(kind,), max_priority=max_priority, exclude_agent=exclude_agent)

    def postpone_item(self, item_id: Union[int, str]) -> bool:
        """Sets status = 'postponed', updated_at = now for an item that is queued/pending or failed."""
        now_str = datetime.now(timezone.utc).isoformat()
        item_id_str = str(item_id)
        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE translation_queue
                SET status = 'postponed', updated_at = ?
                WHERE (id = ? OR id = ?) AND status IN ('queued', 'pending', 'failed', 'paused_daily_limit')
                """,
                (now_str, item_id_str, item_id)
            )
            conn.commit()
            return cursor.rowcount > 0

    def continue_item(self, item_id: Union[int, str], boost_priority: bool = True) -> bool:
        """Sets status = 'queued', clears next_retry_at = None, and if boost_priority=True, sets priority = 10."""
        now_str = datetime.now(timezone.utc).isoformat()
        item_id_str = str(item_id)
        with self._conn() as conn:
            if boost_priority:
                cursor = conn.execute(
                    """
                    UPDATE translation_queue
                    SET status = 'queued', next_retry_at = NULL, priority = 10, updated_at = ?
                    WHERE id = ? OR id = ?
                    """,
                    (now_str, item_id_str, item_id)
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE translation_queue
                    SET status = 'queued', next_retry_at = NULL, updated_at = ?
                    WHERE id = ? OR id = ?
                    """,
                    (now_str, item_id_str, item_id)
                )
            conn.commit()
            return cursor.rowcount > 0

    def stop_item(self, item_id: Union[int, str]) -> bool:
        """Cancels queued/postponed/pending items (status = 'cancelled'), and if status == 'running',
        requests cancellation via jobs_manager / worker and marks status = 'cancelled'."""
        now_str = datetime.now(timezone.utc).isoformat()
        item_id_str = str(item_id)

        try:
            from utils.jobs_manager import jobs
            job = jobs.get(f"bg_{item_id_str}")
            if job:
                job.request_cancel()
        except Exception:
            pass

        try:
            from services.background_worker import worker
            worker.cancel_task(item_id_str)
        except Exception:
            pass

        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE translation_queue
                SET status = 'cancelled', updated_at = ?
                WHERE id = ? OR id = ?
                """,
                (now_str, item_id_str, item_id)
            )
            conn.commit()
            return cursor.rowcount > 0

    def claim_backlog_window(self, limit: int = 200) -> List[Dict]:
        """Atomically claim up to ``limit`` pending BACKLOG items (-> running) for batch
        processing (Plan 01). Returns the claimed rows."""
        now_str = datetime.now(timezone.utc).isoformat()
        max_retries = 5
        for attempt in range(max_retries):
            try:
                with self._conn() as conn:
                    if conn.in_transaction:
                        conn.rollback()
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        cursor = conn.execute(
                            f"""
                            SELECT * FROM translation_queue
                            WHERE status = 'pending' AND kind = 'translation' AND priority = ?
                              AND (next_retry_at IS NULL OR next_retry_at <= ?)
                              AND {NOT_AGENT_SQL}
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
            except sqlite3.OperationalError as e:
                if ("locked" in str(e).lower() or "busy" in str(e).lower()) and attempt < max_retries - 1:
                    time.sleep(0.1 * (2 ** attempt) + random.uniform(0.02, 0.1))
                    continue
                raise

    def repend(self, item_id: str) -> None:
        """Return a claimed item to 'pending' (e.g. batch submission failed)."""
        with self._conn() as conn:
            conn.execute("UPDATE translation_queue SET status='pending', started_at=NULL WHERE id=?", (item_id,))
            conn.commit()

    def update_status(self, item_id: str, status: Optional[str] = None, error: Any = _UNSET,
                      started_at: Any = _UNSET, completed_at: Any = _UNSET,
                      next_retry_at: Any = _UNSET, attempts: Any = _UNSET,
                      logs: Any = _UNSET, review_count: Any = _UNSET) -> None:
        """Update fields of a queue item."""
        import json
        updates = {}
        if status is not None:
            updates["status"] = status
        if error is not _UNSET:
            updates["last_error"] = error
        if review_count is not _UNSET:
            updates["review_count"] = review_count
        if started_at is not _UNSET:
            updates["started_at"] = started_at
        if completed_at is not _UNSET:
            updates["completed_at"] = completed_at
        if next_retry_at is not _UNSET:
            updates["next_retry_at"] = next_retry_at
        if attempts is not _UNSET:
            updates["attempts"] = attempts
        if logs is not _UNSET:
            updates["logs"] = json.dumps(logs, ensure_ascii=False) if logs is not None else None

        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [item_id]

        with self._conn() as conn:
            conn.execute(f"UPDATE translation_queue SET {set_clause} WHERE id = ?", values)
            conn.commit()

    def get_item(self, item_id: str) -> Optional[Dict]:
        """Retrieve a specific queue item by ID as a dictionary."""
        with self._conn() as conn:
            cursor = conn.execute("SELECT * FROM translation_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def find_item(self, project_name: str, episode_name: str, lang: str = "",
                  kind: str = KIND_TRANSLATION) -> Optional[Dict]:
        """The row for one work unit, or None.

        The UNIQUE constraint makes (project, episode, lang, kind) a stable identity —
        re-enqueueing resets the same row — so this is how a caller that only knows the
        episode tracks its progress without holding on to item ids.
        """
        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM translation_queue WHERE project_name = ? COLLATE NOCASE "
                "AND episode_name = ? COLLATE NOCASE AND lang = ? AND kind = ?",
                (project_name, episode_name, lang or "", kind),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def peek(self, max_priority: Optional[int] = None) -> Optional[Dict]:
        """Inspect the next claimable pending item without claiming it (status remains pending)."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            prio_clause = "AND priority <= ?" if max_priority is not None else ""
            params = [KIND_TRANSLATION, now_str] + ([max_priority] if max_priority is not None else [])
            cursor = conn.execute(
                f"""
                SELECT * FROM translation_queue
                WHERE status IN ('pending', 'queued') AND kind = ? AND (next_retry_at IS NULL OR next_retry_at <= ?)
                {prio_clause}
                ORDER BY CASE WHEN priority = 10 THEN -2 ELSE priority END ASC, created_at ASC
                LIMIT 1
                """,
                params
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all(
        self,
        limit: int = 100,
        work_types: Optional[Union[tuple, list, str]] = None,
        include_all_kinds: bool = False,
    ) -> List[Dict]:
        """Retrieve items from queue ordered by status, priority, and created time."""
        if isinstance(work_types, str):
            types_list = [work_types]
        elif work_types is not None:
            types_list = list(work_types)
        else:
            types_list = None

        where_clauses = []
        params = []

        if types_list:
            placeholders = ",".join("?" for _ in types_list)
            where_clauses.append(f"kind IN ({placeholders})")
            params.extend(types_list)
        elif not include_all_kinds:
            where_clauses.append("kind = 'translation'")

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        params.append(limit)

        with self._conn() as conn:
            cursor = conn.execute(
                f"""
                SELECT * FROM translation_queue
                {where_sql}
                ORDER BY 
                    CASE status 
                        WHEN 'running' THEN 1
                        WHEN 'pending' THEN 2
                        WHEN 'queued' THEN 2
                        WHEN 'paused_daily_limit' THEN 3
                        WHEN 'postponed' THEN 4
                        WHEN 'failed' THEN 5
                        WHEN 'completed' THEN 6
                        WHEN 'cancelled' THEN 7
                        ELSE 8
                    END,
                    CASE WHEN priority = 10 THEN -2 ELSE priority END ASC,
                    created_at DESC
                LIMIT ?
                """,
                params
            )
            items = []
            for row in cursor.fetchall():
                d = dict(row)
                d["work_type"] = d.get("kind") or "translation"
                items.append(d)
            return items

    def list_items(
        self,
        limit: int = 100,
        work_types: Optional[Union[tuple, list, str]] = None,
        include_all_kinds: bool = True,
    ) -> List[Dict]:
        return self.get_all(limit=limit, work_types=work_types, include_all_kinds=include_all_kinds)

    def get_extractions(self, limit: int = 50) -> List[Dict]:
        """Pending/running/failed embedded-subtitle extractions, newest first.

        Kept out of ``get_all`` so the translation queue UI keeps its existing shape
        (review counts, per-scene progress) — extractions are surfaced as their own
        list by the queue endpoint.
        """
        with self._conn() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM translation_queue
                WHERE kind = ?
                ORDER BY
                    CASE status
                        WHEN 'running' THEN 1
                        WHEN 'pending' THEN 2
                        WHEN 'failed' THEN 3
                        WHEN 'completed' THEN 4
                        ELSE 5
                    END,
                    priority ASC,
                    created_at DESC
                LIMIT ?
                """,
                (KIND_EXTRACTION, limit)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_summary(self) -> Dict:
        """Get counts of tasks by status and limits consumed today."""
        with self._conn() as conn:
            cursor = conn.execute("SELECT status, COUNT(*) as cnt FROM translation_queue WHERE kind = 'translation' GROUP BY status")
            counts = {row["status"]: row["cnt"] for row in cursor.fetchall()}
            
            # Count completed today (UTC)
            today_start = datetime.now(timezone.utc).date().isoformat()
            cursor_today = conn.execute(
                "SELECT COUNT(*) as cnt FROM translation_queue WHERE kind = 'translation' AND status = 'completed' AND completed_at >= ?",
                (today_start,)
            )
            completed_today = cursor_today.fetchone()["cnt"]

            cursor_postponed = conn.execute(
                "SELECT COUNT(*) as cnt FROM translation_queue WHERE status = 'postponed'"
            )
            postponed_count = cursor_postponed.fetchone()["cnt"]

            cursor_extraction = conn.execute(
                "SELECT COUNT(*) as cnt FROM translation_queue WHERE kind = 'extraction' AND status IN ('pending', 'queued', 'running')"
            )
            extraction_count = cursor_extraction.fetchone()["cnt"]

            cursor_export = conn.execute(
                "SELECT COUNT(*) as cnt FROM translation_queue WHERE kind = 'export_retry' AND status IN ('pending', 'queued', 'running')"
            )
            export_retry_count = cursor_export.fetchone()["cnt"]

        return {
            "pending": counts.get("pending", 0) + counts.get("queued", 0),
            "queued": counts.get("queued", 0),
            "running": counts.get("running", 0),
            "completed": counts.get("completed", 0),
            "completed_today": completed_today,
            "failed": counts.get("failed", 0),
            "paused_daily_limit": counts.get("paused_daily_limit", 0),
            "cancelled": counts.get("cancelled", 0),
            "postponed": postponed_count,
            "postponed_count": postponed_count,
            "extraction_count": extraction_count,
            "export_retry_count": export_retry_count,
        }

    def get_logs(self, item_id: str) -> List[str]:
        """Get the execution logs of a specific task."""
        import json
        with self._conn() as conn:
            cursor = conn.execute("SELECT logs FROM translation_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if row and row["logs"]:
                try:
                    return json.loads(row["logs"])
                except Exception:
                    return []
            return []

    def clear(self, status: Optional[str] = None) -> int:
        """Clear queue items matching a status, category, or all items."""
        with self._conn() as conn:
            if not status or status in ("history", "completed_or_failed"):
                cursor = conn.execute("DELETE FROM translation_queue WHERE status IN ('completed', 'cancelled', 'failed')")
            elif status in ("all", "*"):
                cursor = conn.execute("DELETE FROM translation_queue")
            elif status == "active":
                cursor = conn.execute("DELETE FROM translation_queue WHERE status IN ('pending', 'queued', 'running', 'paused', 'paused_daily_limit', 'postponed')")
            elif status == "pending":
                cursor = conn.execute("DELETE FROM translation_queue WHERE status IN ('pending', 'queued')")
            elif status == "postponed":
                cursor = conn.execute("DELETE FROM translation_queue WHERE status = 'postponed'")
            elif status == "cancelled":
                cursor = conn.execute("DELETE FROM translation_queue WHERE status = 'cancelled'")
            elif status == "failed":
                cursor = conn.execute("DELETE FROM translation_queue WHERE status = 'failed'")
            elif status == "completed":
                cursor = conn.execute("DELETE FROM translation_queue WHERE status = 'completed'")
            else:
                cursor = conn.execute("DELETE FROM translation_queue WHERE status = ?", (status,))
            conn.commit()
            return cursor.rowcount

    def cancel(self, item_id: str) -> bool:
        """Cancel a queue item."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE translation_queue SET status = 'cancelled' WHERE id = ? AND status IN ('pending', 'queued', 'running', 'failed', 'paused_daily_limit', 'postponed')",
                (item_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def cancel_all(self, status: Optional[str] = None) -> int:
        """Cancel all pending, running, or paused queue items."""
        with self._conn() as conn:
            if not status or status in ("all", "active"):
                cursor = conn.execute(
                    "UPDATE translation_queue SET status = 'cancelled' WHERE status IN ('pending', 'queued', 'running', 'paused_daily_limit', 'paused', 'postponed')"
                )
            elif status in ("pending", "queued"):
                cursor = conn.execute(
                    "UPDATE translation_queue SET status = 'cancelled' WHERE status IN ('pending', 'queued')"
                )
            elif status == "running":
                cursor = conn.execute(
                    "UPDATE translation_queue SET status = 'cancelled' WHERE status = 'running'"
                )
            else:
                cursor = conn.execute(
                    "UPDATE translation_queue SET status = 'cancelled' WHERE status = ?",
                    (status,)
                )
            conn.commit()
            return cursor.rowcount

    def delete_item(self, item_id: str) -> bool:
        """Permanently remove a queue item by ID."""
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM translation_queue WHERE id = ?", (item_id,))
            conn.commit()
            return cursor.rowcount > 0

    def retry_now(self, item_id: str) -> None:
        """Force retry of a failed or paused task immediately."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE translation_queue SET status = 'pending', next_retry_at = NULL, attempts = 0 WHERE id = ? AND status IN ('failed', 'cancelled', 'paused_daily_limit', 'pending')",
                (item_id,)
            )
            conn.commit()

    def prioritize(self, item_id: str) -> None:
        """Bump priority of a task to high (0)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE translation_queue SET priority = 0 WHERE id = ?",
                (item_id,)
            )
            conn.commit()

    def unpause_daily_limit_items(self, is_model_exhausted_fn=None) -> None:
        """Resume any tasks paused due to daily rate limit exhaustion, optionally checking if their model is unexhausted."""
        if is_model_exhausted_fn is None:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE translation_queue SET status = 'pending', next_retry_at = NULL WHERE status = 'paused_daily_limit'"
                )
                conn.commit()
            return

        import json
        from utils.storage import load_global_config

        with self._conn() as conn:
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
        with self._conn() as conn:
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
        with self._conn() as conn:
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
        with self._conn() as conn:
            # An agent's claim lives outside this process (its workspace on disk), so a
            # restart must not hand that episode to someone else; stale claims are
            # released by the agent service's TTL instead.
            cursor = conn.execute(
                "UPDATE translation_queue SET status = 'pending', started_at = NULL WHERE status = 'running' "
                f"AND {_json_opt('agent_task_id')} = ''"
            )
            conn.commit()
            return cursor.rowcount

    def update_project_priority(self, project_name: str, tier: str) -> Dict[str, int]:
        """Dynamically update priority or cancel queued translation items when a project's priority tier changes."""
        tier = (tier or "normal").lower().strip()
        now_str = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            if tier == "ignored":
                cursor = conn.execute(
                    """
                    UPDATE translation_queue
                    SET status = 'cancelled', completed_at = ?, last_error = 'Project set to Ignored'
                    WHERE project_name = ? COLLATE NOCASE
                      AND kind = 'translation'
                      AND status IN ('pending', 'queued', 'postponed', 'paused_daily_limit')
                    """,
                    (now_str, project_name)
                )
                conn.commit()
                return {"updated": 0, "cancelled": cursor.rowcount}
            elif tier == "high":
                cursor = conn.execute(
                    """
                    UPDATE translation_queue
                    SET priority = -1
                    WHERE project_name = ? COLLATE NOCASE
                      AND kind = 'translation'
                      AND status IN ('pending', 'queued', 'postponed', 'paused_daily_limit')
                      AND priority != 10
                    """,
                    (project_name,)
                )
                conn.commit()
                return {"updated": cursor.rowcount, "cancelled": 0}
            elif tier == "low":
                cursor = conn.execute(
                    """
                    UPDATE translation_queue
                    SET priority = 5
                    WHERE project_name = ? COLLATE NOCASE
                      AND kind = 'translation'
                      AND status IN ('pending', 'queued', 'postponed', 'paused_daily_limit')
                      AND priority != 10
                    """,
                    (project_name,)
                )
                conn.commit()
                return {"updated": cursor.rowcount, "cancelled": 0}
            else:  # normal
                cursor = conn.execute(
                    """
                    UPDATE translation_queue
                    SET priority = 2
                    WHERE project_name = ? COLLATE NOCASE
                      AND kind = 'translation'
                      AND status IN ('pending', 'queued', 'postponed', 'paused_daily_limit')
                      AND priority IN (-1, 5)
                    """,
                    (project_name,)
                )
                conn.commit()
                return {"updated": cursor.rowcount, "cancelled": 0}


def get_translation_queue() -> TranslationQueue:
    """Return a TranslationQueue singleton/instance."""
    return TranslationQueue()
