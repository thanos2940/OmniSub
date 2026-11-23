"""Claims over time: reconciling with storage, releasing stale or cancelled claims, pruning old workspaces.

Part of the agent task service (services/agent_task_service.py): ``AgentTaskService`` inherits this mixin.
Moved here unchanged from that module; ``self`` is the service.
"""

import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils import storage
from utils.translation_queue import TranslationQueue, KIND_TRANSLATION
from utils.language_codes import to_code
from services import agent_workspace as aw
from services.agent_tasks.common import logger, CLOSED_WORKSPACE_DAYS, _now, _options


class LifecycleMixin:
    def reconcile_queue_with_storage(self) -> int:
        """Complete pending items (not explicit agent/user requests) whose episode already
        has its target subtitle."""
        q = TranslationQueue()
        with q._conn() as conn:
            rows = conn.execute("""
                SELECT id, project_name, episode_name, lang, options FROM translation_queue
                WHERE status IN ('pending', 'paused_daily_limit', 'paused') AND kind = ?
            """, (KIND_TRANSLATION,)).fetchall()
        done = 0
        for r in rows:
            opts = _options(r)
            if opts.get("mode") or opts.get("force") or opts.get("translation_type") == "original":
                continue
            lang_code = to_code(r["lang"]) if r["lang"] else None
            ep_meta = storage.load_episode_metadata(r["project_name"], r["episode_name"]) or {}
            if storage.episode_has_target(ep_meta, lang_code=lang_code) and not storage.episode_translation_is_stale(ep_meta, lang_code):
                done += self.mark_task_completed(r["project_name"], r["episode_name"], queue_id=r["id"])
        if done:
            self._broadcast("queue_updated", {"reason": "storage_reconciliation", "reconciled_count": done})
        return done

    def release_stale_claims(self) -> List[Dict[str, Any]]:
        """Return abandoned agent claims to the queue (workspace gone or idle past the TTL)."""
        ttl_s = self._claim_ttl_hours() * 3600
        q = TranslationQueue()
        with q._conn() as conn:
            rows = conn.execute("""
                SELECT id, project_name, episode_name, started_at, options FROM translation_queue
                WHERE status = 'running' AND kind = ?
                  AND COALESCE(json_extract(CASE WHEN json_valid(options) THEN options END, '$.agent_task_id'), '') != ''
            """, (KIND_TRANSLATION,)).fetchall()
        released = []
        now = time.time()
        for r in rows:
            task_id = _options(r).get("agent_task_id")
            folder = self._find_task_folder(task_id)
            if folder:
                manifest = self._read_manifest(folder)
                if manifest.get("status") == "completed":
                    self.mark_task_completed(r["project_name"], r["episode_name"], queue_id=r["id"])
                    self._set_item_options(r["id"], agent_task_id=None)
                    continue
                activity = max((p.stat().st_mtime for p in folder.iterdir() if p.is_file()), default=0)
                if now - activity < ttl_s:
                    continue
                manifest.update(status="expired", expired_at=_now())
                self._write_manifest(folder, manifest)
            self._release(r["id"], "workspace missing" if not folder else "idle past claim TTL")
            released.append({"task_id": task_id, "project_name": r["project_name"], "episode_name": r["episode_name"]})
        if released:
            self._broadcast("queue_updated", {"reason": "agent_claims_released", "count": len(released)})
        return released

    def housekeeping(self) -> List[Dict[str, Any]]:
        """Cheap periodic upkeep: release abandoned claims. Never ingests anything —
        a translation only enters the project through an explicit submit."""
        try:
            return self.release_stale_claims()
        except Exception as e:
            logger.debug(f"Agent housekeeping failed: {e}")
            return []

    def detect_and_finish_completed_tasks(self) -> List[Dict[str, Any]]:
        """Back-compat name for :meth:`housekeeping` (auto-ingest was removed: it could
        swallow a work file the agent was still writing)."""
        return self.housekeeping()

    def workspace_current(self, folder: Path) -> bool:
        """Whether a workspace's plan still matches its episode as stored now. False for a plan made
        before the episode was re-imported, compacted or edited: such a workspace must be released and
        planned again, never submitted."""
        try:
            manifest = self._read_manifest(folder)
            plan = json.loads((folder / aw.PLAN_FILE).read_text(encoding="utf-8"))
            cues = storage.load_episode_data(manifest["project_name"], manifest["episode_name"]) or []
            from utils.ass_parser import refresh_passthrough
            refresh_passthrough(cues)
            return aw.plan_matches(cues, plan)
        except Exception as exc:
            logger.warning(f"Could not check workspace {folder}: {exc}")
            return False

    def release_agent_task(self, task_id: str) -> bool:
        """Give a claimed episode back to the queue (e.g. the session is ending)."""
        folder = self._find_task_folder(task_id)
        manifest = self._read_manifest(folder) if folder else {}
        queue_id = manifest.get("queue_id")
        if not folder or not queue_id:
            return False
        if manifest.get("status") != "completed":
            manifest.update(status="released", released_at=_now())
            self._write_manifest(folder, manifest)
            self._release(queue_id, "released by agent")
            self._broadcast("queue_updated", {"reason": "agent_released", "queue_id": queue_id})
        return True

    def cancel_agent_task(self, task_id: str) -> bool:
        """Cancel a task: its workspace is closed, the queue item cancelled, and a runner lane working on it
        lets go at once (its request in flight is cut off)."""
        folder = self._find_task_folder(task_id)
        if not folder:
            return False
        manifest = self._read_manifest(folder)
        if manifest.get("status") != "completed":
            manifest["status"] = "cancelled"
            self._write_manifest(folder, manifest)
            if manifest.get("queue_id"):
                TranslationQueue().cancel(manifest["queue_id"])
                self._set_item_options(manifest["queue_id"], agent_task_id=None)
            self._stop_runner_lane(task_id)
        self._broadcast("queue_updated", {"reason": "agent_cancelled", "task_id": task_id})
        return True

    @staticmethod
    def _stop_runner_lane(task_id: str) -> None:
        """Tell the agent runner (when this process has one) that the episode of ``task_id`` was cancelled."""
        try:
            from services import agent_runner
            runner = agent_runner.current_runner()
            if runner is not None:
                runner.cancel_task(task_id)
        except Exception as e:
            logger.debug(f"Could not tell the runner about the cancelled task {task_id}: {e}")

    def prune_closed_workspaces(self, older_than_days: float = CLOSED_WORKSPACE_DAYS) -> int:
        """Delete workspaces closed (completed, cancelled, expired, released) more than ``older_than_days`` ago.
        Each holds the episode's lines, its plan and a copy of the show's glossary; nothing reads a closed one
        again, and over a library thousands pile up. Open claims are never touched, nor folders of older builds'
        locations. Returns how many."""
        cutoff = time.time() - older_than_days * 86400
        closed = {"completed", "cancelled", "expired", "released"}
        removed = 0
        try:
            entries = list(self.tasks_dir.iterdir())
        except OSError:
            entries = []
        for folder in entries:
            if not (folder.is_dir() and folder.name.startswith("task_")):
                continue
            try:
                if (folder / "manifest.json").stat().st_mtime > cutoff:
                    continue
            except OSError:
                continue
            if self._read_manifest(folder).get("status") not in closed:
                continue
            try:
                shutil.rmtree(folder)
                removed += 1
            except OSError as e:
                logger.debug(f"Could not remove {folder}: {e}")
        if removed:
            logger.info(f"Removed {removed} closed agent workspace(s) older than {older_than_days:g} days.")
        return removed

    def cancel_all_agent_tasks(self, status: Optional[str] = None) -> int:
        """Cancel every open (prepared) agent task."""
        cancelled = 0
        for d in self._candidate_dirs():
            for folder in list(d.iterdir()):
                if folder.is_dir() and folder.name.startswith("task_"):
                    if self._read_manifest(folder).get("status", "prepared") == "prepared" and self.cancel_agent_task(folder.name):
                        cancelled += 1
        return cancelled

    def clear_agent_tasks(self, status: Optional[str] = None) -> int:
        """Delete workspace folders. ``status``: all | completed | cancelled | expired |
        released | finished (every closed task). Open claims are only removed by ``all``."""
        status = status or "finished"
        closed = {"completed", "cancelled", "expired", "released"}
        cleared = 0
        for d in self._candidate_dirs():
            for folder in list(d.iterdir()):
                if not (folder.is_dir() and folder.name.startswith("task_")):
                    continue
                st = self._read_manifest(folder).get("status", "prepared")
                if status == "all" or (status == "finished" and st in closed) or st == status:
                    try:
                        shutil.rmtree(folder)
                        cleared += 1
                    except Exception as e:
                        logger.debug(f"Could not remove {folder}: {e}")
        return cleared
