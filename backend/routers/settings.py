import os
import asyncio
import base64
import logging
from pathlib import Path
from typing import Any, Optional, Dict

from fastapi import APIRouter, HTTPException, Request

from utils import storage
from utils.rate_limiter import translation_rate_limiter, per_model_rate_limiter
from routers.schemas import SettingsRequest, ApiKeyRequest

router = APIRouter()
logger = logging.getLogger("omnisub.settings")


def _validate_subtitle_edit_path(path: str) -> None:
    """Guard against subtitle_edit_path being turned into an arbitrary-executable
    launcher: it's later passed straight to subprocess.run() (utils/subtitle_fixer.py)
    whenever a user clicks "Fix Common Errors". Require an absolute path to an
    existing SubtitleEdit*.exe rather than trusting whatever the client sends."""
    if not path:
        return
    p = Path(path)
    if not p.is_absolute():
        raise HTTPException(status_code=422, detail="subtitle_edit_path must be an absolute path.")
    if p.suffix.lower() != ".exe":
        raise HTTPException(status_code=422, detail="subtitle_edit_path must point to an .exe file.")
    if not p.name.lower().startswith("subtitleedit"):
        raise HTTPException(
            status_code=422,
            detail="subtitle_edit_path must point to a SubtitleEdit*.exe executable.",
        )
    if not p.is_file():
        raise HTTPException(status_code=422, detail=f"subtitle_edit_path does not exist: {path}")


def _validate_ffmpeg_path(path: str) -> None:
    """Same arbitrary-executable guard as subtitle_edit_path: this value is passed
    straight to subprocess when extracting embedded subtitles.

    Accepts either the ffmpeg executable itself or the directory holding it (ffprobe
    is resolved as a sibling), so the user can point at a release folder. Empty means
    "look on PATH", which is how the Docker image finds it.
    """
    if not path:
        return
    p = Path(path)
    if not p.is_absolute():
        raise HTTPException(status_code=422, detail="ffmpeg_path must be an absolute path.")
    if p.is_dir():
        names = ("ffmpeg.exe", "ffmpeg")
        if not any((p / n).is_file() for n in names):
            raise HTTPException(
                status_code=422,
                detail=f"ffmpeg_path directory does not contain an ffmpeg executable: {path}",
            )
        return
    if not p.is_file():
        raise HTTPException(status_code=422, detail=f"ffmpeg_path does not exist: {path}")
    if not p.stem.lower().startswith("ffmpeg"):
        raise HTTPException(
            status_code=422,
            detail="ffmpeg_path must point to an ffmpeg executable (or the folder containing it).",
        )


def obfuscate_api_key(api_key: str) -> str:
    """Base64-obfuscate the API key."""
    return base64.b64encode(api_key.encode('utf-8')).decode('utf-8')


def deobfuscate_api_key(obfuscated_api_key: str) -> str:
    """Deobfuscate the base64-encoded API key."""
    try:
        return base64.b64decode(obfuscated_api_key.encode('utf-8')).decode('utf-8')
    except Exception:
        return ""


def get_api_key() -> Optional[str]:
    # Try environment variable first
    key = os.environ.get("GOOGLE_API_KEY")
    if key:
        return key
    
    # Fallback to config.json
    try:
        config = storage.load_global_config()
        obfuscated = config.get("api_key_obfuscated")
        if obfuscated:
            decrypted = deobfuscate_api_key(obfuscated)
            if decrypted:
                return decrypted
    except Exception:
        pass
    return None


def has_api_key() -> bool:
    return get_api_key() is not None


def validate_api_key(project_metadata: Optional[Dict] = None):
    try:
        config = storage.load_global_config()
        if config.get("ai_provider") == "local":
            return
        if project_metadata:
            proj_settings = project_metadata.get("settings", {})
            if proj_settings.get("ai_provider") == "local":
                return
    except Exception:
        pass

    if not has_api_key():
        raise HTTPException(status_code=400, detail={
            "error": "api_key_missing",
            "message": "Google Gemini API key not configured."
        })


def validate_llm_access(project_metadata: Optional[Dict] = None):
    """For endpoints whose work goes to agy in agent mode (the API is only its rescue for refused lines):
    no Gemini key needed there; with the model engine the key is required as before."""
    from services import agy_llm
    if agy_llm.agent_mode():
        return
    validate_api_key(project_metadata)


@router.get("/api/config/api-key")
async def get_api_key_status():
    return {"has_key": has_api_key()}


@router.post("/api/config/api-key")
async def set_api_key(request: ApiKeyRequest):
    os.environ["GOOGLE_API_KEY"] = request.api_key
    try:
        config = storage.load_global_config()
        config["api_key_obfuscated"] = obfuscate_api_key(request.api_key)
        storage.save_global_config(config)
    except Exception as e:
        logger.error(f"Failed to persist API key to config: {e}")
    return {"status": "success"}


@router.delete("/api/config/api-key")
async def delete_api_key():
    if "GOOGLE_API_KEY" in os.environ:
        del os.environ["GOOGLE_API_KEY"]
    try:
        config = storage.load_global_config()
        if "api_key_obfuscated" in config:
            del config["api_key_obfuscated"]
            import json
            with open(storage.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to clear API key from config: {e}")
    return {"status": "success"}



# A key is only re-checked this often: the health page and settings both ask.
_KEY_CHECK_TTL_SECONDS = 600
_key_checks: Dict[str, tuple] = {}


def _looks_like_bad_key(message: str) -> bool:
    text = message.lower()
    return any(marker in text for marker in (
        "api key not valid", "api_key_invalid", "invalid api key", "permission_denied",
        "api key expired", "unauthenticated", "401", "403",
    ))


async def verify_gemini_key(key: str, *, use_cache: bool = True) -> Dict[str, Any]:
    """Ask Gemini whether this key works: {"valid": True|False|None, "error": str|None}.

    Lists models rather than generating: it is free, touches no daily quota, and
    fails the same way a bad key fails a translation. `valid` is None when Gemini
    could not be reached at all — that says nothing about the key.
    """
    import hashlib
    import time
    fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()
    cached = _key_checks.get(fingerprint)
    if use_cache and cached and time.monotonic() - cached[0] < _KEY_CHECK_TTL_SECONDS:
        return cached[1]

    from google import genai
    try:
        client = genai.Client(api_key=key)
        await asyncio.wait_for(client.aio.models.list(config={"page_size": 1}), timeout=10)
        verdict = {"valid": True, "error": None}
    except Exception as e:
        message = str(e) or type(e).__name__
        verdict = {"valid": False if _looks_like_bad_key(message) else None, "error": message[:300]}
    _key_checks[fingerprint] = (time.monotonic(), verdict)
    return verdict


@router.post("/api/config/test-gemini-key")
async def test_gemini_key(request: Optional[ApiKeyRequest] = None):
    key = (request and request.api_key) or get_api_key()
    if not key:
        return {"valid": False, "error": "No API key provided or configured."}
    verdict = await verify_gemini_key(key, use_cache=False)
    if verdict["valid"]:
        return {"valid": True, "message": "Gemini API key is valid and connected successfully!"}
    if verdict["valid"] is None:
        return {"valid": False, "error": f"Could not reach Gemini to check the key: {verdict['error']}"}
    return {"valid": False, "error": verdict["error"]}


# Keys whose real value is never sent back over the wire. GET masks them with
# SECRET_SENTINEL (plus a `secrets_set` map so the UI can show "configured"
# without knowing the value); POST drops any field still equal to the sentinel
# so re-submitting the form untouched can't overwrite the stored secret.
SECRET_KEYS = (
    "api_key_obfuscated",
    "sonarr_api_key",
    "radarr_api_key",
    "webhook_secret",
    "discord_webhook_url",
    "opensubtitles_api_key",
    "api_key",
    "github_webhook_secret",
    "plex_token",
)
# Never returned at all, not even masked — there is no legitimate reason for a
# client to see this, and it isn't a field anyone edits through /settings.
_NEVER_RETURNED_KEYS = ("auth_password_hash",)
SECRET_SENTINEL = "__SECRET_UNCHANGED__"


@router.get("/api/settings")
async def get_settings():
    config = storage.load_global_config()
    secrets_set = {}
    for key in SECRET_KEYS:
        value = config.get(key)
        secrets_set[key] = bool(value)
        if value:
            config[key] = SECRET_SENTINEL
    for key in _NEVER_RETURNED_KEYS:
        config.pop(key, None)
    config["secrets_set"] = secrets_set
    return config


@router.post("/api/settings")
async def update_settings(settings: SettingsRequest):
    # Persist only the fields the client actually sent, so saving one setting
    # never resets the others to their schema defaults.
    payload = settings.model_dump(exclude_unset=True)
    
    # If api_key was supplied and changed, update GOOGLE_API_KEY environment & obfuscation
    if "api_key" in payload:
        raw_key = payload.pop("api_key", None)
        if raw_key and raw_key != SECRET_SENTINEL:
            os.environ["GOOGLE_API_KEY"] = raw_key
            payload["api_key_obfuscated"] = obfuscate_api_key(raw_key)
            
    for key in SECRET_KEYS:
        if payload.get(key) == SECRET_SENTINEL:
            payload.pop(key)
    if "path_mappings" in payload:
        pm = payload.pop("path_mappings")
        if pm is not None and "arr_path_mappings" not in payload:
            payload["arr_path_mappings"] = pm
    if "subtitle_edit_path" in payload:
        _validate_subtitle_edit_path(payload["subtitle_edit_path"])
    if "ffmpeg_path" in payload:
        _validate_ffmpeg_path(payload["ffmpeg_path"])
    if payload.get("autopilot_enabled"):
        # Switching the autopilot on starts its clock: only files imported after this are "new".
        known = payload["autopilot_enabled_at"] if "autopilot_enabled_at" in payload             else storage.load_global_config().get("autopilot_enabled_at")
        if not known:
            from services.autopilot.store import iso_utc
            from datetime import datetime, timezone
            payload["autopilot_enabled_at"] = iso_utc(datetime.now(timezone.utc))
    storage.save_global_config(payload)
    # Reload rate-limit config in case rpm/rpd or model limits changed.
    per_model_rate_limiter.load_config_and_state()
    return {"status": "success"}


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


@router.post("/settings/browse-executable")
async def browse_executable(request: Request):
    """Open a native Windows 'Open file' dialog (on the machine running the backend)
    so the user can pick an executable instead of typing its path.

    Returns ``{"path": <selected path or null>}``. Windows-only — this app runs the
    backend locally, so the dialog appears on the user's own desktop. Restricted to
    loopback callers: the dialog is only meaningful when the browser is on the same
    machine as the server, and it would otherwise be a way to pop a native file
    picker on someone else's screen from across the network.
    """
    client_host = request.client.host if request.client else None
    if client_host not in _LOOPBACK_HOSTS:
        raise HTTPException(
            status_code=403,
            detail="This dialog only opens for requests from the server's own machine; type the path manually.",
        )

    import sys
    if sys.platform != "win32":
        raise HTTPException(
            status_code=400,
            detail="Native file browsing is only available on Windows; type the path manually.",
        )

    import asyncio
    import subprocess

    # -STA is required for WinForms dialogs. An always-on-top owner form brings the
    # picker to the foreground instead of hiding behind the browser.
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
        "$dlg = New-Object System.Windows.Forms.OpenFileDialog;"
        "$dlg.Title = 'Select SubtitleEdit executable';"
        "$dlg.Filter = 'SubtitleEdit (SubtitleEdit*.exe)|SubtitleEdit*.exe|"
        "Executables (*.exe)|*.exe|All files (*.*)|*.*';"
        "$owner = New-Object System.Windows.Forms.Form -Property @{ TopMost = $true };"
        "if ($dlg.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) "
        "{ [Console]::Out.Write($dlg.FileName) }"
    )

    def _run() -> str:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps_script],
            capture_output=True, text=True, timeout=300,
        )
        return (proc.stdout or "").strip()

    try:
        path = await asyncio.to_thread(_run)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not open the file dialog: {e}")

    return {"path": path or None}


@router.get("/rate-limit/stats")
async def get_rate_limit_stats():
    return translation_rate_limiter.get_stats()


@router.post("/rate-limit/configure")
async def configure_rate_limit(model: str = "default", rpm: int = 15, rpd: int = 1500):
    limiter = per_model_rate_limiter.get_limiter(model)
    limiter.requests_per_minute = rpm
    limiter.daily_limit = rpd
    
    cfg = storage.load_global_config()
    rate_limits = cfg.get("rate_limits", {})
    rate_limits[model] = {"rpm": rpm, "rpd": rpd}
    storage.save_global_config({"rate_limits": rate_limits})
    
    per_model_rate_limiter.load_config_and_state()
    return {"status": "updated", "model": model, "rpm": rpm, "rpd": rpd}


@router.get("/api/models")
async def list_all_models():
    """Unified model registry — returns available Google Gemini models."""
    gemini_models = [
        {"value": "gemini-flash-latest",     "label": "Gemini Flash Latest",  "group": "gemini"},
        {"value": "gemini-flash-lite-latest","label": "Gemini Flash Lite Latest",      "group": "gemini"},
        {"value": "gemini-pro-latest",     "label": "Gemini Pro Latest",  "group": "gemini"},
        {"value": "gemini-2.5-flash",        "label": "Gemini 2.5 Flash",  "group": "gemini"},
        {"value": "gemini-2.5-pro",          "label": "Gemini 2.5 Pro",    "group": "gemini"},
        {"value": "gemini-3.1-flash-lite",   "label": "Gemini 3.1 Flash Lite Preview",  "group": "gemini"},
        {"value": "gemma-4-31b-it",          "label": "Gemma 31B",  "group": "gemini"},
        {"value": "gemma-4-26b-a4b-it",      "label": "Gemma 26B",  "group": "gemini"},
    ]

    return {
        "gemini": gemini_models,
        "models": gemini_models,
    }
