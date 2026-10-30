"""
Agent CLIs as plain translation engines
=======================================
One request = one prompt piped into Antigravity (``agy``), Claude Code (``claude -p``) or the
Gemini CLI, with every bit of context inline and no tools: the model answers in text and the
caller parses it. That is what makes the direct runner fast — no agent loop re-reading files
and re-sending a growing conversation.

Also here: reading failures (quota, rate limit, sign-in, safety refusals) and, for
Antigravity, its exact quota report (``agy -p /quota``), which costs no quota to ask.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

ENGINES = {"antigravity": "Antigravity", "claude": "Claude Code", "gemini": "Gemini CLI"}
CALL_TIMEOUT = 30 * 60
IDLE_TIMEOUT = 10 * 60   # a CLI that prints nothing for this long is stopped (it streams while it answers)
EXIT_GRACE = 15          # seconds a CLI may take to exit after its final event before it is stopped
CLAUDE_SYSTEM = "You are an expert subtitle translator. Follow the user's instructions exactly. Answer in plain text."


# ------------------------------------------------------------------ finding the CLIs

def _find(name: str, extra: List[Path]) -> Optional[str]:
    for candidate in extra:
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def _locate(engine: str) -> Optional[str]:
    home = Path.home()
    if engine == "antigravity":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return _find("agy", [local / "agy" / "bin" / "agy.exe"])
    if engine == "claude":
        return _find("claude", [home / ".local" / "bin" / "claude.exe"])
    if engine == "gemini":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        return _find("gemini", [appdata / "npm" / "gemini.cmd"])
    return None


def engine_command(engine: str) -> Optional[List[str]]:
    """The command for an engine: ``OMNISUB_<ENGINE>_CMD`` (e.g. a custom install) or the detected CLI."""
    custom = os.environ.get(f"OMNISUB_{engine.upper()}_CMD")
    if custom:  # Windows-style splitting keeps the quotes around a path with spaces: drop them
        return [t[1:-1] if len(t) > 1 and t[0] == t[-1] == '"' else t for t in shlex.split(custom, posix=False)]
    exe = _locate(engine)
    return [exe] if exe else None


def engine_status() -> Dict[str, Optional[str]]:
    return {name: (" ".join(engine_command(name) or []) or None) for name in ENGINES}


def call_dir() -> Path:
    """The folder every CLI request runs in: one empty folder, the same for every request, outside
    OmbiSub's own tree. Claude Code and the Gemini CLI describe their working folder in the context they send
    (the Gemini CLI with its listing) and read CLAUDE.md / GEMINI.md from it and its parents; one folder keeps
    that part identical from request to request, so the provider's prompt cache can reuse it together with a
    show's shared block, and outside the checkout none of OmbiSub's own files reach a translation request. A
    model that reaches for tools finds no files either."""
    folder = Path(tempfile.gettempdir()) / "ombisub-agent-cli"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:   # no usable temp folder for this account: the data folder still keeps it stable
        from utils import paths
        folder = Path(paths.DATA_DIR) / "agy_calls"
        folder.mkdir(parents=True, exist_ok=True)
    return folder


# ------------------------------------------------------------------ reading failures

QUOTA_WORDS = ("quota", "exhausted", "usage limit", "limit reached", "limit will reset", "out of credits",
               "capacity on this model", "resource_exhausted", "credit balance", "credits balance", "ai credits", "billing")
RATE_WORDS = ("rate limit", "rate-limit", "ratelimit", "too many requests", "overloaded", "unavailable",
              "try again later", "temporarily")
AUTH_WORDS = ("not logged in", "please run /login", "/login", "authenticat", "oauth", "unauthorized",
              "not authenticated", "invalid api key", "api key not valid", "unsupported client", "no longer supported")
BLOCK_WORDS = ("content safety", "safety filter", "prohibited use", "blocked by", "harm_category", "harm category",
               "finish_reason: safety", "finishreason.safety", "recitation", "violate google")
# an HTTP status only counts when it is written as one ("code 429", "error_code":401), never inside an id
_HTTP_CODE = re.compile(r'(?:\b(?:http|code|status|error)[ _]?(?:code)?["\']?\s*[:=( ]\s*["\']?|\()(\d{3})\b', re.I)


def classify_failure(text: str) -> str:
    """blocked | auth | quota | rate | error"""
    raw = text or ""
    t = raw.lower()
    if any(w in t for w in BLOCK_WORDS):
        return "blocked"
    for m in re.finditer(r"AGY_ERROR:\s*(\{.*\})", raw):  # Antigravity's structured error is the best signal
        try:
            err = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        status, code = str(err.get("status", "")).upper(), err.get("error_code")
        if status in ("UNAUTHENTICATED", "PERMISSION_DENIED") or code in (401, 403):
            return "auth"
        if status == "RESOURCE_EXHAUSTED" or code == 429:
            return "quota" if any(w in t for w in QUOTA_WORDS) else "rate"
    codes = {int(c) for c in _HTTP_CODE.findall(raw)}
    if any(w in t for w in AUTH_WORDS) or 401 in codes:
        return "auth"
    if any(w in t for w in QUOTA_WORDS):
        return "quota"
    if any(w in t for w in RATE_WORDS) or codes & {429, 503, 529}:
        return "rate"
    return "error"


_DUR = re.compile(r"(?:(\d+)\s*h(?:ours?|rs?)?)?[\s,]*(?:(\d+)\s*m(?:in(?:ute)?s?)?)?[\s,]*(?:(\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?s?)?)?\b", re.I)
_CUE = re.compile(r"(reset|retry|try again|available|wait|in about|after|resets)", re.I)


def parse_wait(text: str, now: Optional[datetime] = None) -> Optional[float]:
    """Seconds until a quota resets, read from an error message; None when it does not say."""
    if not text:
        return None
    now = now or datetime.now()
    m = re.search(r"\|(\d{10})\b", text)                       # Claude: "usage limit reached|1760000000"
    if m:
        return max(0.0, int(m.group(1)) - time.time())
    m = re.search(r"retry[- ]after[\"':\s]+(\d+(?:\.\d+)?)", text, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)", text)
    if m and _CUE.search(text):
        try:
            ts = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            return max(0.0, ts.timestamp() - time.time())
        except ValueError:
            pass
    for cue in _CUE.finditer(text):                             # "reset after 4h3m12s", "retry in 37.5s"
        tail = text[cue.end(): cue.end() + 60]
        for d in _DUR.finditer(tail):
            h, mi, s = d.groups()
            if h or mi or s:
                return int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)
    m = re.search(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text, re.I)  # "resets 5pm"
    if m:
        hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        if hour < 24:
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return (target - now).total_seconds()
    return None


# ------------------------------------------------------------------ Antigravity's quota report

FAMILIES = ("gemini", "claude", "gpt")


def family(model: str) -> str:
    m = (model or "").lower()
    return next((f for f in FAMILIES if m.startswith(f)), m)


def antigravity_quota(exe: List[str]) -> Optional[List[Dict]]:
    """Antigravity's quota groups — ``agy -p /quota --output-format json`` — without spending quota.
    Each group: {name, description, buckets: [{window, remaining_fraction, reset_time}]}.
    None: no report this time; []: this CLI version has no /quota (asking again would spend quota)."""
    try:
        proc = subprocess.run([*exe, "-p", "/quota", "--output-format", "json"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60, stdin=subprocess.DEVNULL, creationflags=_SUBPROCESS_FLAGS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("{"):
            try:
                data = json.loads(line)
                if "command" not in data and ("conversation_id" in data or "response" in data):
                    return []  # this agy has no /quota command: it ran it as a prompt (a spent request)
                return ((data.get("command") or {}).get("data") or {}).get("groups") or None
            except json.JSONDecodeError:
                return None
    return None


def _tokens(text: str) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t}


def group_of(model: str, groups: Optional[List[Dict]]) -> Optional[Dict]:
    """The quota group a model belongs to ('Gemini Models', 'Claude and GPT models', ...).

    A family can have more than one group ('Gemini Models' and 'Gemini 4 Argon'): the group whose name
    shares the most of the model's own tokens (a version, a code name) wins; with none shared, the most
    generic group of the family (the fewest extra words) — never simply the first one listed."""
    fam = family(model)
    if fam not in FAMILIES:
        return None
    model_tokens = _tokens(model) - {fam}
    best, best_key = None, None
    for g in groups or []:
        label = f"{g.get('name', '')} {g.get('description', '')}".lower()
        if fam not in label:
            continue
        name_tokens = _tokens(g.get("name", "")) - {fam, "models", "model", "and"}
        shared = len(name_tokens & model_tokens)
        key = (shared, -len(name_tokens))
        if best_key is None or key > best_key:
            best, best_key = g, key
    return best


def exhausted_until(group: Optional[Dict], limit: float = 0.001) -> Optional[float]:
    """When a used-up quota group is usable again (the latest reset among its buckets at or below
    ``limit``), else None. After a real quota error a larger ``limit`` (10%) is used: the provider
    rounds, and the nearly empty bucket is the one that stopped the request."""
    if not group:
        return None
    resets = []
    for b in group.get("buckets") or []:
        if (b.get("remaining_fraction") or 0) <= limit and b.get("reset_time"):
            try:
                resets.append(datetime.fromisoformat(b["reset_time"].replace("Z", "+00:00")).timestamp())
            except ValueError:
                continue
    return max(resets) if resets else None


# ------------------------------------------------------------------ one call

@dataclass
class Reply:
    text: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    seconds: float = 0.0
    failure: str = ""        # "", quota, rate, auth, blocked, error, stopped
    message: str = ""
    wait: Optional[float] = None
    tools_used: List = field(default_factory=list)


def build_command(engine: str, exe: List[str], model: str, prompt: str) -> Tuple[List[str], str, Dict]:
    env = dict(os.environ)
    if engine == "antigravity":
        cmd = [*exe, "--input-format", "stream-json", "--output-format", "stream-json", "--disable-slash-commands",
               "--print-timeout", f"{CALL_TIMEOUT // 60}m"]
        if model:
            cmd += ["--model", model]
        stdin = json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n"
    elif engine == "claude":
        cmd = [*exe, "-p", "--output-format", "stream-json", "--verbose", "--tools", "", "--system-prompt", CLAUDE_SYSTEM,
               "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
        if model:
            cmd += ["--model", model]
        stdin = prompt
    else:
        cmd = [*exe, "-p", "Answer the request above.", "-o", "stream-json"]
        if model:
            cmd += ["-m", model]
        env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
        stdin = prompt
    return cmd, stdin, env


def kill_process(proc: subprocess.Popen) -> None:
    """Stop a CLI and everything it started (a launcher script's node or language server holds the
    output pipe open, so killing only the launcher would leave the read waiting)."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            done = subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=20,
                                  creationflags=_SUBPROCESS_FLAGS)
            if done.returncode == 0:
                return
        elif os.getpgid(proc.pid) == proc.pid:   # started with its own process group (call_model)
            os.killpg(proc.pid, signal.SIGKILL)
            return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def call_model(engine: str, exe: List[str], model: str, prompt: str, cwd: Path, stop: threading.Event,
               on_proc: Callable[[subprocess.Popen], None] = lambda p: None) -> Reply:
    """Send one prompt and read the answer from the CLI's JSON event stream."""
    cmd, stdin, env = build_command(engine, exe, model, prompt)
    started = time.time()
    try:
        # Its own process group on POSIX, so kill_process can end the helpers it starts (Windows: taskkill /T).
        proc = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", creationflags=_SUBPROCESS_FLAGS, env=env,
                                start_new_session=os.name != "nt")
    except OSError as exc:
        return Reply(failure="error", message=f"could not start {ENGINES.get(engine, engine)}: {exc}")
    on_proc(proc)
    reply = Reply()
    deltas: List[str] = []
    raw: List[str] = []
    status_error = ""
    # The answer is complete once the CLI prints its final event; the process exiting is not waited for
    # beyond EXIT_GRACE (agy has been seen to finish an answer and then never exit, which held a lane
    # until the hard timeout). A watchdog stops a CLI that goes silent or runs past the hard timeout.
    finished = False
    lingered = False
    watch = {"last": time.time(), "killed": ""}
    reading = threading.Event()

    def watchdog() -> None:
        tick = max(0.2, min(5.0, IDLE_TIMEOUT / 4))
        while not reading.wait(tick):
            now = time.time()
            if now - started > CALL_TIMEOUT + 60:
                watch["killed"] = "timeout"
            elif now - watch["last"] > IDLE_TIMEOUT:
                watch["killed"] = "idle"
            else:
                continue
            kill_process(proc)
            return

    threading.Thread(target=watchdog, daemon=True, name="cli-watchdog").start()
    try:
        try:
            proc.stdin.write(stdin)
            proc.stdin.close()
        except OSError:
            pass
        for line in proc.stdout:
            watch["last"] = time.time()
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                raw.append(line[:500])
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("event") == "step_update":                      # Antigravity
                su = ev.get("step_update") or {}
                if su.get("step_type") == "agent_response" and su.get("text_delta"):
                    deltas.append(su["text_delta"])
                if su.get("step_type") == "tool" and su.get("state") == "ACTIVE":
                    reply.tools_used.append(su.get("tool_name"))
                if su.get("state") == "ERROR":
                    raw.append(json.dumps(su)[:500])
            elif ev.get("event") == "result":
                r = ev.get("result") or {}
                reply.text = r.get("response") or "".join(deltas)
                u = r.get("usage") or {}
                reply.tokens_in += u.get("input_tokens") or 0
                reply.tokens_out += (u.get("output_tokens") or 0) + (u.get("thinking_tokens") or 0)
                if str(r.get("status", "")).upper() not in ("SUCCESS", ""):
                    status_error = str(r.get("error") or r.get("status"))
                finished = True
                break
            elif ev.get("event") == "error":
                status_error = json.dumps(ev)[:800]
            elif ev.get("type") == "assistant":                        # Claude Code
                for c in (ev.get("message") or {}).get("content", []):
                    if c.get("type") == "text":
                        deltas.append(c.get("text", ""))
            elif ev.get("type") == "result":
                if isinstance(ev.get("result"), str):
                    reply.text = ev["result"]
                elif not reply.text:
                    reply.text = "".join(deltas)
                u = ev.get("usage") or {}
                if isinstance(u, dict):
                    reply.tokens_in += (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) \
                        + (u.get("cache_creation_input_tokens") or 0)
                    reply.tokens_out += u.get("output_tokens") or 0
                if ev.get("is_error") or ev.get("status") == "error":
                    status_error = str(ev.get("result") or ev.get("error") or "error")
                finished = True
                break
            elif ev.get("type") == "message" and ev.get("role") == "assistant":   # Gemini CLI
                deltas.append(ev.get("content") or "")
            elif ev.get("type") == "error":
                status_error = json.dumps(ev)[:800]
    finally:
        try:
            proc.wait(timeout=EXIT_GRACE)
        except subprocess.TimeoutExpired:
            lingered = True
            kill_process(proc)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
        reading.set()
    if not reply.text:
        reply.text = "".join(deltas)
    reply.seconds = time.time() - started
    returncode = proc.returncode
    if finished and lingered:
        returncode = 0  # the answer was complete; the exit code is only that of our kill
    if stop.is_set() and (returncode != 0 or not reply.text.strip()):
        reply.failure = "stopped"  # cut off; an answer that did arrive is kept and used
        return reply
    if watch["killed"] and not finished:
        why = (f"no output for {IDLE_TIMEOUT // 60} min" if watch["killed"] == "idle"
               else f"still running after {CALL_TIMEOUT // 60} min")
        if reply.text.strip() and not status_error:
            # What was streamed is kept: the runner checks every line and asks again for what is missing.
            reply.message = f"{ENGINES.get(engine, engine)} stopped ({why}); the answer it had streamed is used."
            return reply
        reply.failure = "error"
        reply.message = f"{ENGINES.get(engine, engine)} stopped: {why}."
        return reply
    blob = " ".join([status_error, *raw[-20:]])
    if returncode != 0 or status_error:
        reply.failure = classify_failure(blob)
        reply.message = (status_error or " ".join(raw[-5:]) or f"exit code {returncode}")[:600]
        reply.wait = parse_wait(blob)
    return reply
