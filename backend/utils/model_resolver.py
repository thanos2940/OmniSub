import logging
from typing import Dict, Optional, Any
from utils import storage

logger = logging.getLogger(__name__)

_DEFAULT_ROLE_MODELS = {
    "translation": "gemini-flash-lite-latest",
    "scan": "gemini-flash-lite-latest",
    "context": "gemini-flash-lite-latest",
    "glossary": "gemini-flash-lite-latest",
    "review": "gemini-flash-lite-latest",
    "summary": "gemini-flash-lite-latest",
    "research": "gemini-flash-lite-latest",
    "repair": "gemini-flash-lite-latest",
    "reconciliation": "gemini-flash-lite-latest",
    "consistency": "gemini-flash-lite-latest",
}

_LOGGED_FAILOVERS: set = set()


def clear_logged_failovers() -> None:
    """Clear logged failover states when limits reset."""
    _LOGGED_FAILOVERS.clear()


def mark_model_daily_exhausted(model_name: str, reset_time: Optional[float] = None) -> None:
    """Mark a model as daily exhausted in the rate limiter."""
    from utils.rate_limiter import per_model_rate_limiter
    limiter = per_model_rate_limiter.get_limiter(model_name)
    limiter.trigger_daily_limit(reset_time=reset_time)


def is_model_daily_exhausted(model_name: str) -> bool:
    """Check if a model is currently latched as daily exhausted."""
    from utils.rate_limiter import per_model_rate_limiter
    return model_name in per_model_rate_limiter.exhausted_models()


def clear_model_daily_exhaustion(model_name: str) -> None:
    """Clear daily exhaustion for a model."""
    from utils.rate_limiter import per_model_rate_limiter
    limiter = per_model_rate_limiter.get_limiter(model_name)
    limiter.clear_daily_limit()


try:
    from utils.rate_limiter import RateLimiter, ProxyRateLimiter
    if not hasattr(RateLimiter, "is_daily_limit_exhausted"):
        RateLimiter.is_daily_limit_exhausted = lambda self: bool(getattr(self, "_daily_exhausted", False))
    if not hasattr(ProxyRateLimiter, "is_daily_limit_exhausted"):
        ProxyRateLimiter.is_daily_limit_exhausted = lambda self: self.current_limiter.is_daily_limit_exhausted()
except Exception:
    pass


def get_daily_limit_policy(metadata: Optional[Dict] = None) -> str:
    """
    Get the daily limit policy for quota exhaustion.
    Returns 'auto_fallback' (default) or 'pause_and_wait'.
    """
    if metadata:
        policy = storage.get_project_setting(metadata, "daily_limit_policy")
        if policy in ("auto_fallback", "pause_and_wait"):
            return policy
    try:
        cfg = storage.load_global_config()
        policy = cfg.get("daily_limit_policy")
        if policy in ("auto_fallback", "pause_and_wait"):
            return policy
    except Exception:
        pass
    return "auto_fallback"


def resolve_model(
    role: str,
    metadata: Optional[Dict] = None,
    check_exhaustion: bool = True,
    model_override: Optional[str] = None,
    daily_limit_policy: Optional[str] = None,
) -> str:
    """
    Resolve the concrete model name for a given role honoring ai_provider
    ('cloud', 'local', 'hybrid') and daily_limit_policy ('auto_fallback', 'pause_and_wait').
    If the primary model (or model_override) is daily-exhausted and daily_limit_policy
    is 'auto_fallback' (default), automatically resolves to the fallback model.
    If daily_limit_policy is 'pause_and_wait', returns the primary model so callers
    can pause and wait for daily reset.
    """
    if metadata is None:
        metadata = {}

    provider = storage.get_project_setting(metadata, "ai_provider")
    if not provider or not isinstance(provider, str):
        try:
            cfg = storage.load_global_config()
            provider = cfg.get("ai_provider", "cloud") if isinstance(cfg, dict) else "cloud"
        except Exception:
            provider = "cloud"
    if not isinstance(provider, str):
        provider = "cloud"

    model = model_override
    if not model:
        if provider == "local":
            # Local resolution precedence: local_{role}_model -> local_model -> default_local_model -> "local"
            model = (
                storage.get_project_setting(metadata, f"local_{role}_model") or
                storage.get_project_setting(metadata, "local_model")
            )
            if not model or not isinstance(model, str):
                try:
                    cfg = storage.load_global_config()
                    model = cfg.get("default_local_model", "local") if isinstance(cfg, dict) else "local"
                except Exception:
                    model = "local"
            if not isinstance(model, str):
                model = "local"
        else:
            cloud_key = f"{role}_model"
            model = storage.get_project_setting(metadata, cloud_key)
            if not model or not isinstance(model, str):
                model = _DEFAULT_ROLE_MODELS.get(role, "gemini-flash-lite-latest")

    # Local models do not suffer Gemini daily quota exhaustion
    if provider == "local":
        return model

    policy = daily_limit_policy or get_daily_limit_policy(metadata)
    if policy == "pause_and_wait":
        return model

    # Failover check: If primary model is daily-exhausted, check for fallback model
    if check_exhaustion and model:
        try:
            from utils.rate_limiter import per_model_rate_limiter
            exhausted = per_model_rate_limiter.exhausted_models()

            if model in exhausted:
                fallback_model = (
                    storage.get_project_setting(metadata, f"fallback_{role}_model") or
                    storage.get_project_setting(metadata, "fallback_translation_model") or
                    storage.get_project_setting(metadata, "default_fallback_model") or
                    "gemini-3.1-flash-lite"
                )
                if fallback_model and fallback_model != model:
                    if fallback_model not in exhausted:
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
