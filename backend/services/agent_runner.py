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
* A finished episode is submitted through the normal agent submit — validation, Script Guard,
  glossary merge, Translation Memory, summary, export — exactly as if an agent had typed
  ``agent_task.py submit``.

Its on/off state, pauses and log survive restarts; an enabled runner resumes when Omnisub
starts, continuing its half-done episodes first. ``info()`` feeds the panel (lanes with lines
done/total, queue length, throughput, ETA); every status change goes out once as an
``agent_runner`` event with its status and message.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from utils import paths, storage
from utils.translation_queue import TranslationQueue, KIND_TRANSLATION
from services import agent_cli, agent_prompts as ap, agent_workspace as aw

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
    "fallback_models": ["claude-sonnet-4-6"],   # tried while the main model's quota is used up
    "lanes": 3,                  # episodes translated at the same time
    "per_show": 2,               # at most this many of them from one show
    "chunk_lines": 250,          # lines per request; longer episodes are split at scene changes
    "fix_rounds": 2,             # follow-up requests for lines that came back missing or wrong
    "max_episodes": 0,           # 0: until the queue is empty or the runner is stopped
    "watch": False,              # keep running on an empty queue and take new episodes as they are queued
    "api_rescue": True,          # lines the CLI model's safety filter refuses go to the Gemini API at the end
    "rescue_model": "",          # ... with this model ("": the project's translation model)
    "api_url": DEFAULT_API_URL,
}
LIVE_KEYS = ("model", "fallback_models", "per_show", "chunk_lines", "fix_rounds", "max_episodes", "watch",
             "api_rescue", "rescue_model", "api_url")
API_PREFIX = "api:"              # a model name with this prefix is called through the Gemini API, not the CLI
WATCH_SECONDS = 60
QUOTA_MARGIN = 30
QUOTA_CACHE_SECONDS = 120
SMALL_BLOCK = 20                 # refused requests this small skip the retry and may go to another model family
EARLIER_LINES = 8                # translated lines of the previous chunk shown for continuity
AROUND_BEFORE, AROUND_AFTER = 2, 1   # translated lines shown around a line filled into a translated episode
MAX_AROUND = 80                  # ... for at most this many lines per request
MAX_FAILURES = 2                 # an episode that fails this often in one session is left for a person
KEEPALIVE_SECONDS = 1800         # a waiting runner touches its claimed workspaces so they are not released as idle
ENGINE_CACHE_SECONDS = 60
HOUSEKEEPING_SECONDS = 60        # abandoned claims are released at most this often while the runner looks for work
PENDING_CACHE_SECONDS = 5        # the queue length shown in the panel is read at most this often
CONTEXT_CACHE_SECONDS = 300      # a show's glossary matcher and prompt prefix are reused this long
PID_STALE_SECONDS = 45 * 60      # a lock file not touched for this long belongs to a runner that died
TERMINAL = ("completed", "cancelled", "expired", "released")
NO_WORK = {"task": None, "project": None, "episode": None, "progress": None,
           "units_done": 0, "units_total": 0}   # a lane's fields while it has no episode in hand
SENSITIVE = re.compile(r"\b(child|children|kids?|bab(?:y|ies)|infants?|toddlers?|boys?|girls?|sons?|daughters?|"
                       r"grand(?:daughter|son|child)s?|orphans?|little ones?|lads?|lass(?:es)?|brats?|offspring|"
                       r"pregnan\w*|womb|unborn)\b", re.I)


def _human(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
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
    if "api_url" in out:
        out["api_url"] = normalize_api_url(str(out["api_url"] or "").strip())
    for key in ("watch", "api_rescue"):
        if key in out:
            out[key] = out[key] in (True, 1, "1", "true", "True", "yes", "on")
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


class RunnerConfig(dict):
    """Configuration container for AgentRunner."""
    def __init__(self, *args, **kwargs):
        initial = {}
        if args and isinstance(args[0], dict):
            initial.update(args[0])
        initial.update(kwargs)
        cleaned = clean_config(initial)
        super().__init__(cleaned)
        for k, v in cleaned.items():
            setattr(self, k, v)


class AgentRunner:
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
        self.failures: Dict[str, int] = {}       # queue id -> failed attempts this session
        self.log_lines: deque = deque(maxlen=1500)
        self.seq = 0
        self.session = {"episodes": 0, "lines": 0, "calls": 0, "tokens_in": 0, "tokens_out": 0, "quota_hits": 0}
        self.consecutive_errors = 0
        self._kept = 0.0
        self._swept = 0.0                        # when the service last released abandoned claims for us
        self._engines: Tuple[float, Dict] = (0.0, {})
        self._pending: Tuple[float, int] = (0.0, 0)   # (read at, pending translation items)
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
        self.config = {**DEFAULT_CONFIG, **saved}
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
                                       lane=f"runner-{lane + 1}", cost_usd=0.0)
            finally:
                telemetry.usage_ctx.reset(token)
        except Exception:
            pass

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
                self.config = {**self.config, **clean}
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
                self.failures = {}
                self.held = {}
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
        if limit and self.started >= limit:
            return "limit"
        return None

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

    # ------------------------------------------------------------------ models, pauses, quota

    def _exe(self) -> Optional[List[str]]:
        engine = self.config["engine"]
        return self.exe_override.get(engine) or agent_cli.engine_command(engine)

    def _models(self) -> List[str]:
        out = [self.config["model"] or ""]
        for m in self.config["fallback_models"] or []:
            m = (m or "").strip()
            if m and m not in out:
                out.append(m)
        return out

    def _available(self, model: str) -> bool:
        with self.lock:
            return self.pauses.get(model or "default", 0) <= time.time()

    def _pick(self, lane: Optional[int] = None) -> Tuple[Optional[str], float]:
        """The first usable model. With ``lane`` (about to start an episode): the provider's quota is
        checked first, and after a pause only one lane at a time tests whether a model is back."""
        if lane is not None:
            self._check_quota()
        now = time.time()
        with self.lock:
            waits = []
            for m in self._models():
                key = m or "default"
                until = self.pauses.get(key, 0)
                if until > now:
                    waits.append(until - now)
                    continue
                if key in self.recovering:
                    owner = self.recovering[key]
                    if lane is not None and owner not in (None, lane):
                        waits.append(10.0)
                        continue
                    if lane is not None:
                        self.recovering[key] = lane
                return m, 0.0
        return None, min(waits) if waits else 60.0

    def _release_probe(self, model: Optional[str], lane: int) -> None:
        with self.lock:
            key = model or "default"
            if self.recovering.get(key) == lane:
                self.recovering[key] = None

    def _pause(self, model: str, seconds: float, why: str, group: Optional[List[str]] = None) -> None:
        until_ts = time.time() + seconds
        keys = sorted({(m or "default") for m in (group or [model])})
        with self.lock:
            fresh = [k for k in keys if self.pauses.get(k, 0) < until_ts - 60]
            for k in keys:
                self.pauses[k] = max(self.pauses.get(k, 0), until_ts)
                self.recovering[k] = None
        if not fresh:
            return
        self._save()
        until = datetime.now() + timedelta(seconds=seconds)
        self.log(f"{why} — {', '.join(keys)} paused until {until:%H:%M} ({_human(seconds)}).", "warn")

    def _recovered(self, model: str) -> None:
        with self.lock:
            self.recovering.pop(model or "default", None)
            self.quota_streak[model or "default"] = 0

    def quota_status(self, force: bool = False) -> Optional[Dict]:
        if self.config["engine"] != "antigravity":
            return None
        with self.quota_lock:
            fresh = self.quota and time.time() - self.quota["fetched"] < QUOTA_CACHE_SECONDS
            if force or not fresh:
                exe = self._exe()
                groups = agent_cli.antigravity_quota(exe) if exe else None
                self.quota = {"groups": groups or [], "fetched": time.time()}  # failures are cached too
            return self.quota if self.quota and self.quota["groups"] else None

    def _quota_group(self, model: str) -> Tuple[Optional[Dict], List[str]]:
        q = self.quota_status()
        if not q:
            return None, [model]
        group = agent_cli.group_of(model, q["groups"])
        if not group:
            return None, [model]
        members = [m for m in self._models() if agent_cli.group_of(m, q["groups"]) is group]
        return group, sorted(members) or [model]

    def _check_quota(self) -> None:
        """Pause models whose quota group the provider reports as used up (no request is spent)."""
        for m in self._models():
            if not self._available(m):
                continue
            group, members = self._quota_group(m)
            until = agent_cli.exhausted_until(group) if group else None
            if until and until > time.time():
                self._pause(m, until - time.time() + QUOTA_MARGIN,
                            f"{group['name']}: quota used up (reported by Antigravity)", group=members)

    def _failure(self, reply: agent_cli.Reply, model: str, lane: int, what: str) -> None:
        kind = reply.failure
        if kind == "stopped":
            return
        key = model or "default"
        if kind == "auth":
            self._finish("error", f"{agent_cli.ENGINES[self.config['engine']]} is not signed in or not allowed: "
                                  f"{reply.message[:200]} — sign in (run the CLI once in a terminal), then start again.",
                         "error")
            return
        if kind == "quota":
            with self.lock:
                if self.pauses.get(key, 0) > time.time():
                    return  # another lane already reported this quota stop
                self.session["quota_hits"] += 1
                streak = self.quota_streak.get(key, 0) + 1
                self.quota_streak[key] = streak
            members = [model]
            if self.config["engine"] == "antigravity":  # ask when it refills, for the whole quota group
                self.quota_status(force=True)
                group, members = self._quota_group(model)
                until = (agent_cli.exhausted_until(group) or agent_cli.exhausted_until(group, 0.10)) if group else None
                if until and until > time.time():
                    self._pause(model, until - time.time() + QUOTA_MARGIN,
                                f"Quota reached — {group['name']} refills at the time Antigravity reports", group=members)
                    return
            if reply.wait is not None:
                wait, how = reply.wait + QUOTA_MARGIN, "reset time from the error"
            else:
                wait, how = min(3600, 600 * 2 ** (streak - 1)), "no reset time given; backing off"
            self._pause(model, wait, f"Quota reached ({how}): {reply.message[:160]}", group=members)
            return
        if kind == "rate":
            self._pause(model, reply.wait + 5 if reply.wait is not None else 90, f"Rate limited: {reply.message[:160]}")
            return
        with self.lock:
            self.consecutive_errors += 1
            errors = self.consecutive_errors
        self.log(f"Lane {lane + 1}: {what} failed ({reply.message[:300]}).", "error")
        if errors >= 8:
            self._finish("error", "Stopped after 8 failures in a row; see the log.", "error")
        elif errors >= 3:
            self._pause(model, 600, "Several failures in a row")
        else:
            self.stop_event.wait(20)

    # ------------------------------------------------------------------ choosing episodes

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
        metadata reads: id, project and episode are all a claim needs."""
        with TranslationQueue()._conn() as conn:
            rows = conn.execute("""
                SELECT id, project_name, episode_name
                FROM translation_queue
                WHERE status = 'pending' AND kind = ?
                ORDER BY priority ASC, created_at ASC
            """, (KIND_TRANSLATION,)).fetchall()
        return [{k: r[k] for k in ("id", "project_name", "episode_name")} for r in rows]

    def _pending_count(self) -> int:
        """Pending translation items for the panel (read at most every few seconds)."""
        at, count = self._pending
        if time.time() - at < PENDING_CACHE_SECONDS:
            return count
        try:
            with TranslationQueue()._conn() as conn:
                count = conn.execute("SELECT COUNT(*) FROM translation_queue WHERE status = 'pending' AND kind = ?",
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

    def _next_task(self) -> Optional[Dict]:
        per_show = int(self.config["per_show"] or 1)
        with self.claim_lock:
            if self._no_new_work():
                return None
            with self.lock:
                busy = dict(self.held)

            def show_full(project: str) -> bool:
                return sum(1 for p in busy.values() if p == project) >= per_show

            for t in self._runner_tasks():
                if t["task_id"] not in busy and not show_full(t["project_name"]):
                    return self._hold(t)
            self._housekeeping()
            for item in self._pending_items():
                if self.failures.get(item["id"], 0) >= MAX_FAILURES or show_full(item["project_name"]):
                    continue
                res = self.service.prepare_task_workspace(queue_item_id=item["id"])
                self._queue_changed()
                status = res.get("status")
                if status == "claimed":
                    folder = Path(res["workspace_dir"])
                    manifest = self.service._read_manifest(folder)
                    manifest["runner"] = {"engine": self.config["engine"], "claimed_at": _now_iso()}
                    self.service._write_manifest(folder, manifest)
                    return self._hold({k: res[k] for k in ("task_id", "queue_id", "project_name", "episode_name",
                                                           "workspace_dir")})
                if status == "nothing_to_translate":
                    self.log(f"{item['project_name']} {item['episode_name']}: already fully translated (re-exported).")
                elif status == "missing_source":
                    self.log(f"{item['project_name']} {item['episode_name']}: no source subtitle — skipped.", "warn")
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
        stuck = sum(1 for n in self.failures.values() if n >= MAX_FAILURES)
        if stuck:
            return (f"Nothing more the runner can do: {stuck} episode(s) failed twice and stay in the queue for a "
                    f"person (see the log). Runner stopped.")
        return "The queue is empty. Runner stopped."

    # ------------------------------------------------------------------ lanes

    def _lane(self, lane: int) -> None:
        try:
            while not self.stop_event.is_set():
                self._touch_pid()
                if self._no_new_work():
                    break
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
                task = self._next_task()
                if task is None:
                    self._release_probe(model, lane)
                    if self._no_new_work():
                        break
                    with self.lock:
                        others_busy = any(v.get("task") for i, v in self.lanes.items() if i != lane)
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
                try:
                    self._episode(lane, task, model)
                finally:
                    self._work_end()
                    with self.lock:
                        self.held.pop(task["task_id"], None)
                    self._queue_changed()
                    self._release_probe(model, lane)
                    self._lane_set(lane, state="between episodes", **NO_WORK)
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

    # ------------------------------------------------------------------ one episode

    def _still_mine(self, folder: Path, lane: int, title: str) -> bool:
        status = self.service._read_manifest(folder).get("status")
        if status in TERMINAL:
            self.log(f"Lane {lane + 1}: {title} was {status} elsewhere — leaving it.", "warn")
            return False
        return True

    def _episode(self, lane: int, task: Dict, model: str) -> None:
        folder = Path(task["workspace_dir"])
        manifest = self.service._read_manifest(folder)
        project = manifest.get("project_name", task["project_name"])
        episode = manifest.get("episode_name", task["episode_name"])
        title = f"{project} — {episode}"
        if not self._still_mine(folder, lane, title):
            return
        plan = json.loads((folder / aw.PLAN_FILE).read_text(encoding="utf-8"))
        units = aw.units_from_plan(plan)
        by_id = {u.id: u for u in units}
        target_name = manifest.get("target_language", "Greek")
        code = manifest.get("target_code") or "el"
        work = folder / aw.WORK_FILE
        whole = not (plan.get("stats") or {}).get("already_translated_units")
        ctx = self._context(project, episode, target_name, units, whole, code, manifest.get("is_primary", True))
        self._lane_set(lane, state="working", task=task["task_id"], project=project, episode=episode, model=model,
                       progress=None, units_done=0, units_total=0, since=_now_iso())

        def current() -> Dict[int, str]:
            return aw.parse_work_file(work.read_text(encoding="utf-8"))[0]

        def needs(u: aw.Unit, cur: Dict[int, str]) -> bool:
            return aw.looks_untranslated(u.text, cur.get(u.id, ""), code)

        def progress(cur: Dict[int, str]) -> None:
            done = sum(1 for u in units if not needs(u, cur))
            self._lane_set(lane, progress=f"{done}/{len(units)}", units_done=done, units_total=len(units))

        cur = current()
        todo = [u for u in units if needs(u, cur)]
        progress(cur)
        chunks = self._chunks(todo, int(self.config["chunk_lines"] or 250))
        resumed = len(todo) < len(units)
        self.log(f"Lane {lane + 1}: {title} — {len(todo)} line(s){f' of {len(units)} (resumed)' if resumed else ''}"
                 f"{f' in {len(chunks)} parts' if len(chunks) > 1 else ''}, {model or 'default model'}"
                 f"{'' if whole else ' (filling in an already translated episode)'}.")
        started = time.time()
        tokens = [0, 0]
        # Lines the filter refuses are set aside here and sent on their own at the end, when there is
        # somewhere to send them (else a refusal is narrowed down on the spot, as before).
        refused: Optional[set] = set() if self._rescue_target(project, model) else None
        for k, chunk in enumerate(chunks):
            if self.stop_event.is_set():
                return
            part = f"part {k + 1} of {len(chunks)}" if len(chunks) > 1 else ""
            ok = self._chunk(lane, model, folder, title, ctx, units, by_id, chunk, code, needs, current,
                             part=part, first_part=k == 0, last=k == len(chunks) - 1, tokens=tokens,
                             project=project, episode=episode, refused=refused)
            if not ok:
                return  # stopped, cancelled, or the model is paused: the episode stays claimed and resumes later
            progress(current())
        if self.stop_event.is_set():
            return
        if refused:
            if not self._rescue(lane, model, folder, title, ctx, units, by_id, refused, code, needs, current,
                                tokens=tokens, project=project, episode=episode):
                return
            progress(current())
            if self.stop_event.is_set():
                return
        self._submit(lane, task, folder, units, current, needs, started, tokens)

    # ------------------------------------------------------------------ refused lines

    def _has_gemini_api_key(self) -> bool:
        if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
            return True
        cfg = getattr(storage, "GLOBAL_CONFIG", {}) or {}
        return bool(cfg.get("gemini_api_key"))

    def _get_gemini_api_key(self) -> str:
        key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
        if not key:
            cfg = getattr(storage, "GLOBAL_CONFIG", {}) or {}
            key = cfg.get("gemini_api_key") or ""
        return key

    def _rescue_model(self, project: str) -> Optional[str]:
        """The Gemini API model for lines the CLI model's filter refused; None when the rescue is off,
        there is no Gemini API key, or the project translates with a local model."""
        if not self.config.get("api_rescue", True) or not self._has_gemini_api_key():
            return None
        model = (self.config.get("rescue_model") or "").strip()
        if not model:
            try:
                from utils.model_resolver import resolve_model
                model = resolve_model("translation", storage.load_resolved_project_metadata(project) or {})
            except Exception:
                model = ""
        if not model or model.lower().startswith("local"):
            return None
        return model

    def _other_family(self, model: str, exclude: Tuple[str, ...] = ()) -> Optional[str]:
        """The first usable CLI model of another family than ``model`` (the Gemini API counts as Gemini)."""
        fam = agent_cli.family(model[len(API_PREFIX):] if model.startswith(API_PREFIX) else model)
        for alt in self._models():
            if alt and alt not in exclude and agent_cli.family(alt) != fam and self._available(alt):
                return alt
        return None

    def _rescue_target(self, project: str, model: str) -> Optional[str]:
        api = self._rescue_model(project)
        return API_PREFIX + api if api else self._other_family(model, exclude=(model,))

    def _rescue(self, lane: int, model: str, folder: Path, title: str, ctx: Dict, units: List[aw.Unit],
                by_id: Dict[int, aw.Unit], refused: set, code: str, needs, current, *, tokens: List[int],
                project: str, episode: str) -> bool:
        """The lines set aside after a refusal, sent on their own now that the rest of the episode is
        translated (so each comes with the translated lines around it): to the Gemini API, else to a
        model of another family. Returns False when the work must stop (the episode resumes later)."""
        todo = [u for u in units if u.id in refused and needs(u, current())]
        target = self._rescue_target(project, model)
        if not todo or not target:
            return True
        where = f"the Gemini API ({target[len(API_PREFIX):]})" if target.startswith(API_PREFIX) else target
        self.log(f"Lane {lane + 1}: sending the {len(todo)} refused line(s) of {title} to {where}.")
        need_summary = ctx["whole"] and not self._summary(folder)  # the refused part was the one asked for it
        parts = self._chunks(todo, int(self.config["chunk_lines"] or 250))
        for k, chunk in enumerate(parts):
            part = "refused lines" + (f", part {k + 1} of {len(parts)}" if len(parts) > 1 else "")
            ok = self._chunk(lane, target, folder, title, ctx, units, by_id, chunk, code, needs, current, part=part,
                             first_part=False, last=k == len(parts) - 1, tokens=tokens, project=project,
                             episode=episode, start={"after_rescue": True, "tried": (model,)}, extras=need_summary,
                             around_done=True)
            if not ok:
                return False
        return True

    @staticmethod
    def _around_done(units: List[aw.Unit], todo: List[aw.Unit], cur: Dict[int, str], needs) -> ap.Neighbours:
        """The translated dialogue right before and after each line of ``todo``, from the work file."""
        dialogue = [u for u in units if u.kind == "dialogue"]
        pos = {u.id: i for i, u in enumerate(dialogue)}
        out: ap.Neighbours = {}
        for u in todo[:MAX_AROUND]:
            i = pos.get(u.id)
            if i is None:
                continue
            before = [(d.text, cur[d.id]) for d in dialogue[max(0, i - 6):i] if not needs(d, cur)][-AROUND_BEFORE:]
            after = [(d.text, cur[d.id]) for d in dialogue[i + 1:i + 6] if not needs(d, cur)][:AROUND_AFTER]
            if before or after:
                out[u.id] = (before, after)
        return out

    def _api_call(self, model: str, prompt: str, project: str, episode: str) -> agent_cli.Reply:
        """One request to the Gemini API through Omnisub's model seam (rate limiter, usage and cost
        telemetry, the Gemini safety setting). No text back counts as a failure, with the reason."""
        import asyncio
        from adk_agents.llm_factory import generate
        from utils import telemetry
        api_key = self._get_gemini_api_key()
        if api_key and not os.environ.get("GOOGLE_API_KEY"):
            os.environ["GOOGLE_API_KEY"] = api_key
        blocked: List[str] = []
        started = time.time()
        token = telemetry.usage_ctx.set({"project": project, "episode": episode})
        try:
            text = asyncio.run(generate(model, prompt, role="translation", on_blocked=blocked.append))
        except Exception as exc:
            return agent_cli.Reply(failure="error", message=f"{type(exc).__name__}: {exc}"[:300],
                                   seconds=time.time() - started)
        finally:
            telemetry.usage_ctx.reset(token)
        reply = agent_cli.Reply(text=text or "", seconds=time.time() - started)
        if not reply.text.strip():
            reply.failure = "blocked" if blocked else "error"
            reply.message = blocked[0] if blocked else "the answer was empty"
        return reply

    def _project_context(self, project: str, target_name: str) -> Dict:
        """What every episode of a show shares: the glossary, the matcher over it and the static part of the
        prompt. Reused for a few minutes; a changed glossary or context guide builds a new one."""
        from utils.translation_rules import build_style_guide
        from utils.glossary_judgment import Matcher, project_vocabulary
        proj_meta = storage.load_resolved_project_metadata(project) or {}
        glossary = proj_meta.get("glossary") or {"terms": []}
        guide = (proj_meta.get("context_guide") or "").strip()
        terms = glossary.get("terms") or []
        stamp = hashlib.sha1(json.dumps([terms, guide], ensure_ascii=False, sort_keys=True, default=str)
                             .encode("utf-8")).hexdigest()
        key, now = (project, target_name), time.time()
        with self.lock:
            hit = self._shared.get(key)
            if hit and hit["stamp"] == stamp and now - hit["at"] < CONTEXT_CACHE_SECONDS:
                return hit["value"]
        value = {"glossary": glossary, "has_guide": bool(guide),
                 "matcher": Matcher(terms, project_vocabulary(project)),
                 "static": ap.static_part(target_name, guide, build_style_guide(target_name))}
        with self.lock:
            self._shared = {k: v for k, v in self._shared.items() if now - v["at"] < CONTEXT_CACHE_SECONDS}
            self._shared[key] = {"at": now, "stamp": stamp, "value": value}
        return value

    def _context(self, project: str, episode: str, target_name: str, units: List[aw.Unit], whole: bool,
                 code: str, is_primary: bool) -> Dict:
        story = ""
        try:
            from utils.episode_summaries import EpisodeSummaryManager
            prev = EpisodeSummaryManager(project).get_previous_summaries(episode, count=3)
            story = "\n".join(f"- {p['episode']}: {p['summary']}" for p in prev if p.get("summary"))
        except Exception:
            pass
        return {**self._project_context(project, target_name), "story": story, "whole": whole,
                "around": {} if whole else self._neighbours(project, episode, units, code, is_primary)}

    @staticmethod
    def _neighbours(project: str, episode: str, units: List[aw.Unit], code: str, is_primary: bool) -> ap.Neighbours:
        """For lines filled into an already translated episode: the translated dialogue around each."""
        try:
            cues = storage.load_episode_data(project, episode) or []
        except Exception:
            return {}

        def pair(i: int) -> Optional[Tuple[str, str]]:
            cue = cues[i]
            if not isinstance(cue, dict):
                return None
            meta = cue.get("_ass") if isinstance(cue.get("_ass"), dict) else {}
            if meta.get("kind") == "sign" or meta.get("translatable") is False:
                return None
            src = (cue.get("original") or "").strip()
            tgt = aw.cue_translation(cue, code, is_primary)
            if not src or not tgt or aw.looks_untranslated(src, tgt, code):
                return None
            return src, tgt

        out: ap.Neighbours = {}
        for u in units:
            if u.kind != "dialogue" or not u.cues or max(u.cues) >= len(cues):
                continue
            first, last = min(u.cues), max(u.cues)
            before = [p for p in (pair(i) for i in range(first - 1, max(-1, first - 6), -1)) if p][:AROUND_BEFORE][::-1]
            after = [p for p in (pair(i) for i in range(last + 1, min(len(cues), last + 6))) if p][:AROUND_AFTER]
            if before or after:
                out[u.id] = (before, after)
        return out

    @staticmethod
    def _characters(project: str, glossary: Dict, texts: List[str]) -> str:
        try:
            from utils.character_profiles import CharacterProfileManager
            cpm = CharacterProfileManager(project)
            return cpm.build_profile_context(cpm.get_profiles_for_chunk(texts, glossary=glossary, fallback_to_main_cast=True))
        except Exception:
            return ""

    @staticmethod
    def _chunks(units: List[aw.Unit], size: int) -> List[List[aw.Unit]]:
        """Dialogue in order, cut at scene changes near ``size`` lines; on-screen text rides with the
        last chunk (or gets its own when there is a lot of it)."""
        dialogue = [u for u in units if u.kind == "dialogue"]
        signs = [u for u in units if u.kind != "dialogue"]
        chunks: List[List[aw.Unit]] = []
        cur: List[aw.Unit] = []
        for i, u in enumerate(dialogue):
            cur.append(u)
            nxt = dialogue[i + 1] if i + 1 < len(dialogue) else None
            if nxt is None:
                break
            scene_break = nxt.start_ms - u.end_ms > aw.SCENE_GAP_MS
            rest = len(dialogue) - i - 1
            if len(cur) >= size * 1.25 or (len(cur) >= size * 0.6 and scene_break and rest > size * 0.3):
                chunks.append(cur)
                cur = []
        if cur:
            chunks.append(cur)
        if signs:
            if chunks and len(chunks[-1]) + len(signs) <= size * 1.4:
                chunks[-1] = chunks[-1] + signs
            else:
                chunks.append(signs)
        return chunks

    @staticmethod
    def _earlier(units: List[aw.Unit], first: aw.Unit, cur: Dict[int, str], needs) -> List[Tuple[str, str]]:
        """The last translated dialogue lines before a chunk (continuity between parts)."""
        if first.kind != "dialogue":
            return []
        prev = [u for u in units if u.kind == "dialogue" and u.start_ms < first.start_ms and u.id != first.id
                and not needs(u, cur)]
        return [(u.text, cur[u.id]) for u in prev[-EARLIER_LINES:]]

    def _chunk(self, lane: int, model: str, folder: Path, title: str, ctx: Dict, units: List[aw.Unit],
               by_id: Dict[int, aw.Unit], chunk: List[aw.Unit], code: str, needs, current, *, part: str,
               first_part: bool, last: bool, tokens: List[int], project: str, episode: str,
               refused: Optional[set] = None, start: Optional[Dict] = None, extras: bool = True,
               around_done: bool = False) -> bool:
        """Translate one chunk, then fix rounds for what came back missing or wrong. ``refused``: the
        episode's set-aside lines (left out here); ``start``: flags for the first request (see ``_ask``);
        ``extras``: ask for the summary/guide; ``around_done``: show the translated lines around each."""
        from utils.glossary_judgment import GLOSSARY_INSTRUCTION, brief_lines
        from utils.line_alignment import repair_reply
        work = folder / aw.WORK_FILE
        fix_rounds = int(self.config["fix_rounds"] or 0)
        problems: Dict[int, str] = {}
        flagged_before: Dict[int, str] = {}
        aside = refused if refused is not None else set()
        for round_no in range(1 + fix_rounds):
            if not self._still_mine(folder, lane, title):
                return False
            cur = current()
            wanted = {u.id for u in chunk if needs(u, cur)} if round_no == 0 else set(problems)
            todo = [u for u in chunk if u.id in wanted and u.id not in aside]
            if not todo:
                return True
            glossary_lines = brief_lines(ctx["matcher"].find_all([u.text for u in todo]))
            around = {u.id: ctx["around"][u.id] for u in todo[:MAX_AROUND] if u.id in ctx["around"]}
            if around_done:
                around = {**self._around_done(units, todo, cur, needs), **around}
            if round_no == 0:
                earlier = self._earlier(units, chunk[0], cur, needs)
                so_far = "" if first_part else self._summary(folder)
                characters = self._characters(project, ctx["glossary"], [u.text for u in todo])
                want_summary = ctx["whole"] and extras
                want_guide = ctx["whole"] and last and not ctx["has_guide"] and extras

                def build(sub: List[aw.Unit], first: bool = True) -> str:
                    ids = {u.id for u in sub}
                    return ap.translate_prompt(
                        ctx["static"], title=title, story=ctx["story"], characters=characters,
                        glossary_lines=glossary_lines, glossary_note=GLOSSARY_INSTRUCTION, earlier=earlier, units=sub,
                        want_summary=want_summary and first, want_guide=want_guide and first, part=part,
                        around={i: v for i, v in around.items() if i in ids}, episode_so_far=so_far)
            else:
                snapshot, asked = dict(cur), dict(problems)

                def build(sub: List[aw.Unit], first: bool = True) -> str:
                    ids = {u.id for u in sub}
                    return ap.fix_prompt(
                        ctx["static"], title=title, glossary_lines=glossary_lines, glossary_note=GLOSSARY_INSTRUCTION,
                        units=sub, current=snapshot, problems={i: p for i, p in asked.items() if i in ids},
                        around={i: v for i, v in around.items() if i in ids})
            doing = "translating" if round_no == 0 else "fixing"
            self._lane_set(lane, state=f"{doing} {len(todo)} line(s){f' ({part})' if part else ''}")
            result = self._ask(lane, model, build, todo, folder, title, project, episode, tokens,
                               refused=refused, start=start)
            if result is None:
                return False
            todo = [u for u in todo if u.id not in aside]  # the request was refused: its lines wait for the end
            if not todo:
                return True
            answers, slip = repair_reply([u.id for u in todo], result["answers"], {u.id: u.text for u in todo},
                                         result["extra"], code)
            if slip:
                self._log_slip(lane, slip)
            if answers:
                work.write_text(aw.write_answers(work.read_text(encoding="utf-8"), answers), encoding="utf-8")
            self._keep_extras(folder, result, ctx)
            label = "translated" if round_no == 0 else "fix round"
            self.log(f"Lane {lane + 1}: {label}{f' ({part})' if part else ''} — {len(answers)}/{len(todo)} line(s) back "
                     f"in {_human(result['seconds'])} ({result['tokens_in']:,} tokens in / {result['tokens_out']:,} out).")
            if not answers and result.get("text"):
                self.log(f"Lane {lane + 1}: the reply had no usable lines: {result['text'][:200]!r}", "warn")
            cur = current()
            problems = {}
            for u in chunk:
                if u.id in aside:
                    continue
                if not (cur.get(u.id) or "").strip():
                    problems[u.id] = "not translated yet"
                elif needs(u, cur):
                    problems[u.id] = "still in English (or not a translation of this line)"
            for m in aw.misplaced_lines(chunk, cur, code):
                if m["id"] in problems or m["id"] in aside or flagged_before.get(m["id"]) == cur.get(m["id"]):
                    continue  # the same answer twice: accept it
                other = by_id.get(m["belongs_to"])
                problems[m["id"]] = ("this looks like the translation of another line (“"
                                     f"{aw._norm(other.text)[:70] if other else '?'}”), not of this one")
            flagged_before = {i: cur.get(i, "") for i in problems}
            if not problems:
                return True
        if problems:
            self.log(f"Lane {lane + 1}: {len(problems)} line(s) still need work after {fix_rounds + 1} round(s) — "
                     f"they stay untranslated for now (the audit finds them later).", "warn")
        return True

    def _log_slip(self, lane: int, slip: Dict) -> None:
        gap = slip["gap"]
        what = f"skipped {gap}" if gap > 0 else f"split or repeated {-gap}"
        if slip["moved"]:
            asked_again = f"; {slip['dropped']} unclear one(s) are asked again" if slip["dropped"] else ""
            self.log(f"Lane {lane + 1}: the reply lost its place at line {slip['from']} (it {what} line(s) but kept "
                     f"counting) — moved {slip['moved']} answer(s) back to their own lines{asked_again}.", "warn")
        else:
            self.log(f"Lane {lane + 1}: the reply seems to have lost its place at line {slip['from']} — "
                     f"{slip['dropped']} answer(s) are asked again.", "warn")

    @staticmethod
    def _summary(folder: Path) -> str:
        try:
            return (folder / aw.SUMMARY_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _keep_extras(self, folder: Path, result: Dict, ctx: Dict) -> None:
        """The reply's episode summary, context guide and new terms go into the workspace files
        (so they survive an interruption and the normal submit picks them up)."""
        if result.get("summary") and ctx["whole"]:
            (folder / aw.SUMMARY_FILE).write_text(result["summary"].strip() + "\n", encoding="utf-8")
        if result.get("guide") and not ctx["has_guide"]:
            project = self.service._read_manifest(folder).get("project_name", "")
            (folder / aw.GUIDE_FILE).write_text(f"# Context Guide: {project}\n\n{result['guide'].strip()}\n",
                                                encoding="utf-8")
        if result.get("terms"):
            path = folder / aw.TERMS_FILE
            try:
                existing = json.loads(path.read_text(encoding="utf-8") or "[]")
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
            seen = {str(t.get("term", "")).lower() for t in existing if isinstance(t, dict)}
            for t in result["terms"]:
                if t["term"].lower() not in seen:
                    seen.add(t["term"].lower())
                    existing.append(t)
            path.write_text(json.dumps(existing, ensure_ascii=False, indent=1), encoding="utf-8")

    def _ask(self, lane: int, model: str, build: Callable[..., str], units: List[aw.Unit], folder: Path, title: str,
             project: str, episode: str, tokens: List[int], refused: Optional[set] = None,
             start: Optional[Dict] = None) -> Optional[Dict]:
        """One round: send the request. When a safety filter refuses it, its lines go into ``refused``
        (the episode's set-aside lines, sent on their own at the end) or, without that, the refusal is
        narrowed down (retry once, set the lines that usually trip it aside, try another model family,
        split). ``start``: flags for the first request (``after_rescue``, ``tried``); a model named
        ``api:<model>`` is called through the Gemini API.
        Returns the merged reply, or None when the work must stop (quota, failure, stop)."""
        exe = self._exe()
        engine = self.config["engine"]
        models = self._models()
        pieces = [{"units": units, "model": model, "tries": 0, "first": True, **(start or {})}]
        if pieces[0].get("after_rescue"):
            pieces[0]["tries"] = 1  # these lines were refused once already: no plain retry
        merged = {"answers": {}, "summary": "", "guide": "", "terms": [], "extra": 0, "seconds": 0.0,
                  "tokens_in": 0, "tokens_out": 0, "text": ""}
        while pieces:
            if self.stop_event.is_set():
                return None
            piece = pieces.pop(0)
            prompt = build(piece["units"], piece["first"])
            self._touch_pid()
            via_api = piece["model"].startswith(API_PREFIX)
            if via_api:
                reply = self._api_call(piece["model"][len(API_PREFIX):], prompt, project, episode)
            else:
                reply = agent_cli.call_model(engine, exe, piece["model"], prompt, folder, self.stop_event,
                                             on_proc=lambda p: self.procs.__setitem__(lane, p))
                self.procs.pop(lane, None)
            with self.lock:
                self.session["calls"] += 1
                self.session["tokens_in"] += reply.tokens_in
                self.session["tokens_out"] += reply.tokens_out
            tokens[0] += reply.tokens_in
            tokens[1] += reply.tokens_out
            if not via_api:  # API calls record their own usage and cost
                self._usage(piece["model"], lane, project, episode, reply)
            if reply.failure == "stopped":
                return None
            if via_api and reply.failure:
                self.log(f"Lane {lane + 1}: the Gemini API did not translate {len(piece['units'])} line(s) "
                         f"({reply.message[:200]}).", "warn")
                reply.failure = "blocked"  # what the API cannot do goes on like a refusal: another model family
            if reply.failure == "blocked":
                kept = self._before_the_cut(reply.text, piece["units"])
                if kept:  # an output filter stopped the answer midway: keep the lines that came through
                    merged["answers"].update(kept)
                    merged["seconds"] += reply.seconds
                    self.log(f"Lane {lane + 1}: the safety filter cut the reply off after {len(kept) + 1} line(s); "
                             f"keeping the first {len(kept)}.", "warn")
                    piece = {**piece, "units": [u for u in piece["units"] if u.id not in kept]}
                if refused is not None:
                    refused.update(u.id for u in piece["units"])
                    self.log(f"Lane {lane + 1}: the safety filter refused {len(piece['units'])} line(s) of {title}; "
                             f"they are set aside and sent on their own when the rest is done.", "warn")
                    continue
                nxt = self._after_block(piece, models, lane)
                if nxt:
                    pieces[:0] = nxt
                else:
                    self.log(f"Lane {lane + 1}: the safety filter refuses line {piece['units'][0].id} of {title}; "
                             f"it stays untranslated for now.", "warn")
                continue
            if reply.failure:
                self._failure(reply, piece["model"], lane, title)
                return None
            with self.lock:
                self.consecutive_errors = 0
            self._recovered(piece["model"])
            ids = {u.id for u in piece["units"]}
            parsed = ap.parse_reply(reply.text, allowed=ids,
                                    two_line={u.id for u in piece["units"] if "\n" in u.text.strip()})
            merged["answers"].update(parsed["answers"])
            merged["extra"] += parsed["extra"]
            merged["summary"] = merged["summary"] or parsed["summary"]
            merged["guide"] = merged["guide"] or parsed["guide"]
            merged["terms"] += parsed["terms"]
            merged["seconds"] += reply.seconds
            merged["tokens_in"] += reply.tokens_in
            merged["tokens_out"] += reply.tokens_out
            merged["text"] = merged["text"] or reply.text
            if reply.tools_used:
                self.log(f"Lane {lane + 1}: the model used tools ({', '.join(map(str, reply.tools_used[:5]))}); its "
                         f"answer is still read from the reply.", "warn")
        return merged

    @staticmethod
    def _before_the_cut(text: str, units: List[aw.Unit]) -> Dict[int, str]:
        """The answers of a reply an output filter stopped midway (the CLI hands over what was written
        until then), without the last one, which the cut may have hit mid-line."""
        if not (text or "").strip():
            return {}
        answers = ap.parse_reply(text, allowed={u.id for u in units},
                                 two_line={u.id for u in units if "\n" in u.text.strip()})["answers"]
        if answers:
            answers.pop(max(answers))
        return answers

    def _after_block(self, piece: Dict, models: List[str], lane: int) -> List[Dict]:
        """A provider's safety filter refused a whole answer because of one or a few lines."""
        units = piece["units"]
        if piece.get("after_rescue") and not piece.get("isolated"):
            alt = self._other_family(piece["model"], exclude=(*piece.get("tried", ()), piece["model"]))
            if alt:
                self.log(f"Lane {lane + 1}: trying {alt} for {len(units)} refused line(s).", "warn")
                return [{**piece, "model": alt, "tried": (*piece.get("tried", ()), piece["model"])}]
            # nowhere else to send them: narrow the refusal down on the usual models, as below
        if piece["tries"] == 0 and len(units) > SMALL_BLOCK:
            self.log(f"Lane {lane + 1}: the safety filter refused {len(units)} lines; retrying once.", "warn")
            return [{**piece, "tries": 1}]
        if len(units) > 1 and not piece.get("isolated"):
            risky = [u for u in units if SENSITIVE.search(aw._norm(u.text))]
            rest = [u for u in units if u not in risky]
            if risky and rest:
                self.log(f"Lane {lane + 1}: setting aside {len(risky)} line(s) that often trip the filter; "
                         f"sending the other {len(rest)} on their own.", "warn")
                return [{"units": rest, "model": piece["model"], "tries": 1, "isolated": True, "first": piece["first"]},
                        {"units": risky, "model": piece["model"], "tries": 1, "isolated": True, "first": False}]
        if len(units) <= SMALL_BLOCK:
            fam = agent_cli.family(piece["model"])
            for alt in models:
                if alt != piece["model"] and agent_cli.family(alt) != fam and self._available(alt) \
                        and alt not in piece.get("tried", ()):
                    self.log(f"Lane {lane + 1}: trying {alt} for {len(units)} refused line(s).", "warn")
                    return [{**piece, "model": alt, "tries": 1, "isolated": True,
                             "tried": (*piece.get("tried", ()), piece["model"])}]
        if len(units) > 1:
            mid = len(units) // 2
            self.log(f"Lane {lane + 1}: splitting {len(units)} refused lines to find the one the filter objects to.",
                     "warn")
            first = models[0] if models else piece["model"]
            tried = tuple(m for m in (*piece.get("tried", ()), piece["model"]) if m != first)
            return [{"units": units[:mid], "model": first, "tries": 1, "isolated": True, "tried": tried,
                     "first": piece["first"]},
                    {"units": units[mid:], "model": first, "tries": 1, "isolated": True, "tried": tried, "first": False}]
        return []

    def _submit(self, lane: int, task: Dict, folder: Path, units: List[aw.Unit], current, needs, started: float,
                tokens: List[int]) -> None:
        """Submit through the normal agent path. Lines that never came back stay untranslated (they
        export as authored and the audit re-queues them); an episode that is mostly untranslated goes
        back to the queue instead."""
        task_id = task["task_id"]
        title = f"{task['project_name']} {task['episode_name']}"
        queue_id = task.get("queue_id") or task_id
        cur = current()
        left = [u for u in units if needs(u, cur)]
        if len(left) > len(units) * 0.5:
            self.service.release_agent_task(task_id)
            with self.lock:
                n = self.failures[queue_id] = self.failures.get(queue_id, 0) + 1
            gave_up = " and left for a person" if n >= MAX_FAILURES else ""
            self.log(f"Lane {lane + 1}: {title} — {len(left)} of {len(units)} lines never came back; put back in the "
                     f"queue{gave_up}.", "error")
            return
        if left:  # clear what is still English, so it stays a gap instead of being saved as a translation
            work = folder / aw.WORK_FILE
            work.write_text(aw.write_answers(work.read_text(encoding="utf-8"), {u.id: "" for u in left}, clear=True),
                            encoding="utf-8")
        with self.submit_lock:
            res = self.service.submit_task_result(task_id, force=bool(left))
        if not res.get("success"):
            self.log(f"Lane {lane + 1}: {title} could not be submitted: {res.get('message') or res.get('status')}",
                     "error")
            if res.get("status") in ("episode_changed", "legacy_workspace"):
                self.service.release_agent_task(task_id)
            return
        done = len(units) - len(left)
        with self.lock:
            self.session["episodes"] += 1
            self.session["lines"] += done
        notes = []
        if left:
            notes.append(f"{len(left)} left for later")
        if res.get("harvested_terms_count"):
            notes.append(f"+{res['harvested_terms_count']} glossary term(s)")
        notes.append("exported" if res.get("exported_path") else "not exported (media folder unreachable)")
        self.log(f"Lane {lane + 1}: submitted {title} — {done}/{len(units)} lines in {_human(time.time() - started)} "
                 f"({tokens[0]:,} tokens in / {tokens[1]:,} out); {', '.join(notes)}.", "ok")
        self._event("agent_runner_submitted", {"task_id": task_id, "project_name": task["project_name"],
                                               "episode_name": task["episode_name"]})  # not a status change

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
                "config": dict(self.config), "defaults": dict(DEFAULT_CONFIG), "models": self._models(),
                "lanes": [{"lane": i + 1, **v} for i, v in sorted(self.lanes.items())],
                "pauses": pauses, "session": dict(self.session), "started": self.started,
                "pending": pending, "throughput": throughput, "eta_minutes": eta,
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


def nudge() -> None:
    """Tell a running runner that a translation was queued (no runner yet: nothing to do)."""
    runner = _runner
    if runner is not None and runner.enabled:
        runner.nudge()


def autostart() -> bool:
    """Resume the runner if it was on when Omnisub last stopped."""
    r = get_runner()
    if r.enabled and not r.running():
        try:
            r.start(resumed=True)
            return True
        except Exception as exc:
            r.log(f"Could not resume: {exc}", "error")
    return False


def stop_all() -> None:
    """Shutdown: stop the CLI processes but leave the runner enabled, so it resumes next time."""
    r = _runner
    if r is None:
        return
    r.stop_event.set()
    for p in list(r.procs.values()):
        agent_cli.kill_process(p)
