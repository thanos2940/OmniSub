"""
Background Worker Service (Modular Facade).

Drives the background queue loop, off-peak scheduling, embedded extraction,
and translation job execution. Decomposed into single-responsibility submodules
under ``services.worker``.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from utils.translation_queue import TranslationQueue
from utils import storage
from services.worker.scheduler import is_within_window, get_schedule_ceiling
from services.worker.daily_probe import recover_daily_limits
from services.worker.extraction_worker import dispatch_extractions, process_extraction_item
from services.worker.translation_item_worker import process_translation_item, _record_target

logger = logging.getLogger(__name__)


class BackgroundTranslationWorker:
    def __init__(self):
        self.queue = TranslationQueue()
        self.running_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self._new_item_event = asyncio.Event()
        self.active_tasks: dict = {}  # Dict[str, asyncio.Task] mapping item_id -> running Task
        self._last_task_sync_at = 0.0

    def cancel_task(self, item_id: str) -> bool:
        """Cancel an in-flight translation task by queue item_id."""
        task = self.active_tasks.get(item_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def cancel_all_tasks(self) -> int:
        """Cancel all in-flight translation tasks."""
        cancelled = 0
        for task in list(self.active_tasks.values()):
            if not task.done():
                task.cancel()
                cancelled += 1
        self.active_tasks.clear()
        self.trigger()
        return cancelled

    def start(self):
        if not self.running_task or self.running_task.done():
            self._shutdown_event.clear()
            self._new_item_event.clear()
            self.running_task = asyncio.create_task(self._run_loop())
            logger.info("BackgroundTranslationWorker started.")

    def stop(self):
        if self.running_task:
            self._shutdown_event.set()
            self._new_item_event.set()
            for task in list(self.active_tasks.values()):
                if not task.done():
                    task.cancel()
            self.running_task.cancel()
            logger.info("BackgroundTranslationWorker stopped.")

    def trigger(self):
        """Wake up the worker loop immediately to process a new task."""
        self._new_item_event.set()

    @staticmethod
    def _within_window(now, start_str: str, end_str: str) -> bool:
        """True if local time ``now`` falls within [start, end) (handles midnight wrap)."""
        return is_within_window(now, start_str, end_str)

    def _schedule_ceiling(self, config: dict) -> Optional[int]:
        """Off-peak scheduling: max claimable priority right now, or None for unrestricted."""
        return get_schedule_ceiling(config)

    def _dispatch_extractions(self, extraction_tasks: set, global_config: dict,
                              max_priority: Optional[int]) -> None:
        """Fill the embedded-extraction lane up to its own concurrency cap."""
        dispatch_extractions(self.queue, extraction_tasks, global_config, max_priority, self.trigger)

    async def _process_extraction_item(self, item: dict, global_config: dict):
        """Process one embedded extraction task."""
        await process_extraction_item(self.queue, item, global_config)

    async def _process_item(self, item: dict, global_config: dict):
        """Process one translation queue item."""
        await process_translation_item(self.queue, item, global_config)

    async def _run_loop(self):
        active_tasks = set()
        extraction_tasks = set()
        export_tasks = set()
        while not self._shutdown_event.is_set():
            try:
                global_config = storage.load_global_config()

                # Adaptive concurrency: effective parallel-episode count.
                from utils.concurrency_controller import concurrency_manager
                from utils.model_resolver import resolve_model
                concurrency_manager.set_defaults(
                    enabled=global_config.get("adaptive_concurrency_enabled", False),
                    base=global_config.get("concurrent_episodes", 2),
                    c_max=global_config.get("concurrency_max", 6),
                )
                model_name = resolve_model("translation")
                concurrent_episodes = concurrency_manager.current(model_name)

                # 1. Recover daily-exhausted models and unpause queue items
                await recover_daily_limits(self.queue)

                # Off-peak scheduling ceiling
                max_priority = self._schedule_ceiling(global_config)

                # Embedded-subtitle extraction lane (runs even when translation worker is paused)
                self._dispatch_extractions(extraction_tasks, global_config, max_priority)

                # Export retry lane (Requirement R1 - runs even when translation worker is paused)
                from services.worker.export_retry_worker import dispatch_export_retries
                dispatch_export_retries(self.queue, export_tasks, global_config, self.trigger)

                # Release abandoned agent claims once a minute.
                import time as _time
                _now_mono = _time.monotonic()
                if _now_mono - self._last_task_sync_at >= 60.0:
                    self._last_task_sync_at = _now_mono
                    try:
                        from services.agent_task_service import agent_task_service
                        await asyncio.to_thread(agent_task_service.housekeeping)
                    except Exception:
                        pass

                # Check global pause switch (pauses only translation claiming)
                if global_config.get("queue_worker_paused", False):
                    try:
                        await asyncio.wait_for(self._new_item_event.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        pass
                    self._new_item_event.clear()
                    completed = {t for t in active_tasks | extraction_tasks | export_tasks if t.done()}
                    active_tasks.difference_update(completed)
                    extraction_tasks.difference_update(completed)
                    export_tasks.difference_update(completed)
                    continue

                # Translation concurrency gate
                if len(active_tasks) >= concurrent_episodes:
                    done, _ = await asyncio.wait(active_tasks, return_when=asyncio.FIRST_COMPLETED)
                    active_tasks.difference_update(done)
                    continue

                # 2. Get next pending item and claim it atomically. In agent mode the
                # translations wait for external agents; only the lanes above run.
                agent_mode = global_config.get("translation_engine", "agent") == "agent"
                item = None if agent_mode else self.queue.claim_next(max_priority=max_priority, exclude_agent=True)
                if not item:
                    timeout_val = 15.0
                    try:
                        with self.queue._conn() as conn:
                            cursor = conn.execute(
                                "SELECT MIN(next_retry_at) as earliest FROM translation_queue WHERE status = 'pending' AND next_retry_at IS NOT NULL"
                            )
                            r = cursor.fetchone()
                            if r and r["earliest"]:
                                earliest_dt = datetime.fromisoformat(r["earliest"])
                                delay = (earliest_dt - datetime.now(timezone.utc)).total_seconds()
                                if 0 < delay < 60.0:
                                    timeout_val = max(1.0, delay)
                                elif delay >= 60.0:
                                    timeout_val = 60.0
                    except Exception:
                        pass

                    wait_coros = [self._new_item_event.wait()]
                    if active_tasks:
                        wait_coros.append(asyncio.wait(active_tasks, return_when=asyncio.FIRST_COMPLETED))
                    
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*wait_coros, return_exceptions=True) if len(wait_coros) > 1 else wait_coros[0],
                            timeout=timeout_val
                        )
                    except asyncio.TimeoutError:
                        pass
                        
                    self._new_item_event.clear()
                    completed = {t for t in active_tasks if t.done()}
                    active_tasks.difference_update(completed)
                    continue

                logger.info(f"Background worker processing task {item['id']}: {item['project_name']} - {item['episode_name']} (active tasks: {len(active_tasks) + 1})")
                
                task = asyncio.create_task(self._process_item(item, global_config))
                active_tasks.add(task)
                self.active_tasks[item["id"]] = task
                
                def clean_task(t, iid=item["id"]):
                    active_tasks.discard(t)
                    self.active_tasks.pop(iid, None)
                    self.trigger()
                task.add_done_callback(clean_task)

            except asyncio.CancelledError:
                self.active_tasks.clear()
                for t in active_tasks | extraction_tasks | export_tasks:
                    t.cancel()
                if active_tasks or extraction_tasks or export_tasks:
                    await asyncio.gather(*(active_tasks | extraction_tasks | export_tasks), return_exceptions=True)
                break
            except Exception as e:
                logger.error(f"Error in background worker loop: {e}", exc_info=True)
                await asyncio.sleep(30)


# Singleton worker instance
worker = BackgroundTranslationWorker()

__all__ = [
    "BackgroundTranslationWorker",
    "worker",
    "_record_target",
]
