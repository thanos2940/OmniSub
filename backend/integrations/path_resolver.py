"""
PathResolver — Standalone path translation utility.

Converts remote/*arr paths to local Omnisub-accessible paths using
a list of {remote, local} prefix mappings. Critical for LAN deployments
where Sonarr/Radarr run on a different machine.
"""

import os
import re
import socket
from typing import List, Dict, Optional, Any


class PathResolver:
    """Translate remote media paths to locally-accessible paths."""

    def __init__(self, mappings: List[Dict[str, str]] = None):
        self.mappings = mappings or []

    def _try_unc_local_fallback(self, path_str: str) -> Optional[str]:
        """Attempt automatic dual UNC <-> Local Drive resolution."""
        if not path_str:
            return None

        # Pattern A (UNC single-letter drive share -> Local drive):
        # Matches: \\server\e\... or //server/e/...
        unc_m = re.match(r"^(?:\\\\|//)([^\\/]+)[\\/]([a-zA-Z])(?:\$(?:[\\/](.*))?|[\\/](.*)|$)", path_str)
        if unc_m:
            server = unc_m.group(1)
            drive = unc_m.group(2).upper()
            rest = unc_m.group(3) if unc_m.group(3) is not None else (unc_m.group(4) or "")
            rest_clean = rest.replace("/", "\\").lstrip("\\")
            candidate = f"{drive}:\\{rest_clean}" if rest_clean else f"{drive}:\\"

            try:
                candidate_exists = os.path.exists(candidate)
            except OSError:
                candidate_exists = False

            try:
                drive_root_exists = os.path.exists(f"{drive}:\\")
            except OSError:
                drive_root_exists = False

            try:
                unc_exists = os.path.exists(path_str)
            except OSError:
                unc_exists = False

            try:
                is_server_host = socket.gethostname().lower() == server.lower()
            except Exception:
                is_server_host = False

            if candidate_exists:
                return candidate
            if is_server_host:
                return candidate
            if (not unc_exists) and drive_root_exists:
                return candidate

        # Pattern B (Local drive -> UNC share from configured mappings):
        # Matches: E:\... or E:/...
        local_m = re.match(r"^([a-zA-Z]):[\\/](.*)$", path_str)
        if local_m:
            drive_letter = local_m.group(1).lower()
            rest = local_m.group(2).replace("/", "\\").lstrip("\\")

            try:
                local_exists = os.path.exists(path_str)
            except OSError:
                local_exists = False

            if not local_exists:
                for m in self.mappings:
                    for key in ("remote", "local"):
                        prefix = m.get(key, "")
                        unc_match = re.match(r"^(?:\\\\|//)([^\\/]+)[\\/]([a-zA-Z])(?:\$(?:[\\/].*)?|[\\/].*|$)", prefix)
                        if unc_match:
                            srv = unc_match.group(1)
                            for d_cand in (drive_letter, drive_letter.upper()):
                                unc_cand = f"\\\\{srv}\\{d_cand}\\{rest}" if rest else f"\\\\{srv}\\{d_cand}"
                                try:
                                    if os.path.exists(unc_cand) or os.path.exists(f"\\\\{srv}\\{d_cand}"):
                                        return unc_cand
                                except OSError:
                                    continue

        return None

    def resolve(self, remote_path: str) -> str:
        """Apply explicit mappings first with fallback to dual UNC/Local Drive resolution."""
        if not remote_path:
            return ""

        mapped_path = None
        for mapping in self.mappings:
            remote_prefix = mapping.get("remote", "")
            local_prefix = mapping.get("local", "")
            if not remote_prefix or not local_prefix:
                continue

            # Normalize to forward slashes for comparison
            norm_path = remote_path.replace("\\", "/").lower()
            norm_remote = remote_prefix.replace("\\", "/").lower().rstrip("/")

            if norm_path == norm_remote or norm_path.startswith(norm_remote + "/"):
                suffix = remote_path[len(norm_remote):]
                
                has_local_sep = local_prefix.endswith("/") or local_prefix.endswith("\\")
                has_suffix_sep = suffix.startswith("/") or suffix.startswith("\\")
                
                if not suffix:
                    translated = local_prefix
                elif not has_local_sep and not has_suffix_sep:
                    sep = "\\" if "\\" in local_prefix or (os.name == "nt" and "/" not in local_prefix) else "/"
                    translated = local_prefix + sep + suffix
                else:
                    translated = local_prefix + suffix

                if "\\" in local_prefix or (os.name == "nt" and "/" not in local_prefix):
                    translated = translated.replace("/", "\\")
                else:
                    translated = translated.replace("\\", "/")

                mapped_path = translated
                break

        if mapped_path is not None:
            try:
                if os.path.exists(mapped_path):
                    return mapped_path
            except OSError:
                pass

            # Fallback if mapped path does not exist on disk
            fallback = self._try_unc_local_fallback(mapped_path)
            if fallback:
                try:
                    if os.path.exists(fallback):
                        return fallback
                except OSError:
                    pass

            fallback_remote = self._try_unc_local_fallback(remote_path)
            if fallback_remote:
                try:
                    if os.path.exists(fallback_remote):
                        return fallback_remote
                except OSError:
                    pass

            return mapped_path

        # If no explicit mapping matched, try UNC/local fallback on remote_path
        fallback = self._try_unc_local_fallback(remote_path)
        if fallback:
            return fallback

        return remote_path

    def validate_mappings(self) -> Dict[str, Any]:
        """Validate configured mappings and check UNC <-> Local Drive auto-resolution health."""
        hostname = socket.gethostname()
        mapping_statuses = []
        for m in self.mappings:
            remote = m.get("remote", "")
            local = m.get("local", "")
            reachable = self.is_reachable(local)
            mapping_statuses.append({
                "remote": remote,
                "local": local,
                "reachable": reachable,
            })

        drives_detected = []
        if os.name == "nt":
            for char in "CDEFGHIJKLMNOPQRSTUVWXYZ":
                drive_path = f"{char}:\\"
                try:
                    if os.path.exists(drive_path):
                        drives_detected.append(drive_path)
                except OSError:
                    pass

        unc_auto_status = {
            "active": True,
            "hostname": hostname,
            "drives_detected": drives_detected,
            "status": "healthy",
        }

        all_reachable = all(ms["reachable"] for ms in mapping_statuses) if mapping_statuses else True

        return {
            "valid": all_reachable,
            "mappings": mapping_statuses,
            "unc_auto_resolution": unc_auto_status,
        }

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

