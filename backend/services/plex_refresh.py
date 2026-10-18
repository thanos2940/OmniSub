"""Tell Plex about every subtitle OmbiSub writes, without ever slowing an export down.

``request_refresh(path)`` is called wherever a subtitle lands next to a video (the export
formatter, the export-retry lane, the Autopilot). Paths are collected for a short window and
then handed to Plex in one pass on a background thread: one scan per library section (a
partial scan of the folder when only one folder of the section changed, the common parent
when several did, the whole section when the parent is the section root). A season of
episodes finishing within the window costs Plex one scan, and a Plex that is down costs
nothing but a log line.

Nothing here raises to callers.
"""

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from utils import storage

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 30.0


class PlexRefresher:
    def __init__(self, delay: float = DEBOUNCE_SECONDS,
                 config_provider: Optional[Callable[[], Dict]] = None,
                 client_factory: Optional[Callable[[Dict], Any]] = None):
        """``delay`` <= 0 refreshes inside ``request`` (tests); otherwise on a timer thread."""
        self.delay = delay
        self._config_provider = config_provider or storage.load_global_config
        self._client_factory = client_factory
        self._lock = threading.Lock()
        self._pending: Dict[str, None] = {}   # an ordered set of paths
        self._timer: Optional[threading.Timer] = None
        self._state: Dict[str, Any] = {"ok": None, "error": None}

    @property
    def state(self) -> Dict[str, Any]:
        """Outcome of the last refresh: ``{"ok": True|False|None, "error": str|None}``."""
        return dict(self._state)

    def _client(self):
        cfg = self._config_provider() or {}
        if self._client_factory is not None:
            return self._client_factory(cfg)
        from services.autopilot.plex import plex_from_config
        return plex_from_config(cfg)

    def request(self, path) -> bool:
        """Queue ``path`` (a subtitle file or its folder) for the next refresh. True when queued."""
        try:
            if not path or self._client() is None:
                return False
            with self._lock:
                self._pending[str(path)] = None
                if self.delay > 0 and self._timer is None:
                    self._timer = threading.Timer(self.delay, self.flush)
                    self._timer.daemon = True
                    self._timer.start()
                    return True
            if self.delay <= 0:
                self.flush()
            return True
        except Exception as e:
            logger.warning(f"Could not queue a Plex refresh for '{path}': {e}")
            return False

    def flush(self) -> None:
        """Refresh Plex for everything collected so far (blocking)."""
        with self._lock:
            paths: List[str] = list(self._pending)
            self._pending.clear()
            timer, self._timer = self._timer, None
        if timer is not None and timer is not threading.current_thread():
            timer.cancel()
        if not paths:
            return
        try:
            client = self._client()
            if client is None:
                return
            ok = bool(client.refresh_paths(paths))
            self._state = {"ok": ok, "error": None if ok else getattr(client, "last_error", None)}
        except Exception as e:
            self._state = {"ok": False, "error": str(e)}
            logger.warning(f"Plex refresh failed: {e}")


_refresher: Optional[PlexRefresher] = None


def get_refresher() -> PlexRefresher:
    global _refresher
    if _refresher is None:
        _refresher = PlexRefresher()
    return _refresher


def set_refresher(refresher: Optional[PlexRefresher]) -> None:
    """Install the process's refresher (tests); ``None`` makes the next ``get_refresher`` build a new one."""
    global _refresher
    _refresher = refresher


def request_refresh(path) -> bool:
    """Ask for a Plex scan of the folder of ``path``; debounced, non-blocking, never raises."""
    return get_refresher().request(path)
