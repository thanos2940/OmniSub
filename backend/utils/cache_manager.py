"""
Cache Manager — Gemini Context Caching for Omnisub

Manages explicit Gemini context caching for translation operations.
Provides fingerprint-based staleness detection to ensure agents always
receive the latest glossary and context guide.

How it works:
1. Before a batch translation starts, we create a CachedContent object
   containing the full system instruction (glossary + context guide).
2. The cache name is stored in project metadata alongside a fingerprint
   (hash of glossary + context_guide).
3. On subsequent batch translations, if the fingerprint matches we reuse
   the cache. If it doesn't (glossary/context was updated), we delete the
   old cache and create a new one.
4. Any endpoint that modifies glossary or context_guide calls
   `invalidate_cache()` to clear the stored cache name.
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from google import genai
from google.genai import types as genai_types

logger = logging.getLogger(__name__)


def _get_client() -> genai.Client:
    """Get a configured Gemini client."""
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    return genai.Client(api_key=api_key)


def compute_fingerprint(glossary: Dict, context_guide: str) -> str:
    """
    Compute a deterministic hash of glossary + context guide.
    
    Used to detect when cached content is stale and needs recreation.
    Changes to glossary terms (add/remove/edit) or context guide text
    will produce a different fingerprint.
    """
    # Sort terms by 'term' field for deterministic ordering
    terms = sorted(
        glossary.get("terms", []),
        key=lambda t: t.get("term", "")
    )
    payload = json.dumps({"terms": terms, "context_guide": context_guide}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def invalidate_cache(metadata: Dict) -> Dict:
    """
    Clear cached content references from project metadata.
    
    Call this whenever glossary or context_guide is modified.
    Returns the updated metadata dict (caller must save it).
    """
    old_cache_name = metadata.get("context_cache_name")

    # Try to delete the remote cache if it exists
    if old_cache_name:
        try:
            client = _get_client()
            client.caches.delete(name=old_cache_name)
            logger.debug(f"Deleted old cache: {old_cache_name}")
        except Exception as e:
            # Cache may have already expired or been deleted — that's fine
            logger.debug(f"Could not delete cache '{old_cache_name}': {e}")

    metadata["context_cache_name"] = None
    metadata["context_cache_expiry"] = None
    metadata["context_cache_fingerprint"] = None
    return metadata


def get_or_create_cache(
    metadata: Dict,
    model_name: str,
    system_instruction: str,
) -> Tuple[Optional[str], Dict]:
    """
    Get an existing cache or create a new one for translation.
    
    Uses fingerprint comparison to detect stale caches. If the glossary
    or context guide has changed since the cache was created, the old
    cache is invalidated and a new one is created.
    
    Args:
        metadata: Project metadata dict (modified in-place with cache info)
        model_name: Gemini model name (caches are model-specific)
        system_instruction: Full system instruction for the translator agent
        
    Returns:
        Tuple of (cache_name or None, updated metadata)
    """
    glossary = metadata.get("glossary", {"terms": []})
    context_guide = metadata.get("context_guide", "")
    current_fingerprint = compute_fingerprint(glossary, context_guide)

    # Check if existing cache is still valid
    stored_name = metadata.get("context_cache_name")
    stored_fingerprint = metadata.get("context_cache_fingerprint")
    stored_expiry = metadata.get("context_cache_expiry")

    if stored_name and stored_fingerprint == current_fingerprint:
        # Check if cache hasn't expired
        if stored_expiry:
            try:
                expiry_dt = datetime.fromisoformat(stored_expiry)
                if expiry_dt > datetime.now(timezone.utc):
                    logger.debug(f"Reusing valid cache: {stored_name}")
                    return stored_name, metadata
            except (ValueError, TypeError):
                pass
        # If we can't parse the expiry, verify the cache still exists
        try:
            client = _get_client()
            client.caches.get(name=stored_name)
            logger.debug(f"Reusing valid cache (no expiry check): {stored_name}")
            return stored_name, metadata
        except Exception:
            logger.debug(f"Stored cache '{stored_name}' no longer exists")

    # Fingerprint mismatch or cache expired/missing — invalidate and recreate
    if stored_name:
        invalidate_cache(metadata)

    # Create new cache with rate limiter pacing
    try:
        from utils.rate_limiter import per_model_rate_limiter

        limiter = per_model_rate_limiter.get_limiter(model_name)
        limiter.acquire_sync()
        client = _get_client()

        cache = client.caches.create(
            model=model_name,
            config=genai_types.CreateCachedContentConfig(
                display_name=f"omnisub_{metadata.get('show_name', 'project')}",
                system_instruction=system_instruction,
                ttl="3600s",  # 1 hour
            ),
        )

        # Parse expiry from cache response
        cache_expiry = None
        if hasattr(cache, "expire_time") and cache.expire_time:
            cache_expiry = cache.expire_time.isoformat()

        metadata["context_cache_name"] = cache.name
        metadata["context_cache_fingerprint"] = current_fingerprint
        metadata["context_cache_expiry"] = cache_expiry
        logger.debug(f"Created new cache: {cache.name} (fingerprint: {current_fingerprint})")
        return cache.name, metadata

    except Exception as e:
        logger.debug(f"Failed to create cache (will proceed without): {e}")
        metadata["context_cache_name"] = None
        metadata["context_cache_fingerprint"] = current_fingerprint
        metadata["context_cache_expiry"] = None
        return None, metadata
