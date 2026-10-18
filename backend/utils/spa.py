"""Serving the built web app (``backend/static``) from the API process.

``install_spa(app, static_dir, url_base)`` mounts ``/assets``, answers browser loads of the app's pages with
``index.html`` and hands every other non-API GET to the static files / ``index.html`` (so React Router's
client-side routes survive a hard refresh). ``index.html`` gets ``<base href>`` and
``window.__OMBISUB_BASE__`` so the app works under a reverse-proxy path (see utils/url_base.py).
"""
import html
import json
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from utils.url_base import safe_url_base

# The web app's pages (App.jsx routes). A browser loading one directly (address bar, refresh) gets the app
# even where an API alias shares the path (GET /queue) or the API-key gate covers it (/settings); API
# clients ask for JSON and still reach the API.
SPA_PAGES = {"project", "settings", "library", "dashboard", "health", "autopilot", "review", "queue",
             "system", "activity"}

_HEAD = re.compile(r"<head[^>]*>", re.IGNORECASE)


def inject_base(index_html: str, url_base: str) -> str:
    """index.html with ``<base href="{url_base}/">`` and ``window.__OMBISUB_BASE__`` right after ``<head>``.

    ``url_base`` is re-validated here (an invalid value counts as "no prefix") and escaped for both the
    attribute and the script, so a configured value can never inject markup.
    """
    base = safe_url_base(url_base)
    as_js = json.dumps(base).replace("</", "<\\/")
    tags = (
        f'<base href="{html.escape(base + "/", quote=True)}">'
        f"<script>window.__OMBISUB_BASE__ = {as_js};</script>"
    )
    match = _HEAD.search(index_html)
    if not match:
        return tags + index_html
    return index_html[:match.end()] + tags + index_html[match.end():]


class SpaIndex:
    """index.html with the base injected, re-read when the file changes (an update swaps the build)."""

    def __init__(self, static_dir: Path, url_base: str):
        self.path = Path(static_dir) / "index.html"
        self.url_base = safe_url_base(url_base)
        self._mtime: Optional[float] = None
        self._html = ""

    def render(self) -> str:
        mtime = self.path.stat().st_mtime
        if mtime != self._mtime:
            self._html = inject_base(self.path.read_text(encoding="utf-8"), self.url_base)
            self._mtime = mtime
        return self._html

    def response(self) -> HTMLResponse:
        return HTMLResponse(self.render(), headers={"Cache-Control": "no-cache"})


def install_spa(app: FastAPI, static_dir: Path, url_base: str = "") -> SpaIndex:
    static_dir = Path(static_dir)
    index = SpaIndex(static_dir, url_base)

    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="spa-assets")

    @app.middleware("http")
    async def spa_pages_for_browsers(request: Request, call_next):
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            if request.url.path.strip("/").split("/", 1)[0] in SPA_PAGES:
                return index.response()
        return await call_next(request)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (static_dir / full_path).resolve()
        # Serve a real static file (favicon, manifest, etc.) if the path matches one that exists directly
        # under static/; otherwise fall through to index.html so React Router owns the route client-side.
        if full_path and candidate.is_file() and candidate.is_relative_to(static_dir.resolve()):
            if candidate.name != "index.html":
                return FileResponse(candidate)
        return index.response()

    return index
