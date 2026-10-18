"""URL base: serving OmbiSub under a path of a reverse proxy (Caddy ``example.com/ombisub``).

``url_base`` is "" (the root) or "/segment[/segment]" without a trailing slash. The backend accepts
requests with the prefix (Caddy ``handle``, prefix kept) and without it (``handle_path``, prefix stripped),
and tells the web app where it lives through ``<base href>`` and ``window.__OMBISUB_BASE__``.
"""
import re
from typing import Optional

# One path segment: letters, digits and . _ ~ - only. Nothing that could close an HTML attribute or script.
_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]+$")


def normalize_url_base(value: Optional[str]) -> str:
    """"" or "/a/b" for any sensible input ("a/b/", " /a//b ", "/"). Raises ValueError for a bad segment."""
    segments = [seg for seg in str(value or "").strip().split("/") if seg]
    for seg in segments:
        if seg in (".", "..") or not _SEGMENT.match(seg):
            raise ValueError(
                "url_base must look like /ombisub: letters, digits, '.', '_', '~' and '-' in each path segment."
            )
    return "".join("/" + seg for seg in segments)


def safe_url_base(value: Optional[str]) -> str:
    """normalize_url_base, but an invalid stored value means "no prefix" (never raises)."""
    try:
        return normalize_url_base(value)
    except ValueError:
        return ""


class UrlBaseMiddleware:
    """Pure ASGI middleware: strips the configured prefix from the request path.

    ``root_path`` is deliberately NOT set: Starlette strips it from the path a second time when routing,
    so "/ombisub/ombisub/api/settings" would reach "/api/settings" while the API-key gate (which looks at
    the once-stripped path "/ombisub/api/settings") considered it public.

    Requests that do not start with the prefix pass through unchanged, so the app also answers at the
    root (a proxy that already stripped the prefix, direct access by IP:port).
    """

    def __init__(self, app, url_base: str = ""):
        self.app = app
        self.url_base = safe_url_base(url_base)

    async def __call__(self, scope, receive, send):
        base = self.url_base
        if base and scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == base or path.startswith(base + "/"):
                scope = dict(scope)
                scope["path"] = path[len(base):] or "/"
                raw_path = scope.get("raw_path")
                if raw_path:
                    prefix = base.encode("latin-1")
                    if raw_path == prefix or raw_path.startswith(prefix + b"/") or raw_path.startswith(prefix + b"?"):
                        scope["raw_path"] = raw_path[len(prefix):] or b"/"
        await self.app(scope, receive, send)
