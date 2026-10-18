"""Optional Plex client: find the library section of a folder and ask Plex to scan it.

Nothing here raises to callers; failures are logged and reported as ``False`` / ``None`` / ``[]``.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


_FILE_SUFFIXES = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".webm",
                  ".srt", ".ass", ".ssa", ".sub", ".vtt"}


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(str(p))).replace("\\", "/").rstrip("/")


class PlexClient:
    def __init__(self, url: str, token: str, timeout: float = 10.0, transport: Optional[httpx.BaseTransport] = None):
        self.url = (url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout
        self._transport = transport
        self.last_error: Optional[str] = None

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[httpx.Response]:
        headers = {"Accept": "application/json", "X-Plex-Token": self.token}
        try:
            with httpx.Client(base_url=self.url, timeout=self.timeout, transport=self._transport) as client:
                resp = client.get(path, params=params, headers=headers)
            if resp.status_code >= 400:
                self.last_error = f"Plex answered HTTP {resp.status_code}"
                logger.warning("Plex GET %s failed: HTTP %s", path, resp.status_code)
                return None
            self.last_error = None
            return resp
        except Exception as e:
            self.last_error = str(e)
            logger.warning("Plex GET %s failed: %s", path, e)
            return None

    def sections(self) -> List[dict]:
        """Library sections: ``[{"key", "title", "type", "locations": [path, ...]}]``."""
        resp = self._get("/library/sections")
        if resp is None:
            return []
        try:
            directories = (resp.json().get("MediaContainer") or {}).get("Directory") or []
            out = []
            for d in directories:
                locations = [loc.get("path") for loc in (d.get("Location") or []) if loc.get("path")]
                out.append({"key": str(d.get("key")), "title": d.get("title", ""),
                            "type": d.get("type", ""), "locations": locations})
            return out
        except Exception as e:
            self.last_error = f"Unexpected answer from Plex: {e}"
            logger.warning("Plex sections could not be parsed: %s", e)
            return []

    def section_for(self, path) -> Optional[dict]:
        """The section with the longest location that contains ``path``."""
        target = _norm(path)
        best, best_len = None, -1
        for section in self.sections():
            for loc in section.get("locations", []):
                base = _norm(loc)
                if target == base or target.startswith(base + "/"):
                    if len(base) > best_len:
                        best, best_len = section, len(base)
        return best

    def refresh_path(self, path) -> bool:
        """Ask Plex to scan the folder of ``path`` (a file path or a folder)."""
        try:
            p = Path(str(path))
            is_file = p.suffix.lower() in _FILE_SUFFIXES and not p.is_dir()
            folder = str(p.parent if is_file else p)
            self.last_error = None
            section = self.section_for(folder)
            if not section:
                if not self.last_error:
                    self.last_error = f"No Plex library contains '{folder}'"
                logger.info("Plex refresh skipped for '%s': %s", folder, self.last_error)
                return False
            resp = self._get(f"/library/sections/{section['key']}/refresh", params={"path": folder})
            if resp is None:
                return False
            logger.info("Asked Plex to scan '%s' (library '%s').", folder, section.get("title"))
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.warning("Plex refresh failed: %s", e)
            return False


def plex_from_config(cfg: Optional[Dict]) -> Optional[PlexClient]:
    """A client when ``plex_refresh_enabled`` and a token are set, else None."""
    cfg = cfg or {}
    token = (cfg.get("plex_token") or "").strip()
    if not cfg.get("plex_refresh_enabled", False) or not token:
        return None
    return PlexClient((cfg.get("plex_url") or "http://localhost:32400").strip(), token)
