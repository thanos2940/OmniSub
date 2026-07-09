import os
import base64
import logging
from typing import Optional
from urllib.parse import urlparse
import httpx

from fastapi import APIRouter, HTTPException

from utils import storage
from utils.rate_limiter import translation_rate_limiter, per_model_rate_limiter
from routers.schemas import SettingsRequest, ApiKeyRequest

router = APIRouter()
logger = logging.getLogger("omnisub.settings")


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



@router.get("/settings")
async def get_settings():
    return storage.load_global_config()


@router.post("/settings")
async def update_settings(settings: SettingsRequest):
    # Persist only the fields the client actually sent, so saving one setting
    # never resets the others to their schema defaults.
    storage.save_global_config(settings.dict(exclude_unset=True))
    # Reload rate-limit config in case rpm/rpd or model limits changed.
    per_model_rate_limiter.load_config_and_state()
    return {"status": "success"}


@router.post("/settings/browse-executable")
async def browse_executable():
    """Open a native Windows 'Open file' dialog (on the machine running the backend)
    so the user can pick an executable instead of typing its path.

    Returns ``{"path": <selected path or null>}``. Windows-only — this app runs the
    backend locally, so the dialog appears on the user's own desktop.
    """
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
      "gemini": [{"value": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "group": "gemini"}, ...],
      "local":  [{"value": "local/my-model", "label": "my-model", "group": "local"}, ...],
      "local_endpoint": "http://localhost:1234/v1",
      "local_online": true | false
    }
    """
    gemini_models = [
        {"value": "gemini-flash-lite-latest",     "label": "Gemini Flash Latest",  "group": "gemini"},
        {"value": "gemini-flash-lite-latest","label": "Gemini Flash Lite Latest",      "group": "gemini"},
        {"value": "gemma-4-31b-it",     "label": "Gemma 31B",  "group": "gemini"},
        {"value": "gemma-4-26b-a4b-it",     "label": "Gemma 26B",  "group": "gemini"},
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
