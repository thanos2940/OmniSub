"""The runner's episodes in the queue: what is pending, claiming the next one, keeping and giving back claims.

Part of the agent runner (services/agent_runner.py): ``AgentRunner`` inherits this mixin. Moved here unchanged
from that module; ``self`` is the runner.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from utils.translation_queue import TranslationQueue, KIND_TRANSLATION
from services.runner.common import (
    MAX_FAILURES,
    KEEPALIVE_SECONDS,
    HOUSEKEEPING_SECONDS,
    PENDING_CACHE_SECONDS,
    PREPARING_STATE,
    CLAIM_PREFIX,
    NO_WORK,
    logger,
    _now_iso,
)


class ClaimsMixin:
    def _runner_tasks(self) -> List[Dict]:
        """Episodes this runner claimed and has not submitted yet (they are resumed first)."""
        out = []
        for t in self.service.list_active_tasks():
            folder = Path(t["workspace_dir"]) if t.get("workspace_dir") else None
            if folder and self.service._read_manifest(folder).get("runner"):
                out.append(t)
        return out

    def _hold(self, task: Dict) -> Dict:
        with self.lock:
            self.held[task["task_id"]] = task["project_name"]
            self.started += 1
        return task

    def _pending_items(self) -> List[Dict]:
        """Pending translation items in claim order (``list_pending_tasks``' order), without its per-episode
        metadata reads: id, project and episode are all a claim needs. An item whose retry time has not come
        (a media share that was offline, a delayed request) waits for it, as it does for the worker."""
        now = datetime.now(timezone.utc).isoformat()
        with TranslationQueue()._conn() as conn:
            rows = conn.execute("""
                SELECT id, project_name, episode_name
                FROM translation_queue
                WHERE status IN ('pending', 'queued') AND kind = ?
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY CASE WHEN priority = 10 THEN -2 ELSE priority END ASC, created_at ASC
            """, (KIND_TRANSLATION, now)).fetchall()
        return [{k: r[k] for k in ("id", "project_name", "episode_name")} for r in rows]

    def _pending_count(self) -> int:
        """Pending translation items for the panel (read at most every few seconds)."""
        at, count = self._pending
        if time.time() - at < PENDING_CACHE_SECONDS:
            return count
        try:
            with TranslationQueue()._conn() as conn:
                count = conn.execute("SELECT COUNT(*) FROM translation_queue WHERE status IN ('pending', 'queued') AND kind = ?",
                                     (KIND_TRANSLATION,)).fetchone()[0]
        except Exception:
            pass  # keep the last count
        self._pending = (time.time(), count)
        return count

    def _queue_changed(self) -> None:
        """The runner just claimed, finished or released an item: the next panel update reads the count again."""
        self._pending = (0.0, self._pending[1])

    def _housekeeping(self) -> None:
        """Have abandoned claims released (so they show up as pending again), at most once a minute."""
        with self.lock:
            if time.time() - self._swept < HOUSEKEEPING_SECONDS:
                return
            self._swept = time.time()
        self.service.housekeeping()

    def _next_task(self, lane: Optional[int] = None) -> Optional[Dict]:
        """The next episode for a lane: one of the runner's own half-done claims first, else the next waiting item.
        The claim is taken under ``claim_lock`` (so per-show limits stay exact); the workspace is built outside
        it, because building may first pull the source subtitle out of the video, which takes minutes and must
        not keep the other lanes from claiming. While it is built the item counts as held, and ``preparing``
        lists it (the queue shows it as being prepared, not as an orphan)."""
        per_show = int(self.config["per_show"] or 1)
        for _ in range(50):   # items that turn out to need no work are passed over
            with self.claim_lock:
                if self.stop_event.is_set() or self._no_new_work():
                    return None
                with self.lock:
                    busy = dict(self.held)

                def show_full(project: str) -> bool:
                    return sum(1 for p in busy.values() if p == project) >= per_show

                for t in self._runner_tasks():
                    if t["task_id"] not in busy and not show_full(t["project_name"]):
                        return self._hold(t)
                self._housekeeping()
                item = None
                for row in self._pending_items():
                    if self.failures.get(row["id"], 0) >= MAX_FAILURES or show_full(row["project_name"]):
                        continue
                    item = self.service.claim_item(row["id"])
                    if item:
                        break
                if item is None:
                    return None
                placeholder = f"{CLAIM_PREFIX}{item['id']}"
                with self.lock:
                    self.held[placeholder] = item["project_name"]
                    self._building += 1
            if lane is not None:
                self._lane_set(lane, state=PREPARING_STATE, task=placeholder, project=item["project_name"],
                               episode=item["episode_name"], progress=None, units_done=0, units_total=0,
                               since=_now_iso())
            self._queue_changed()
            placed = False

            def unplace() -> None:
                with self.lock:
                    self.held.pop(placeholder, None)
                    self._building -= 1

            try:
                res = self.service.build_claimed_workspace(item)
                status = res.get("status")
                if status == "claimed":
                    with self.claim_lock:   # stamped and held at once: no other lane may resume it as its own
                        folder = Path(res["workspace_dir"])
                        manifest = self.service._read_manifest(folder)
                        manifest["runner"] = {"engine": self.config["engine"], "claimed_at": _now_iso()}
                        self.service._write_manifest(folder, manifest)
                        unplace()
                        placed = True
                        return self._hold({k: res[k] for k in ("task_id", "queue_id", "project_name", "episode_name",
                                                               "workspace_dir")})
            finally:
                if not placed:
                    unplace()
                if lane is not None:
                    self._lane_set(lane, state="between episodes", **NO_WORK)
            self._queue_changed()
            if status == "nothing_to_translate":
                self.log(f"{item['project_name']} {item['episode_name']}: already fully translated (re-exported).")
            elif status == "missing_source":
                self.log(res.get("message") or f"{item['project_name']} {item['episode_name']}: no source subtitle.", "warn")
            elif status == "cancelled":
                self.log(f"{item['project_name']} {item['episode_name']}: cancelled while it was being prepared.", "warn")
        return None

    def _keep_claims(self) -> None:
        """While every model is paused, keep the half-done episodes claimed (the queue releases
        workspaces that look abandoned)."""
        with self.lock:
            if time.time() - self._kept < KEEPALIVE_SECONDS:
                return
            self._kept = time.time()
        try:
            for t in self._runner_tasks():
                os.utime(Path(t["workspace_dir"]) / "manifest.json")
        except Exception:
            pass

    def _empty_message(self) -> str:
        if self.gave_up:
            return (f"The queue is empty. {self.gave_up} episode(s) failed {MAX_FAILURES} times and are marked failed "
                    f"(see the log; Retry them in Activity > History). Runner stopped.")
        return "The queue is empty. Runner stopped."

    def _put_back(self, task: Dict, why: str, crash: bool = False) -> str:
        """Give an episode back to the queue after a failed attempt and count the failure. The ``MAX_FAILURES``-th
        failure in a session marks the queue item failed with ``why`` instead, so it shows in the history with a
        Retry rather than waiting in the queue for nobody; a retry, or a new request for the episode, starts
        counting again. ``crash``: an unexpected error, which may turn out to be the runner's own (see
        ``_crashed``). Returns what happened, for the log line."""
        queue_id = task.get("queue_id") or task["task_id"]
        with self.claim_lock:   # no lane can claim it between the release and the failure
            try:
                self.service.release_agent_task(task["task_id"])
            except Exception as exc:
                logger.warning(f"Could not release {task['task_id']}: {exc}")
            with self.lock:
                n = self.failures[queue_id] = self.failures.get(queue_id, 0) + 1
                if n >= MAX_FAILURES:
                    self.failures.pop(queue_id, None)
                    self.gave_up += 1
                    if crash:
                        self._crash_run_failed.append(queue_id)
            if n < MAX_FAILURES:
                return "put back in the queue"
            try:
                with TranslationQueue()._conn() as conn:
                    conn.execute("UPDATE translation_queue SET status = 'failed', last_error = ?, completed_at = ? "
                                 "WHERE id = ? AND status IN ('pending', 'queued')",
                                 (f"The translation runner gave up after {n} attempts: {why}"[:2000],
                                  datetime.now(timezone.utc).isoformat(), queue_id))
                    conn.commit()
                TranslationQueue()._changed("runner_gave_up")
            except Exception as exc:
                logger.warning(f"Could not mark {queue_id} failed: {exc}")
        return f"marked failed after {n} attempts (Retry it in Activity > History)"
