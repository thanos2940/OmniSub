"""
Keep sidecar subtitles named after their video file.

Plex/Jellyfin/Emby only attach ``<video stem>.<lang>[.flags].<ext>`` to a video. When
Sonarr/Radarr upgrades or renames a release ("... AAC 2.0.mkv" -> "... Opus 2.0.mkv")
the existing subtitles keep the old stem and silently stop showing up. This module
finds such orphans and renames them to the current video stem, keeping their
language / forced / SDH tags.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

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


def apply_plan(
    plan: List[Dict[str, str]],
    project_name: Optional[str] = None,
    episode_name: Optional[str] = None,
    ledger: Optional[Any] = None,
) -> Tuple[List[Dict[str, str]], List[str]]:
    """Perform the ``rename`` entries of a plan and sync ownership in ownership.db. Returns (renamed, errors)."""
    renamed, errors = [], []
    for entry in plan:
        if entry["status"] != "rename":
            continue
        old, new = Path(entry["old"]), Path(entry["new"])
        try:
            if new.exists():
                errors.append(f"{new.name} already exists; left {old.name} alone")
                continue

            was_ours = False
            try:
                if ledger is not None and hasattr(ledger, "is_ours"):
                    was_ours = ledger.is_ours(str(old))
                else:
                    from services.autopilot import ownership
                    if hasattr(ownership, "OwnershipLedger"):
                        temp_ledger = ownership.OwnershipLedger()
                        was_ours = temp_ledger.is_ours(str(old))
                    elif hasattr(ownership, "is_ours"):
                        was_ours = ownership.is_ours(str(old))
            except Exception:
                was_ours = False

            os.replace(str(old), str(new))

            if was_ours:
                try:
                    if ledger is not None:
                        if hasattr(ledger, "record_write"):
                            ledger.record_write(str(new), project=project_name, episode=episode_name)
                        elif hasattr(ledger, "register_file"):
                            ledger.register_file(str(new), project_name=project_name, episode_name=episode_name)
                    else:
                        from services.autopilot import ownership
                        if hasattr(ownership, "OwnershipLedger"):
                            temp_ledger = ownership.OwnershipLedger()
                            if hasattr(temp_ledger, "record_write"):
                                temp_ledger.record_write(str(new), project=project_name, episode=episode_name)
                        if hasattr(ownership, "forget"):
                            ownership.forget(str(old))
                        if hasattr(ownership, "register_file"):
                            ownership.register_file(str(new), project_name=project_name, episode_name=episode_name)
                        if hasattr(ownership, "record_write"):
                            ownership.record_write(str(new), project=project_name, episode=episode_name)
                except Exception:
                    pass

            renamed.append({"old_name": old.name, "new_name": new.name, "path": str(new), "old_path": str(old)})
        except OSError as e:
            errors.append(f"Failed to rename {old.name}: {e}")
    return renamed, errors


def rename_episode_subtitles_to_match_video(
    target: Any,
    new_path: Optional[Any] = None,
    project_name: Optional[str] = None,
    episode_name: Optional[str] = None,
    ledger: Optional[Any] = None,
) -> Any:
    """Rename sidecar subtitle(s) to match the current video stem and synchronize ownership in ownership.db.

    Supports:
    1. Single file rename: rename_episode_subtitles_to_match_video(old_path, new_path, project_name=..., episode_name=..., ledger=...)
    2. Directory scanning: rename_episode_subtitles_to_match_video(directory, project_name=..., episode_name=..., ledger=...)
    3. Video file path: rename_episode_subtitles_to_match_video(video_path, project_name=..., episode_name=..., ledger=...)
    """
    target_path = Path(target)

    # Case 1: old_path and new_path both provided
    if new_path is not None and isinstance(new_path, (str, Path)):
        dest_path = Path(new_path)
        if not target_path.exists():
            return False

        was_ours = False
        try:
            if ledger is not None and hasattr(ledger, "is_ours"):
                was_ours = ledger.is_ours(str(target_path))
            else:
                from services.autopilot import ownership
                if hasattr(ownership, "OwnershipLedger"):
                    temp_ledger = ownership.OwnershipLedger()
                    was_ours = temp_ledger.is_ours(str(target_path))
                elif hasattr(ownership, "is_ours"):
                    was_ours = ownership.is_ours(str(target_path))
        except Exception:
            was_ours = False

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(target_path), str(dest_path))

        if was_ours:
            try:
                if ledger is not None:
                    if hasattr(ledger, "record_write"):
                        ledger.record_write(str(dest_path), project=project_name, episode=episode_name)
                    elif hasattr(ledger, "register_file"):
                        ledger.register_file(str(dest_path), project_name=project_name, episode_name=episode_name)
                else:
                    from services.autopilot import ownership
                    if hasattr(ownership, "OwnershipLedger"):
                        temp_ledger = ownership.OwnershipLedger()
                        if hasattr(temp_ledger, "record_write"):
                            temp_ledger.record_write(str(dest_path), project=project_name, episode=episode_name)
                    if hasattr(ownership, "forget"):
                        ownership.forget(str(target_path))
                    if hasattr(ownership, "register_file"):
                        ownership.register_file(str(dest_path), project_name=project_name, episode_name=episode_name)
                    if hasattr(ownership, "record_write"):
                        ownership.record_write(str(dest_path), project=project_name, episode=episode_name)
            except Exception:
                pass
        return True

    # Case 2 & 3: Directory or video file path
    directory = target_path if target_path.is_dir() else target_path.parent
    plan = plan_directory(directory)
    return apply_plan(plan, project_name=project_name, episode_name=episode_name, ledger=ledger)

