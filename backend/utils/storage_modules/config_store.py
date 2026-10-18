"""
Global Configuration and Project Setting Resolution.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Dict, Optional, Any

import utils.paths as paths
from utils.storage_modules.atomic_io import write_json_atomic as _atomic_write_json

logger = logging.getLogger(__name__)

_config_defaults_cache: Optional[Dict] = None
_config_lock = threading.RLock()
GLOBAL_CONFIG: Dict[str, Any] = {}


def _get_config_file() -> Path:
    """Dynamic resolution of CONFIG_FILE supporting test monkeypatching."""
    try:
        import utils.storage as storage_mod
        c = getattr(storage_mod, "CONFIG_FILE", None)
        if c is not None:
            return Path(c)
    except Exception:
        pass
    return paths.CONFIG_FILE


def _config_defaults() -> Dict:
    """Canonical default values for every known setting — single source of truth
    (the SettingsRequest schema). Cached after first build."""
    global _config_defaults_cache
    if _config_defaults_cache is None:
        try:
            from routers.schemas import SettingsRequest
            _config_defaults_cache = SettingsRequest().model_dump()
        except Exception:
            _config_defaults_cache = {}
    return dict(_config_defaults_cache)


def _load_raw_config() -> Dict:
    """The on-disk config only (no defaults overlaid)."""
    cfg_file = _get_config_file()
    if not cfg_file.exists():
        return {}
    try:
        with open(cfg_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def load_config_and_state() -> Dict[str, Any]:
    """Load configuration from disk into memory and sync state/keys."""
    global GLOBAL_CONFIG
    with _config_lock:
        GLOBAL_CONFIG.clear()
        GLOBAL_CONFIG.update(_load_raw_config())
        try:
            import os
            from routers.settings import get_api_key
            key = get_api_key()
            if key and not os.environ.get("GOOGLE_API_KEY"):
                os.environ["GOOGLE_API_KEY"] = key
        except Exception:
            pass
        try:
            from utils.rate_limiter import per_model_rate_limiter
            per_model_rate_limiter.load_config_and_state()
        except Exception:
            pass
        return GLOBAL_CONFIG


def load_global_config() -> Dict:
    """Load global configuration: schema defaults overlaid with on-disk values.

    Guarantees every known setting is present with its canonical default, so callers
    don't need to repeat inline defaults.
    """
    with _config_lock:
        raw = _load_raw_config()
        GLOBAL_CONFIG.clear()
        GLOBAL_CONFIG.update(raw)
        return {**_config_defaults(), **raw}


def save_global_config(new_config: Dict) -> None:
    """Merge new_config into GLOBAL_CONFIG and save atomically under lock."""
    global GLOBAL_CONFIG
    cfg_file = _get_config_file()
    with _config_lock:
        current = _load_raw_config()
        current.update(new_config)
        GLOBAL_CONFIG.clear()
        GLOBAL_CONFIG.update(current)
        _atomic_write_json(cfg_file, GLOBAL_CONFIG)


try:
    import sys
    _mod = sys.modules.get("utils.storage")
    if _mod is not None:
        setattr(_mod, "GLOBAL_CONFIG", GLOBAL_CONFIG)
        setattr(_mod, "load_config_and_state", load_config_and_state)
        setattr(_mod, "_config_lock", _config_lock)
except Exception:
    pass


def get_project_setting(metadata: Dict, key: str, default_val=None) -> Any:
    """Resolve a project-specific setting, falling back to global configuration.

    If the project setting matches the old hardcoded default models, it falls back
    to the global configuration defaults.
    """
    project_settings = {}
    if isinstance(metadata, dict):
        raw_settings = metadata.get("settings")
        if isinstance(raw_settings, dict):
            project_settings = raw_settings

    try:
        import utils.storage as storage_mod
        load_cfg_fn = getattr(storage_mod, "load_global_config", load_global_config)
        global_config = load_cfg_fn()
    except Exception:
        global_config = load_global_config()

    global_key_map = {
        "translation_model": "default_translation_model",
        "fallback_translation_model": "fallback_translation_model",
        "scan_model": "default_scan_model",
        "context_model": "default_context_model",
        "glossary_model": "default_glossary_model",
        "review_model": "review_model",
        "summary_model": "summary_model",
        "research_model": "research_model",
        "repair_model": "repair_model",
        "reconciliation_model": "reconciliation_model",
        "consistency_model": "consistency_model",
        "temperature": "temperature",
        "top_k": "top_k",
        "top_p": "top_p",
    }

    val = project_settings.get(key)
    if val is not None and val != "":
        # Check if it's the old hardcoded default model name
        is_default_model = (
            key in ("translation_model", "scan_model", "context_model", "glossary_model", "review_model", "summary_model", "research_model", "repair_model", "reconciliation_model", "consistency_model") and
            val in ("gemini-flash-lite-latest", "gemini-2.0-flash", "gemini-1.5-flash")
        )
        if is_default_model:
            g_key = global_key_map.get(key, key)
            if g_key and g_key in global_config and global_config[g_key]:
                return global_config[g_key]
        return val

    g_key = global_key_map.get(key, key)
    if g_key in global_config and global_config[g_key] is not None:
        return global_config[g_key]

    return default_val
