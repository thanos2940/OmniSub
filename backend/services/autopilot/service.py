"""Autopilot lifecycle: the loops, the health watch, the pipeline's integrations (docs/PLAN_autopilot.md 4.10).

``AutopilotService`` owns one store, watcher, pipeline and notifier and runs four loops:

* poll      -- Sonarr/Radarr history (``autopilot_poll_seconds``)
* pipeline  -- due items and queue tracking (every 15 s)
* reconcile -- library walk (``autopilot_reconcile_hours``, and shortly after start)
* health    -- arr outages, missing engine, runner problems, daily digest, pruning (every 60 s)

Every loop catches and logs its own errors and idles while ``autopilot_enabled`` is off (the
setting is re-read each tick). The work of a tick is also exposed as ``run_<loop>_once`` so tests
and the API can drive it without sleeping.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from services.autopilot.config import AutopilotSettings
from services.autopilot.notify import AutopilotNotifier, item_label
from services.autopilot.pipeline import AutopilotPipeline
from services.autopilot.store import ACTIVE_STATES, TERMINAL_STATES, AutopilotStore, iso_utc, parse_iso
from services.autopilot.watcher import ArrWatcher
from utils import storage

logger = logging.getLogger(__name__)

LOOPS = ("poll", "pipeline", "reconcile", "health")
PIPELINE_SECONDS = 15
HEALTH_SECONDS = 60
RECONCILE_FIRST_DELAY = 30
IDLE_SECONDS = 5                      # how often a disabled/waiting loop looks at the settings again
ARR_OUTAGE_ALERT_SECONDS = 10 * 60    # an outage this long is worth a message
RUNNER_WAITING_ALERT_SECONDS = 6 * 3600
RUNNER_ACTIVE_STATUSES = ("running", "waiting", "idle")
PRUNE_DAYS = 90
PRUNE_EVERY_SECONDS = 24 * 3600
DIGEST_WINDOW = timedelta(hours=24)
ENGINE_STATES = ("extracting", "translating")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AutopilotService:
    def __init__(self, *, store: Optional[AutopilotStore] = None, watcher: Optional[ArrWatcher] = None,
                 pipeline: Optional[AutopilotPipeline] = None, notifier: Optional[AutopilotNotifier] = None,
                 clock: Optional[Callable[[], datetime]] = None,
                 config_provider: Optional[Callable[[], Dict]] = None,
                 runner_provider: Optional[Callable[[], Any]] = None,
                 plex_factory: Optional[Callable[[Dict], Any]] = None):
        self._clock = clock or _utcnow
        self._config_provider = config_provider or storage.load_global_config
        self._runner_provider = runner_provider
        self._plex_factory = plex_factory
        self._store = store
        self._watcher = watcher
        self._pipeline = pipeline
        self._notifier = notifier
        self._tasks: List[asyncio.Task] = []
        self._stopping: Optional[asyncio.Event] = None  # created on start (needs a running loop)
        self._running = False
        self._poll_lock: Optional[asyncio.Lock] = None
        self._reconcile_lock: Optional[asyncio.Lock] = None
        self._loops: Dict[str, Dict] = {n: {"last_run": None, "last_ok": None, "last_error": None} for n in LOOPS}
        # health bookkeeping
        self._arr_alerted: Dict[str, bool] = {}
        self._runner_waiting_since: Optional[datetime] = None
        self._plex_state: Dict[str, Any] = {"ok": None, "error": None}

    # ------------------------------------------------------------------
    # Components (built lazily so importing the module or asking for status costs nothing)
    # ------------------------------------------------------------------

    @property
    def store(self) -> AutopilotStore:
        if self._store is None:
            self._store = AutopilotStore(clock=self._clock)
        return self._store

    @property
    def watcher(self) -> ArrWatcher:
        if self._watcher is None:
            self._watcher = ArrWatcher(self.store, config_provider=self._config_provider, clock=self._clock)
        return self._watcher

    @property
    def notifier(self) -> AutopilotNotifier:
        if self._notifier is None:
            self._notifier = AutopilotNotifier(config_provider=self._config_provider, clock=self._clock)
        return self._notifier

    @property
    def pipeline(self) -> AutopilotPipeline:
        if self._pipeline is None:
            self._pipeline = AutopilotPipeline(
                self.store, clock=self._clock, on_event=self.on_event, config_provider=self._config_provider)
        return self._pipeline

    def now(self) -> datetime:
        return self._clock()

    def _cfg(self) -> Dict:
        try:
            return self._config_provider() or {}
        except Exception as e:
            logger.warning(f"Autopilot could not read the config: {e}")
            return {}

    def settings(self) -> AutopilotSettings:
        return AutopilotSettings.from_config(self._cfg())

    def _ensure_enabled_at(self, cfg: Optional[Dict] = None) -> Optional[str]:
        """Remember when the autopilot was first switched on (the reconcile only adds files after it)."""
        cfg = cfg if cfg is not None else self._cfg()
        settings = AutopilotSettings.from_config(cfg)
        if not settings.enabled or settings.enabled_at:
            return settings.enabled_at
        stamp = iso_utc(self.now())
        try:
            storage.save_global_config({"autopilot_enabled_at": stamp})
            logger.info(f"Autopilot switched on; only files imported after {stamp} count as new.")
        except Exception as e:
            logger.warning(f"Could not save autopilot_enabled_at: {e}")
        return stamp

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._stopping = asyncio.Event()
        self._poll_lock, self._reconcile_lock = asyncio.Lock(), asyncio.Lock()
        try:
            recovered = await asyncio.to_thread(self.store.recover)
            if recovered:
                logger.info(f"Autopilot: {recovered} interrupted item(s) are due again.")
        except Exception as e:
            logger.error(f"Autopilot store recovery failed: {e}", exc_info=True)
        await asyncio.to_thread(self._ensure_enabled_at)
        self._running = True
        self._tasks = [asyncio.create_task(self._loop(name), name=f"autopilot-{name}") for name in LOOPS]
        logger.info("Autopilot service started (it idles while 'autopilot_enabled' is off).")

    async def stop(self) -> None:
        if not self._running and not self._tasks:
            return
        self._running = False
        if self._stopping is not None:
            self._stopping.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        logger.info("Autopilot service stopped.")

    # ------------------------------------------------------------------
    # Loops
    # ------------------------------------------------------------------

    def _interval(self, name: str, settings: AutopilotSettings) -> float:
        if name == "poll":
            return float(settings.poll_seconds)
        if name == "reconcile":
            return settings.reconcile_hours * 3600.0
        return float(PIPELINE_SECONDS if name == "pipeline" else HEALTH_SECONDS)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=max(0.05, seconds))
        except asyncio.TimeoutError:
            pass

    async def _loop(self, name: str) -> None:
        """Run one tick every interval while enabled; never dies of an error in a tick."""
        next_at = time.monotonic() + (RECONCILE_FIRST_DELAY if name == "reconcile" else 0)
        was_enabled = False
        while not self._stopping.is_set():
            try:
                cfg = self._cfg()
                settings = AutopilotSettings.from_config(cfg)
                if not settings.enabled:
                    was_enabled = False
                    await self._sleep(IDLE_SECONDS)
                    continue
                if not was_enabled:  # just switched on (or first tick): start at once, reconcile a bit later
                    was_enabled = True
                    await asyncio.to_thread(self._ensure_enabled_at, cfg)
                    next_at = time.monotonic() + (RECONCILE_FIRST_DELAY if name == "reconcile" else 0)
                wait = next_at - time.monotonic()
                if wait > 0:
                    await self._sleep(min(wait, IDLE_SECONDS))
                    continue
                await self.run_loop_once(name)
                next_at = time.monotonic() + self._interval(name, self.settings())
            except asyncio.CancelledError:
                raise
            except Exception as e:  # defensive: run_loop_once already catches the tick's own errors
                logger.error(f"Autopilot {name} loop error: {e}", exc_info=True)
                await self._sleep(IDLE_SECONDS)

    async def run_loop_once(self, name: str) -> bool:
        """One tick of a loop with its bookkeeping. Returns whether it succeeded."""
        tick = {"poll": self._poll_tick, "pipeline": self._pipeline_tick,
                "reconcile": self._reconcile_tick, "health": self._health_tick}[name]
        state = self._loops[name]
        state["last_run"] = iso_utc(self.now())
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            state["last_ok"], state["last_error"] = False, f"{type(e).__name__}: {e}"[:300]
            logger.error(f"Autopilot {name} tick failed: {e}", exc_info=True)
            return False
        state["last_ok"], state["last_error"] = True, None
        return True

    async def _poll_tick(self) -> Dict:
        return await self.poll_now()

    async def _pipeline_tick(self) -> None:
        await self.pipeline.process_due()
        await self.pipeline.track_active()

    async def _reconcile_tick(self) -> Dict:
        return await self.reconcile_now()

    async def poll_now(self) -> Dict:
        """Poll Sonarr/Radarr history (serialised with the loop). Returns the watcher's summary."""
        lock = self._poll_lock or asyncio.Lock()
        async with lock:
            return await self.watcher.poll_once()

    async def reconcile_now(self) -> Dict:
        lock = self._reconcile_lock or asyncio.Lock()
        async with lock:
            return await self.watcher.reconcile_once()

    # ------------------------------------------------------------------
    # Pipeline events -> Plex, ownership, Discord
    # ------------------------------------------------------------------

    def _plex_client(self, cfg: Dict):
        if self._plex_factory is not None:
            return self._plex_factory(cfg)
        from services.autopilot.plex import plex_from_config
        return plex_from_config(cfg)

    def _refresh_plex(self, path) -> None:
        """Ask Plex to scan the folder of ``path`` (blocking); never raises."""
        try:
            client = self._plex_client(self._cfg())
            if client is None or not path:
                return
            ok = bool(client.refresh_path(str(path)))
            self._plex_state = {"ok": ok, "error": None if ok else getattr(client, "last_error", None)}
        except Exception as e:
            self._plex_state = {"ok": False, "error": str(e)}
            logger.warning(f"Plex refresh failed: {e}")

    async def on_event(self, kind: str, item: Dict, **data) -> None:
        """The pipeline's ``on_event`` hook."""
        if kind == "done":
            outputs = data.get("outputs") or item.get("outputs") or []
            folder = str(Path(outputs[0]).parent) if outputs else str(Path(item["media_path"]).parent)
            await asyncio.to_thread(self._refresh_plex, folder)
            await asyncio.to_thread(self.notifier.item_done, item)
        elif kind == "foreign_repaired":
            repaired = [str(p) for p in data.get("paths") or []]
            if repaired:
                await asyncio.to_thread(self._refresh_plex, str(Path(repaired[0]).parent))
            for path in repaired:
                await asyncio.to_thread(self.notifier.foreign_repaired, item, path)
                await asyncio.to_thread(self._forget, path)
        elif kind == "no_source":
            await asyncio.to_thread(self.notifier.no_source, item)
        elif kind == "failed":
            await asyncio.to_thread(self.notifier.item_failed, item)

    @staticmethod
    def _forget(path: str) -> None:
        from services.autopilot import ownership
        ownership.forget(path)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def _runner(self):
        if self._runner_provider is not None:
            return self._runner_provider()
        from services.agent_runner import get_runner
        return get_runner()

    def _runner_info(self) -> Dict:
        try:
            info = self._runner().info()
            return {"status": str(info.get("status") or "stopped"), "running": bool(info.get("running")),
                    "message": str(info.get("message") or "")}
        except Exception as e:
            logger.debug(f"Agent runner state unavailable: {e}")
            return {"status": "unknown", "running": False, "message": ""}

    def engine_state(self, counts: Optional[Dict[str, int]] = None, cfg: Optional[Dict] = None) -> Dict:
        """``{runner_status, runner_running, worker_paused, ok, message, missing}``.

        ``missing`` is how many routed items wait while no engine can serve them: extraction
        needs the worker (unpaused); translation needs the agent runner, or -- unless the
        installation is in agent mode -- the Gemini worker.
        """
        cfg = cfg if cfg is not None else self._cfg()
        counts = counts if counts is not None else self.store.counts()
        runner = self._runner_info()
        worker_paused = bool(cfg.get("queue_worker_paused", False))
        runner_active = runner["status"] in RUNNER_ACTIVE_STATUSES
        agent_mode = cfg.get("translation_engine", "model") == "agent"
        worker_translates = not worker_paused and not agent_mode
        missing = 0
        if worker_paused:
            missing += int(counts.get("extracting", 0))
        if not (runner_active or worker_translates):
            missing += int(counts.get("translating", 0))
        if missing:
            message = (f"{missing} subtitle(s) are waiting but no translation engine is running. "
                       f"Start the agent runner (watch mode) or the Gemini worker.")
        elif runner["status"] == "error":
            message = f"Agent runner error: {runner['message']}"
        else:
            message = runner["message"] or ("Agent runner " + runner["status"] if runner_active else "")
        return {"runner_status": runner["status"], "runner_running": runner["running"],
                "worker_paused": worker_paused, "ok": missing == 0 and runner["status"] != "error",
                "message": message, "missing": missing}

    async def _health_tick(self) -> None:
        cfg = self._cfg()
        settings = AutopilotSettings.from_config(cfg)
        now = self.now()
        await self._check_arr(now)
        counts = await asyncio.to_thread(self.store.counts)
        await self._check_engine(cfg, counts, now)
        if settings.digest_hour >= 0:
            await self._maybe_digest(settings.digest_hour, now)
        await self._maybe_prune(now)

    async def _check_arr(self, now: datetime) -> None:
        health = await asyncio.to_thread(self.watcher.health)
        for source, h in health.items():
            if not h.get("configured"):
                self._arr_alerted.pop(source, None)
                continue
            if h.get("ok") is False:
                since = parse_iso(h.get("since"))
                if since and not self._arr_alerted.get(source) \
                        and (now - since).total_seconds() >= ARR_OUTAGE_ALERT_SECONDS:
                    self._arr_alerted[source] = True
                    await asyncio.to_thread(self.notifier.arr_unreachable, source, h.get("error"), h.get("since"))
            elif h.get("ok") is True and self._arr_alerted.pop(source, False):
                await asyncio.to_thread(self.notifier.arr_recovered, source)

    async def _check_engine(self, cfg: Dict, counts: Dict, now: datetime) -> None:
        state = await asyncio.to_thread(self.engine_state, counts, cfg)
        if state["missing"]:
            await asyncio.to_thread(self.notifier.engine_missing, state["missing"])
        status = state["runner_status"]
        if status == "waiting":
            self._runner_waiting_since = self._runner_waiting_since or now
            if (now - self._runner_waiting_since).total_seconds() > RUNNER_WAITING_ALERT_SECONDS:
                await asyncio.to_thread(self.notifier.runner_problem, status, self._runner_info()["message"])
        else:
            self._runner_waiting_since = None
        if status == "error":
            await asyncio.to_thread(self.notifier.runner_problem, status, self._runner_info()["message"])

    def digest_stats(self) -> Dict:
        """Counts of the last 24 hours plus the items that need a person (blocking)."""
        cutoff = self.now() - DIGEST_WINDOW
        stats: Dict[str, Any] = {}
        attention: List[str] = []
        for state in ("done", "has_greek", "skipped", "failed", "no_source"):
            rows, _ = self.store.list(state=state, limit=1000)
            recent = [r for r in rows if (parse_iso(r["updated_at"]) or cutoff) >= cutoff]
            stats[state] = len(recent)
            if state == "failed":
                attention += [f"{item_label(r)}: failed ({r.get('detail') or r.get('reason') or 'error'})"
                              for r in rows]
            elif state == "no_source":
                attention += [f"{item_label(r)}: no usable subtitle ({r.get('reason') or 'unknown'})"
                              for r in recent]
        stats["attention"] = attention
        return stats

    async def _maybe_digest(self, hour: int, now: datetime) -> None:
        local = now.astimezone()
        if local.hour != hour:
            return
        today = local.strftime("%Y-%m-%d")
        last = await asyncio.to_thread(self.store.get_cursor, "digest") or {}
        if last.get("day") == today:
            return
        await asyncio.to_thread(self.store.set_cursor, "digest", {"day": today})
        stats = await asyncio.to_thread(self.digest_stats)
        await asyncio.to_thread(self.notifier.daily_digest, stats)

    async def _maybe_prune(self, now: datetime) -> None:
        last = parse_iso(((await asyncio.to_thread(self.store.get_cursor, "prune")) or {}).get("at"))
        if last and (now - last).total_seconds() < PRUNE_EVERY_SECONDS:
            return
        await asyncio.to_thread(self.store.set_cursor, "prune", {"at": iso_utc(now)})
        removed = await asyncio.to_thread(self.store.prune, PRUNE_DAYS)
        if removed:
            logger.info(f"Autopilot: pruned {removed} finished item(s) older than {PRUNE_DAYS} days.")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> Dict:
        """The snapshot of docs/PLAN_autopilot.md 4.10 (blocking: call from a thread)."""
        cfg = self._cfg()
        settings = AutopilotSettings.from_config(cfg)
        counts = self.store.counts()
        engine = self.engine_state(counts, cfg)
        engine.pop("missing", None)
        waiting, _ = self.store.list(state="waiting", limit=1000)
        waiting.sort(key=lambda i: i["due_at"])
        plex_enabled = bool(settings.plex_refresh_enabled and settings.plex_token)
        return {
            "enabled": settings.enabled,
            "running": self._running,
            "settings": settings.to_dict(),
            "loops": {name: dict(state) for name, state in self._loops.items()},
            "arr": self.watcher.health(),
            "engine": engine,
            "plex": {"enabled": plex_enabled, "ok": self._plex_state["ok"] if plex_enabled else None,
                     "error": self._plex_state["error"] if plex_enabled else None},
            "counts": counts,
            "next_due": waiting[:5],
        }

    # ------------------------------------------------------------------
    # Item actions (blocking)
    # ------------------------------------------------------------------

    def _require(self, item_id: str) -> Dict:
        item = self.store.get(item_id)
        if item is None:
            raise LookupError(item_id)
        return item

    def retry_item(self, item_id: str) -> Dict:
        item = self._require(item_id)
        if item["state"] not in ("failed", "no_source", "skipped", "has_greek"):
            raise ValueError(f"an item in state '{item['state']}' cannot be retried")
        return self.store.transition(item_id, "waiting", message="retry requested", due_at=self.now(),
                                     attempts=0, next_attempt_at=None)

    def skip_item(self, item_id: str) -> Dict:
        item = self._require(item_id)
        if item["state"] not in ACTIVE_STATES:
            raise ValueError(f"an item in state '{item['state']}' cannot be skipped")
        updated = self.store.transition(item_id, "skipped", reason="manual", message="skipped by the user")
        if item["state"] in ("extracting", "translating"):
            self.watcher._cancel_queue_rows(item)
        return updated

    def check_now(self, item_id: str) -> Dict:
        item = self._require(item_id)
        if item["state"] != "waiting":
            raise ValueError(f"an item in state '{item['state']}' is not waiting")
        return self.store.transition(item_id, "waiting", message="check requested", due_at=self.now())

    def list_items(self, state: Optional[str], q: Optional[str], limit: int, offset: int):
        """With no ``state``: active items first (waiting by due time, then checking/extracting/translating),
        then the most recently updated finished ones."""
        if state:
            return self.store.list(state=state, q=q, limit=limit, offset=offset)
        active, n_active = self.store.list(state=",".join(ACTIVE_STATES), q=q, limit=5000)
        rank = {"waiting": 0, "checking": 1, "extracting": 2, "translating": 3}
        active.sort(key=lambda i: i["updated_at"], reverse=True)
        active.sort(key=lambda i: (rank.get(i["state"], 9), i["due_at"] if i["state"] == "waiting" else ""))
        page = active[offset:offset + limit]
        total = n_active
        room = limit - len(page)
        rest, n_rest = self.store.list(state=",".join(TERMINAL_STATES), q=q, limit=max(room, 1),
                                       offset=max(0, offset - len(active)))
        total += n_rest
        if room > 0:
            page += rest[:room]
        return page, total


# ----------------------------------------------------------------------
# The one service of the process
# ----------------------------------------------------------------------

_service: Optional[AutopilotService] = None


def get_service() -> AutopilotService:
    global _service
    if _service is None:
        _service = AutopilotService()
    return _service


def reset_service() -> None:
    """Forget the singleton (tests: the data folder changes between tests)."""
    global _service
    _service = None
