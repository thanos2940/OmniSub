"""
Omnisub Agent Task Service
==========================
Lets an external AI agent (Claude Code, Antigravity, any CLI assistant) take the
translation model's place. Omnisub prepares each episode as a small workspace — a
brief (context, story so far, characters, glossary, style rules) and a work file of
English lines — the agent replaces the English with the translation, and submission
runs the same post-processing a model translation gets: validation, Script Guard,
glossary merge, Translation Memory, episode summary, export next to the video.

Queue contract
  * Items meant for agents carry ``options.engine = "agent"``; the background worker
    never claims those, and claims no translations at all while the global
    ``translation_engine`` is ``"agent"``.
  * A claim marks the item ``running`` and records ``options.agent_task_id``. Claims
    survive a server restart and are released after ``agent_claim_ttl_hours``
    without workspace activity, so an abandoned session never blocks an episode.
  * ``options.mode`` is ``"gaps"`` (translate only lines lacking a translation — the
    default) or ``"full"`` (retranslate everything). An item with a mode is an
    explicit request, so storage/disk reconciliation never completes it behind the
    agent's back.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils import storage
from utils.paths import DATA_DIR
from utils.translation_queue import TranslationQueue, KIND_TRANSLATION
from utils.language_codes import to_code
from services import agent_workspace as aw

from services.agent_tasks.common import (  # noqa: F401 — re-exported
    logger, AGENT_TASKS_DIR, DEFAULT_CLAIM_TTL_HOURS, CLOSED_WORKSPACE_DAYS, GUIDE_MAX_CHARS, GUIDE_ADDITIONS_HEADING,
    REPO_ROOT, _LEGACY_OUTPUTS, _ACTIVE, _now, _options, _ignored_item, cli_command,
)
from services.agent_tasks.audit import AuditMixin
from services.agent_tasks.knowledge import KnowledgeMixin
from services.agent_tasks.lifecycle import LifecycleMixin
from services.agent_tasks.submission import SubmissionMixin
from services.agent_tasks.workspaces import WorkspacesMixin


class AgentTaskService(WorkspacesMixin, SubmissionMixin, KnowledgeMixin, AuditMixin, LifecycleMixin):
    """Claims queue items for agents, builds their workspaces and ingests results."""

    def __init__(self, tasks_dir: Optional[Path] = None):
        self.tasks_dir = Path(tasks_dir) if tasks_dir else AGENT_TASKS_DIR
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ helpers

    def _candidate_dirs(self) -> List[Path]:
        """Where workspaces may live (older builds wrote next to backend/ or data/)."""
        backend_dir = Path(__file__).resolve().parent.parent
        seen, dirs = set(), []
        for d in (self.tasks_dir, DATA_DIR / "agent_tasks", backend_dir / "agent_tasks",
                  backend_dir.parent / "data" / "agent_tasks"):
            try:
                r = d.resolve()
            except OSError:
                continue
            if r not in seen and r.exists():
                seen.add(r)
                dirs.append(r)
        return dirs

    def _find_task_folder(self, task_id: str) -> Optional[Path]:
        if not task_id:
            return None
        direct = Path(task_id)
        if direct.is_dir() and (direct / "manifest.json").is_file():
            return direct
        for d in self._candidate_dirs():
            cand = d / task_id
            if (cand / "manifest.json").is_file():
                return cand
        return None

    @staticmethod
    def _read_manifest(folder: Path) -> Dict[str, Any]:
        try:
            return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _write_manifest(folder: Path, manifest: Dict[str, Any]) -> None:
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    def _set_item_options(self, item_id: str, **changes: Any) -> None:
        """Merge ``changes`` into a queue item's options (``None`` removes a key)."""
        q = TranslationQueue()
        with q._conn() as conn:
            row = conn.execute("SELECT options FROM translation_queue WHERE id = ?", (item_id,)).fetchone()
            opts = _options(row) if row else {}
            for k, v in changes.items():
                if v is None:
                    opts.pop(k, None)
                else:
                    opts[k] = v
            conn.execute("UPDATE translation_queue SET options = ? WHERE id = ?",
                         (json.dumps(opts, ensure_ascii=False) if opts else None, item_id))
            conn.commit()

    @staticmethod
    def _broadcast(event_type: str, data: Dict[str, Any]) -> None:
        try:
            from utils.event_broadcaster import broadcast_event
            broadcast_event(event_type, data)
        except Exception as e:
            logger.debug(f"Event broadcast failed: {e}")

    @staticmethod
    def _claim_ttl_hours() -> float:
        try:
            return float(storage.load_global_config().get("agent_claim_ttl_hours") or DEFAULT_CLAIM_TTL_HOURS)
        except Exception:
            return DEFAULT_CLAIM_TTL_HOURS

    @staticmethod
    def _target(item: Dict[str, Any], proj_meta: Dict[str, Any]):
        """(target language name, code, is_primary) for a queue item."""
        primary_name = proj_meta.get("target_language") or "Greek"
        primary_code = to_code(primary_name)
        opts = item.get("_opts") or {}
        lang = (item.get("lang") or "").strip()
        code = to_code(lang) if lang else primary_code
        name = opts.get("target_language_name") or (primary_name if code == primary_code else lang)
        return name, code, code == primary_code

    # ------------------------------------------------------------------ fuzzy lookup

    @staticmethod
    def find_matching_project(query: str) -> Optional[str]:
        """Find a project by exact, case-insensitive, prefix or substring match."""
        if not query:
            return None
        all_projects = storage.list_projects()
        q_clean = query.strip()
        if q_clean in all_projects:
            return q_clean
        q_lower = q_clean.lower()
        for test in (lambda p: p.lower() == q_lower, lambda p: p.lower().startswith(q_lower),
                     lambda p: q_lower in p.lower()):
            for p in all_projects:
                if test(p):
                    return p
        return None

    @staticmethod
    def find_matching_episode(project_name: str, query: str) -> Optional[str]:
        """Find an episode by exact name, SxxExx / 1x02 tag, or bare episode number."""
        if not query:
            return None
        episodes = storage.list_episodes(project_name)
        q_clean = query.strip()
        if q_clean in episodes:
            return q_clean
        q_lower = q_clean.lower()
        for ep in episodes:
            if ep.lower() == q_lower:
                return ep
        m = re.search(r's?(\d+)[ex](\d+)', q_lower)
        if m:
            pattern = f"s{int(m.group(1)):02d}e{int(m.group(2)):02d}"
            for ep in episodes:
                if pattern in ep.lower():
                    return ep
        for ep in episodes:
            if q_lower in ep.lower():
                return ep
        m_ep = re.search(r'\b(?:ep?|episode)?\s*(\d+)\b', q_lower)
        if m_ep:
            pattern = f"e{int(m_ep.group(1)):02d}"
            for ep in episodes:
                if pattern in ep.lower():
                    return ep
        return None

    # ------------------------------------------------------------------ listing

    def list_pending_tasks(self) -> List[Dict[str, Any]]:
        """Pending translation items, in the order ``next`` would claim them."""
        self.housekeeping()
        q = TranslationQueue()
        with q._conn() as conn:
            rows = conn.execute("""
                SELECT id, project_name, episode_name, priority, status, created_at, lang, options
                FROM translation_queue
                WHERE status IN ('pending', 'queued') AND kind = ?
                ORDER BY CASE WHEN priority = 10 THEN -2 ELSE priority END ASC, created_at ASC
            """, (KIND_TRANSLATION,)).fetchall()
        items = []
        for r in rows:
            opts = _options(r)
            if _ignored_item(r, opts):
                continue  # cancelled when it would be claimed
            item = {k: r[k] for k in ("id", "project_name", "episode_name", "priority", "status", "created_at", "lang")}
            item["engine"] = opts.get("engine") or "model"
            item["mode"] = opts.get("mode") or "gaps"
            meta = storage.load_episode_metadata(r["project_name"], r["episode_name"]) or {}
            item["total_cues"] = meta.get("line_count") or 0
            items.append(item)
        return items

    def list_active_tasks(self) -> List[Dict[str, Any]]:
        """Items currently claimed by agents, with workspace progress."""
        q = TranslationQueue()
        with q._conn() as conn:
            rows = conn.execute("""
                SELECT id, project_name, episode_name, started_at, options
                FROM translation_queue
                WHERE status = 'running' AND kind = ?
                  AND COALESCE(json_extract(CASE WHEN json_valid(options) THEN options END, '$.agent_task_id'), '') != ''
                ORDER BY started_at ASC
            """, (KIND_TRANSLATION,)).fetchall()
        active = []
        for r in rows:
            task_id = _options(r).get("agent_task_id")
            folder = self._find_task_folder(task_id)
            entry = {
                "task_id": task_id,
                "queue_id": r["id"],
                "project_name": r["project_name"],
                "episode_name": r["episode_name"],
                "claimed_at": r["started_at"],
                "workspace_dir": str(folder) if folder else None,
            }
            if folder:
                entry.update(self._progress(folder))
            active.append(entry)
        return active

    def _progress(self, folder: Path) -> Dict[str, Any]:
        """How far the agent got: units whose work-file text is no longer the source."""
        try:
            manifest = self._read_manifest(folder)
            plan = json.loads((folder / aw.PLAN_FILE).read_text(encoding="utf-8"))
            units = aw.units_from_plan(plan)
            work = folder / aw.WORK_FILE
            parsed, _ = aw.parse_work_file(work.read_text(encoding="utf-8"))
            code = manifest.get("target_code") or to_code(manifest.get("target_language", "Greek"))
            done = sum(1 for u in units if not aw.looks_untranslated(u.text, parsed.get(u.id, ""), code))
            return {
                "units": len(units),
                "units_done": done,
                "last_activity": datetime.fromtimestamp(work.stat().st_mtime, timezone.utc).isoformat(),
            }
        except Exception:
            return {}

    # ------------------------------------------------------------------ claiming

    # ------------------------------------------------------------------ targeting

    # ------------------------------------------------------------------ parsing / checking

    # ------------------------------------------------------------------ ingestion helpers

    # ------------------------------------------------------------------ submit

    # ------------------------------------------------------------------ completion from elsewhere

    # ------------------------------------------------------------------ housekeeping

    # ------------------------------------------------------------------ cancel / release / clear

# Singleton instance
agent_task_service = AgentTaskService()
