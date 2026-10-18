"""
Keep sidecar subtitles named after their video file.

Plex/Jellyfin/Emby only attach ``<video stem>.<lang>[.flags].<ext>`` to a video. When
Sonarr/Radarr upgrades or renames a release ("... AAC 2.0.mkv" -> "... Opus 2.0.mkv")
the existing subtitles keep the old stem and silently stop showing up. This module
finds such orphans and renames them to the current video stem, keeping their
language / forced / SDH tags.
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.language_codes import LANG_VARIANTS

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov", ".webm"}
SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sub"}

_LANG_SEGMENTS = {v for variants in LANG_VARIANTS.values() for v in variants} | {"gr", "und", "pt-br", "zh-hans", "zh-hant"}
_FLAG_SEGMENTS = {"forced", "sdh", "hi", "cc", "default", "full", "signs", "songs", "commentary", "dialogue"}
_TAG_RE = re.compile(r"(?i)(?<![a-z0-9])s(\d{1,3})[ ._-]?e(\d{1,4})(?!\d)")
_ALT_TAG_RE = re.compile(r"(?i)(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?!\d)")


def episode_tag(name: str) -> Optional[str]:
    """``S01E02`` for names carrying an SxxExx (or 1x02) tag, else None."""
    m = _TAG_RE.search(name) or _ALT_TAG_RE.search(name)
    return f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}" if m else None


def split_subtitle_name(name: str) -> Tuple[str, str]:
    """Split ``Show - S01E01 - Title Opus 2.0.el.forced.ass`` into
    (``Show - S01E01 - Title Opus 2.0``, ``.el.forced.ass``).

    Only real language codes and flags are peeled off, so a stem ending in e.g.
    ``.AAC``, ``.WEB`` or the ``.0`` of "Opus 2.0" is never mistaken for a tag.
    """
    stem, dot, ext = name.rpartition(".")
    if not dot:
        return name, ""
    parts = stem.split(".")
    tail: List[str] = []
    while len(parts) > 1 and len(tail) < 3:
        seg = parts[-1].strip().lower()
        if seg in _LANG_SEGMENTS or seg in _FLAG_SEGMENTS:
            tail.insert(0, parts.pop())
        else:
            break
    suffix = "".join(f".{t}" for t in tail) + f".{ext}"
    return ".".join(parts), suffix


def plan_directory(directory: Path) -> List[Dict[str, str]]:
    """Subtitles in ``directory`` whose stem matches no video, with the rename that fixes each.

    Each entry: ``{"old": path, "new": path | "", "status": ...}`` where status is
    ``rename`` (safe), ``conflict`` (target name already taken — both kept),
    ``ambiguous`` (several videos share the episode tag) or ``unmatched``
    (no video for it). A folder with exactly one video (a movie) matches any
    subtitle without an episode tag to that video.
    """
    try:
        files = [f for f in directory.iterdir() if f.is_file()]
    except OSError:
        return []
    videos = [f for f in files if f.suffix.lower() in VIDEO_EXTS]
    if not videos:
        return []
    video_stems = {v.stem.lower() for v in videos}
    by_tag: Dict[str, List[Path]] = {}
    for v in videos:
        tag = episode_tag(v.name)
        if tag:
            by_tag.setdefault(tag, []).append(v)
    existing = {f.name.lower() for f in files}

    plan: List[Dict[str, str]] = []
    for sub in sorted(f for f in files if f.suffix.lower() in SUB_EXTS):
        lower = sub.name.lower()
        # Already "<video stem>.<anything>" — attached to that video whatever its tags.
        if any(lower.startswith(vs + ".") for vs in video_stems):
            continue
        stem, suffix = split_subtitle_name(sub.name)
        tag = episode_tag(sub.name)
        matches = by_tag.get(tag, []) if tag else (videos if len(videos) == 1 else [])
        if len(matches) != 1:
            plan.append({"old": str(sub), "new": "", "status": "ambiguous" if matches else "unmatched"})
            continue
        new_name = matches[0].stem + suffix
        status = "conflict" if new_name.lower() in existing else "rename"
        plan.append({"old": str(sub), "new": str(directory / new_name), "status": status})
    return plan


def apply_plan(plan: List[Dict[str, str]]) -> Tuple[List[Dict[str, str]], List[str]]:
    """Perform the ``rename`` entries of a plan. Returns (renamed, errors)."""
    renamed, errors = [], []
    for entry in plan:
        if entry["status"] != "rename":
            continue
        old, new = Path(entry["old"]), Path(entry["new"])
        try:
            if new.exists():
                errors.append(f"{new.name} already exists; left {old.name} alone")
                continue
            old.rename(new)
            renamed.append({"old_name": old.name, "new_name": new.name, "path": str(new), "old_path": str(old)})
        except OSError as e:
            errors.append(f"Failed to rename {old.name}: {e}")
    return renamed, errors
