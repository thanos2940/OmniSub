import logging
from typing import Dict, Optional, Any
from utils import storage

logger = logging.getLogger(__name__)

# Hybrid Strategy: Which roles default to local vs cloud
# True = Local by default, False = Cloud by default
_HYBRID_LOCAL_PREFERENCE = {
    "translation": True,
    "scan": True,
    "consistency": True,
    "condense": True,
    "reconciliation": True,
    "glossary": False,
    "context": False,
    "review": False,
    "summary": False,
    "research": False,
}

_LOGGED_FAILOVERS: set = set()


def clear_logged_failovers():
    """Clear logged failover states when limits reset."""
    _LOGGED_FAILOVERS.clear()


def resolve_model(
    role: str,
    metadata: Optional[Dict] = None,
    check_exhaustion: bool = True,
    model_override: Optional[str] = None
) -> str:
    """
    Resolve the concrete model name for a given role based on the ai_provider.
    If the primary model (or model_override) is daily-exhausted and a fallback
    model is configured, automatically resolves to the fallback model.
    """
    if metadata is None:
        metadata = {}

    model = model_override
    if not model:
        provider = storage.get_project_setting(metadata, "ai_provider", "cloud")
        
        # 1. Determine if we should use local or cloud for this role
        use_local = False
        if provider == "local":
            use_local = True
        elif provider == "hybrid":
            use_local = _HYBRID_LOCAL_PREFERENCE.get(role, False)
        else: # cloud
            use_local = False
            
        # 2. Resolve the primary model name
        if use_local:
            local_key = f"local_{role}_model"
            model = storage.get_project_setting(metadata, local_key)
            
            if not model:
                model = storage.get_project_setting(metadata, "local_translation_model")
                
            if model and not model.startswith("local/"):
                model = f"local/{model}"
            model = model or "local/default"
        else:
            cloud_key = f"{role}_model"
            model = storage.get_project_setting(metadata, cloud_key)
            
            if not model:
                fallback_map = {
                    "translation": "gemini-flash-lite-latest",
                    "scan": "gemini-flash-lite-latest",
                    "context": "gemini-flash-lite-latest",
                    "glossary": "gemini-flash-lite-latest",
                    "review": "gemini-flash-lite-latest",
                }
                model = fallback_map.get(role, "gemini-flash-lite-latest")

    # 3. Failover check: If primary model is daily-exhausted, check for fallback model
    if check_exhaustion and model:
        try:
            from utils.rate_limiter import per_model_rate_limiter
            exhausted = per_model_rate_limiter.exhausted_models()
            model_key = model[6:] if model.startswith("local/") else model

            if model_key in exhausted:
                fallback_model = (
                    storage.get_project_setting(metadata, f"fallback_{role}_model") or
                    storage.get_project_setting(metadata, "fallback_translation_model") or
                    "gemini-3.1-flash-lite"
                )
                if fallback_model and fallback_model != model:
                    fb_key = fallback_model[6:] if fallback_model.startswith("local/") else fallback_model
                    if fb_key not in exhausted:
                        log_key = (model, role, fallback_model)
                        if log_key not in _LOGGED_FAILOVERS:
                            _LOGGED_FAILOVERS.add(log_key)
                            logger.info(
                                f"Primary model '{model}' is daily exhausted for role '{role}'. "
                                f"Failing over to configured fallback model '{fallback_model}'."
                            )
                        return fallback_model
                    else:
                        log_key = (model, role, "exhausted_all")
                        if log_key not in _LOGGED_FAILOVERS:
                            _LOGGED_FAILOVERS.add(log_key)
                            logger.warning(
                                f"Both primary model '{model}' and fallback model '{fallback_model}' are daily exhausted. "
                                f"Failing task cleanly."
                            )
        except Exception as err:
            logger.debug(f"Failed to check model exhaustion failover: {err}")

    return model

