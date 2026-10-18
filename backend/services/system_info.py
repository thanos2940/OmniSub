"""What the System page shows: runtime status and the tail of OmbiSub's log files."""
import os
import platform
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils import storage
from utils.paths import DATA_DIR
from utils.url_base import safe_url_base

# The only files the log viewer reads: a name from this table, under <data dir>/logs. Never a path from the client.
LOG_FILES = {
    "ombisub": "ombisub.log",
    "agent_runner": "agent_runner.log",
    "tray_app": "tray_app.log",
    "service_console": "service_console.log",
}
DEFAULT_LINES = 500
MAX_LINES = 5000
_MAX_TAIL_BYTES = 8 * 1024 * 1024  # a log without line breaks must not be read whole
_STARTED = time.time()


def logs_dir() -> Path:
    return DATA_DIR / "logs"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def tail_lines(path: Path, count: int) -> List[str]:
    """The last ``count`` lines of a text file, read backwards from the end (UTF-8, bad bytes replaced)."""
    count = max(1, count)
    block = 64 * 1024
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        data = b""
        while pos > 0 and data.count(b"\n") <= count and len(data) < _MAX_TAIL_BYTES:
            step = min(block, pos)
            pos -= step
            fh.seek(pos)
            data = fh.read(step) + data
    lines = data.decode("utf-8", errors="replace").splitlines()
    if pos > 0 and lines:
        lines = lines[1:]  # the first line was cut in the middle
    return lines[-count:]


def list_logs() -> List[Dict[str, Any]]:
    out = []
    for name, filename in LOG_FILES.items():
        path = logs_dir() / filename
        if path.is_file():
            st = path.stat()
            out.append({"name": name, "file": filename, "size": st.st_size, "modified": _iso(st.st_mtime)})
    return out


def read_log(name: str, lines: int = DEFAULT_LINES) -> Dict[str, Any]:
    """{name, path, lines, size, modified} for a whitelisted log. KeyError: unknown name. FileNotFoundError: not written yet."""
    filename = LOG_FILES[name]
    path = logs_dir() / filename
    st = path.stat()  # FileNotFoundError when the file does not exist
    wanted = max(1, min(int(lines), MAX_LINES))
    return {"name": name, "path": str(path), "lines": tail_lines(path, wanted), "size": st.st_size,
            "modified": _iso(st.st_mtime)}


def _disk(path: Path) -> Dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
        return {"free_bytes": usage.free, "total_bytes": usage.total}
    except OSError:
        return {"free_bytes": None, "total_bytes": None}


def _runner_summary() -> Dict[str, Any]:
    """The agent runner in a few cheap fields (no quota poll, no log)."""
    try:
        from services import agent_runner
        runner = agent_runner.get_runner()
        return {"enabled": runner.enabled, "running": runner.running(), "status": runner.status,
                "message": runner.message, "pending": runner._pending_count(), "engines": runner.engines()}
    except Exception as exc:  # the status page must not fail because of the runner
        return {"error": str(exc)}


def collect_status(app_version: str = "", active_url_base: Optional[str] = None) -> Dict[str, Any]:
    from services.update_service import update_service
    config = storage.load_global_config()
    version = update_service.get_current_version_info()
    url_base = safe_url_base(config.get("url_base"))
    try:
        from integrations.embedded_subs import resolve_tools
        tools = resolve_tools(config)
    except Exception:
        tools = None
    try:
        import psutil
        started = psutil.Process().create_time()
    except Exception:
        started = _STARTED
    return {
        "app_version": app_version,
        "commit": version.get("commit_hash", ""),
        "commit_short": version.get("commit_short", ""),
        "branch": version.get("branch", ""),
        "commit_message": version.get("message", ""),
        "started_at": _iso(started),
        "uptime_seconds": int(max(0, time.time() - started)),
        "port": os.environ.get("OMBISUB_PORT") or None,
        "url_base": url_base,
        "url_base_active": active_url_base if active_url_base is not None else url_base,
        "data_dir": str(DATA_DIR),
        "supervisor": os.environ.get("OMBISUB_SUPERVISOR") or "none",
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "platform": platform.platform(),
        "ffmpeg": tools.ffmpeg if tools else None,
        "ffprobe": tools.ffprobe if tools else None,
        "agent_clis": {"agy": shutil.which("agy"), "claude": shutil.which("claude")},
        "agent_runner": _runner_summary(),
        "disk": _disk(DATA_DIR),
        "logs": list_logs(),
    }
