"""
PathResolver — Standalone path translation utility.

Converts remote/*arr paths to local Omnisub-accessible paths using
a list of {remote, local} prefix mappings. Critical for LAN deployments
where Sonarr/Radarr run on a different machine.
"""

import os
from typing import List, Dict


class PathResolver:
    """Translate remote media paths to locally-accessible paths."""

    def __init__(self, mappings: List[Dict[str, str]] = None):
        self.mappings = mappings or []

    def resolve(self, remote_path: str) -> str:
        """Apply the first matching prefix mapping to remote_path.

        If no mapping matches, return remote_path unchanged.
        """
        if not remote_path:
            return ""

        for mapping in self.mappings:
            remote_prefix = mapping.get("remote", "")
            local_prefix = mapping.get("local", "")
            if not remote_prefix or not local_prefix:
                continue

            # Normalize to forward slashes for comparison
            norm_path = remote_path.replace("\\", "/").lower()
            norm_remote = remote_prefix.replace("\\", "/").lower().rstrip("/")

            # Require a path-segment boundary right after the prefix — a plain
            # startswith would let remote="/data" also match "/database/...",
            # producing a wrong (but plausible-looking) translated path.
            if norm_path == norm_remote or norm_path.startswith(norm_remote + "/"):
                suffix = remote_path[len(norm_remote):]
                
                # Check if we need to insert a directory separator between local_prefix and suffix
                has_local_sep = local_prefix.endswith("/") or local_prefix.endswith("\\")
                has_suffix_sep = suffix.startswith("/") or suffix.startswith("\\")
                
                if not suffix:
                    translated = local_prefix
                elif not has_local_sep and not has_suffix_sep:
                    sep = "\\" if "\\" in local_prefix or (os.name == "nt" and "/" not in local_prefix) else "/"
                    translated = local_prefix + sep + suffix
                else:
                    translated = local_prefix + suffix

                # Keep slash style consistent with the local prefix
                if "\\" in local_prefix or (os.name == "nt" and "/" not in local_prefix):
                    translated = translated.replace("/", "\\")
                else:
                    translated = translated.replace("\\", "/")

                return translated

        return remote_path

    def is_reachable(self, path: str) -> bool:
        """Quick check — path exists, its parent is accessible, or root UNC share/drive is reachable."""
        if not path:
            return False
        try:
            from pathlib import Path
            p = Path(path)
            # Windows UNC network share path (e.g. \\server\share\...)
            if str(p).startswith("\\\\"):
                parts = p.parts
                if parts:
                    return Path(parts[0]).exists()
                return False
            # Standard existence check for local path or immediate parent
            if p.exists() or p.parent.exists():
                return True
            # Windows drive letter paths (e.g. Z:\media\...) - test drive root directly
            if p.drive and p.parts:
                return Path(p.parts[0]).exists()
            return False
        except OSError:
            return False

    def find_media_file(self, media_path: str, episode_name: str = None) -> str:
        """Resolve remote media_path, verifying existence or discovering renamed/upgraded files.

        If the file at the resolved path does not exist, but its parent directory exists,
        searches the parent directory for a video file matching episode_name.
        Returns the resolved file path string if found, or None.
        """
        if not media_path:
            return None

        resolved = self.resolve(media_path)
        from pathlib import Path
        try:
            p = Path(resolved)
            if p.exists() and p.is_file():
                return str(p)

            # If direct path doesn't exist, check parent directory for upgraded/renamed release matching episode
            if episode_name and p.parent.exists():
                import re
                video_exts = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov"}
                pattern = rf"(?i)(?:\b|_){re.escape(episode_name)}(?:\b|_)"
                for f in p.parent.iterdir():
                    if f.is_file() and f.suffix.lower() in video_exts:
                        if re.search(pattern, f.name) or episode_name.lower() in f.name.lower():
                            return str(f)
        except OSError:
            pass

        return None

