import os
import base64
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
import httpx

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


def validate_api_key():
    if not has_api_key():
        raise HTTPException(status_code=400, detail={
            "error": "api_key_missing",
            "message": "Google Gemini API key not configured."
        })


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



@router.post("/api/config/test-gemini-key")
async def test_gemini_key(request: Optional[ApiKeyRequest] = None):
    key = (request and request.api_key) or get_api_key()
    if not key:
        return {"valid": False, "error": "No API key provided or configured."}
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="Say 'OK'",
            config=types.GenerateContentConfig(max_output_tokens=5, temperature=0.0)
        )
        return {"valid": True, "message": "Gemini API key is valid and connected successfully!"}
    except Exception as e:
        return {"valid": False, "error": str(e)}


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
    if "subtitle_edit_path" in payload:
        _validate_subtitle_edit_path(payload["subtitle_edit_path"])
    if "ffmpeg_path" in payload:
        _validate_ffmpeg_path(payload["ffmpeg_path"])
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
async def list_all_models(base_url: Optional[str] = None):
    """Unified model registry — returns Gemini cloud models and discovered local models.

    Response shape:
    {
      "gemini": [{"value": "gemini-flash-latest", "label": "Gemini Flash Latest", "group": "gemini"}, ...],
      "local":  [{"value": "local/my-model", "label": "my-model", "group": "local"}, ...],
      "local_endpoint": "http://localhost:1234/v1",
      "local_online": true | false
    }
    """
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

    from adk_agents.llm_factory import _resolve_local_base_url
    global_config = storage.load_global_config()
    resolved_url = _resolve_local_base_url(base_url or global_config.get("local_llm_base_url"))

    local_models: list = []
    local_online = False

    try:
        candidates = [resolved_url.rstrip("/") + "/models"]
        if not resolved_url.rstrip("/").endswith("/v1"):
            candidates.append(resolved_url.rstrip("/") + "/v1/models")

        async with httpx.AsyncClient(timeout=2.0) as client:
            for target in candidates:
                try:
                    resp = await client.get(target)
                    if resp.status_code == 200:
                        data = resp.json()
                        local_models = [
                            {"value": f"local/{m['id']}", "label": m["id"], "group": "local"}
                            for m in data.get("data", [])
                        ]
                        local_online = True
                        break
                except Exception:
                    pass
    except Exception:
        pass

    return {
        "gemini": gemini_models,
        "local": local_models,
        "local_endpoint": resolved_url,
        "local_online": local_online,
    }


@router.get("/api/config/models/local")
async def list_local_models(base_url: Optional[str] = None):
    """Fetch available models from a local OpenAI-compatible server."""
    from adk_agents.llm_factory import _resolve_local_base_url
    
    # Resolve URL from provided arg, then global config, then default
    if not base_url:
        global_config = storage.load_global_config()
        base_url = global_config.get("local_llm_base_url")
    
    url = _resolve_local_base_url(base_url)
    
    try:
        async with httpx.AsyncClient() as client:
            parsed = urlparse(url)
            root_url = f"{parsed.scheme}://{parsed.netloc}"
            
            # Alternative: if netloc is 'localhost', also try '127.0.0.1'
            alt_root_url = None
            if parsed.netloc.startswith('localhost:'):
                alt_root_url = f"{parsed.scheme}://127.0.0.1:{parsed.netloc.split(':')[1]}"
            elif parsed.netloc == 'localhost':
                alt_root_url = f"{parsed.scheme}://127.0.0.1"
            
            # Endpoints to try with different strategies
            check_urls = []
            
            base_urls = [root_url]
            if alt_root_url: base_urls.append(alt_root_url)
            
            # Add base-relative URL
            if url.endswith('/v1') or url.endswith('/v1/'):
                check_urls.append(url.rstrip('/') + "/models")
                if alt_root_url:
                    alt_url = url.replace(parsed.netloc, alt_root_url.split('://')[1])
                    check_urls.append(alt_url.rstrip('/') + "/models")
            else:
                check_urls.append(url.rstrip('/') + "/v1/models")
                
            for b in base_urls:
                check_urls.append(b + "/v1/models")
                check_urls.append(b + "/api/v1/models")
            
            # De-duplicate
            check_urls = list(dict.fromkeys(check_urls))
            
            all_errors = []
            for target_url in check_urls:
                try:
                    response = await client.get(target_url, timeout=2.0)
                    if response.status_code == 200:
                        data = response.json()
                        models = [
                            {"value": f"local/{m['id']}", "label": m['id']} 
                            for m in data.get("data", [])
                        ]
                        if models:
                            return {"models": models, "active_endpoint": target_url}
                except Exception as e:
                    all_errors.append(f"{target_url}: {str(e)}")
            
            return {
                "models": [], 
                "error": "Models not found at any common endpoint",
                "diagnostics": {
                    "tried_urls": check_urls,
                    "errors": all_errors
                }
            }
    except Exception as e:
        return {"models": [], "error": str(e)}
