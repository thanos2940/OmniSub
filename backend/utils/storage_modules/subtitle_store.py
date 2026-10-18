"""
Original Subtitle File Storage (SRT, ASS, SSA).
"""

import logging
from pathlib import Path
from typing import Optional, Tuple

from utils.storage_modules.atomic_io import _safe_episode_dir

logger = logging.getLogger(__name__)


def save_original_subtitle(project_name: str, episode_name: str, content: str, filename: Optional[str] = None) -> None:
    """Save the original subtitle using its true original file extension (e.g. original.ass).
    
    Check for its presence under any valid extension (.srt, .ass, .ssa),
    and save the format mapping in metadata (using original_extension and original_format keys).
    """
    from utils.storage_modules.episode_store import load_episode_metadata, save_episode_metadata

    ep_dir = _safe_episode_dir(project_name, episode_name)
    ep_dir.mkdir(parents=True, exist_ok=True)

    ext = "srt"
    if filename:
        lower_fn = filename.lower()
        if lower_fn.endswith(".srt"):
            ext = "srt"
        elif lower_fn.endswith(".ass"):
            ext = "ass"
        elif lower_fn.endswith(".ssa"):
            ext = "ssa"
        else:
            from utils.subtitle_io import detect_format
            ext = detect_format(content=content)
    else:
        # Content sniffing wins over any original.<ext> already on disk: re-saving
        # different content (e.g. an SRT-reconstructed source over a former .ass)
        # must land in the file matching the new content, not inherit the stale one.
        from utils.subtitle_io import detect_format
        ext = detect_format(content=content)

    with open(ep_dir / f"original.{ext}", 'w', encoding='utf-8') as f:
        f.write(content)

    # Clean up other extensions
    for possible_ext in ["srt", "ass", "ssa"]:
        if possible_ext != ext:
            try:
                (ep_dir / f"original.{possible_ext}").unlink(missing_ok=True)
            except Exception:
                pass

    # Save the format mapping in metadata
    meta = load_episode_metadata(project_name, episode_name) or {}
    meta["original_extension"] = ext
    meta["original_format"] = "ass" if ext in ["ass", "ssa"] else "srt"
    save_episode_metadata(project_name, episode_name, meta, update_stats=True)


def save_original_srt(project_name: str, episode_name: str, content: str, filename: Optional[str] = None) -> None:
    """Save original SRT file content (delegates to generalized helper)."""
    save_original_subtitle(project_name, episode_name, content, filename=filename)


def load_original_subtitle(project_name: str, episode_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Load original subtitle file content and its extension, returning (content, extension)."""
    from utils.storage_modules.episode_store import load_episode_metadata

    meta = load_episode_metadata(project_name, episode_name)
    ext = None
    if meta:
        ext = meta.get("original_extension")

    ep_dir = _safe_episode_dir(project_name, episode_name)

    if ext in ["srt", "ass", "ssa"]:
        subtitle_file = ep_dir / f"original.{ext}"
        if subtitle_file.exists():
            with open(subtitle_file, 'r', encoding='utf-8') as f:
                return f.read(), ext

    # Fall back to checking files on disk
    for possible_ext in ["srt", "ass", "ssa"]:
        subtitle_file = ep_dir / f"original.{possible_ext}"
        if subtitle_file.exists():
            with open(subtitle_file, 'r', encoding='utf-8') as f:
                return f.read(), possible_ext

    return None, None


def load_original_srt(project_name: str, episode_name: str) -> Optional[str]:
    """Load original subtitle file content (supporting srt, ass, ssa)."""
    content, _ = load_original_subtitle(project_name, episode_name)
    return content


def original_subtitle_exists(project_name: str, episode_name: str) -> bool:
    """Check if an original subtitle file exists with any valid extension (srt, ass, ssa)."""
    from utils.storage_modules.episode_store import load_episode_metadata

    ep_dir = _safe_episode_dir(project_name, episode_name)
    # Check metadata first
    meta = load_episode_metadata(project_name, episode_name)
    if meta:
        ext = meta.get("original_extension")
        if ext in ["srt", "ass", "ssa"] and (ep_dir / f"original.{ext}").exists():
            return True

    # Fallback to checking disk
    for possible_ext in ["srt", "ass", "ssa"]:
        if (ep_dir / f"original.{possible_ext}").exists():
            return True
    return False


def target_subtitle_exists(project_name: str, episode_name: str, lang_code: Optional[str] = None) -> bool:
    """Check if target subtitle exists in storage or on disk for this episode (Plan BE-P1-06)."""
    from utils.storage_modules.episode_store import load_episode_metadata, episode_has_target
    import json

    ep_dir = _safe_episode_dir(project_name, episode_name)
    meta = load_episode_metadata(project_name, episode_name)
    if episode_has_target(meta, lang_code=lang_code):
        return True
    if meta and (meta.get("translated") or meta.get("translation_status") == "completed" or meta.get("arr_has_target")):
        return True

    # Check disk inside episode folder
    target_files = ["target.srt", "target.ass", "target.ssa"]
    if lang_code:
        target_files.extend([f"target.{lang_code}.srt", f"target.{lang_code}.ass", f"target.{lang_code}.ssa"])
    for tf in target_files:
        if (ep_dir / tf).exists():
            return True

    # Check if data.json has translated cues (fast text probe first before full JSON parse)
    data_file = ep_dir / "data.json"
    if data_file.exists():
        try:
            with open(data_file, "r", encoding="utf-8") as f:
                content = f.read(65536)
                if '"translated": "' in content or '"translations": {' in content:
                    f.seek(0)
                    d = json.load(f)
                    if any(line.get("translated") for line in d.get("data", [])):
                        return True
        except Exception:
            pass
    return False
