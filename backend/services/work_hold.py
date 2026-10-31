"""
Hold on new work
================
While an update waits for a gap between episodes, or is being installed, nothing starts a new piece of
work: the agy runner's lanes take no new episode and resume none of their half-done ones, and the Gemini
worker claims no translation, extraction or export retry. Work already in hand goes on to its end.

The hold lives in memory only. The restart that installs the update ends it, and an update that fails or
is cancelled lifts it. Unlike "Pause translations" (``queue_worker_paused``) it is never saved and never
stands in for the user's own switch. Each holder gets a token, so one holder letting go (an install
attempt that failed) does not lift another's hold (the update still waiting for its gap).
"""

from __future__ import annotations

import itertools
import threading
import time
from typing import Dict, Optional, Tuple

_lock = threading.Lock()
_holds: Dict[int, Tuple[str, float]] = {}   # token -> (why, since)
_tokens = itertools.count(1)


def hold(reason: str) -> int:
    """Take no new work until ``release(token)``. Returns the token."""
    with _lock:
        token = next(_tokens)
        _holds[token] = (reason, time.time())
    return token


def release(token: Optional[int]) -> None:
    """Let go of one hold. When it was the last, the runner and the worker look at the queue right away."""
    with _lock:
        if _holds.pop(token, None) is None or _holds:
            return
    _wake()


def reason() -> Optional[str]:
    """Why new work is held (the newest holder's words), or None when it is not."""
    with _lock:
        return _holds[max(_holds)][0] if _holds else None


def since() -> Optional[float]:
    """When the oldest hold in force began."""
    with _lock:
        return min(t for _, t in _holds.values()) if _holds else None


def _wake() -> None:
    try:
        from services.agent_runner import current_runner
        runner = current_runner()
        if runner is not None:
            runner.nudge()
    except Exception:
        pass
    try:
        from services.background_worker import worker
        worker.trigger()
    except Exception:
        pass
