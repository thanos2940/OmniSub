from typing import Dict, Optional, Any
from utils import storage

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

def resolve_model(role: str, metadata: Optional[Dict] = None) -> str:
    """
    Resolve the concrete model name for a given role based on the ai_provider.
    
    Args:
        role: The agent role (e.g., "translation", "review", "scan")
        metadata: Optional project metadata containing settings overrides.
        
    Returns:
        The model name, prefixed with "local/" if it's a local model.
    """
    if metadata is None:
        metadata = {}
        
    provider = storage.get_project_setting(metadata, "ai_provider", "cloud")
    
    # 1. Determine if we should use local or cloud for this role
    use_local = False
    if provider == "local":
        use_local = True
    elif provider == "hybrid":
        use_local = _HYBRID_LOCAL_PREFERENCE.get(role, False)
    else: # cloud
        use_local = False
        
    # 2. Resolve the model name
    if use_local:
        # Check for explicit local model for this role
        local_key = f"local_{role}_model"
        model = storage.get_project_setting(metadata, local_key)
        
        # Fallback: if no specific local role model, use the default local translation model
        if not model:
            model = storage.get_project_setting(metadata, "local_translation_model")
            
        # Ensure it has the local/ prefix
        if model and not model.startswith("local/"):
            return f"local/{model}"
        return model or "local/default" # Should ideally never be "local/default" if config is right
    else:
        # Cloud path
        cloud_key = f"{role}_model"
        # Backwards compatibility: storage.get_project_setting already handles 
        # translation_model -> default_translation_model fallback.
        model = storage.get_project_setting(metadata, cloud_key)
        
        # Fallback if the retrieved model is falsy (empty string or None)
        if not model:
            fallback_map = {
                "translation": "gemini-flash-lite-latest",
                "scan": "gemini-flash-lite-latest",
                "context": "gemini-flash-lite-latest",
                "glossary": "gemini-flash-lite-latest",
                "review": "gemini-flash-lite-latest",
            }
            model = fallback_map.get(role, "gemini-flash-lite-latest")
            
        # If it happens to be a local model name (prefixed), return as is
        return model
