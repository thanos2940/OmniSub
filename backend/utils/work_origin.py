"""
Where a work comes from: its original language, and whether it is Japanese (anime or live action).

Some rules only make sense for Japanese works: the Greek accent on the first syllable of Japanese names
(utils/text_normalize.py) and the matching style rule (utils/translation_rules.py). Applied to every show
they rewrite ordinary Greek words and Western names ("Περίμενε" -> "Πέριμενε", "Μαρία" -> "Μάρια"), so
every caller asks ``is_japanese_work`` first.

The facts come from Sonarr/Radarr (``series_type`` "anime", ``arr_original_language``, ``arr_genres``),
from the project's own fields (``is_anime``, ``type``, ``genres``, ``tags``) and from the Autopilot's
``original_language``.
"""

from typing import Dict, Optional

_JAPANESE = {"japanese", "ja", "jpn", "jp", "日本語"}


def original_language(meta: Optional[Dict]) -> str:
    """The work's original language as stored ("" when nothing says)."""
    meta = meta or {}
    for key in ("arr_original_language", "original_language"):
        value = meta.get(key)
        if isinstance(value, dict):   # Sonarr/Radarr shape {"id": 8, "name": "Japanese"}
            value = value.get("name")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def is_japanese_work(meta: Optional[Dict]) -> bool:
    """True for an anime or a work whose original language is Japanese."""
    meta = meta or {}
    if original_language(meta).lower() in _JAPANESE:
        return True
    if str(meta.get("series_type") or "").strip().lower() == "anime":
        return True
    if meta.get("is_anime") is True or str(meta.get("type") or "").strip().lower() == "anime":
        return True
    for key in ("arr_genres", "genres"):
        genres = meta.get(key) or []
        if isinstance(genres, str):
            genres = [genres]
        if any(str(g).strip().lower() == "anime" for g in genres):
            return True
    tags = meta.get("tags") or []
    return any("anime" in str(t).strip().lower() for t in tags if t)
