"""
Bazarr Integration — API client for discovering missing subtitles.

Connects to Bazarr's API to find episodes and movies that have English
subtitles but are missing Greek (or other target language) subtitles.
Reads subtitle files from disk and delivers translated SRT files next to
the original media for Plex/Jellyfin auto-detection.

Bazarr API reference:
    GET /api/series                   — list all series
    GET /api/episodes?seriesid=X      — episodes for a series
    GET /api/movies                   — list all movies
    GET /api/system/languages         — available languages
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# ISO 639-2 (3-letter) to ISO 639-1 (2-letter) mapping for common languages
_LANG3_TO_LANG2 = {
    "eng": "en", "ell": "el", "gre": "el", "spa": "es", "fra": "fr",
    "fre": "fr", "deu": "de", "ger": "de", "ita": "it", "por": "pt",
    "rus": "ru", "jpn": "ja", "kor": "ko", "zho": "zh", "chi": "zh",
    "ara": "ar", "tur": "tr", "pol": "pl", "nld": "nl", "dut": "nl",
    "swe": "sv", "nor": "no", "dan": "da", "fin": "fi", "hun": "hu",
    "ces": "cs", "cze": "cs", "ron": "ro", "rum": "ro", "tha": "th",
    "vie": "vi", "ind": "id", "heb": "he", "bul": "bg", "hrv": "hr",
    "srp": "sr", "slk": "sk", "slo": "sk", "ukr": "uk", "cat": "ca",
}

# Full language name to ISO 639-1
LANG_NAME_TO_CODE = {
    "Greek": "el", "English": "en", "Spanish": "es", "French": "fr",
    "German": "de", "Italian": "it", "Portuguese": "pt", "Russian": "ru",
    "Japanese": "ja", "Korean": "ko", "Chinese": "zh", "Arabic": "ar",
    "Turkish": "tr", "Polish": "pl", "Dutch": "nl", "Swedish": "sv",
    "Norwegian": "no", "Danish": "da", "Finnish": "fi", "Hungarian": "hu",
    "Czech": "cs", "Romanian": "ro", "Thai": "th", "Vietnamese": "vi",
    "Indonesian": "id", "Hebrew": "he", "Bulgarian": "bg", "Croatian": "hr",
    "Serbian": "sr", "Slovak": "sk", "Ukrainian": "uk", "Catalan": "ca",
}


@dataclass
class BazarrConfig:
    """Configuration for Bazarr integration."""
    base_url: str = "http://localhost:6767"
    api_key: str = ""
    target_language: str = "Greek"          # Full name as used in OmbiSub
    source_language_code: str = "en"        # ISO 639-1
    enabled: bool = False
    poll_interval_minutes: int = 30
    auto_translate: bool = True             # Translate automatically or queue for review
    media_types: str = "both"               # "series", "movies", "both"
    path_mappings: List[Dict[str, str]] = field(default_factory=list) # [{"remote": "E:\\", "local": "\\\\Server\\e\\"}]

    def translate_path(self, remote_path: str) -> str:
        """Translate a remote path from Bazarr to a local path based on mappings."""
        if not remote_path:
            return ""
            
        for mapping in self.path_mappings:
            remote = mapping.get("remote", "")
            local = mapping.get("local", "")
            if remote and local and remote_path.lower().startswith(remote.lower()):
                # Case-insensitive replacement of the prefix
                translated = local + remote_path[len(remote):]
                # Normalize slashes (Bazarr might use \ on Windows or / on Linux)
                # If we are on Windows, we want \. If the mapping uses \, we follow.
                if "\\" in local:
                    translated = translated.replace("/", "\\")
                else:
                    translated = translated.replace("\\", "/")
                return translated
                
        return remote_path

    @property
    def normalized_url(self) -> str:
        """Base URL without trailing slash."""
        return self.base_url.rstrip('/')

    @property
    def target_language_code2(self) -> str:
        """ISO 639-1 code for target language."""
        return LANG_NAME_TO_CODE.get(self.target_language, "el")

    @property
    def target_language_code3(self) -> str:
        """ISO 639-2/3 code for target language (used by Bazarr internally)."""
        code2 = self.target_language_code2
        # Reverse lookup
        for k, v in _LANG3_TO_LANG2.items():
            if v == code2:
                return k
        return code2  # fallback


@dataclass
class MissingSubtitleItem:
    """An episode or movie that needs translation."""
    title: str                    # Show or movie name
    media_type: str               # "series" or "movie"
    media_path: str               # Path to the video file
    english_sub_path: str         # Path to the English .srt file
    season: int = 0               # Season number (series only)
    episode: int = 0              # Episode number (series only)
    episode_title: str = ""       # Episode title
    sonarr_series_id: Optional[int] = None
    sonarr_episode_id: Optional[int] = None
    radarr_id: Optional[int] = None
    series_type: str = ""         # "anime", "standard", etc.


def _sub_code(sub: Dict) -> str:
    """Extract a 2-letter language code from a Bazarr subtitle entry."""
    # Bazarr uses code2 (2-letter) and code3 (3-letter) fields
    code2 = sub.get("code2", "")
    if code2:
        return code2.lower()
    code3 = sub.get("code3", "")
    return _LANG3_TO_LANG2.get(code3.lower(), code3.lower())


async def test_connection(config: BazarrConfig) -> Dict:
    """Test connection to Bazarr and return system info.

    Returns:
        Dict with keys: connected (bool), version (str), error (str or None)
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{config.normalized_url}/api/system/status",
                headers={"X-API-KEY": config.api_key},
            )
            if resp.status_code == 401:
                return {"connected": False, "error": "Invalid API key"}
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "connected": True,
                    "version": data.get("data", {}).get("bazarr_version", "unknown"),
                    "error": None,
                }
            return {"connected": False, "error": f"HTTP {resp.status_code}"}
    except httpx.ConnectError:
        return {"connected": False, "error": f"Cannot connect to {config.base_url}"}
    except Exception as e:
        return {"connected": False, "error": str(e)}


async def get_missing_subtitles(config: BazarrConfig) -> List[MissingSubtitleItem]:
    """Find episodes and movies that have English subs but no target language subs.

    Queries Bazarr's wanted endpoints which list items where Bazarr has been
    unable to find subtitles for configured languages.

    Returns a list of MissingSubtitleItem objects ready for translation.
    """
    items: List[MissingSubtitleItem] = []
    target_code = config.target_language_code2
    headers = {"X-API-KEY": config.api_key}

    async with httpx.AsyncClient(timeout=30.0) as client:
        # --- Series ---
        if config.media_types in ("series", "both"):
            try:
                items.extend(
                    await _get_missing_series_subs(client, config, headers, target_code)
                )
            except Exception as e:
                logger.error(f"Bazarr series scan failed: {e}")

        # --- Movies ---
        if config.media_types in ("movies", "both"):
            try:
                items.extend(
                    await _get_missing_movie_subs(client, config, headers, target_code)
                )
            except Exception as e:
                logger.error(f"Bazarr movie scan failed: {e}")

    logger.info(f"Bazarr scan: found {len(items)} items missing {config.target_language} subs")
    return items


async def _get_missing_series_subs(
    client: httpx.AsyncClient,
    config: BazarrConfig,
    headers: Dict,
    target_code: str,
) -> List[MissingSubtitleItem]:
    """Scan all series for episodes with English but no target subs."""
    items = []

    # 1. Get all series
    page = 1
    page_size = 50
    all_series = []

    while True:
        resp = await client.get(
            f"{config.normalized_url}/api/series",
            headers=headers,
            params={"start": (page - 1) * page_size, "length": page_size},
        )
        if resp.status_code != 200:
            logger.error(f"Bazarr /api/series returned {resp.status_code}")
            break

        data = resp.json().get("data", [])
        if not data:
            break
        all_series.extend(data)
        if len(data) < page_size:
            break
        page += 1

    # 2. For each series, get episodes and check subtitles
    for series in all_series:
        series_id = series.get("sonarrSeriesId")
        series_title = series.get("title", "Unknown")
        series_type = series.get("seriesType", "standard")
        series_languages = series.get("languages", [])
        
        if not series_id:
            continue
            
        # Filter: Only process if the target language is in the series language profile
        if config.target_language not in series_languages:
            logger.debug(f"Skipping series {series_title}: {config.target_language} not in language profile")
            continue

        try:
            resp = await client.get(
                f"{config.normalized_url}/api/episodes",
                headers=headers,
                params={"seriesid[]": series_id},
            )
            if resp.status_code != 200:
                continue

            episodes = resp.json().get("data", [])
            for ep in episodes:
                subs = ep.get("subtitles", [])
                media_path = ep.get("path", "")

                # Check if English sub exists
                english_sub = None
                has_target = False
                for sub in subs:
                    code = _sub_code(sub)
                    if code == config.source_language_code and sub.get("path"):
                        english_sub = sub
                    if code == target_code:
                        has_target = True

                if english_sub and not has_target and media_path:
                    items.append(MissingSubtitleItem(
                        title=series_title,
                        media_type="series",
                        media_path=config.translate_path(media_path),
                        english_sub_path=config.translate_path(english_sub["path"]),
                        season=ep.get("season", 0),
                        episode=ep.get("episode", 0),
                        episode_title=ep.get("title", ""),
                        sonarr_series_id=series_id,
                        sonarr_episode_id=ep.get("sonarrEpisodeId"),
                        series_type=series_type,
                    ))
        except Exception as e:
            logger.warning(f"Failed to scan series {series_title}: {e}")

    return items


async def _get_missing_movie_subs(
    client: httpx.AsyncClient,
    config: BazarrConfig,
    headers: Dict,
    target_code: str,
) -> List[MissingSubtitleItem]:
    """Scan all movies for ones with English but no target subs."""
    items = []
    page = 1
    page_size = 50

    while True:
        resp = await client.get(
            f"{config.normalized_url}/api/movies",
            headers=headers,
            params={"start": (page - 1) * page_size, "length": page_size},
        )
        if resp.status_code != 200:
            break

        data = resp.json().get("data", [])
        if not data:
            break

        for movie in data:
            movie_title = movie.get("title", "Unknown")
            movie_languages = movie.get("languages", [])
            
            # Filter: Only process if the target language is in the movie language profile
            if config.target_language not in movie_languages:
                logger.debug(f"Skipping movie {movie_title}: {config.target_language} not in language profile")
                continue
                
            subs = movie.get("subtitles", [])
            media_path = movie.get("path", "")

            english_sub = None
            has_target = False
            for sub in subs:
                code = _sub_code(sub)
                if code == config.source_language_code and sub.get("path"):
                    english_sub = sub
                if code == target_code:
                    has_target = True

            if english_sub and not has_target and media_path:
                items.append(MissingSubtitleItem(
                    title=movie.get("title", "Unknown"),
                    media_type="movie",
                    media_path=config.translate_path(media_path),
                    english_sub_path=config.translate_path(english_sub["path"]),
                    radarr_id=movie.get("radarrId"),
                ))

        if len(data) < page_size:
            break
        page += 1

    return items


def read_subtitle_file(sub_path: str) -> Optional[str]:
    """Read a subtitle file from disk.

    Since OmbiSub runs on the same machine as the *arr stack, we can access
    subtitle files directly by their filesystem path.
    """
    path = Path(sub_path)
    if not path.exists():
        logger.warning(f"Subtitle file not found: {sub_path}")
        return None

    # Try UTF-8 first, then fallback encodings common in subtitle files
    for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue

    logger.error(f"Failed to read subtitle file with any encoding: {sub_path}")
    return None


def deliver_translated_subtitle(
    media_path: str,
    srt_content: str,
    language_code: str = "el",
) -> str:
    """Write translated SRT file next to the media file.

    Naming convention follows Plex/Jellyfin standard:
        Movie.2024.1080p.mkv  -> Movie.2024.1080p.el.srt
        Show.S01E05.720p.mkv  -> Show.S01E05.720p.el.srt

    Args:
        media_path: Full path to the video file.
        srt_content: Translated SRT file content.
        language_code: ISO 639-1 language code (default "el" for Greek).

    Returns:
        Path where the subtitle was written.
    """
    media = Path(media_path)
    # Strip all extensions from media file (handles .en.srt, .1080p.mkv etc.)
    stem = media.stem
    # Remove any existing language suffix
    stem = re.sub(r'\.[a-z]{2,3}$', '', stem)
    output_name = f"{stem}.{language_code}.srt"
    output_path = media.parent / output_name

    # Write with UTF-8 BOM for maximum compatibility
    output_path.write_text(srt_content, encoding="utf-8-sig")
    logger.info(f"Delivered translated subtitle: {output_path}")
    return str(output_path)


def make_project_name(title: str) -> str:
    """Sanitize a show/movie title into a valid OmbiSub project name.

    Preserves readability while ensuring filesystem safety.
    """
    # Remove problematic characters but keep spaces and basic punctuation
    sanitized = re.sub(r'[<>:"/\\|?*]', '', title)
    # Collapse multiple spaces
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    # Limit length
    return sanitized[:80] if sanitized else "Untitled"


def make_episode_name(item: MissingSubtitleItem) -> str:
    """Generate an OmbiSub episode name from a MissingSubtitleItem."""
    if item.media_type == "series":
        return f"S{item.season:02d}E{item.episode:02d}"
    else:
        # Movies: use the title, sanitized
        return make_project_name(item.title)


# ---------------------------------------------------------------------------
# Extended Bazarr API — Library & Sync Support
# ---------------------------------------------------------------------------

def get_subtitle_fingerprint(sub_path: str) -> Optional[str]:
    """Return a change-detection fingerprint for a subtitle file.

    Format: ``{mtime_ns}_{size}`` — changes whenever the file is
    modified or replaced.  Returns None if the file is unreachable.
    """
    try:
        p = Path(sub_path)
        if not p.exists():
            return None
        stat = p.stat()
        return f"{int(stat.st_mtime_ns)}_{stat.st_size}"
    except OSError:
        return None


async def get_language_profiles(config: BazarrConfig) -> List[Dict]:
    """Fetch all configured language profiles from Bazarr.

    Each profile contains an ``id``, ``name``, and ``items`` list with
    the languages that belong to it (including cutoff info).
    """
    headers = {"X-API-KEY": config.api_key}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{config.normalized_url}/api/system/languages/profiles",
                headers=headers,
            )
            if resp.status_code == 200:
                return resp.json() or []
    except Exception as e:
        logger.error(f"Failed to fetch Bazarr language profiles: {e}")
    return []


async def get_all_series(config: BazarrConfig) -> List[Dict]:
    """Fetch *all* series from Bazarr with full metadata.

    Returns the raw Bazarr series objects (including ``languages``,
    ``sonarrSeriesId``, ``title``, ``seriesType``, ``profileId``, etc.).
    Pagination is handled internally.
    """
    headers = {"X-API-KEY": config.api_key}
    all_series: List[Dict] = []
    page_size = 50

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            start = 0
            while True:
                resp = await client.get(
                    f"{config.normalized_url}/api/series",
                    headers=headers,
                    params={"start": start, "length": page_size},
                )
                if resp.status_code != 200:
                    logger.error(f"Bazarr /api/series returned {resp.status_code}")
                    break
                data = resp.json().get("data", [])
                if not data:
                    break
                all_series.extend(data)
                if len(data) < page_size:
                    break
                start += page_size
    except Exception as e:
        logger.error(f"Failed to fetch Bazarr series: {e}")

    return all_series


async def get_all_movies(config: BazarrConfig) -> List[Dict]:
    """Fetch *all* movies from Bazarr with full metadata."""
    headers = {"X-API-KEY": config.api_key}
    all_movies: List[Dict] = []
    page_size = 50

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            start = 0
            while True:
                resp = await client.get(
                    f"{config.normalized_url}/api/movies",
                    headers=headers,
                    params={"start": start, "length": page_size},
                )
                if resp.status_code != 200:
                    logger.error(f"Bazarr /api/movies returned {resp.status_code}")
                    break
                data = resp.json().get("data", [])
                if not data:
                    break
                all_movies.extend(data)
                if len(data) < page_size:
                    break
                start += page_size
    except Exception as e:
        logger.error(f"Failed to fetch Bazarr movies: {e}")

    return all_movies


async def get_series_episodes(
    config: BazarrConfig,
    series_id: int,
) -> List[Dict]:
    """Fetch all episodes for a specific series from Bazarr."""
    headers = {"X-API-KEY": config.api_key}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{config.normalized_url}/api/episodes",
                headers=headers,
                params={"seriesid[]": series_id},
            )
            if resp.status_code == 200:
                return resp.json().get("data", [])
    except Exception as e:
        logger.error(f"Failed to fetch episodes for series {series_id}: {e}")
    return []


def check_path_reachable(path: str) -> bool:
    """Quick check whether a filesystem path is reachable.

    Useful for detecting unreachable UNC paths / NAS shares before
    attempting heavy I/O during sync.
    """
    try:
        p = Path(path)
        # For UNC roots like \\Server\share, check the parent exists
        if str(p).startswith("\\\\"):
            # Check if the root share is accessible
            parts = Path(path).parts
            if len(parts) >= 2:
                root = Path(parts[0]) / parts[1]
                return root.exists()
        return p.exists() or p.parent.exists()
    except OSError:
        return False
