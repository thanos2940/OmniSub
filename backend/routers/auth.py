"""Single-user authentication endpoints (Sonarr-style: username/password + API key).

See docs/PLAN_auth_security.md. Not gated by SettingsRequest — auth fields live
directly in config.json and are only ever written here, never through the generic
POST /settings endpoint.
"""
import logging
import time
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from utils import storage
from utils.auth import hash_password, verify_password, generate_api_key

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger("omnisub.auth")

_MAX_FAILURES = 5
_LOCKOUT_SECONDS = 60
# ip -> [consecutive_failures, locked_until_monotonic]
_login_failures: Dict[str, list] = {}


class LoginRequest(BaseModel):
    username: str
    password: str


class CredentialsRequest(BaseModel):
    username: str
    password: str
    regenerate_api_key: bool = False


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_lockout(ip: str) -> None:
    entry = _login_failures.get(ip)
    if entry and entry[1] > time.monotonic():
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts. Try again in a minute.",
        )


def _record_failure(ip: str) -> None:
    entry = _login_failures.setdefault(ip, [0, 0.0])
    entry[0] += 1
    if entry[0] >= _MAX_FAILURES:
        entry[1] = time.monotonic() + _LOCKOUT_SECONDS
        entry[0] = 0


def _record_success(ip: str) -> None:
    _login_failures.pop(ip, None)


def _request_api_key(request: Request) -> Optional[str]:
    return request.headers.get("X-Api-Key")


@router.get("/status")
async def auth_status(request: Request):
    """Public. Tells the frontend whether to show the login screen, and whether the
    caller (if it already sent a key) is currently authenticated."""
    config = storage.load_global_config()
    auth_enabled = bool(config.get("auth_enabled"))
    authenticated = False
    if auth_enabled:
        supplied = _request_api_key(request)
        stored = config.get("api_key") or ""
        authenticated = bool(supplied) and bool(stored) and supplied == stored
    else:
        authenticated = True  # nothing to authenticate against
    return {"auth_enabled": auth_enabled, "authenticated": authenticated}


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    """Public. Exchanges username/password for the API key the frontend then
    attaches to every subsequent request as X-Api-Key."""
    ip = _client_ip(request)
    _check_lockout(ip)

    config = storage.load_global_config()
    if not config.get("auth_enabled") or not config.get("auth_username"):
        raise HTTPException(status_code=400, detail="Authentication is not configured on this server.")

    stored_hash = config.get("auth_password_hash") or ""
    username_ok = body.username == config.get("auth_username")
    password_ok = bool(stored_hash) and verify_password(body.password, stored_hash)

    if not (username_ok and password_ok):
        _record_failure(ip)
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    _record_success(ip)
    api_key = config.get("api_key")
    if not api_key:
        # Should not happen (main.py generates one at startup) — self-heal.
        api_key = generate_api_key()
        storage.save_global_config({"api_key": api_key})
    return {"api_key": api_key}


@router.post("/credentials")
async def set_credentials(body: CredentialsRequest, request: Request):
    """Set or change the username/password. Reachable without a key only while
    auth is not yet enabled (first-run bootstrap) — once auth_enabled is true,
    the standard X-Api-Key middleware gates this endpoint like any other."""
    if len(body.password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters.")
    if not body.username.strip():
        raise HTTPException(status_code=422, detail="Username is required.")

    config = storage.load_global_config()
    update = {
        "auth_enabled": True,
        "auth_username": body.username.strip(),
        "auth_password_hash": hash_password(body.password),
    }
    if body.regenerate_api_key or not config.get("api_key"):
        update["api_key"] = generate_api_key()
    storage.save_global_config(update)

    logger.info("Auth credentials updated for user %r", update["auth_username"])
    return {"status": "success", "api_key": update.get("api_key") or config.get("api_key")}


@router.post("/disable")
async def disable_auth():
    """Turn authentication off. Gated by the middleware like any other endpoint
    once auth is enabled, so this requires already being authenticated."""
    storage.save_global_config({"auth_enabled": False})
    logger.warning("Authentication disabled via /api/auth/disable")
    return {"status": "success"}
