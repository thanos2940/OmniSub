"""
Direct agent runner — continuous, unattended, quota-aware translation through agent CLIs
=======================================================================================
The interactive agent mode (Claude Code / Antigravity editing ``translation.txt`` in passes,
running check and submit) works, but every step is another round trip that re-sends a growing
conversation. This runner drives the same workspaces directly:

* Several lanes work at once, each on its own episode (``per_show`` caps how many episodes of
  one show run at the same time, so later episodes still get the story so far).
* An episode is sent as one prompt per chunk — long episodes and movies are split at scene
  changes — with all context inline (show context, previous episodes, characters, glossary
  terms with their senses, style rules) and no tools. The reply's ``N| text`` lines go straight
  into the workspace's work file; the episode summary and new glossary terms come back in the
  same reply.
* Only lines that are missing, still English or on the wrong line go back for a fix round.
  A reply that skipped a line but kept counting is put back in order first.
* Quota: Antigravity's own quota report is read before each episode; on a quota stop the whole
  quota group pauses until it refills and the fallback model (another family) takes over.
* Safety refusals: a part the CLI model's filter refuses is set aside, and once the rest of the
  episode is done its lines go on their own to the Gemini API (``api_rescue``; Omnisub's Gemini
  safety setting applies, see utils/gemini_safety.py) or, without one, to a model of another
  family. What still does not come back is narrowed down (split, another family) as before.
* A show without a context guide or without glossary terms gets both from one request before its
  first episode (``bootstrap``; services/project_bootstrap.py): the show's facts, a spread of its
  dialogue and the model's own knowledge. A failure never holds the translation up. Every few
  translated episodes the last part of an episode may add a few short facts to the guide.
* "Pause translations" (``queue_worker_paused``) makes the lanes take no new episode; the ones in
  hand are finished. Requests outside the lanes (retranslate a line, guide and glossary buttons,
  summaries, term harvest, wrong-alphabet repair) go through services/agy_llm.py.
* A finished episode is submitted through the normal agent submit — validation, Script Guard,
  glossary merge, Translation Memory, summary, export — exactly as if an agent had typed
  ``agent_task.py submit``.

Its on/off state, pauses and log survive restarts; an enabled runner resumes when Omnisub
starts, continuing its half-done episodes first. ``info()`` feeds the panel (lanes with lines
done/total, queue length, throughput, ETA); every status change goes out once as an
``agent_runner`` event with its status and message.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

from utils import paths, storage
from utils.translation_queue import TranslationQueue
from services import agent_cli

logger = logging.getLogger("omnisub.agent_runner")

DEFAULT_API_URL = "http://127.0.0.1:8000"


def normalize_api_url(url: Optional[str]) -> str:
    """Normalize local API URL to 127.0.0.1 to avoid Windows IPv6 localhost 2s timeout."""
    if not url:
        return DEFAULT_API_URL
    u = url.rstrip("/")
    if "://localhost" in u:
        u = u.replace("://localhost:", "://127.0.0.1:").replace("://localhost/", "://127.0.0.1/")
        if u.endswith("://localhost"):
            u = u[:-len("localhost")] + "127.0.0.1"
    if "://[::1]" in u:
        u = u.replace("://[::1]:", "://127.0.0.1:").replace("://[::1]/", "://127.0.0.1/")
        if u.endswith("://[::1]"):
            u = u[:-len("[::1]")] + "127.0.0.1"
    return u


DEFAULT_CONFIG = {
    "engine": "antigravity",
    "model": "gemini-3.8-flash-low",
    "fallback_models": ["claude-sonnet-5-5-low"],   # tried while the main model's quota is used up
    "lanes": 3,                  # episodes translated at the same time
    "per_show": 2,               # at most this many of them from one show
    "chunk_lines": 250,          # lines per request; longer episodes are split at scene changes
    "fix_rounds": 2,             # follow-up requests for lines that came back missing or wrong
    "max_episodes": 0,           # 0: until the queue is empty or the runner is stopped
    "watch": False,              # keep running on an empty queue and take new episodes as they are queued
    "keep_watching": False,      # alias for watch / daemon mode
    "api_rescue": True,          # lines the CLI model's safety filter refuses go to the Gemini API at the end
    "rescue_model": "",          # ... with this model ("": the project's translation model)
    "bootstrap": True,           # a show without a context guide or glossary gets both before its first episode
    "review": "fix",             # second pass per part: "fix" corrects real errors (unsure ones flagged), "flag", "off"
    "api_url": DEFAULT_API_URL,
}
LIVE_KEYS = ("model", "fallback_models", "per_show", "chunk_lines", "fix_rounds", "max_episodes", "watch",
             "api_rescue", "rescue_model", "bootstrap", "review", "api_url")
WATCH_SECONDS = 60
PAUSE_CACHE_SECONDS = 3          # the "Pause translations" switch is read at most this often

from services.runner.common import (  # noqa: F401 — re-exported: agent_runner.X keeps working
    API_PREFIX, QUOTA_MARGIN, QUOTA_CACHE_SECONDS, SMALL_BLOCK, EARLIER_LINES, AROUND_BEFORE,
    AROUND_AFTER, MAX_AROUND, MAX_FAILURES, MAX_CRASHES, KEEPALIVE_SECONDS, ENGINE_CACHE_SECONDS,
    HOUSEKEEPING_SECONDS, PENDING_CACHE_SECONDS, PAUSED_MESSAGE, GUIDE_GROWTH_EVERY, GUIDE_ADDITIONS_FILE, BOOTSTRAP_TIMEOUT,
    BOOTSTRAP_STATE, PREPARING_STATE, CLAIM_PREFIX, CONTEXT_CACHE_SECONDS, PID_STALE_SECONDS, TERMINAL,
    NO_WORK, SENSITIVE, _human, _now_iso,
)
from services.runner.claims import ClaimsMixin
from services.runner.context import ContextMixin
from services.runner.episode import EpisodeMixin
from services.runner.models import ModelsMixin
from services.runner.requests import RequestsMixin
from services.runner.review import ReviewMixin

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    if os.name == "nt":  # os.kill(pid, 0) would terminate the process on Windows
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            # Access denied: the process exists but runs as someone else (another logon session).
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return not ok or code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def clean_config(cfg: Optional[Dict]) -> Dict:
    """Known keys only, with sane types and bounds."""
    out = {k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG}
    if "engine" in out and out["engine"] not in agent_cli.ENGINES:
        raise ValueError(f"unknown engine {out['engine']!r} (one of: {', '.join(agent_cli.ENGINES)})")
    for key in ("model", "rescue_model"):
        if key in out:
            out[key] = str(out[key] or "").strip()
    if "review" in out:
        out["review"] = str(out["review"] or "").strip().lower()
        if out["review"] not in ("fix", "flag", "off"):
            out["review"] = DEFAULT_CONFIG["review"]
    if "api_url" in out:
        out["api_url"] = normalize_api_url(str(out["api_url"] or "").strip())
    for key in ("watch", "keep_watching", "api_rescue", "bootstrap"):
        if key in out:
            out[key] = out[key] in (True, 1, "1", "true", "True", "yes", "on")
    # keep_watching is an input alias of watch; only watch is stored.
    if out.pop("keep_watching", False):
        out["watch"] = True
    if "fallback_models" in out:
        fb = out["fallback_models"] or []
        if isinstance(fb, str):
            fb = fb.split(",")
        out["fallback_models"] = [str(m).strip() for m in fb if str(m).strip()]
    for key, low, high in (("lanes", 1, 8), ("per_show", 1, 8), ("chunk_lines", 40, 600), ("fix_rounds", 0, 4),
                           ("max_episodes", 0, 100000)):
        if key in out:
            try:
                out[key] = max(low, min(high, int(out[key])))
            except (TypeError, ValueError):
                out[key] = DEFAULT_CONFIG[key]
    return out


# Model names an engine dropped, and what replaces them there (Antigravity names carry an
# effort suffix: `agy models`). Stored runner settings are upgraded when they are loaded.
RETIRED_MODELS = {
    "claude-sonnet-4-6": {"antigravity": "claude-sonnet-5-5-low", "claude": "claude-sonnet-5-5"},
}


def current_model_name(model: str, engine: str) -> str:
    return RETIRED_MODELS.get(model, {}).get(engine, model)


class RunnerConfig(dict):
    """Configuration container for AgentRunner supporting dual dict and attribute access.

    ``keep_watching`` is an alias of ``watch``: reading or writing it touches ``watch``.
    """
    def __init__(self, *args, **kwargs):
        initial = {}
        if args and isinstance(args[0], dict):
            initial.update(args[0])
        initial.update(kwargs)
        cleaned = clean_config(initial)
        engine = cleaned.get("engine") or DEFAULT_CONFIG["engine"]
        if cleaned.get("model"):
            cleaned["model"] = current_model_name(cleaned["model"], engine)
        if cleaned.get("fallback_models"):
            cleaned["fallback_models"] = [current_model_name(m, engine) for m in cleaned["fallback_models"]]
        super().__init__(cleaned)

    def __getattr__(self, name: str):
        if name == "keep_watching":
            name = "watch"
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name: str, value):
        self[name] = value

    def __setitem__(self, key: str, value):
        if key == "keep_watching":
            key, value = "watch", bool(value)
        super().__setitem__(key, value)


class AgentRunner(ModelsMixin, ClaimsMixin, EpisodeMixin, ReviewMixin, RequestsMixin, ContextMixin):
    def __init__(self, service=None, exe_override: Optional[Dict[str, List[str]]] = None,
                 state_dir: Optional[Path] = None):
        if service is None:
            from services.agent_task_service import agent_task_service as service
        self.service = service
        self.exe_override = exe_override or {}
        self._state_dir = Path(state_dir) if state_dir else None
        self.lock = threading.RLock()
        self.claim_lock = threading.Lock()       # one claim at a time (per-show limits stay exact)
        self.submit_lock = threading.Lock()      # submits write project metadata: one at a time
        self.stop_event = threading.Event()
        self.draining = threading.Event()        # graceful stop: finish the episodes in hand, take no new ones
        self.wake_gen = 0                        # bumped by nudge(): idle lanes look at the queue right away
        self.started = 0
        self.alive = 0
        self.threads: List[threading.Thread] = []
        self.procs: Dict[int, subprocess.Popen] = {}
        self.lanes: Dict[int, Dict] = {}
        self.held: Dict[str, str] = {}           # task_id -> project, for episodes in a lane's hands
        self._building = 0                       # claims whose workspace is being built (outside claim_lock)
        self.failures: Dict[str, int] = {}       # queue id -> failed attempts this session
        self.gave_up = 0                         # episodes marked failed this session (MAX_FAILURES reached)
        self.log_lines: deque = deque(maxlen=1500)
        self.seq = 0
        self.session = {"episodes": 0, "lines": 0, "calls": 0, "tokens_in": 0, "tokens_out": 0, "quota_hits": 0}
        self.consecutive_errors = 0
        self.crashes = 0                         # unexpected errors in a row, reset by a submit
        self._crash_run_failed: List[str] = []   # queue ids marked failed during the current run of crashes
        self._kept = 0.0
        self._swept = 0.0                        # when the service last released abandoned claims for us
        self._engines: Tuple[float, Dict] = (0.0, {})
        self._pending: Tuple[float, int] = (0.0, 0)   # (read at, pending translation items)
        self._pause_read: Tuple[float, bool] = (0.0, False)   # (read at, "Pause translations" is on)
        self._shared: Dict[Tuple[str, str], Dict] = {}   # (project, target language) -> see _project_context
        self._busy = 0                           # lanes working on an episode right now
        self._busy_since = 0.0                   # ... since (while at least one is)
        self._active = 0.0                       # seconds this session with at least one lane working
        state = self._load()
        self.enabled = bool(state.get("enabled"))
        try:
            saved = clean_config(state.get("config") or {})
        except ValueError:
            saved = {}
        self.config = RunnerConfig({**DEFAULT_CONFIG, **saved})
        now = time.time()
        self.pauses: Dict[str, float] = {}
        for m, t in (state.get("pauses") or {}).items():
            try:
                if float(t) > now:
                    self.pauses[m] = float(t)
            except (TypeError, ValueError):
                continue
        self.quota_streak: Dict[str, int] = {}
        self.recovering: Dict[str, Optional[int]] = {k: None for k in self.pauses}
        self.quota: Optional[Dict] = None
        self.quota_lock = threading.Lock()
        self.quota_unsupported = False  # this agy answers /quota with a model turn: stop asking
        self.status = "stopped"
        self.message = "" if self.enabled else state.get("message", "")
        self._read_log_tail()

    # ------------------------------------------------------------------ persistence & log

    @property
    def state_dir(self) -> Path:
        return self._state_dir or Path(paths.DATA_DIR)

    def _state_file(self) -> Path:
        return self.state_dir / "agent_runner.json"

    def _log_file(self) -> Path:
        return self.state_dir / "logs" / "agent_runner.log"

    def _pid_file(self) -> Path:
        return self.state_dir / "agent_runner.pid"

    def _other_process(self) -> Optional[int]:
        """The PID of another Omnisub process whose runner is working this queue, if any."""
        path = self._pid_file()
        try:
            pid = int(json.loads(path.read_text(encoding="utf-8")).get("pid") or 0)
            fresh = time.time() - path.stat().st_mtime < PID_STALE_SECONDS
        except Exception:
            return None
        return pid if pid != os.getpid() and fresh and _pid_alive(pid) else None

    def _touch_pid(self) -> None:
        try:
            os.utime(self._pid_file())
        except OSError:
            pass

    def _drop_pid(self) -> None:
        try:
            path = self._pid_file()
            if int(json.loads(path.read_text(encoding="utf-8")).get("pid") or 0) == os.getpid():
                path.unlink()
        except Exception:
            pass

    def _load(self) -> Dict:
        try:
            data = json.loads(self._state_file().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self) -> None:
        with self.lock:
            state = {"enabled": self.enabled, "config": self.config, "pauses": self.pauses, "status": self.status,
                     "message": self.message, "updated_at": _now_iso()}
        try:
            path = self._state_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as e:
            logger.warning(f"Could not save the runner state: {e}")

    def log(self, text: str, level: str = "info") -> None:
        stamp = datetime.now()
        with self.lock:
            self.seq += 1
            self.log_lines.append({"seq": self.seq, "t": stamp.strftime("%H:%M:%S"), "level": level, "text": text})
            try:
                path = self._log_file()
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists() and path.stat().st_size > 5_000_000:
                    path.replace(path.with_name("agent_runner.old.log"))
                with open(path, "a", encoding="utf-8") as f:
                    f.write(f"{stamp:%Y-%m-%d %H:%M:%S}\t{level}\t{text}\n")
            except OSError:
                pass
        getattr(logger, {"error": "error", "warn": "warning"}.get(level, "info"))(f"[runner] {text}")
        self._event("agent_runner_log", {"level": level, "text": text})

    def _read_log_tail(self) -> None:
        path = self._log_file()
        if not path.is_file():
            return
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]:
                parts = line.split("\t", 2)
                if len(parts) == 3:
                    self.seq += 1
                    self.log_lines.append({"seq": self.seq, "t": parts[0][11:], "level": parts[1], "text": parts[2]})
        except OSError:
            pass

    @staticmethod
    def _event(kind: str, data: Dict) -> None:
        try:
            from utils.event_broadcaster import broadcast_event
            broadcast_event(kind, data)
        except Exception:
            pass

    def _usage(self, model: str, lane: int, project: str, episode: str, reply: agent_cli.Reply) -> None:
        """Calls and tokens go to Omnisub's usage table (no cost: the agent CLIs run on a subscription)."""
        try:
            from utils import telemetry
            token = telemetry.usage_ctx.set({"project": project, "episode": episode})
            try:
                telemetry.record_usage(f"{self.config['engine']}:{model or 'default'}", "agent_runner",
                                       {"prompt_tokens": reply.tokens_in, "output_tokens": reply.tokens_out},
                                       lane=f"runner-{lane + 1}" if lane >= 0 else "one-off", cost_usd=0.0)
            finally:
                telemetry.usage_ctx.reset(token)
        except Exception:
            pass

    def note_call(self, model: str, reply: agent_cli.Reply, project: str, purpose: str) -> None:
        """A request made outside the lanes (``agy_llm``) counts in the session and the usage table."""
        with self.lock:
            self.session["calls"] += 1
            self.session["tokens_in"] += reply.tokens_in
            self.session["tokens_out"] += reply.tokens_out
        self._usage(model, -1, project, purpose, reply)

    def _lane_set(self, lane: int, **changes) -> None:
        with self.lock:
            self.lanes.setdefault(lane, {}).update(changes)

    def _work_begin(self) -> None:
        """A lane starts on an episode. The clock for throughput runs while at least one lane is working."""
        with self.lock:
            if self._busy == 0:
                self._busy_since = time.time()
            self._busy += 1

    def _work_end(self) -> None:
        with self.lock:
            if self._busy > 0:
                self._busy -= 1
                if self._busy == 0:
                    self._active += time.time() - self._busy_since

    def _transition(self, status: str, message: str = "") -> None:
        """Change the status and send its one ``agent_runner`` event {status, message}. Sent under the lock,
        so the events arrive in the order of the changes."""
        with self.lock:
            self.status, self.message = status, message
            self._event("agent_runner", {"status": status, "message": message})

    def _set_status(self, status: str, message: str = "") -> None:
        """Running/waiting updates from the lanes; never over a stop or a final state."""
        with self.lock:
            if self.stop_event.is_set() or self.draining.is_set() or self.status in ("done", "error"):
                return
            if (status, message) != (self.status, self.message):
                self._transition(status, message)

    # ------------------------------------------------------------------ control

    def running(self) -> bool:
        return any(t.is_alive() for t in self.threads)

    def _spawn(self, lane: int) -> threading.Thread:
        t = threading.Thread(target=self._lane, args=(lane,), name=f"agent-runner-{lane + 1}", daemon=True)
        self.alive += 1
        return t

    def start(self, config: Optional[Dict] = None, resumed: bool = False) -> Dict:
        clean = clean_config(config)
        with self.lock:
            if self.running():
                live = {k: v for k, v in clean.items() if k in LIVE_KEYS and v != self.config.get(k)}
                later = sorted(k for k in clean if k not in LIVE_KEYS and clean[k] != self.config.get(k))
                if live:
                    self.config.update(live)
                    self.log(f"Settings updated: {', '.join(sorted(live))}.")
                if later:
                    self.log(f"{', '.join(later)}: changes when the runner starts again.", "warn")
                if self.stop_event.is_set():
                    self.log("Still stopping; start again in a moment.", "warn")
                elif self.draining.is_set():  # "start" during a graceful stop: keep going
                    self.draining.clear()
                    self.enabled = True
                    self._transition("running", "")
                    for i, t in enumerate(self.threads):
                        if not t.is_alive():
                            self.threads[i] = self._spawn(i)
                            self.threads[i].start()
                    self.log("Stop cancelled; the runner keeps going.", "ok")
            else:
                other = self._other_process()
                if other:
                    raise RuntimeError(f"The runner is already working in another Omnisub process (PID {other}); "
                                       f"stop it there first.")
                self.config = RunnerConfig({**self.config, **clean})
                cfg = self.config
                if not self._exe():
                    raise FileNotFoundError(f"{agent_cli.ENGINES[cfg['engine']]} is not installed (or not on PATH)")
                try:
                    self._pid_file().parent.mkdir(parents=True, exist_ok=True)
                    self._pid_file().write_text(json.dumps({"pid": os.getpid(), "started": _now_iso()}), encoding="utf-8")
                except OSError:
                    pass
                self.enabled = True
                self._transition("running", "")
                self.stop_event = threading.Event()
                self.draining = threading.Event()
                self.session = {k: 0 for k in self.session}
                self.started = 0
                self.alive = 0
                self.consecutive_errors = 0
                self.crashes = 0
                self._crash_run_failed = []
                self.recovering = {}
                self.quota_streak = {}
                if not resumed:  # a restart after an error keeps the count, so a bad episode is not retried for ever
                    self.failures = {}
                self.gave_up = 0
                self.held = {}
                self._building = 0
                self._swept = 0.0
                self._busy, self._busy_since, self._active = 0, 0.0, 0.0
                self.lanes = {i: {"state": "starting", "task": None, "project": None, "episode": None, "model": None,
                                  "progress": None, "units_done": 0, "units_total": 0, "since": _now_iso()}
                              for i in range(cfg["lanes"])}
                self.threads = [self._spawn(i) for i in range(cfg["lanes"])]
                limit = int(cfg["max_episodes"] or 0)
                self.log(f"{'Resumed after a restart' if resumed else 'Started'}: {agent_cli.ENGINES[cfg['engine']]} "
                         f"({', '.join(m or 'default model' for m in self._models())}), {cfg['lanes']} lane(s), "
                         f"up to {cfg['per_show']} episode(s) of one show at a time"
                         f"{f', stopping after {limit} episode(s)' if limit else ''}.", "ok")
                for t in self.threads:  # started under the lock: a second click cannot start a second set
                    t.start()
        self._save()
        return self.info()

    def stop(self, now: bool = False, reason: str = "") -> Dict:
        """Gracefully (default): take no new episodes and stop when the ones in hand are submitted.
        ``now``: also cut off requests in flight (translated lines stay in the workspaces)."""
        with self.lock:
            self.enabled = False
            self.draining.set()
            procs: List[subprocess.Popen] = []
            if now:
                self._transition("stopped", reason or "Stopped. Half-done episodes continue when you start again.")
                self.stop_event.set()
                procs = list(self.procs.values())
            elif self.running():
                self._transition("stopping", reason or "Finishing the episodes in hand, then stopping.")
            else:
                self._transition("stopped", reason or "Stopped.")
            message = self.message
        for p in procs:
            agent_cli.kill_process(p)
        self._save()
        self.log(message, "warn")
        return self.info()

    def resume_now(self) -> Dict:
        """Clear every pause (e.g. after topping up a quota) so the lanes try right away."""
        with self.lock:
            self.pauses.clear()
            self.recovering.clear()
            self.quota_streak.clear()
            self.quota = None
            self.consecutive_errors = 0
        self._save()
        self.log("Pauses cleared; trying again now.", "ok")
        return self.info()

    def _no_new_work(self) -> Optional[str]:
        if self.stop_event.is_set():
            return "stopped"
        if self.draining.is_set():
            return "draining"
        limit = int(self.config["max_episodes"] or 0)
        if limit and self.started + self._building >= limit:
            return "limit"
        return None

    def preparing(self) -> List[str]:
        """Queue ids claimed by a lane whose workspace is being built right now."""
        with self.lock:
            return [k[len(CLAIM_PREFIX):] for k in self.held if k.startswith(CLAIM_PREFIX)]

    def cancel_task(self, task_id: str) -> bool:
        """An episode a lane holds was taken away from outside (cancelled, or released by hand; its workspace is
        closed): cut off that lane's request in flight, so the lane lets go now rather than when the reply comes.
        A show-context build in progress is left to finish, since it serves the show's next episodes too. True
        when a lane held the episode."""
        with self.lock:
            lanes = [i for i, v in self.lanes.items() if v.get("task") == task_id]
            procs = [self.procs[i] for i in lanes if i in self.procs and self.lanes[i].get("state") != BOOTSTRAP_STATE]
        for proc in procs:
            agent_cli.kill_process(proc)
        if lanes:
            self.log(f"Lane {lanes[0] + 1}: its episode was taken back from the queue ({task_id}); leaving it.", "warn")
        return bool(lanes)

    def _paused(self) -> bool:
        """"Pause translations" (the queue's pause switch) is on: the lanes take no new episode, they finish the
        ones in hand. Extraction and export belong to the worker's lanes and go on."""
        at, value = self._pause_read
        if time.time() - at >= PAUSE_CACHE_SECONDS:
            try:
                value = bool((storage.load_global_config() or {}).get("queue_worker_paused", False))
            except Exception:
                pass  # keep the last reading
            self._pause_read = (time.time(), value)
        return value

    def _sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and not (self.stop_event.is_set() or self.draining.is_set()):
            self.stop_event.wait(min(1.0, max(0.0, end - time.time())))

    def nudge(self) -> None:
        """New work was queued: idle lanes look now instead of at their next check."""
        with self.lock:
            self.wake_gen += 1

    def _idle_sleep(self, seconds: float) -> None:
        """The empty-queue wait: like ``_sleep``, but a ``nudge()`` ends it early."""
        gen = self.wake_gen
        end = time.time() + seconds
        while time.time() < end and self.wake_gen == gen                 and not (self.stop_event.is_set() or self.draining.is_set()):
            self.stop_event.wait(min(1.0, max(0.0, end - time.time())))

    def _wrap_up(self) -> None:
        """Called by the last lane to leave: set the final status if nothing else did."""
        with self.lock:
            if self.status in ("done", "error") or self.stop_event.is_set():
                return
            limit = int(self.config["max_episodes"] or 0)
            if self.draining.is_set():
                message = "Stopped after finishing the episodes in hand."
            elif limit and self.started >= limit:
                message = f"Finished the requested {limit} episode(s)."
            else:
                return
            self._transition("stopped", message)
            self.enabled = False
        self._save()
        self.log(message, "ok")

    def _finish(self, status: str, message: str, level: str = "ok") -> None:
        with self.lock:
            if self.status in ("done", "error") and self.stop_event.is_set():
                return
            self.enabled = False
            self._transition(status, message)
            self.stop_event.set()
            procs = list(self.procs.values()) if status == "error" else []
        for p in procs:
            agent_cli.kill_process(p)
        self._save()
        self.log(message, level)

    # Models, pauses and quota: services/runner/models.py. Choosing episodes: services/runner/claims.py.

    # ------------------------------------------------------------------ lanes

    def _lane(self, lane: int) -> None:
        try:
            while not self.stop_event.is_set():
                self._touch_pid()
                if self._no_new_work():
                    break
                if self._paused():
                    self._set_status("waiting", PAUSED_MESSAGE)
                    self._lane_set(lane, state="paused", **NO_WORK)
                    self._keep_claims()
                    self._idle_sleep(5)
                    continue
                model, wait = self._pick()
                if model is None:
                    resume = datetime.now() + timedelta(seconds=wait)
                    self._set_status("waiting", f"All models are paused (see the log). Trying again at {resume:%H:%M}.")
                    self._lane_set(lane, state="waiting for quota", **NO_WORK)
                    self._keep_claims()
                    self._sleep(min(wait + 1, 60))
                    continue
                model, wait = self._pick(lane)
                if model is None:
                    if self._pick()[0] is not None:  # only being tested by another lane
                        self._sleep(min(wait, 30))
                    continue  # else: the quota check just paused every model; the top of the loop says so
                try:
                    task = self._next_task(lane)
                except Exception as exc:  # claiming went wrong (a broken plan, an unreadable episode)
                    self._release_probe(model, lane)
                    if self._crashed(lane, None, exc):
                        break
                    self._idle_sleep(30)
                    continue
                if task is None:
                    self._release_probe(model, lane)
                    if self._no_new_work():
                        break
                    # Under the claim lock, so no claim can slip in between this check and the stop: another
                    # lane counts as busy from its claim (``held``), not only once it starts translating.
                    with self.claim_lock:
                        with self.lock:
                            others_busy = bool(self.held) or any(v.get("task") for i, v in self.lanes.items() if i != lane)
                        if not others_busy and not self.config["watch"]:
                            self._finish("done", self._empty_message())
                            break
                    if not others_busy:
                        self._set_status("idle", "The queue is empty — watching for new episodes.")
                    self._lane_set(lane, state="idle", **NO_WORK)
                    self._idle_sleep(WATCH_SECONDS if not others_busy else 15)
                    continue
                self._set_status("running", "")
                self._work_begin()
                crashed = False
                try:
                    self._episode(lane, task, model)
                except Exception as exc:  # one bad episode must not stop the other lanes
                    crashed = self._crashed(lane, task, exc)
                finally:
                    self._work_end()
                    with self.lock:
                        self.held.pop(task["task_id"], None)
                    self._queue_changed()
                    self._release_probe(model, lane)
                    self._lane_set(lane, state="between episodes", **NO_WORK)
                if crashed:
                    break
        except Exception as exc:  # keep the runner honest about crashes
            logger.exception("agent runner lane crashed")
            self._finish("error", f"Lane {lane + 1} stopped after an unexpected error: {exc!r}", "error")
        finally:
            self._lane_set(lane, state="stopped", **NO_WORK)
            with self.lock:
                self.alive -= 1
                last = self.alive <= 0
            if last:
                self._drop_pid()
                self._wrap_up()

    # ------------------------------------------------------------------ crashes

    def _crashed(self, lane: int, task: Optional[Dict], exc: BaseException) -> bool:
        """An unexpected exception in a lane. With ``task``: that episode is given back to the queue and
        counted against it (``MAX_FAILURES`` leaves it for a person), and the lane goes on. Returns True
        when the runner should stop instead: ``MAX_CRASHES`` crashes in a row with nothing submitted in
        between means the fault is not in one episode."""
        logger.exception("agent runner lane crashed")
        what = f"{exc.__class__.__name__}: {exc}"
        with self.lock:
            self.crashes += 1
            crashes = self.crashes
        if task is not None:
            outcome = self._put_back(task, f"unexpected error ({what})", crash=True)
            self.log(f"Lane {lane + 1}: {task['project_name']} {task['episode_name']} failed with an unexpected "
                     f"error ({what}); {outcome}. Task {task['task_id']}.", "error")
        else:
            self.log(f"Lane {lane + 1}: unexpected error while looking for work ({what}).", "error")
        if crashes >= MAX_CRASHES:
            self._unfail_crash_run()
            self._finish("error", f"Stopped after {crashes} unexpected errors in a row (last: {what}); see the log.",
                         "error")
            return True
        return False

    def _unfail_crash_run(self) -> None:
        """The runner stops on a run of crashes with nothing submitted in between: the fault is the runner's (or
        the machine's), not the episodes'. Those it marked failed during the run wait in the queue again."""
        with self.lock:
            ids, self._crash_run_failed = list(self._crash_run_failed), []
            self.gave_up = max(0, self.gave_up - len(ids))
        if not ids:
            return
        try:
            with TranslationQueue()._conn() as conn:
                for queue_id in ids:
                    conn.execute("UPDATE translation_queue SET status = 'pending', completed_at = NULL WHERE id = ? "
                                 "AND status = 'failed' AND last_error LIKE 'The translation runner gave up%'", (queue_id,))
                conn.commit()
            TranslationQueue()._changed("runner_crash_run")
        except Exception as exc:
            logger.warning(f"Could not put the episodes of the crash run back in the queue: {exc}")

    def _still_mine(self, folder: Path, lane: int, title: str) -> bool:
        status = self.service._read_manifest(folder).get("status")
        if status in TERMINAL:
            self.log(f"Lane {lane + 1}: {title} was {status} elsewhere — leaving it.", "warn")
            return False
        return True

    # One episode: services/runner/episode.py (its requests: runner/requests.py; its context: runner/context.py).

    # ------------------------------------------------------------------ reporting

    def engines(self) -> Dict:
        now = time.time()
        if now - self._engines[0] > ENGINE_CACHE_SECONDS:
            status = agent_cli.engine_status()
            for name, cmd in self.exe_override.items():
                status[name] = " ".join(cmd)
            self._engines = (now, status)
        return self._engines[1]

    def _rates(self, now: float, pending: int) -> Tuple[Dict, Optional[float]]:
        """Throughput over the time lanes spent on episodes this session (idle and paused time left out) and
        the minutes the pending queue should take at that pace. Lock held."""
        active = self._active + (max(0.0, now - self._busy_since) if self._busy else 0.0)
        minutes = active / 60
        episodes, lines = self.session["episodes"], self.session["lines"]
        rate = lines / minutes if episodes and lines and minutes > 0 else None
        throughput = {"lines_per_min": round(rate, 1) if rate else None,
                      "episodes_per_hour": round(episodes * 60 / minutes, 2) if rate else None,
                      "active_minutes": round(minutes, 3)}
        return throughput, round(pending * (lines / episodes) / rate, 1) if rate else None

    def info(self, log_since: int = 0, quota: bool = False) -> Dict:
        if quota:
            self.quota_status(force=True)
        now = time.time()
        engines = self.engines()
        pending = self._pending_count()
        with self.lock:
            pauses = {m: datetime.fromtimestamp(t).isoformat(timespec="seconds") for m, t in self.pauses.items() if t > now}
            groups = None
            if self.quota and self.quota.get("groups"):
                groups = [{"name": g.get("name"), "description": g.get("description"), "buckets": [
                    {"window": b.get("window"), "remaining": b.get("remaining_fraction"), "reset": b.get("reset_time")}
                    for b in g.get("buckets") or []]} for g in self.quota["groups"]]
            log = [e for e in self.log_lines if e["seq"] > log_since]
            throughput, eta = self._rates(now, pending)
            return {
                "enabled": self.enabled, "running": self.running(), "status": self.status, "message": self.message,
                "paused": self._paused(),
                "config": dict(self.config), "defaults": dict(DEFAULT_CONFIG), "models": self._models(),
                "lanes": [{"lane": i + 1, **v} for i, v in sorted(self.lanes.items())],
                "pauses": pauses, "session": dict(self.session), "started": self.started,
                "pending": pending, "throughput": throughput, "eta_minutes": eta,
                "preparing": [k[len(CLAIM_PREFIX):] for k in self.held if k.startswith(CLAIM_PREFIX)],
                "quota": groups,
                "quota_fetched": datetime.fromtimestamp(self.quota["fetched"]).isoformat(timespec="seconds")
                if self.quota else None,
                "engines": engines,
                "log": log if log_since else log[-300:],
                "log_seq": self.seq,
            }


# ------------------------------------------------------------------ the one runner

_runner: Optional[AgentRunner] = None
_runner_lock = threading.Lock()


def get_runner() -> AgentRunner:
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = AgentRunner()
        return _runner


def current_runner() -> Optional[AgentRunner]:
    """The process's runner if one was built (never builds one: for callers that only need to notify it)."""
    return _runner


def nudge() -> None:
    """Tell a running runner that a translation was queued (no runner yet: nothing to do)."""
    runner = _runner
    if runner is not None and runner.enabled:
        runner.nudge()


RECOVER_AFTER_SECONDS = 30 * 60
_error_seen_at: Optional[float] = None


def recover() -> bool:
    """Start the runner again when it stopped on an error (a CLI that lost its sign-in, eight failures in a
    row) and RECOVER_AFTER_SECONDS have passed since this was first seen — what the next boot would do —
    provided the engine is agent and starting on boot is on. A runner stopped by hand, or one that
    finished its work, stays as it is. Called by the maintenance loop."""
    global _error_seen_at
    r = get_runner()
    if r.running() or r.status != "error":
        _error_seen_at = None
        return False
    now = time.time()
    if _error_seen_at is None:
        _error_seen_at = now
        return False
    if now - _error_seen_at < RECOVER_AFTER_SECONDS:
        return False
    cfg = storage.load_global_config() or {}
    if cfg.get("translation_engine", "agent") != "agent" or cfg.get("agent_runner_autostart", True) is not True:
        return False
    _error_seen_at = None
    try:
        r.log("Starting again after the error above.", "warn")
        r.start({**r.config, "watch": True}, resumed=True)
        return True
    except Exception as exc:
        r.log(f"Could not start again: {exc}", "error")
        return False


def autostart() -> bool:
    """Resume the runner if it was on when Omnisub last stopped, or start it (watching the queue) when the
    translation engine is 'agent' — the default — and ``agent_runner_autostart`` has not been turned off."""
    r = get_runner()
    global_cfg = storage.load_global_config() or {}
    agent_auto = (
        global_cfg.get("translation_engine", "agent") == "agent"
        and global_cfg.get("agent_runner_autostart", True) is True
    )
    if (r.enabled or agent_auto) and not r.running():
        try:
            r.start({**r.config, "watch": True}, resumed=r.enabled)
            logger.info("Agent runner auto-started on boot (keep watching)")
            return True
        except Exception as exc:
            r.log(f"Could not autostart: {exc}", "error")
            logger.error(f"Could not autostart agent runner: {exc}")
    return False


def stop_all() -> None:
    """Shutdown: stop the CLI processes (the lanes' and those of one-off requests) but leave the runner enabled,
    so it resumes next time."""
    try:
        from services import agy_llm
        agy_llm.kill_all()
    except Exception:
        pass
    r = _runner
    if r is None:
        return
    r.stop_event.set()
    for p in list(r.procs.values()):
        agent_cli.kill_process(p)
