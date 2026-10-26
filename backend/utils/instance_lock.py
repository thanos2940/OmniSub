"""
One OmbiSub process per data folder
===================================
Two servers on one data folder each start a translation runner, a worker and the autopilot, and they
claim and submit the same episodes twice (2026-10-27: the boot service on port 2945 and a second
server on port 8000). A PID file cannot prevent that on Windows: a process in another logon session
(the boot service versus one started from the desktop) cannot be opened, so it looked dead and its
lock was taken over as stale.

This lock is an OS byte-range lock (``msvcrt.locking`` on Windows, ``flock`` elsewhere) on a file in the
data folder, held for the life of the process. The operating system releases it when the process
exits, however it exits, so it never goes stale, and a second process cannot take it whoever it runs
as. It is taken in the app's startup (``main.lifespan``), which every way of starting the server goes
through (``run_server.py``, the tray, ``uvicorn`` by hand, the updater's restart).

A small ``<lock>.json`` beside it says who holds it, for the message a refused process logs.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

LOCK_NAME = "ombisub_instance.lock"

_held: Dict[str, int] = {}   # resolved lock path -> file descriptor (kept open: closing it would unlock)


def _info_path(path: Path) -> Path:
    return path.with_name(path.name + ".json")


def _try_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def acquire(data_dir: Path, port: Optional[int] = None) -> bool:
    """Take the data folder's instance lock for this process. True when this process holds it (also when
    it already did); False when another live process does."""
    path = (Path(data_dir) / LOCK_NAME).resolve()
    key = str(path)
    if key in _held:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(key, os.O_RDWR | os.O_CREAT, 0o644)
    if not _try_lock(fd):
        os.close(fd)
        return False
    _held[key] = fd
    try:
        _info_path(path).write_text(json.dumps({
            "pid": os.getpid(), "port": port,
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }), encoding="utf-8")
    except OSError:
        pass
    return True


def holder(data_dir: Path) -> Dict:
    """Who holds the lock, as the holder wrote it ({} when unknown)."""
    try:
        data = json.loads(_info_path((Path(data_dir) / LOCK_NAME).resolve()).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def describe_holder(data_dir: Path) -> str:
    info = holder(data_dir)
    if not info:
        return "another process"
    port = f", port {info['port']}" if info.get("port") else ""
    return f"PID {info.get('pid', '?')}{port}, started {info.get('started', '?')}"


def release(data_dir: Path) -> None:
    """Give the lock up (tests; a process that exits needs no call)."""
    key = str((Path(data_dir) / LOCK_NAME).resolve())
    fd = _held.pop(key, None)
    if fd is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    os.close(fd)
