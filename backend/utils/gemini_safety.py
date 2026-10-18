"""
Gemini API safety thresholds
============================
Omnisub translates published films and series, where violence, threats, insults and crude
language are part of the dialogue. Google's default content filter refuses some of those lines,
so every Gemini API request Omnisub makes (live calls through ``cached_translator``, the Batch
API, the ADK agents) carries the thresholds chosen in Settings as ``gemini_safety_filter``:

* ``off`` (default): the adjustable filters are switched off (``OFF``) for the four text
  categories (harassment, hate speech, sexually explicit, dangerous content);
* ``only_high``: only content rated a high probability of harm is blocked;
* ``default``: no thresholds are sent and Google's defaults apply.

Some protections cannot be switched off by any setting (``PROHIBITED_CONTENT``, child safety),
and ``RECITATION`` (reproducing copyrighted text) is a separate check. ``describe_block`` names
the reason when a response comes back without text.
"""

from __future__ import annotations

from typing import Dict, List, Optional

LEVELS: Dict[str, Optional[str]] = {"off": "OFF", "only_high": "BLOCK_ONLY_HIGH", "default": None}
DEFAULT_LEVEL = "off"
CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)
# Finish reasons that mean a filter, not the model, ended the answer.
_FILTER_FINISH = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY",
                  "IMAGE_PROHIBITED_CONTENT"}


def current_level(cfg: Optional[Dict] = None) -> str:
    """The configured level; anything unknown counts as the default (``off``)."""
    if cfg is None:
        try:
            from utils.storage import load_global_config
            cfg = load_global_config()
        except Exception:
            cfg = {}
    value = str((cfg or {}).get("gemini_safety_filter") or DEFAULT_LEVEL).strip().lower()
    return value if value in LEVELS else DEFAULT_LEVEL


def safety_settings(level: Optional[str] = None) -> Optional[List]:
    """``types.SafetySetting`` objects for a request, or None when Google's defaults should apply."""
    from google.genai import types
    threshold = LEVELS[level if level in LEVELS else current_level()]
    if threshold is None:
        return None
    return [types.SafetySetting(category=types.HarmCategory[c], threshold=types.HarmBlockThreshold[threshold])
            for c in CATEGORIES]


def _name(value) -> str:
    return getattr(value, "name", None) or str(value or "")


def describe_block(response) -> Optional[str]:
    """Why a Gemini response has no text when a filter is the reason, e.g. ``prompt blocked:
    PROHIBITED_CONTENT`` or ``SAFETY: HARM_CATEGORY_HARASSMENT``; None otherwise."""
    try:
        feedback = getattr(response, "prompt_feedback", None)
        reason = getattr(feedback, "block_reason", None) if feedback else None
        if reason and _name(reason) != "BLOCKED_REASON_UNSPECIFIED":
            return f"prompt blocked: {_name(reason)}"
        for candidate in getattr(response, "candidates", None) or []:
            finish = _name(getattr(candidate, "finish_reason", None))
            if finish in _FILTER_FINISH:
                ratings = getattr(candidate, "safety_ratings", None) or []
                cats = [_name(r.category) for r in ratings if getattr(r, "blocked", False)]
                return f"{finish}: {', '.join(cats)}" if cats else finish
    except Exception:
        return None
    return None
