"""
Library audit job
=================
``AgentTaskService.audit_library`` reads every episode of every project and, with ``repair``,
writes subtitle files to the media shares - minutes of work - so the API runs it in a thread and
lets clients poll. One run at a time; progress per project; the last finished state is kept in
``library_audit.json`` in the data directory, so a restart or a late page load still shows it.

State (``GET /api/agent/library-audit``)::

    {"status": "idle" | "running" | "done" | "error", "started_at", "finished_at",
     "options": {"repair", "requeue", "misplaced"},
     "progress": {"done", "total", "project"},   # project: the one that finished last
     "result": <audit_library result> | null, "error": str | null}
"""

import json
import logging
import os
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from utils import paths

logger = logging.getLogger("omnisub.library_audit")

STATE_FILE = "library_audit.json"
_lock = threading.Lock()
_state: Dict[str, Any] = {}  # this process's latest run; empty until one starts
_thread: Optional[threading.Thread] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _idle() -> Dict[str, Any]:
    return {"status": "idle", "started_at": None, "finished_at": None,
            "options": {"repair": False, "requeue": False, "misplaced": True},
            "progress": {"done": 0, "total": 0, "project": None}, "result": None, "error": None}


def _state_path():
    return paths.DATA_DIR / STATE_FILE  # read at call time: tests (and relocated data dirs) change it


def _save(state: Dict[str, Any]) -> None:
    path = _state_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as e:
        logger.warning(f"Could not save the library audit result to {path}: {e}")


def _load() -> Optional[Dict[str, Any]]:
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return None
    return state if isinstance(state, dict) and state.get("status") in ("done", "error") else None


def get_state() -> Dict[str, Any]:
    """The run of this process (going or finished), else the last finished one from disk, else idle."""
    with _lock:
        if _state:
            return deepcopy(_state)
    return _load() or _idle()


def is_running() -> bool:
    with _lock:
        return _state.get("status") == "running"


def start(repair: bool = False, requeue: bool = False, misplaced: bool = True) -> Tuple[bool, Dict[str, Any]]:
    """Start a run unless one is going: ``(started, state)`` - the state of the new run, or of the
    one that is still going."""
    global _state, _thread
    with _lock:
        if _state.get("status") == "running":
            return False, deepcopy(_state)
        options = {"repair": bool(repair), "requeue": bool(requeue), "misplaced": bool(misplaced)}
        _state = {**_idle(), "status": "running", "started_at": _now(), "options": options}
        snapshot = deepcopy(_state)
        _thread = threading.Thread(target=_run, args=(options, snapshot["started_at"]), name="library-audit",
                                   daemon=True)
        _thread.start()
    return True, snapshot


def wait(timeout: Optional[float] = None) -> bool:
    """Block until the current run has finished (for callers that have no use for polling)."""
    thread = _thread
    if thread is not None:
        thread.join(timeout)
    return not is_running()


def reset() -> None:
    """Forget this process's run (the saved state on disk stays)."""
    global _state, _thread
    with _lock:
        _state = {}
        _thread = None


def _run(options: Dict[str, bool], started_at: str) -> None:
    from services.agent_task_service import agent_task_service
    from utils import storage

    def update(**changes: Any) -> Optional[Dict[str, Any]]:
        with _lock:
            if _state.get("started_at") != started_at:  # forgotten by reset() meanwhile
                return None
            _state.update(changes)
            return deepcopy(_state)

    def progress(done: int, total: int, project: str) -> None:
        update(progress={"done": done, "total": total, "project": project})

    try:
        update(progress={"done": 0, "total": len(storage.list_projects()), "project": None})
        result = agent_task_service.audit_library(
            requeue=options["requeue"], repair=options["repair"], misplaced=options["misplaced"], progress=progress)
        final = {"status": "done", "result": result, "error": None}
    except Exception as e:
        logger.exception("Library audit failed")
        final = {"status": "error", "result": None, "error": str(e) or e.__class__.__name__}
    snapshot = update(finished_at=_now(), **final)
    if snapshot is not None:
        _save(snapshot)
