"""
Embedded (muxed) ASS/SSA subtitle discovery and extraction.

Releases that carry typeset subtitles usually mux them into the container instead
of shipping a sidecar, which makes them invisible to ``subtitle_scanner`` (which
only ever looks at files on disk). This module closes that gap:

  probe_subtitle_tracks()  — ffprobe the container's subtitle streams (header read, fast)
  select_track()           — pure ranking function; no I/O, fully unit-testable
  extract_track()          — ffmpeg the chosen stream out as ASS text (async)
  sidecar_path_for()       — where the extracted track is written

The extracted track is written as a normal ``<stem>.<lang>.ass`` sidecar next to
the media file (plan decision D-A), so everything downstream — import, fingerprinting,
prune safety, export — is the existing sidecar path with no changes.

**Only ASS/SSA tracks are candidates** (D-B). Image-based subtitles (PGS/VobSub)
would need OCR, and embedded SRT is deliberately out of scope.

See docs/PLAN_embedded_ass_extraction.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from utils.language_codes import output_filename, variants_for

logger = logging.getLogger(__name__)

# Codecs we can turn into a subtitle sidecar.
ASS_CODECS = ("ass", "ssa")
SRT_CODECS = ("subrip", "srt", "text", "mov_text", "webvtt")
SUPPORTED_TEXT_CODECS = ASS_CODECS + SRT_CODECS

# Reported for diagnostics so the UI can say "found subtitles, but none usable"
# rather than staying silent. These need OCR and are never candidates.
IMAGE_CODECS = ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub")

DEFAULT_DEPRIORITIZE_KEYWORDS = "signs, songs, s&s, signs & songs, forced, commentary, karaoke"

# Header read only — if this takes longer than this, the share is in trouble.
PROBE_TIMEOUT_SECONDS = 120
# Extraction demuxes the WHOLE container (subtitle packets are interleaved across
# every cluster), so a large file on a remote share legitimately takes minutes.
EXTRACT_TIMEOUT_SECONDS = 3600

# Language tag values that mean "nobody said" — extremely common in fansub muxes.
_UNKNOWN_LANGS = {"", "und", "unknown", "none", "zxx"}


class FfmpegUnavailable(Exception):
    """ffmpeg/ffprobe could not be located. Raised so callers can surface an
    actionable message instead of silently no-opping."""


@dataclass(frozen=True)
class EmbeddedTools:
    """Resolved paths to the ffmpeg pair."""
    ffmpeg: str
    ffprobe: str


@dataclass
class SubtitleTrack:
    """One subtitle stream inside a media container."""
    index: int                      # absolute stream index (what -map 0:<index> takes)
    codec: str = ""
    language: str = ""
    title: str = ""
    forced: bool = False
    default: bool = False
    frames: int = 0                 # NUMBER_OF_FRAMES tag, 0 when the muxer didn't write one
    # Filled in by select_track for diagnostics / UI.
    penalized: bool = False
    penalty_reasons: List[str] = field(default_factory=list)

    @property
    def is_ass(self) -> bool:
        return self.codec.lower() in ASS_CODECS

    @property
    def is_srt(self) -> bool:
        return self.codec.lower() in SRT_CODECS

    @property
    def is_supported_text(self) -> bool:
        return self.codec.lower() in SUPPORTED_TEXT_CODECS

    @property
    def is_image(self) -> bool:
        return self.codec.lower() in IMAGE_CODECS

    @property
    def output_format(self) -> str:
        return "ass" if self.is_ass else "srt"

    @classmethod
    def from_dict(cls, data: Dict) -> "SubtitleTrack":
        """Rebuild from a ``to_dict`` payload (probe cache / queue options), ignoring
        any key this version no longer knows about."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})

    def to_dict(self) -> Dict:
        return {
            "index": self.index,
            "codec": self.codec,
            "language": self.language,
            "title": self.title,
            "forced": self.forced,
            "default": self.default,
            "frames": self.frames,
            "penalized": self.penalized,
            "penalty_reasons": list(self.penalty_reasons),
            "is_ass": self.is_ass,
            "is_srt": self.is_srt,
            "is_supported_text": self.is_supported_text,
            "output_format": self.output_format,
        }


# ---------------------------------------------------------------------------
# Tool resolution
# ---------------------------------------------------------------------------

def _candidate_names(base: str) -> List[str]:
    return [f"{base}.exe", base] if sys.platform == "win32" else [base]


def _find_beside(directory: Path, base: str) -> Optional[str]:
    for name in _candidate_names(base):
        candidate = directory / name
        if candidate.is_file():
            return str(candidate)
    return None


def resolve_tools(config: Optional[Dict] = None) -> Optional[EmbeddedTools]:
    """Locate the ffmpeg/ffprobe pair, or None when either is missing.

    ``ffmpeg_path`` may point at the executable itself or at the directory holding
    it; ffprobe is resolved as its sibling. With no setting we fall back to PATH,
    which is how the Docker image (and most Linux hosts) will find it.
    """
    config = config if config is not None else {}
    configured = (config.get("ffmpeg_path") or "").strip()

    ffmpeg = ffprobe = None
    if configured:
        p = Path(configured)
        directory = p if p.is_dir() else p.parent
        ffmpeg = str(p) if p.is_file() else _find_beside(directory, "ffmpeg")
        ffprobe = _find_beside(directory, "ffprobe")

    if not ffmpeg:
        ffmpeg = shutil.which("ffmpeg")
    if not ffprobe:
        ffprobe = shutil.which("ffprobe")

    if not ffmpeg or not ffprobe:
        return None
    return EmbeddedTools(ffmpeg=ffmpeg, ffprobe=ffprobe)


def tools_status(config: Optional[Dict] = None) -> Dict:
    """Availability summary for /api/health and the settings UI."""
    tools = resolve_tools(config)
    if not tools:
        return {"available": False, "ffmpeg": None, "ffprobe": None}
    return {"available": True, "ffmpeg": tools.ffmpeg, "ffprobe": tools.ffprobe}


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------

def _frames_from_tags(tags: Dict) -> int:
    """Read mkvmerge's NUMBER_OF_FRAMES statistics tag.

    The tag is sometimes language-suffixed (``NUMBER_OF_FRAMES-eng``), so match on
    the prefix rather than an exact key. Returns 0 when absent — selection treats
    that as "no information", not "empty track".
    """
    for key, value in (tags or {}).items():
        if str(key).lower().startswith("number_of_frames"):
            try:
                return int(str(value).strip())
            except (TypeError, ValueError):
                continue
    return 0


def parse_probe_output(raw: str) -> List[SubtitleTrack]:
    """Turn ffprobe JSON into SubtitleTrack objects. Split out from the subprocess
    call so the parsing is testable without ffmpeg installed."""
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as e:
        logger.warning(f"Could not parse ffprobe output: {e}")
        return []

    tracks: List[SubtitleTrack] = []
    for stream in payload.get("streams") or []:
        try:
            index = int(stream.get("index"))
        except (TypeError, ValueError):
            continue
        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}
        # Tag keys vary in case between muxers; normalise before lookup.
        lower_tags = {str(k).lower(): v for k, v in tags.items()}
        tracks.append(
            SubtitleTrack(
                index=index,
                codec=str(stream.get("codec_name") or ""),
                language=str(lower_tags.get("language") or "").strip().lower(),
                title=str(lower_tags.get("title") or "").strip(),
                forced=bool(disposition.get("forced")),
                default=bool(disposition.get("default")),
                frames=_frames_from_tags(tags),
            )
        )
    return tracks


_PROBE_STAT_CACHE: Dict[str, Tuple[float, int, List[SubtitleTrack]]] = {}


def probe_subtitle_tracks(media_path: str, tools: Optional[EmbeddedTools] = None,
                          config: Optional[Dict] = None,
                          force_refresh: bool = False) -> List[SubtitleTrack]:
    """List the subtitle streams in ``media_path``.

    Reads container headers only, so this is cheap even over a network share.
    Caches results against file mtime and size for instant repeated lookups.
    Returns [] (never raises) when ffmpeg is missing, the file is unreadable, or
    ffprobe fails — an unprobeable file is simply one with no candidates.
    """
    if not force_refresh:
        try:
            st = os.stat(media_path)
            cached = _PROBE_STAT_CACHE.get(media_path)
            if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
                return cached[2]
        except Exception:
            pass

    tools = tools or resolve_tools(config)
    if not tools:
        return []

    cmd = [
        tools.ffprobe, "-v", "error",
        "-select_streams", "s",
        "-show_entries", "stream=index,codec_name,codec_type,disposition:stream_tags",
        "-of", "json",
        media_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"ffprobe timed out for {media_path}")
        return []
    except OSError as e:
        logger.warning(f"ffprobe could not run for {media_path}: {e}")
        return []

    if result.returncode != 0:
        logger.debug(f"ffprobe exited {result.returncode} for {media_path}: {result.stderr!r}")
        return []

    tracks = parse_probe_output(result.stdout)
    try:
        st = os.stat(media_path)
        _PROBE_STAT_CACHE[media_path] = (st.st_mtime, st.st_size, tracks)
    except Exception:
        pass
    return tracks


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def parse_keywords(raw: Optional[str]) -> List[str]:
    """Split the user's comma-separated deprioritize list into lowercase terms."""
    if raw is None:
        raw = DEFAULT_DEPRIORITIZE_KEYWORDS
    return [part.strip().lower() for part in str(raw).split(",") if part.strip()]


def _language_rank(track: SubtitleTrack, source_lang_code: str) -> Optional[int]:
    """0 = tag matches the source language, 1 = untagged/unknown, None = drop.

    A track explicitly tagged as some *other* language is dropped: that is a wrong
    language, not a weaker candidate. Untagged tracks are kept because fansub muxes
    frequently omit the tag entirely.
    """
    lang = (track.language or "").strip().lower()
    if lang in _UNKNOWN_LANGS:
        return 1
    if lang in {v.lower() for v in variants_for(source_lang_code)}:
        return 0
    return None


def _penalties(track: SubtitleTrack, keywords: Sequence[str]) -> List[str]:
    """Reasons this track looks partial (signs/songs/forced) rather than full dialogue."""
    reasons: List[str] = []
    title = (track.title or "").lower()
    for kw in keywords:
        if kw and kw in title:
            reasons.append(f"title contains '{kw}'")
    if track.forced:
        reasons.append("forced disposition")
    return reasons


def select_track(
    tracks: Sequence[SubtitleTrack],
    source_lang_code: str = "en",
    keywords: Optional[Sequence[str]] = None,
    prefer_ass: bool = False,
) -> Optional[SubtitleTrack]:
    """Pick the best subtitle track (ASS/SSA or SRT/SubRip), or None when there is no usable candidate.

    Signs/songs and forced tracks are **ranked last, never excluded** (plan decision
    D-C): some releases ship a single track that carries signs, songs *and* dialogue
    under a "Signs & Songs" title, and dropping those would make the episode silently
    unavailable. A full dialogue track always outranks them when one exists.

    Ranking, ascending:
      1. language match (exact source language > untagged)
      2. format preference (ASS gets 0, SRT gets 1 so ASS is chosen if both exist)
      3. full-dialogue-looking (penalized signs/songs/forced get 1, normal gets 0)
      4. cue / frame count (more events > fewer events)
      5. default disposition (default gets 0, non-default gets 1)
      6. stream index (lowest index first)
    """
    keywords = parse_keywords(None) if keywords is None else keywords

    ranked = []
    for track in tracks:
        if not track.is_supported_text:
            continue
        lang_rank = _language_rank(track, source_lang_code)
        if lang_rank is None:
            continue
        reasons = _penalties(track, keywords)
        track.penalized = bool(reasons)
        track.penalty_reasons = reasons
        format_rank = 0 if track.is_ass else 1
        key = (
            lang_rank,
            format_rank,
            1 if reasons else 0,
            -track.frames,
            0 if track.default else 1,
            track.index,
        )
        ranked.append((key, track))

    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0])
    return ranked[0][1]


def describe_candidates(tracks: Sequence[SubtitleTrack]) -> List[Dict]:
    """Serialisable summary of every subtitle stream found, for episode metadata."""
    return [t.to_dict() for t in tracks]


def analyze_tracks_for_ui(
    tracks: Sequence[SubtitleTrack],
    source_lang_code: str = "en",
    keywords: Optional[Sequence[str]] = None,
    prefer_ass: bool = False,
) -> Dict:
    """Provide a comprehensive diagnostic analysis of all subtitle tracks in a container.

    Enriches each track with `is_ass`, `is_srt`, `is_image`, `output_format`, `penalized`,
    `penalty_reasons`, `candidate`, and `is_recommended`, and returns the chosen `recommended_stream_index`.
    """
    keywords = parse_keywords(None) if keywords is None else keywords
    best_track = select_track(tracks, source_lang_code, keywords, prefer_ass=prefer_ass)
    recommended_idx = best_track.index if best_track else None

    analyzed = []
    for t in tracks:
        lang_rank = _language_rank(t, source_lang_code)
        is_candidate = (t.is_supported_text and lang_rank is not None)
        reasons = _penalties(t, keywords) if t.is_supported_text else []
        track_dict = t.to_dict()
        track_dict.update({
            "is_ass": t.is_ass,
            "is_srt": t.is_srt,
            "is_image": t.is_image,
            "output_format": t.output_format,
            "candidate": is_candidate,
            "penalized": bool(reasons),
            "penalty_reasons": reasons,
            "is_recommended": (t.index == recommended_idx),
        })
        analyzed.append(track_dict)

    return {
        "tracks": analyzed,
        "recommended_stream_index": recommended_idx,
        "total_tracks": len(tracks),
        "text_tracks_count": sum(1 for t in tracks if t.is_supported_text),
        "ass_tracks_count": sum(1 for t in tracks if t.is_ass),
        "srt_tracks_count": sum(1 for t in tracks if t.is_srt),
        "image_tracks_count": sum(1 for t in tracks if t.is_image),
        "has_candidate": recommended_idx is not None,
    }


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def sidecar_path_for(media_path: str, source_lang_code: str = "en", ext: str = "ass") -> Path:
    """Where an extracted track is written: ``<media stem>.<lang>.<ext>`` beside the media.

    Uses the same ``output_filename`` helper the exporter uses, so the name the
    scanner will later match (it strips a trailing language tag from the media stem
    the same way) and the name the exporter derives stay in agreement.
    """
    media = Path(media_path)
    clean_ext = ext.lstrip(".").lower()
    return media.parent / output_filename(media.stem, source_lang_code, clean_ext)


def _decode(raw: bytes) -> str:
    """ASS and SRT out of a container is UTF-8; tolerate a BOM and never hard-fail on a
    stray byte — a mangled character is recoverable, losing the whole track isn't."""
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _run_sync_cmd(cmd: List[str], timeout: int) -> tuple:
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise asyncio.TimeoutError()


async def _spawn(cmd: List[str], timeout: int) -> tuple:
    """Run ``cmd``, returning (returncode, stdout_bytes, stderr_bytes).

    Runs in a thread to ensure seamless compatibility with Windows and Unix event loops.
    """
    return await asyncio.to_thread(_run_sync_cmd, cmd, timeout)


async def extract_track(
    media_path: str,
    track: SubtitleTrack,
    tools: Optional[EmbeddedTools] = None,
    config: Optional[Dict] = None,
    timeout: int = EXTRACT_TIMEOUT_SECONDS,
) -> str:
    """Extract one subtitle stream as ASS or SRT text.

    For ASS streams, ``-c:s copy`` keeps original styles and typeset events intact.
    For SRT / subrip streams, ``-c:s srt -f srt`` extracts clean SRT cues. Output goes
    to stdout, so no temp file is involved.
    """
    tools = tools or resolve_tools(config)
    if not tools:
        raise FfmpegUnavailable(
            "ffmpeg was not found. Set its path in Settings to extract embedded subtitles."
        )

    out_fmt = "ass" if track.is_ass else "srt"
    codec_args = ["-c:s", "copy"] if track.is_ass else ["-c:s", "srt"]

    cmd = [
        tools.ffmpeg, "-v", "error", "-nostdin", "-y",
        "-i", media_path,
        "-map", f"0:{track.index}",
        *codec_args,
        "-f", out_fmt,
        "pipe:1",
    ]
    returncode, stdout, stderr = await _spawn(cmd, timeout)
    if returncode != 0:
        raise RuntimeError(
            f"ffmpeg exited with code {returncode} extracting stream {track.index} "
            f"from {media_path}: {_decode(stderr or b'')[:500]}"
        )

    content = _decode(stdout or b"")
    if not content.strip():
        raise RuntimeError(
            f"ffmpeg produced no subtitle data for stream {track.index} of {media_path}."
        )
    return content


def looks_like_usable_ass(content: str) -> bool:
    """Cheap sanity gate before an ASS sidecar is written: an ASS document with at least
    one dialogue event. Guards against writing a valid-but-empty header."""
    if not content or "[Events]" not in content:
        return False
    return any(
        line.lstrip().lower().startswith("dialogue:")
        for line in content.splitlines()
    )


def looks_like_usable_srt(content: str) -> bool:
    """Cheap sanity gate before an SRT sidecar is written: contains at least one timecode delimiter."""
    if not content or "-->" not in content:
        return False
    return True


def looks_like_usable_sub(content: str, ext: str = "ass") -> bool:
    """Validate subtitle content for the target format."""
    clean_ext = ext.lstrip(".").lower()
    if clean_ext in ("ass", "ssa"):
        return looks_like_usable_ass(content)
    return looks_like_usable_srt(content)


def write_sidecar(path: Path, content: str) -> None:
    """Write the extracted track atomically.

    A half-written sidecar is worse than none: the scanner would import it on the
    next pass and fingerprint the truncated content as the real source. Write to a
    temp file in the same directory, then replace with retries for Windows SMB oplocks.
    """
    import time
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.omnisub-tmp-{os.getpid()}")
    try:
        tmp.write_text(content, encoding="utf-8-sig")
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except (PermissionError, OSError):
                if attempt == 4:
                    path.write_text(content, encoding="utf-8-sig")
                else:
                    time.sleep(0.25)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
