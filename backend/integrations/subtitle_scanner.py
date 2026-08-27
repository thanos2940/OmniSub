import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from utils.language_codes import matches_language as _matches_language

logger = logging.getLogger(__name__)

# Common subtitle file extensions
_SRT_EXTENSIONS = (".srt", ".ass", ".ssa")

@dataclass
class ScanResult:
    """Result of scanning a single media file for subtitles."""
    media_path: str
    has_source_sub: bool          # English .en.srt found
    has_target_sub: bool          # Target language .el.srt found
    source_sub_path: Optional[str] = None      # primary source (richest format)
    target_sub_path: Optional[str] = None
    source_subs: List[str] = field(default_factory=list)  # ALL source-language files, primary first

class SubtitleScannerService:
    """Detect subtitle presence by inspecting the filesystem directly.

    Args:
        source_lang_code: ISO 639-1 code for source language (default "en")
        target_lang_code: ISO 639-1 code for target language (default "el")
        include_ass: Whether to scan for .ass / .ssa subtitle files alongside .srt
    """

    def __init__(
        self,
        source_lang_code: str = "en",
        target_lang_code: str = "el",
        include_ass: bool = True,
    ):
        self.source_lang_code = source_lang_code
        self.target_lang_code = target_lang_code
        self.include_ass = include_ass

    @property
    def supported_extensions(self):
        return (".srt", ".ass", ".ssa") if self.include_ass else (".srt",)


    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan_media_file(self, media_path: str, dir_index: Optional[dict] = None) -> ScanResult:
        """Scan for subtitles adjacent to a specific media file.

        Looks for files matching the stem and source/target language variants.
        If dir_index is provided (a mapping of stem -> ScanResult), it attempts
        to resolve from the pre-computed index first.
        """
        if dir_index is not None:
            media = Path(media_path)
            stem = self._get_clean_stem(media)
            if stem in dir_index:
                # Return the pre-scanned result (using the correct media_path from dir_index)
                return dir_index[stem]

        result = ScanResult(media_path=media_path, has_source_sub=False, has_target_sub=False)

        try:
            media = Path(media_path)
            if not media.parent.exists():
                logger.debug(f"Media directory unreachable: {media.parent}")
                return result

            stem = self._get_clean_stem(media)

            # Look through all files in the directory (same-format target per D2)
            files = [f for f in media.parent.iterdir() if f.is_file()]
            src_path, _src_ext, tgt_path, src_all = self._match_source_target(files, stem)
            if src_path:
                result.has_source_sub = True
                result.source_sub_path = src_path
                result.source_subs = src_all
            if tgt_path:
                result.has_target_sub = True
                result.target_sub_path = tgt_path

        except Exception as e:
            logger.warning(f"Subtitle scan failed for {media_path}: {e}")

        return result

    def scan_directory_index(self, dir_path: str) -> dict:
        """Scan a directory once and build a mapping of clean stems to ScanResults."""
        results = {}
        video_exts = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov"}
        try:
            directory = Path(dir_path)
            if not directory.exists():
                return results

            # List directory once
            files = [f for f in directory.iterdir() if f.is_file()]
            
            # Find all video files
            video_files = [f for f in files if f.suffix.lower() in video_exts]
            
            for vf in video_files:
                stem = self._get_clean_stem(vf)
                res = ScanResult(media_path=str(vf), has_source_sub=False, has_target_sub=False)
                
                # Match against all files in the directory (same-format target per D2)
                src_path, _src_ext, tgt_path, src_all = self._match_source_target(files, stem)
                if src_path:
                    res.has_source_sub = True
                    res.source_sub_path = src_path
                    res.source_subs = src_all
                if tgt_path:
                    res.has_target_sub = True
                    res.target_sub_path = tgt_path
                results[stem] = res
        except Exception as e:
            logger.error(f"Directory index scan failed for {dir_path}: {e}")
        return results

    def scan_directory(self, dir_path: str) -> List[ScanResult]:
        """Scan all video files in a directory for subtitle presence.

        Only processes common video extensions. Does not recurse into
        subdirectories — series season folders should be scanned individually.
        """
        video_exts = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov"}
        results = []
        try:
            index = self.scan_directory_index(dir_path)
            results = list(index.values())
        except Exception as e:
            logger.error(f"Directory scan failed for {dir_path}: {e}")

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # Format preference for the "primary" source when several formats coexist for one
    # media file. Richest-first so the deterministic single pick keeps the typeset track;
    # also the order in which secondaries are returned for dual-format import.
    _EXT_PRIORITY = {".ass": 0, ".ssa": 1, ".srt": 2}

    def same_format_target(self, files, stem, ext: str) -> Optional[str]:
        """Find the target-language file whose extension matches the provided ext."""
        if not ext.startswith('.'):
            ext = "." + ext
        ext = ext.lower()
        matched = []
        for f in files:
            name = Path(str(f)).name
            if _matches_language(name, stem, self.target_lang_code) and Path(name).suffix.lower() == ext:
                matched.append(str(f))
        if matched:
            # Sort deterministically: exact code tag first, then alphabetical
            def sort_key(filepath):
                name = Path(filepath).name.lower()
                exact_match = False
                pattern = rf"[._]{re.escape(self.target_lang_code.lower())}[._]"
                if re.search(pattern, name) or name.endswith(f".{self.target_lang_code.lower()}{ext}"):
                    exact_match = True
                return (0 if exact_match else 1, filepath.lower())
            matched.sort(key=sort_key)
            return matched[0]
        return None

    def _match_source_target(self, files, stem):
        """Resolve (primary_source, primary_ext, target_path, all_source_paths).

        Source: every file matching the source language, ordered richest-format-first
        (.ass > .ssa > .srt) so the pick is deterministic when multiple source formats
        coexist for one media file (previously it was filesystem-iteration order).
        Target detection prefers the SAME format as the primary source (decision D2): a
        .ass source only counts a .ass target as already-translated, so a leftover .srt
        can't suppress the .ass translation. With no source present, any target ext works.
        """
        source_matches = []  # (path, ext)
        target_matches = []
        for f in files:
            name = Path(str(f)).name
            ext = Path(name).suffix.lower()
            if ext not in self.supported_extensions:
                continue
            if _matches_language(name, stem, self.source_lang_code):
                source_matches.append((str(f), ext))
            if _matches_language(name, stem, self.target_lang_code):
                target_matches.append(str(f))

        source_matches.sort(key=lambda pe: (self._EXT_PRIORITY.get(pe[1], 99), pe[0].lower()))
        source_paths = [p for p, _ in source_matches]
        source_path = source_paths[0] if source_paths else None
        source_ext = source_matches[0][1] if source_matches else None

        if source_ext:
            target_path = self.same_format_target(files, stem, source_ext)
        else:
            if target_matches:
                # Sort target matches deterministically by exact code/alphabetical
                def sort_key_any(filepath):
                    name = Path(filepath).name.lower()
                    f_ext = Path(name).suffix.lower()
                    exact_match = False
                    pattern = rf"[._]{re.escape(self.target_lang_code.lower())}[._]"
                    if re.search(pattern, name) or name.endswith(f".{self.target_lang_code.lower()}{f_ext}"):
                        exact_match = True
                    return (0 if exact_match else 1, filepath.lower())
                target_matches.sort(key=sort_key_any)
                target_path = target_matches[0]
            else:
                target_path = None
        return source_path, source_ext, target_path, source_paths

    @staticmethod
    def _get_clean_stem(media: Path) -> str:
        """Extract subtitle-compatible stem from a media filename.

        Strips the video extension and any existing language suffix so that:
            Show.S01E01.mkv         → Show.S01E01
            Show.S01E01.en.mkv      → Show.S01E01   (edge case, rare)
        """
        stem = media.stem
        # Strip trailing 2-3 letter language code if present (e.g. .en, .eng)
        stem = re.sub(r'\.[a-z]{2,3}$', '', stem, flags=re.IGNORECASE)
        return stem
