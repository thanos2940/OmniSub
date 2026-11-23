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
    """Public. What the web app shows first: "create your login" (no credentials yet), the login form, or the
    app. Login is always required; ``local_bypass`` says whether local addresses are let in without it."""
    from utils.auth import credentials_set, request_authorized
    config = storage.load_global_config()
    client_host = request.client.host if request.client else ""
    return {
        "auth_enabled": True,
        "credentials_set": credentials_set(config),
        "authenticated": credentials_set(config) and request_authorized(config, request.headers, client_host),
        "username": config.get("auth_username") or "" if credentials_set(config) else "",
        "local_bypass": bool(config.get("auth_local_bypass")),
    }


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    """Public. Exchanges username/password for the API key the frontend then
    attaches to every subsequent request as X-Api-Key."""
    ip = _client_ip(request)
    _check_lockout(ip)

    config = storage.load_global_config()
    if not config.get("auth_username") or not config.get("auth_password_hash"):
        raise HTTPException(status_code=400, detail="No login exists yet: create it first.")

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
    """Set or change the username/password. Reachable without a key only while no credentials exist (the first
    login, created from the web app); after that the API-key gate covers it like any other endpoint."""
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
    """Login can no longer be switched off. Kept so an older web app gets a clear answer."""
    raise HTTPException(status_code=400, detail="Login is always required. Settings › Security can let devices on "
                                                "your own network in without it.")


class LocalBypassRequest(BaseModel):
    enabled: bool


@router.post("/local-bypass")
async def set_local_bypass(body: LocalBypassRequest):
    """Let devices that connect directly from this machine or the local network in without a login (requests
    through a reverse proxy still need it). Gated by the API key like any other endpoint."""
    storage.save_global_config({"auth_local_bypass": bool(body.enabled)})
    logger.warning("Login for local addresses %s", "no longer required" if body.enabled else "required again")
    return {"status": "success", "local_bypass": bool(body.enabled)}
