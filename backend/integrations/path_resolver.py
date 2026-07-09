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
        """Quick check — path exists or its parent is accessible."""
        try:
            from pathlib import Path
            p = Path(path)
            if str(p).startswith("\\\\"):
                parts = p.parts
                if len(parts) >= 2:
                    return (Path(parts[0]) / parts[1]).exists()
            return p.exists() or p.parent.exists()
        except OSError:
            return False
