"""What does this video already have? Shared by on-demand requests and the autopilot.

One blocking function, ``inspect``: sidecars next to the video (target and source
language, any format and naming variant), the container's subtitle tracks (probe
cache first) and, from those, which source the autopilot would translate from.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from utils import storage
from utils.language_codes import variants_for

logger = logging.getLogger(__name__)

# Formats a player may load as a Greek subtitle; the scanner only knows srt/ass/ssa.
SIDECAR_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub", ".smi")

# Tags (besides the language) a sidecar name may carry between stem and extension.
_EXTRA_LANGUAGE_TOKENS = {"el": {"gr"}}


def scanner_for(engine, lang_code: str):
    """The engine's scanner when it already targets this language, else one that does."""
    if lang_code == engine.target_lang_code:
        return engine.scanner
    from integrations.subtitle_scanner import SubtitleScannerService
    return SubtitleScannerService(engine.source_lang_code, lang_code, include_ass=engine.scan_ass)


def no_track_reason(tracks) -> str:
    if not tracks:
        return "no_subtitles"
    if all(t.is_image for t in tracks):
        return "image_subtitles_only"
    return "no_source_language"


def probe_tracks(media_path: str, tools):
    """The container's subtitle tracks (probe cache first), or None without ffprobe."""
    from integrations import embedded_subs
    from integrations.media_sync_engine import MediaSyncEngine
    from utils import media_probe_cache

    if not tools:
        return None
    fingerprint = MediaSyncEngine._fingerprint(media_path)
    cached = media_probe_cache.get(media_path, fingerprint)
    if cached is not None:
        return [embedded_subs.SubtitleTrack.from_dict(t) for t in cached]
    tracks = embedded_subs.probe_subtitle_tracks(media_path, tools)
    media_probe_cache.put(media_path, fingerprint, embedded_subs.describe_candidates(tracks))
    return tracks


def find_language_sidecars(media_path: str, lang_code: str) -> List[str]:
    """Every subtitle file beside the video tagged with this language, in any supported format.

    Matches ``<stem>.<tag>[.<more tags>].<ext>`` for any variant of the language (``el``,
    ``ell``, ``gre``, ``greek``, ``gr``), so ``forced``/``sdh`` flavours count too.
    """
    media = Path(media_path)
    tags = {v.lower() for v in variants_for(lang_code)} | _EXTRA_LANGUAGE_TOKENS.get(lang_code, set())
    from integrations.subtitle_scanner import SubtitleScannerService
    stems = {media.stem.lower(), SubtitleScannerService._get_clean_stem(media).lower()}
    found: List[str] = []
    try:
        for f in sorted(media.parent.iterdir(), key=lambda p: p.name.lower()):
            name = f.name.lower()
            if not f.is_file() or f.suffix.lower() not in SIDECAR_EXTENSIONS:
                continue
            for stem in stems:
                if not name.startswith(stem + "."):
                    continue
                middle = name[len(stem) + 1:-len(f.suffix)]
                if any(tok in tags for tok in middle.split(".")):
                    found.append(str(f))
                    break
    except OSError as e:
        logger.warning(f"Could not list {media.parent}: {e}")
    return found


def _external_choice(scan) -> Optional[Dict]:
    """The best English sidecar: ASS before SRT (the scanner lists them richest first)."""
    for path in scan.source_subs or []:
        ext = Path(path).suffix.lower()
        if ext in (".ass", ".ssa"):
            return {"kind": "external_ass", "path": path}
        if ext == ".srt":
            return {"kind": "external_srt", "path": path}
    return None


def inspect(media_path: str, target_code: str = "el", source_code: str = "en", probe: bool = True,
            engine=None, dir_index: Optional[dict] = None, force_probe: bool = False) -> Dict:
    """Filesystem (+ optional container probe) facts for one media file. Blocking.

    Keys: ``reachable``, ``has_target`` (the scanner's same-format test), ``target_file``,
    ``target_paths`` (all target-language sidecars), ``has_source``, ``source_paths`` (source-language sidecars, ASS first), ``embedded_target`` /
    ``embedded_target_kind`` (``text``/``image``/None), ``embedded_source`` (a usable track),
    ``source_choice`` (``{"kind": embedded_ass|embedded_srt|external_ass|external_srt,
    "track"|"path"}`` or None) and ``reason`` (why there is nothing to translate from).
    ``force_probe`` probes the container even when the scanner already sees a target sidecar
    (the autopilot does so when that sidecar is OmbiSub's own and a new source is needed).
    """
    from integrations import embedded_subs

    info: Dict = {
        "reachable": False, "has_target": False, "target_file": None, "target_paths": [],
        "has_source": False, "source_paths": [], "embedded_target": None, "embedded_target_kind": None,
        "embedded_source": None, "source_choice": None, "reason": None,
    }
    if not media_path or not Path(media_path).exists():
        info["reason"] = "media_unreachable"
        return info
    info["reachable"] = True

    if engine is not None:
        scanner = scanner_for(engine, target_code)
    else:
        from integrations.subtitle_scanner import SubtitleScannerService
        scanner = SubtitleScannerService(source_code, target_code, include_ass=True)
    scan = scanner.scan_media_file(media_path, dir_index)
    info["has_target"] = bool(scan.has_target_sub)
    info["target_file"] = Path(scan.target_sub_path).name if scan.target_sub_path else None
    info["has_source"] = bool(scan.has_source_sub)
    info["source_paths"] = list(scan.source_subs or [])
    info["target_paths"] = find_language_sidecars(media_path, target_code)

    track = None
    if probe and (force_probe or not scan.has_target_sub):
        try:
            if engine is not None:
                tools = engine._get_embedded_tools()
                keywords = engine.embedded_keywords
                src = engine.source_lang_code
            else:
                config = storage.load_global_config()
                tools = embedded_subs.resolve_tools(config)
                keywords = embedded_subs.parse_keywords(config.get("embedded_deprioritize_keywords"))
                src = source_code
            tracks = probe_tracks(media_path, tools)
        except Exception as e:
            logger.warning(f"Probe failed for {media_path}: {e}")
            tracks = None
            info["reason"] = "probe_failed"
        if tracks is None:
            info["reason"] = info["reason"] or "ffmpeg_unavailable"
        else:
            wanted = {v.lower() for v in variants_for(target_code)}
            target_tracks = [t for t in tracks if (t.language or "").strip().lower() in wanted]
            if target_tracks:
                kind = "text" if any(t.is_supported_text for t in target_tracks) else "image"
                info["embedded_target"] = kind
                info["embedded_target_kind"] = kind
            track = embedded_subs.select_track(tracks, src, keywords)
            info["embedded_source"] = bool(track)
            if not track:
                info["reason"] = no_track_reason(tracks)

    if track is not None:
        info["source_choice"] = {"kind": "embedded_ass" if track.is_ass else "embedded_srt", "track": track.to_dict()}
    else:
        info["source_choice"] = _external_choice(scan)
    return info
