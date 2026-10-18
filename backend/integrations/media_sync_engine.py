"""
MediaSyncEngine — Sonarr + Radarr library synchronization.

Uses Sonarr (/api/v3/series, /api/v3/episode)
and Radarr (/api/v3/movie) as the source of truth, then scans the local
filesystem via SubtitleScannerService to determine translation needs.

Responsibilities:
  - Create/update Omnisub projects from Sonarr/Radarr data
  - Import English subtitle files found on disk
  - Detect episodes/movies missing the target subtitle
  - Mark removed projects as disabled; prune episodes whose source subtitle is gone
    from disk (mirroring the directory), deleting their exported translations too

Does NOT trigger translations — that is handled by the translation
pipeline in main.py.
"""

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from .sonarr import SonarrConfig, get_all_series, get_episodes, get_episode_file, get_episode_files
from .radarr import RadarrConfig, get_all_movies
from .subtitle_scanner import SubtitleScannerService
from .path_resolver import PathResolver
from utils.text_repair import decode_subtitle_bytes

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sanitization helpers
# ---------------------------------------------------------------------------

def make_project_name(title: str) -> str:
    """Sanitize a show/movie title into a valid Omnisub project name."""
    sanitized = re.sub(r'[<>:"/\\|?*]', '', title)
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    return sanitized[:80] if sanitized else "Untitled"


def make_episode_name(season: int, episode: int) -> str:
    """Generate a zero-padded SxxExx episode identifier."""
    return f"S{season:02d}E{episode:02d}"


def secondary_episode_name(base: str, ext: str) -> str:
    """Generate f"{base} [{ext}]" for alternate subtitle formats."""
    return f"{base} [{ext}]"


_ASS_EXTENSIONS = (".ass", ".ssa")


def prefer_ass_for_project(project_meta: Optional[Dict]) -> bool:
    """Should this project treat ``.ass`` as the subtitle format that must exist?

    When on, an episode with only an ``.srt`` still gets its container probed for a
    muxed ASS track, and the extracted ``.ass`` becomes the episode's primary source
    (the ``.srt`` moves to a dual-format sibling and keeps its own translation).

    The project setting ``prefer_ass_format`` is a tri-state:

    - ``"auto"`` (default) — on for Sonarr series typed **anime**, off otherwise.
      Anime releases carry their real subtitles as typeset ASS; an ``.srt`` next to
      one is usually a stripped-down convenience track, so "has a subtitle already"
      is the wrong test for them.
    - ``True`` / ``"always"`` — on regardless of type.
    - ``False`` / ``"never"`` — off regardless of type.

    Explicit beats automatic, so a user who turns it off for one anime keeps it off.
    """
    meta = project_meta or {}
    value = (meta.get("settings") or {}).get("prefer_ass_format", "auto")

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("always", "true", "on", "yes", "1"):
            return True
        if normalized in ("never", "false", "off", "no", "0"):
            return False
    elif value is not None:
        return bool(value)

    if (meta.get("series_type") or "").strip().lower() == "anime":
        return True
    if meta.get("is_anime") is True or str(meta.get("type", "")).strip().lower() == "anime":
        return True
    genres = meta.get("genres") or []
    if any(str(g).strip().lower() == "anime" for g in genres):
        return True
    tags = meta.get("tags") or []
    if any("anime" in str(t).strip().lower() for t in tags):
        return True
    if "anime" in (meta.get("show_name") or "").strip().lower():
        return True
    return False


def _movie_disambiguator(movie_data: Dict) -> str:
    """Suffix (year, or Radarr id as fallback) for movies that share a title —
    e.g. two different releases both titled "The Power". Extracted once so every
    call site agrees on the same suffix instead of re-deriving it independently."""
    movie_year = movie_data.get("year")
    if not movie_year:
        movie_file = movie_data.get("movieFile") or {}
        path = movie_file.get("path", "")
        if path:
            match = re.search(r'\b(19\d\d|20\d\d)\b', Path(path).name)
            if match:
                movie_year = int(match.group(1))
    return f" ({movie_year})" if movie_year else f" (id-{movie_data.get('id')})"


def movie_project_name(movie_data: Dict, adopted: Optional[str] = None) -> str:
    """Resolve the Omnisub project name for a movie.

    If ``adopted`` is given (an existing project already linked to this Radarr
    movie via arr_radarr_id), it wins — reusing the project a user already has
    translations/glossary/TM in, rather than minting a new disambiguated name and
    orphaning the old one. Only movies with no matching existing project get the
    new "Title (year)" / "Title (id-N)" disambiguated name.
    """
    if adopted:
        return adopted
    title = movie_data.get("title", "Unknown")
    return make_project_name(f"{title}{_movie_disambiguator(movie_data)}")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class SyncResult:
    """Summary of a full sync run."""
    new_projects: int = 0
    updated_episodes: int = 0
    new_episodes: int = 0
    removed_episodes: int = 0
    disabled_projects: int = 0
    re_enabled_projects: int = 0
    skipped_no_subs: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    synced_series: List[Dict] = field(default_factory=list)
    synced_movies: List[Dict] = field(default_factory=list)
    unreachable_paths: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "new_projects": self.new_projects,
            "updated_episodes": self.updated_episodes,
            "new_episodes": self.new_episodes,
            "removed_episodes": self.removed_episodes,
            "disabled_projects": self.disabled_projects,
            "re_enabled_projects": self.re_enabled_projects,
            "skipped_no_subs": self.skipped_no_subs,
            "errors": self.errors,
            "warnings": self.warnings,
            "synced_series_count": len(self.synced_series),
            "synced_movies_count": len(self.synced_movies),
            "unreachable_paths": self.unreachable_paths,
        }


# ---------------------------------------------------------------------------
# Sync engine
# ---------------------------------------------------------------------------

class MediaSyncEngine:
    """Synchronize Sonarr + Radarr libraries with Omnisub projects.

    Usage::

        engine = MediaSyncEngine(
            sonarr_config=sonarr_cfg,
            radarr_config=radarr_cfg,
            resolver=PathResolver(mappings),
            target_lang_code="el",
        )
        result = await engine.full_sync(progress_callback=update_job)
    """

    def __init__(
        self,
        sonarr_config: Optional[SonarrConfig] = None,
        radarr_config: Optional[RadarrConfig] = None,
        resolver: Optional[PathResolver] = None,
        source_lang_code: str = "en",
        target_lang_code: str = "el",
        target_language: str = "Greek",      # Human-readable name for metadata
        embedded_extraction: bool = False,
        embedded_keywords: Optional[str] = None,
        embedded_auto_translate: bool = False,
        scan_ass: bool = True,
    ):
        self.sonarr = sonarr_config
        self.radarr = radarr_config
        self.resolver = resolver or PathResolver()
        self.scan_ass = scan_ass
        self.scanner = SubtitleScannerService(source_lang_code, target_lang_code, include_ass=scan_ass)
        self.source_lang_code = source_lang_code
        self.target_lang_code = target_lang_code
        self.target_language = target_language
        # Embedded ASS extraction (docs/PLAN_embedded_ass_extraction.md). Off by default
        # or when scan_ass is disabled.
        self.embedded_extraction = embedded_extraction and scan_ass
        self.embedded_auto_translate = embedded_auto_translate
        from integrations import embedded_subs as _embedded_subs
        self.embedded_keywords = _embedded_subs.parse_keywords(embedded_keywords)
        self._embedded_tools = None
        self._embedded_tools_resolved = False

    # ------------------------------------------------------------------
    # Webhook Payload Validation & Translation Reuse Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def validate_sonarr_payload(payload: Any) -> Optional[str]:
        """Validate Sonarr webhook payload schema synchronously (INT-P1-06).
        Returns None if valid, or a diagnostic error string if invalid.
        """
        if not isinstance(payload, dict):
            return "Payload body must be a JSON object."
        event_type = payload.get("eventType")
        if not event_type or not isinstance(event_type, str):
            return "Missing or invalid 'eventType' string field."
        if event_type == "Test":
            return None
        if event_type in ("Download", "EpisodeFileRenamed"):
            errors = []
            series = payload.get("series")
            if not isinstance(series, dict) or not series.get("title"):
                errors.append("missing or invalid 'series.title'")
            episodes = payload.get("episodes")
            if not isinstance(episodes, list) or len(episodes) == 0:
                errors.append("missing or empty 'episodes' list")
            episode_file = payload.get("episodeFile")
            if not isinstance(episode_file, dict) or not episode_file.get("path"):
                errors.append("missing or invalid 'episodeFile.path'")
            if errors:
                return f"Invalid Sonarr payload for event '{event_type}': {'; '.join(errors)}"
        return None

    @staticmethod
    def validate_radarr_payload(payload: Any) -> Optional[str]:
        """Validate Radarr webhook payload schema synchronously (INT-P1-06).
        Returns None if valid, or a diagnostic error string if invalid.
        """
        if not isinstance(payload, dict):
            return "Payload body must be a JSON object."
        event_type = payload.get("eventType")
        if not event_type or not isinstance(event_type, str):
            return "Missing or invalid 'eventType' string field."
        if event_type == "Test":
            return None
        if event_type in ("Download", "MovieFileRenamed"):
            errors = []
            movie = payload.get("movie")
            if not isinstance(movie, dict) or not movie.get("title"):
                errors.append("missing or invalid 'movie.title'")
            movie_file = payload.get("movieFile")
            if not isinstance(movie_file, dict) or not movie_file.get("path"):
                errors.append("missing or invalid 'movieFile.path'")
            if errors:
                return f"Invalid Radarr payload for event '{event_type}': {'; '.join(errors)}"
        return None

    def _mark_queue_item_completed(self, project_name: str, episode_name: str) -> None:
        """Mark translation queue items completed when target subtitle exists or is imported."""
        try:
            from services.agent_task_service import agent_task_service
            agent_task_service.mark_task_completed(project_name, episode_name)
        except Exception:
            try:
                from utils.translation_queue import TranslationQueue
                from datetime import datetime, timezone
                q = TranslationQueue()
                now_str = datetime.now(timezone.utc).isoformat()
                with q._conn() as conn:
                    conn.execute("""
                        UPDATE translation_queue
                        SET status = 'completed', completed_at = COALESCE(completed_at, ?)
                        WHERE project_name = ? AND episode_name = ? AND status IN ('pending', 'running')
                    """, (now_str, project_name, episode_name))
                    conn.commit()
            except Exception:
                pass

    def _reuse_and_export_existing_translation(
        self,
        project_name: str,
        episode_name: str,
        media_path: str,
        scan,
        storage,
        existing_meta: Optional[Dict] = None,
    ) -> bool:
        """Reuse existing translation in storage upon media release upgrade/rename (INT-P1-04).

        If storage contains translated cues (data.json), re-exports the existing
        translation to the new media stem instead of triggering an LLM re-translation.
        Returns True if an existing translation was successfully reused and re-exported.
        """
        if scan.has_target_sub:
            return False

        if existing_meta is None:
            existing_meta = storage.load_episode_metadata(project_name, episode_name)
        if not existing_meta:
            return False

        ep_data = storage.load_episode(project_name, episode_name)
        if not ep_data or not ep_data.get("data"):
            return False

        from utils.language_codes import to_code, output_filename_from_original
        target_lang = self.target_language or "Greek"
        target_code = to_code(target_lang) or "el"

        has_translated_cues = any(
            bool((line.get("translations", {}) or {}).get(target_code) or line.get("translated"))
            for line in ep_data["data"]
        )
        if not has_translated_cues and not existing_meta.get("translated"):
            return False

        try:
            from utils.source_clean import reconstruct_cleaned_srt
            from integrations import embedded_subs

            translated_content = reconstruct_cleaned_srt(
                project_name, episode_name, ep_data["data"], target_code
            )
            if not translated_content or not translated_content.strip():
                return False

            media_file = Path(media_path)
            orig_filename = existing_meta.get("original_filename") or ""
            ext = Path(orig_filename).suffix if orig_filename else ""
            if not ext:
                _, ext_sub = storage.load_original_subtitle(project_name, episode_name)
                ext = f".{ext_sub}" if ext_sub else ".srt"

            target_filename = output_filename_from_original(f"{media_file.stem}{ext}", target_code)
            new_target_path = media_file.parent / target_filename

            if not new_target_path.parent.exists():
                logger.warning(f"Cannot re-export translation: parent dir does not exist {new_target_path.parent}")
                return False

            embedded_subs.write_sidecar(new_target_path, translated_content)

            # Update scan result so downstream callers know target sub is present on disk
            scan.has_target_sub = True
            scan.target_sub_path = str(new_target_path)

            # Update metadata
            existing_meta["arr_media_path"] = media_path
            existing_meta["arr_has_target"] = True
            existing_meta["arr_target_path"] = str(new_target_path)
            existing_meta["translated"] = True
            existing_meta["translation_status"] = "completed"
            storage.save_episode_metadata(project_name, episode_name, existing_meta, update_stats=False)

            logger.info(
                f"Sync/Webhook: Reused existing translation for {project_name}/{episode_name} upon "
                f"media release upgrade/rename; re-exported to {new_target_path}"
            )
            return True
        except Exception as e:
            logger.error(
                f"Failed to reuse and re-export existing translation for {project_name}/{episode_name}: {e}",
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # Full sync
    # ------------------------------------------------------------------

    async def full_sync(self, progress_callback=None) -> SyncResult:
        """Run a complete sync cycle against both Sonarr and Radarr."""
        from utils import storage
        from utils.srt_parser import parse_srt

        result = SyncResult()

        def _progress(pct: float, msg: str):
            if progress_callback:
                progress_callback(progress=pct, message=msg)

        loop = asyncio.get_running_loop()
        # Track all project names seen during this sync
        seen_project_names: set = set()
        existing_projects = await loop.run_in_executor(None, storage.list_projects)
        existing_projects_set = set(existing_projects)
        
        existing_arr_projects: set = set()
        existing_project_meta: Dict[str, Dict] = {}
        # Maps a Radarr movie id to the existing project already linked to it, so a
        # movie whose project predates the "Title (year)" disambiguation naming
        # scheme is adopted (reused) instead of spawning a duplicate suffixed
        # project and orphaning the original's translations/glossary/TM.
        radarr_id_to_project: Dict[str, str] = {}
        async def load_meta(p_name):
            try:
                meta = await loop.run_in_executor(None, storage.load_project_metadata, p_name)
            except Exception as e:
                logger.warning(f"Failed to load metadata for project '{p_name}': {e}")
                meta = None
            if meta and meta.get("arr_source"):
                existing_arr_projects.add(p_name)
                existing_project_meta[p_name] = meta
            if meta:
                rid = meta.get("arr_radarr_id")
                if rid is not None:
                    radarr_id_to_project[str(rid)] = p_name
        await asyncio.gather(*[load_meta(p) for p in existing_projects])

        # --- Sonarr sync ---
        sonarr_queried = False
        sonarr_failed = False
        all_series = []
        if self.sonarr and self.sonarr.enabled and self.sonarr.api_key:
            _progress(5.0, "Fetching series from Sonarr...")
            sonarr_queried = True
            try:
                all_series = await get_all_series(self.sonarr)
            except Exception as e:
                err = f"Failed to fetch Sonarr series: {e}"
                logger.error(err)
                result.errors.append(err)
                all_series = []
                sonarr_failed = True

            if not sonarr_failed:
                total = len(all_series)
                done = 0
                
                sem = asyncio.Semaphore(8)  # Concurrency limit for series syncing

                async def sync_one_series(series):
                    nonlocal done
                    series_title = series.get("title", "Unknown")
                    project_name = make_project_name(series_title)
                    seen_project_names.add(project_name)

                    async with sem:
                        try:
                            sync_info = await self._sync_series(series, project_name, result, storage, parse_srt, existing_projects_set)
                            if sync_info:
                                result.synced_series.append(sync_info)
                        except Exception as e:
                            err = f"Error syncing series '{series_title}': {e}"
                            logger.error(err, exc_info=True)
                            result.errors.append(err)

                    done += 1
                    pct = 5.0 + (done / max(total, 1)) * 45.0
                    _progress(pct, f"Synced series: {series_title} ({done}/{total})")

                await asyncio.gather(*[sync_one_series(s) for s in all_series])

        # --- Radarr sync ---
        radarr_queried = False
        radarr_failed = False
        all_movies = []
        if self.radarr and self.radarr.enabled and self.radarr.api_key:
            _progress(52.0, "Fetching movies from Radarr...")
            radarr_queried = True
            try:
                all_movies = await get_all_movies(self.radarr)
            except Exception as e:
                err = f"Failed to fetch Radarr movies: {e}"
                logger.error(err)
                result.errors.append(err)
                all_movies = []
                radarr_failed = True

            if not radarr_failed:
                total = len(all_movies)
                done = 0

                sem = asyncio.Semaphore(15)  # Concurrency limit for movie syncing (faster disk scans)

                async def sync_one_movie(movie):
                    nonlocal done
                    movie_title = movie.get("title", "Unknown")
                    radarr_id = movie.get("id")
                    adopted_name = radarr_id_to_project.get(str(radarr_id)) if radarr_id is not None else None
                    project_name = movie_project_name(movie, adopted=adopted_name)
                    seen_project_names.add(project_name)

                    async with sem:
                        try:
                            sync_info = await self._sync_movie(movie, project_name, result, storage, parse_srt, existing_projects_set)
                            if sync_info:
                                result.synced_movies.append(sync_info)
                        except Exception as e:
                            err = f"Error syncing movie '{movie_title}': {e}"
                            logger.error(err, exc_info=True)
                            result.errors.append(err)

                    done += 1
                    pct = 52.0 + (done / max(total, 1)) * 38.0
                    _progress(pct, f"Synced movie: {movie_title} ({done}/{total})")

                await asyncio.gather(*[sync_one_movie(m) for m in all_movies])

        # --- Disable removed projects ---
        _progress(92.0, "Checking for removed entries...")

        existing_series_count = sum(1 for m in existing_project_meta.values() if m.get("arr_media_type") == "series")
        existing_movies_count = sum(1 for m in existing_project_meta.values() if m.get("arr_media_type") == "movie")

        # Determine if each service is safe to prune from
        sonarr_safe_to_prune = sonarr_queried and not sonarr_failed
        if sonarr_safe_to_prune and len(all_series) == 0 and existing_series_count > 0:
            logger.warning(
                f"Sonarr returned 0 series while {existing_series_count} series exist in storage. "
                "Skipping series disablement as an empty-response safeguard."
            )
            result.warnings.append("Sonarr returned 0 series; skipped series disablement safeguard.")
            sonarr_safe_to_prune = False

        radarr_safe_to_prune = radarr_queried and not radarr_failed
        if radarr_safe_to_prune and len(all_movies) == 0 and existing_movies_count > 0:
            logger.warning(
                f"Radarr returned 0 movies while {existing_movies_count} movies exist in storage. "
                "Skipping movie disablement as an empty-response safeguard."
            )
            result.warnings.append("Radarr returned 0 movies; skipped movie disablement safeguard.")
            radarr_safe_to_prune = False

        series_to_disable = []
        movies_to_disable = []
        other_to_disable = []

        for p in existing_arr_projects:
            if p in seen_project_names:
                continue
            meta = existing_project_meta.get(p)
            if not meta or meta.get("arr_disabled"):
                continue

            media_type = meta.get("arr_media_type")
            if media_type == "series":
                if sonarr_safe_to_prune:
                    series_to_disable.append(p)
            elif media_type == "movie":
                if radarr_safe_to_prune:
                    movies_to_disable.append(p)
            else:
                # Unknown/unspecified type: only allow if BOTH services ran safely
                if sonarr_safe_to_prune and radarr_safe_to_prune:
                    other_to_disable.append(p)

        # Circuit breaker: Abort mass disablement if candidate count exceeds threshold
        if existing_series_count > 0 and len(series_to_disable) > 10 and len(series_to_disable) > 0.3 * existing_series_count:
            msg = (
                f"Circuit breaker triggered: Sonarr sync candidate disable count ({len(series_to_disable)}) "
                f"exceeds 30% of existing series ({existing_series_count}). Disablement skipped."
            )
            logger.warning(msg)
            result.warnings.append(msg)
            series_to_disable.clear()

        if existing_movies_count > 0 and len(movies_to_disable) > 20 and len(movies_to_disable) > 0.3 * existing_movies_count:
            msg = (
                f"Circuit breaker triggered: Radarr sync candidate disable count ({len(movies_to_disable)}) "
                f"exceeds 30% of existing movies ({existing_movies_count}). Disablement skipped."
            )
            logger.warning(msg)
            result.warnings.append(msg)
            movies_to_disable.clear()

        candidates_to_disable = series_to_disable + movies_to_disable + other_to_disable

        async def disable_project(p_name):
            try:
                meta = await loop.run_in_executor(None, storage.load_project_metadata, p_name)
            except Exception as e:
                logger.warning(f"Failed to load metadata for project '{p_name}' during disable check: {e}")
                meta = None
            if meta and not meta.get("arr_disabled"):
                meta["arr_disabled"] = True
                meta["arr_disabled_at"] = datetime.now().isoformat()
                await loop.run_in_executor(None, storage.save_project_metadata, p_name, meta)
                result.disabled_projects += 1

        await asyncio.gather(*[disable_project(p) for p in candidates_to_disable])

        # --- Re-enable projects that reappeared ---
        async def re_enable_project(p_name):
            try:
                meta = await loop.run_in_executor(None, storage.load_project_metadata, p_name)
            except Exception as e:
                logger.warning(f"Failed to load metadata for project '{p_name}' during re-enable check: {e}")
                meta = None
            # A title the user set to Ignored stays ignored; only sync's own disables are undone.
            if meta and meta.get("arr_disabled") and not meta.get("ignored_by_user"):
                meta["arr_disabled"] = False
                meta.pop("arr_disabled_at", None)
                await loop.run_in_executor(None, storage.save_project_metadata, p_name, meta)
                result.re_enabled_projects += 1

        await asyncio.gather(*[re_enable_project(p) for p in seen_project_names if p in existing_arr_projects])

        _progress(98.0, "Finalizing sync...")
        logger.info(
            f"MediaSyncEngine complete: {result.new_projects} new projects, "
            f"{result.new_episodes} new episodes, "
            f"{result.disabled_projects} disabled"
        )
        return result

    # ------------------------------------------------------------------
    # Series sync
    # ------------------------------------------------------------------

    async def _sync_series(
        self, series_data: Dict, project_name: str,
        result: SyncResult, storage, parse_srt, existing_projects_set: set,
        episode_filter: Optional[set] = None,
    ) -> Optional[Dict]:
        """Create/update project and import episodes for one series.

        ``episode_filter`` (a set of SxxExx names) restricts the pass to those
        episodes — used by on-demand requests, where importing a whole 500-episode
        anime to translate one episode would be all cost and no benefit. A filtered
        pass sees only part of the series, so it must not prune or rewrite the
        series-wide stats: both read "not seen" as "gone from disk".
        """
        series_id = series_data.get("id")
        series_title = series_data.get("title", "Unknown")
        series_type = series_data.get("seriesType", "standard")
        series_path = series_data.get("path", "")

        loop = asyncio.get_running_loop()

        is_new = project_name not in existing_projects_set
        if is_new:
            # Snapshot before handing the names to a worker thread: full_sync runs
            # several series concurrently and each new one adds to the shared
            # existing_projects_set, so iterating the live set off-thread raises
            # "Set changed size during iteration". Reserve the name in the same
            # breath, before the first await — two titles can sanitize to one
            # project_name (the 80-char truncation in make_project_name collides
            # on long titles), and without the reservation both tasks would see
            # is_new and each create_project over the other.
            existing_snapshot = list(existing_projects_set)
            existing_projects_set.add(project_name)

            # Sibling project inheritance check (run in executor to avoid blocking)
            def get_inherited_data(existing_names):
                inherited_glossary = {"terms": []}
                inherited_context = ""
                show_name_lower = series_title.lower()
                for existing in existing_names:
                    try:
                        meta = storage.load_project_metadata(existing)
                    except Exception as e:
                        logger.warning(f"Failed to load metadata for {existing} during inheritance check: {e}")
                        meta = None
                    if meta and meta.get("show_name", "").lower() == show_name_lower:
                        if meta.get("glossary", {}).get("terms"):
                            inherited_glossary = meta["glossary"]
                            inherited_context = meta.get("context_guide", "")
                            logger.info(f"Inherited glossary and context from sibling project: {existing}")
                            break
                return inherited_glossary, inherited_context

            inherited_glossary, inherited_context = await loop.run_in_executor(None, get_inherited_data, existing_snapshot)

            await loop.run_in_executor(None, storage.create_project, project_name, {
                "show_name": series_title,
                "target_language": self.target_language,
                "type": "show",
                "arr_source": True,
                "arr_media_type": "series",
                "arr_sonarr_id": series_id,
                "arr_disabled": False,
                "arr_last_sync": datetime.now().isoformat(),
                "series_type": series_type,
                "glossary": inherited_glossary,
                "context_guide": inherited_context,
            })
            result.new_projects += 1
            logger.info(f"Created project from Sonarr: {project_name}")
        else:
            try:
                meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
            except Exception as e:
                logger.warning(f"Failed to load metadata for existing project '{project_name}': {e}")
                meta = None
            if meta:
                meta["arr_last_sync"] = datetime.now().isoformat()
                meta["arr_sonarr_id"] = series_id
                meta["arr_media_type"] = "series"
                meta["type"] = "show"
                # Keep series_type current: the "auto" prefer_ass_format default reads it,
                # so a project created before this field existed (or one Sonarr has since
                # retyped as anime) would otherwise never pick up the anime behaviour.
                meta["series_type"] = series_type
                await loop.run_in_executor(None, storage.save_project_metadata, project_name, meta)

        # Resolved once per series: gates embedded probing for .srt-only episodes and
        # the default for dual-format sibling creation below.
        project_meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
        prefer_ass = prefer_ass_for_project(project_meta)

        episodes = await get_episodes(self.sonarr, series_id)
        # Fetch all episode files for the series in a single API call to avoid N+1 requests
        episode_files = await get_episode_files(self.sonarr, series_id)

        # INT-P0-01: Detect Sonarr file API error or empty anomaly
        has_file_episodes = any(ep.get("hasFile") for ep in episodes)
        file_api_error = False
        if episode_files is None:
            file_api_error = True
            msg = f"{project_name}: Sonarr episodefile API returned an error for series {series_id} — aborting series pruning to safeguard subtitles."
            logger.warning(msg)
            result.warnings.append(msg)
            episode_file_map = {}
        elif not episode_files and has_file_episodes:
            file_api_error = True
            msg = f"{project_name}: Sonarr episodefile API returned 0 files but {len(episodes)} episodes report hasFile=True — aborting series pruning to safeguard subtitles."
            logger.warning(msg)
            result.warnings.append(msg)
            episode_file_map = {}
        else:
            episode_file_map = {ef.get("id"): ef.get("path", "") for ef in episode_files if ef.get("id")}

        imported_count = 0
        total_with_file = 0
        translated_count = 0
        # Episodes found WITH a source subtitle on disk this sync — anything arr-sourced
        # and NOT in here is pruned below so the dashboard mirrors the directory.
        seen_episode_names: set = set()

        # Step 1: Pre-resolve paths and find unique parent directories
        resolved_episodes = []
        parent_dirs = set()

        for ep in episodes:
            if not ep.get("hasFile"):
                continue

            season = ep.get("seasonNumber", 0)
            episode_num = ep.get("episodeNumber", 0)
            ep_title = ep.get("title", "")
            episode_name = make_episode_name(season, episode_num)
            if episode_filter is not None and episode_name not in episode_filter:
                continue

            # Resolve media path
            ep_file = ep.get("episodeFile")
            if ep_file and isinstance(ep_file, dict):
                raw_media_path = ep_file.get("path", "")
            else:
                ep_file_id = ep.get("episodeFileId")
                if not ep_file_id:
                    continue
                raw_media_path = episode_file_map.get(ep_file_id, "")

            if not raw_media_path:
                continue

            media_path = self.resolver.resolve(raw_media_path)
            resolved_episodes.append({
                "ep": ep,
                "episode_name": episode_name,
                "media_path": media_path,
                "season": season,
                "episode_num": episode_num,
                "ep_title": ep_title,
            })
            parent_dirs.add(str(Path(media_path).parent))

        # Step 2: Scan unique directories in parallel.
        # Iterate a fixed list, not the set — the zip below pairs results back up by
        # position, so the two traversals must be in the same order.
        parent_dir_list = sorted(parent_dirs)
        scan_tasks = []
        for d_path in parent_dir_list:
            # Check directory accessibility
            def check_dir(p):
                try:
                    dp = Path(p)
                    if not dp.exists():
                        return False
                    os.listdir(p)
                    return True
                except Exception:
                    return False
            
            accessible = await loop.run_in_executor(None, check_dir, d_path)
            if not accessible:
                if d_path not in result.unreachable_paths:
                    result.unreachable_paths.append(d_path)
            
            scan_tasks.append(
                loop.run_in_executor(None, self.scanner.scan_directory_index, d_path)
            )
        scan_results = await asyncio.gather(*scan_tasks)
        dir_indices = dict(zip(parent_dir_list, scan_results))

        logger.debug(f"Step 3: Processing {len(resolved_episodes)} resolved episodes...")
        # Media files with no source sidecar — probed for embedded ASS after the loop
        # so the probes run concurrently instead of serialising this pass.
        embedded_candidates: List[Dict] = []
        for item in resolved_episodes:
            ep = item["ep"]
            episode_name = item["episode_name"]
            media_path = item["media_path"]
            season = item["season"]
            episode_num = item["episode_num"]
            ep_title = item["ep_title"]

            p_dir = str(Path(media_path).parent)
            dir_index = dir_indices.get(p_dir)

            logger.debug(f"  [{episode_name}] Scanning media file...")
            scan = await loop.run_in_executor(None, self.scanner.scan_media_file, media_path, dir_index)
            logger.debug(f"  [{episode_name}] Scan complete: has_source={scan.has_source_sub}, has_target={scan.has_target_sub}")

            total_with_file += 1
            if scan.has_target_sub:
                translated_count += 1

            # Probe for a muxed ASS track when there's nothing on disk, or when this is
            # an ass-preferring (e.g. anime) project that only has an .srt. Deliberately
            # before the has_source_sub check: an .srt-only episode still imports its
            # .srt now and gains the .ass on the pass after extraction lands.
            if self._wants_embedded_probe(scan, prefer_ass):
                embedded_candidates.append({
                    "episode_name": episode_name,
                    "media_path": media_path,
                    "import_metadata": {
                        "arr_sonarr_episode_id": ep.get("id"),
                        "episode_title": ep_title,
                        "season": season,
                        "episode": episode_num,
                    },
                })

            if not scan.has_source_sub:
                # Retain the episode so it is visible in the UI with 'No Subtitle' status and can be extracted
                seen_episode_names.add(episode_name)
                existing_meta = await loop.run_in_executor(None, storage.load_episode_metadata, project_name, episode_name)
                if not existing_meta:
                    new_meta = {
                        "arr_source": True,
                        "arr_media_path": media_path,
                        "arr_sonarr_episode_id": ep.get("id"),
                        "episode_title": ep_title,
                        "season": season,
                        "episode": episode_num,
                        "has_source_sub": False,
                        "arr_has_target": scan.has_target_sub,
                        "arr_target_path": scan.target_sub_path if scan.has_target_sub else None,
                        "translated": scan.has_target_sub,
                        "line_count": 0,
                        "original_filename": Path(media_path).name,
                    }
                    def _init_empty_ep():
                        ep_dir = storage.episode_dir(project_name, episode_name)
                        ep_dir.mkdir(parents=True, exist_ok=True)
                        storage.save_episode(project_name, episode_name, [], new_meta)
                    await loop.run_in_executor(None, _init_empty_ep)
                    if scan.has_target_sub:
                        self._mark_queue_item_completed(project_name, episode_name)
                    imported_count += 1
                    result.new_episodes += 1
                else:
                    meta_changed = False
                    # INT-P1-04: Reuse existing translation if media was upgraded/renamed
                    if not scan.has_target_sub:
                        reused = self._reuse_and_export_existing_translation(
                            project_name, episode_name, media_path, scan, storage, existing_meta
                        )
                        if reused:
                            meta_changed = True
                            self._mark_queue_item_completed(project_name, episode_name)
                    if existing_meta.get("arr_media_path") != media_path:
                        existing_meta["arr_media_path"] = media_path
                        meta_changed = True
                    if existing_meta.get("arr_has_target") != scan.has_target_sub:
                        existing_meta["arr_has_target"] = scan.has_target_sub
                        if scan.has_target_sub:
                            existing_meta["translated"] = True
                            existing_meta["translation_status"] = "completed"
                            self._mark_queue_item_completed(project_name, episode_name)
                        meta_changed = True
                    if existing_meta.get("has_source_sub") is not False and not existing_meta.get("line_count") and not storage.original_subtitle_exists(project_name, episode_name):
                        existing_meta["has_source_sub"] = False
                        meta_changed = True
                    if meta_changed:
                        await loop.run_in_executor(None, storage.save_episode_metadata, project_name, episode_name, existing_meta, False)
                    imported_count += 1

                result.skipped_no_subs += 1
                continue

            seen_episode_names.add(episode_name)
            item["scan"] = scan

            logger.debug(f"  [{episode_name}] Generating fingerprint for {scan.source_sub_path}...")
            fingerprint = await loop.run_in_executor(None, self._fingerprint, scan.source_sub_path)
            logger.debug(f"  [{episode_name}] Loading episode metadata...")
            existing_meta = await loop.run_in_executor(None, storage.load_episode_metadata, project_name, episode_name)


            should_import = True
            if existing_meta:
                data_file = storage.episode_dir(project_name, episode_name) / "data.json"
                old_fp = existing_meta.get("arr_sub_fingerprint")
                if not data_file.exists() or not existing_meta.get("arr_media_path"):
                    should_import = True
                elif fingerprint is None:
                    should_import = False
                elif old_fp and fingerprint == old_fp:
                    should_import = False
                elif old_fp and fingerprint != old_fp:
                    result.updated_episodes += 1
                else:
                    should_import = False

            # An .ass that has appeared next to the .srt this episode was imported from
            # makes the .ass the new primary. Move the old episode to its sibling name
            # first, or the import below overwrites its translations.
            if should_import and existing_meta:
                migrated_sibling = await loop.run_in_executor(
                    None, self._migrate_primary_format_flip,
                    project_name, episode_name, scan, existing_meta,
                )
                if migrated_sibling:
                    seen_episode_names.add(migrated_sibling)
                    existing_meta = None  # the base slot is free; import the .ass fresh

            if should_import:
                logger.debug(f"  [{episode_name}] Reading subtitle file...")
                srt_content = await loop.run_in_executor(None, self._read_file, scan.source_sub_path)
                if not srt_content:
                    result.warnings.append(f"Cannot read subtitle: {scan.source_sub_path}")
                    continue
                    
                target_srt_content = None
                if scan.has_target_sub and scan.target_sub_path:
                    # Seed translations from the on-disk target only for a brand-new or
                    # not-yet-translated episode. For one already translated in the app,
                    # the incremental carry-over preserves in-app translations; re-aligning
                    # the on-disk target here would clobber user edits with a possibly-stale
                    # exported file.
                    already_translated = bool(
                        existing_meta and (existing_meta.get("translated")
                                           or existing_meta.get("translation_status") == "completed")
                    )
                    if not already_translated:
                        target_srt_content = await loop.run_in_executor(None, self._read_file, scan.target_sub_path)

                from utils.source_clean import import_and_clean_srt
                def _do_import():
                    logger.debug(f"  [{episode_name}] Running import_and_clean_srt...")
                    import_and_clean_srt(
                        project_name,
                        episode_name,
                        srt_content,
                        filename=Path(scan.source_sub_path).name,
                        fingerprint=fingerprint,
                        target_srt_content=target_srt_content,
                        extra_metadata={
                            "arr_source": True,
                            "arr_sub_path": scan.source_sub_path,
                            "arr_media_path": media_path,
                            "arr_sonarr_episode_id": ep.get("id"),
                            "episode_title": ep_title,
                            "season": season,
                            "episode": episode_num,
                            "arr_has_target": scan.has_target_sub,
                            "arr_target_path": scan.target_sub_path if scan.has_target_sub else None,
                            "arr_translated_from_fingerprint": existing_meta.get("arr_translated_from_fingerprint") if existing_meta else None,
                        },
                    )
                await loop.run_in_executor(None, _do_import)
                if scan.has_target_sub:
                    self._mark_queue_item_completed(project_name, episode_name)
                imported_count += 1
                result.new_episodes += 1
            else:
                logger.debug(f"  [{episode_name}] Updating existing metadata/target status...")
                # Update existing_meta if source sub or target sub status changed on disk
                meta_changed = False
                if scan.has_source_sub and not existing_meta.get("has_source_sub"):
                    existing_meta["has_source_sub"] = True
                    meta_changed = True

                # INT-P1-04: Reuse existing translation if media was upgraded/renamed
                if not scan.has_target_sub:
                    reused = self._reuse_and_export_existing_translation(
                        project_name, episode_name, media_path, scan, storage, existing_meta
                    )
                    if reused:
                        meta_changed = True
                        self._mark_queue_item_completed(project_name, episode_name)

                if existing_meta.get("arr_has_target") != scan.has_target_sub:
                    existing_meta["arr_has_target"] = scan.has_target_sub
                    if scan.has_target_sub:
                        existing_meta["translated"] = True
                        existing_meta["translation_status"] = "completed"
                        self._mark_queue_item_completed(project_name, episode_name)
                    meta_changed = True
                
                tgt_path_on_disk = scan.target_sub_path if scan.has_target_sub else None

                if existing_meta.get("arr_target_path") != tgt_path_on_disk:
                    existing_meta["arr_target_path"] = tgt_path_on_disk
                    meta_changed = True

                # Source renamed on disk (same content → same fingerprint, but new path):
                # keep the stored source path/filename current so the dashboard reflects it.
                if existing_meta.get("arr_sub_path") != scan.source_sub_path:
                    existing_meta["arr_sub_path"] = scan.source_sub_path
                    existing_meta["original_filename"] = Path(scan.source_sub_path).name
                    meta_changed = True

                # Media moved/renamed (e.g. *arr relocated it to another share): the export
                # writes next to arr_media_path, so a stale one makes every export fail
                # silently. Refresh it here — this branch is the only path a long-lived,
                # already-imported episode ever takes.
                if existing_meta.get("arr_media_path") != media_path:
                    existing_meta["arr_media_path"] = media_path
                    meta_changed = True

                if meta_changed:
                    await loop.run_in_executor(None, storage.save_episode_metadata, project_name, episode_name, existing_meta, False)
                imported_count += 1

        # Episodes with no sidecar: look inside the container for an embedded ASS track.
        if embedded_candidates:
            from utils.translation_queue import PRIORITY_SYNC
            queued = await self._run_embedded_probes(
                project_name, embedded_candidates, PRIORITY_SYNC, result
            )
            # Treat a queued extraction as "seen" so prune doesn't delete an episode —
            # and its exported translation — in the window before the sidecar is
            # (re)written. Without this, deleting a sidecar by hand would cost the
            # translation too, then silently re-translate it.
            seen_episode_names.update(queued)

        # Sync secondary formats
        project_meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
        create_alt = self._create_alt_formats(project_meta, prefer_ass)

        for item in resolved_episodes:
            if "scan" not in item:
                continue
            scan = item["scan"]
            episode_name = item["episode_name"]
            media_path = item["media_path"]
            season = item["season"]
            episode_num = item["episode_num"]
            ep_title = item["ep_title"]
            ep = item["ep"]

            base_extra_meta = {
                "episode_title": ep_title,
                "season": season,
                "episode": episode_num,
                "arr_sonarr_episode_id": ep.get("id"),
            }
            seen_siblings = await loop.run_in_executor(
                None,
                self._sync_secondary_formats,
                project_name,
                episode_name,
                scan,
                media_path,
                base_extra_meta,
                create_alt,
                result,
                storage
            )
            seen_episode_names.update(seen_siblings)

        # Prune arr-sourced episodes whose source subtitle is no longer on disk, so the
        # dashboard mirrors the directory. Their exported translation files are preserved.
        if file_api_error:
            logger.warning(f"{project_name}: skipping series pruning due to Sonarr episode file API error/empty response.")
        elif episode_filter is None:
            removed = await loop.run_in_executor(
                None, self._prune_removed_episodes, project_name, seen_episode_names, result
            )
            result.removed_episodes += removed

        # Persist stats
        meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
        if meta and episode_filter is None:
            meta["arr_total_episodes"] = total_with_file
            meta["arr_translated_episodes"] = translated_count
            await loop.run_in_executor(None, storage.save_project_metadata, project_name, meta)

        return {
            "title": series_title,
            "project_name": project_name,
            "total_episodes": len(episodes),
            "episodes_with_file": total_with_file,
            "imported_episodes": imported_count,
            "translated_on_disk": translated_count,
            "is_new": is_new,
        }

    def _prune_removed_episodes(self, project_name: str, seen_episode_names: set, result) -> int:
        """Remove arr-sourced episodes whose source subtitle is gone from disk, plus their
        exported translation files. Manually-uploaded episodes are left untouched.

        Safety valve: if NOTHING was seen this sync but episodes exist, treat it as a
        transient scan failure (e.g. an unmounted drive) and skip removal rather than
        wiping the whole project.
        """
        from utils import storage
        existing_eps = storage.list_episodes(project_name)
        if not existing_eps:
            return 0
        if not seen_episode_names:
            result.warnings.append(
                f"{project_name}: no source subtitles found this sync — skipping episode "
                "removal (possible transient scan failure / unmounted path)."
            )
            return 0

        # Match case/whitespace-insensitively: on Windows the regenerated episode_name
        # can differ from the stored folder name only by case, which would otherwise
        # make us delete a still-present episode.
        seen_norm = {s.strip().casefold() for s in seen_episode_names}
        removed = 0
        for ep_name in existing_eps:
            if ep_name in seen_episode_names or ep_name.strip().casefold() in seen_norm:
                continue
            em = storage.load_episode_metadata(project_name, ep_name) or {}
            if not em.get("arr_source"):
                continue  # leave manual uploads alone

            # If the episode's media path or parent folder is unreachable, do NOT prune it.
            media_path = em.get("arr_media_path")
            if media_path:
                parent_dir = str(Path(media_path).parent)
                if parent_dir in result.unreachable_paths:
                    logger.warning(f"Sync: skipping removal of {project_name}/{ep_name} because parent directory is in unreachable_paths: {parent_dir}")
                    continue
                try:
                    if not Path(parent_dir).exists():
                        if parent_dir not in result.unreachable_paths:
                            result.unreachable_paths.append(parent_dir)
                        logger.warning(f"Sync: skipping removal of {project_name}/{ep_name} because parent directory is unreachable: {parent_dir}")
                        continue
                except Exception as e:
                    if parent_dir not in result.unreachable_paths:
                        result.unreachable_paths.append(parent_dir)
                    logger.warning(f"Sync: skipping removal of {project_name}/{ep_name} due to directory access error: {e}")
                    continue

            # INT-P0-01: Under NO circumstances delete target subtitles (.el.srt / .el.ass) during sync pruning!
            # Translated subtitles represent expensive LLM work and user edits.
            # Only unregister the missing episode from project storage.
            storage.delete_episode(project_name, ep_name)
            removed += 1
            logger.info(f"Sync: pruned removed episode {project_name}/{ep_name} from project storage (preserved target subtitles on disk).")
        return removed

    def _sync_secondary_formats(
        self, project_name: str, base_episode_name: str, scan, media_path: str,
        base_extra_meta: Dict, create_new: bool, result: SyncResult, storage
    ) -> set:
        """Import/maintain sibling episodes for every non-primary source format in
        scan.source_subs. Returns the set of sibling episode names that are 'seen'
        (on disk) so the caller can protect them from _prune_removed_episodes.
        """
        seen_siblings = set()
        if not scan or not scan.source_subs or len(scan.source_subs) <= 1:
            base_meta = storage.load_episode_metadata(project_name, base_episode_name)
            if base_meta and base_meta.get("arr_alt_formats"):
                base_meta["arr_alt_formats"] = []
                storage.save_episode_metadata(project_name, base_episode_name, base_meta, update_stats=False)
            return seen_siblings

        media = Path(media_path)
        if not media.parent.exists():
            return seen_siblings
        stem = self.scanner._get_clean_stem(media)
        try:
            files = [f for f in media.parent.iterdir() if f.is_file()]
        except Exception as e:
            logger.warning(f"Failed to list directory for secondary formats: {e}")
            files = []

        # Two source files can share an extension (e.g. "Show.en.srt" AND
        # "Show.eng.srt") — both would otherwise map to the same sibling episode
        # name and fight over its fingerprint every sync. scan.source_subs is
        # already sorted deterministically (richest format, then alphabetical), so
        # keeping the first occurrence per extension is a stable, repeatable pick.
        seen_exts = set()
        deduped_secondaries = []
        for source_path in scan.source_subs[1:]:
            ext = Path(source_path).suffix.lstrip('.').lower()
            if ext in seen_exts:
                continue
            seen_exts.add(ext)
            deduped_secondaries.append(source_path)

        for source_path in deduped_secondaries:
            ext = Path(source_path).suffix.lstrip('.').lower()
            sib_name = secondary_episode_name(base_episode_name, ext)
            existing_meta = storage.load_episode_metadata(project_name, sib_name)

            if existing_meta is None and not create_new:
                continue

            fp = self._fingerprint(source_path)
            tgt_path = self.scanner.same_format_target(files, stem, "." + ext)
            has_target = bool(tgt_path)

            should_import = True
            if existing_meta:
                data_file = storage.episode_dir(project_name, sib_name) / "data.json"
                old_fp = existing_meta.get("arr_sub_fingerprint")
                if not data_file.exists() or not existing_meta.get("arr_media_path"):
                    should_import = True
                elif fp is None:
                    should_import = False
                elif old_fp and fp == old_fp:
                    should_import = False
                elif old_fp and fp != old_fp:
                    result.updated_episodes += 1
                else:
                    should_import = False

            if should_import:
                srt_content = self._read_file(source_path)
                if not srt_content:
                    result.warnings.append(f"Cannot read sibling subtitle: {source_path}")
                    continue

                target_srt_content = None
                if has_target and tgt_path:
                    already_translated = bool(
                        existing_meta and (existing_meta.get("translated")
                                           or existing_meta.get("translation_status") == "completed")
                    )
                    if not already_translated:
                        target_srt_content = self._read_file(tgt_path)

                from utils.source_clean import import_and_clean_srt
                # Clean/merge extra metadata
                extra = dict(base_extra_meta)
                extra.pop("arr_target_path", None)
                extra.pop("arr_has_target", None)
                extra.pop("arr_sub_path", None)
                extra.pop("arr_sub_fingerprint", None)

                import_and_clean_srt(
                    project_name,
                    sib_name,
                    srt_content,
                    filename=Path(source_path).name,
                    fingerprint=fp,
                    target_srt_content=target_srt_content,
                    extra_metadata={
                        "arr_source": True,
                        "arr_sub_path": source_path,
                        "arr_media_path": media_path,
                        "arr_secondary_of": base_episode_name,
                        "arr_source_format": ext,
                        "arr_has_target": has_target,
                        "arr_target_path": tgt_path if has_target else None,
                        "arr_translated_from_fingerprint": existing_meta.get("arr_translated_from_fingerprint") if existing_meta else None,
                        **extra,
                    },
                )
                result.new_episodes += 1
            else:
                meta_changed = False
                if existing_meta.get("arr_has_target") != has_target:
                    existing_meta["arr_has_target"] = has_target
                    meta_changed = True
                
                tgt_path_on_disk = tgt_path if has_target else None
                if existing_meta.get("arr_target_path") != tgt_path_on_disk:
                    existing_meta["arr_target_path"] = tgt_path_on_disk
                    meta_changed = True

                if existing_meta.get("arr_sub_path") != source_path:
                    existing_meta["arr_sub_path"] = source_path
                    existing_meta["original_filename"] = Path(source_path).name
                    meta_changed = True

                # Keep the export target current when the media file moved (see base episode).
                if existing_meta.get("arr_media_path") != media_path:
                    existing_meta["arr_media_path"] = media_path
                    meta_changed = True

                if meta_changed:
                    storage.save_episode_metadata(project_name, sib_name, existing_meta, False)

            seen_siblings.add(sib_name)

        # Collect formats for all siblings whose source file is on disk
        seen_formats = []
        for source_path in deduped_secondaries:
            ext = Path(source_path).suffix.lstrip('.').lower()
            sib_name = secondary_episode_name(base_episode_name, ext)
            if storage.load_episode_metadata(project_name, sib_name) is not None:
                seen_formats.append(ext)

        base_meta = storage.load_episode_metadata(project_name, base_episode_name)
        if base_meta:
            if base_meta.get("arr_alt_formats") != seen_formats:
                base_meta["arr_alt_formats"] = seen_formats
                storage.save_episode_metadata(project_name, base_episode_name, base_meta, update_stats=False)

        return seen_siblings

    # ------------------------------------------------------------------
    # Movie sync
    # ------------------------------------------------------------------

    async def _sync_movie(
        self, movie_data: Dict, project_name: str,
        result: SyncResult, storage, parse_srt, existing_projects_set: set
    ) -> Optional[Dict]:
        """Create/update project and import subtitle for one movie."""
        loop = asyncio.get_running_loop()
        movie_title = movie_data.get("title", "Unknown")
        radarr_id = movie_data.get("id")

        # Radarr includes movieFile inline if the movie has been downloaded
        movie_file = movie_data.get("movieFile")
        raw_media_path = movie_file.get("path", "") if movie_file else ""

        is_new = project_name not in existing_projects_set
        if is_new:
            # Reserve before the first await so two movies resolving to the same
            # project_name can't both create it (see _sync_series).
            existing_projects_set.add(project_name)
            await loop.run_in_executor(None, storage.create_project, project_name, {
                "show_name": movie_title,
                "target_language": self.target_language,
                "type": "movie",
                "arr_source": True,
                "arr_media_type": "movie",
                "arr_radarr_id": radarr_id,
                "arr_disabled": False,
                "arr_last_sync": datetime.now().isoformat(),
            })
            result.new_projects += 1
        else:
            try:
                meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
            except Exception as e:
                logger.warning(f"Failed to load metadata for existing project '{project_name}': {e}")
                meta = None
            if meta:
                meta["arr_last_sync"] = datetime.now().isoformat()
                meta["arr_radarr_id"] = radarr_id
                meta["arr_media_type"] = "movie"
                meta["type"] = "movie"
                await loop.run_in_executor(None, storage.save_project_metadata, project_name, meta)

        if not raw_media_path:
            result.skipped_no_subs += 1
            return {
                "title": movie_title,
                "project_name": project_name,
                "has_file": False,
                "is_new": is_new,
            }

        media_path = self.resolver.resolve(raw_media_path)
        
        # Check parent directory accessibility
        parent_dir = str(Path(media_path).parent)
        def check_dir(p):
            try:
                dp = Path(p)
                if not dp.exists():
                    return False
                os.listdir(p)
                return True
            except Exception:
                return False
        accessible = await loop.run_in_executor(None, check_dir, parent_dir)
        if not accessible:
            if parent_dir not in result.unreachable_paths:
                result.unreachable_paths.append(parent_dir)

        scan = await loop.run_in_executor(None, self.scanner.scan_media_file, media_path)

        project_meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
        prefer_ass = prefer_ass_for_project(project_meta)

        has_target = scan.has_target_sub
        imported = False
        migrated_sibling = None

        # Movies are single-episode-per-project: the episode folder always mirrors
        # the (caller-resolved, possibly adopted) project name. Previously this was
        # recomputed independently from movie_data, which could diverge from
        # project_name — e.g. an adopted legacy project ("Snow Queen") would get a
        # freshly-suffixed episode ("Snow Queen (2002)"), silently orphaning the
        # existing episode's translations inside the very project meant to preserve them.
        episode_name = project_name

        if scan.has_source_sub:
            fingerprint = await loop.run_in_executor(None, self._fingerprint, scan.source_sub_path)
            existing_meta = await loop.run_in_executor(None, storage.load_episode_metadata, project_name, episode_name)

            should_import = True
            if existing_meta:
                data_file = storage.episode_dir(project_name, episode_name) / "data.json"
                old_fp = existing_meta.get("arr_sub_fingerprint")
                if not data_file.exists() or not existing_meta.get("arr_media_path"):
                    should_import = True
                elif fingerprint is None:
                    should_import = False
                    imported = True
                elif old_fp and fingerprint == old_fp:
                    should_import = False
                    imported = True
                elif old_fp and fingerprint != old_fp:
                    result.updated_episodes += 1
                else:
                    # No stored fingerprint (episode predates fingerprinting): treat as
                    # up to date rather than re-importing over it, matching the series
                    # and secondary-format paths.
                    should_import = False
                    imported = True

            if should_import and existing_meta:
                migrated_sibling = await loop.run_in_executor(
                    None, self._migrate_primary_format_flip,
                    project_name, episode_name, scan, existing_meta,
                )
                if migrated_sibling:
                    existing_meta = None

            if should_import:
                srt_content = await loop.run_in_executor(None, self._read_file, scan.source_sub_path)
                if srt_content:
                    target_srt_content = None
                    if has_target and scan.target_sub_path:
                        # Seed translations from the on-disk target only for a brand-new or
                        # not-yet-translated movie. import_and_clean_srt re-aligns the target
                        # over the source and rewrites data.json, so doing this for an already
                        # translated movie replaces in-app edits with a possibly-stale export.
                        already_translated = bool(
                            existing_meta and (existing_meta.get("translated")
                                               or existing_meta.get("translation_status") == "completed")
                        )
                        if not already_translated:
                            target_srt_content = await loop.run_in_executor(None, self._read_file, scan.target_sub_path)

                    from utils.source_clean import import_and_clean_srt
                    def _do_import_movie():
                        import_and_clean_srt(
                            project_name,
                            episode_name,
                            srt_content,
                            filename=Path(scan.source_sub_path).name,
                            fingerprint=fingerprint,
                            target_srt_content=target_srt_content,
                            extra_metadata={
                                "arr_source": True,
                                "arr_sub_path": scan.source_sub_path,
                                "arr_media_path": media_path,
                                "arr_radarr_id": radarr_id,
                                "arr_has_target": has_target,
                                "arr_target_path": scan.target_sub_path if has_target else None,
                                "arr_translated_from_fingerprint": existing_meta.get("arr_translated_from_fingerprint") if existing_meta else None,
                            },
                        )
                    await loop.run_in_executor(None, _do_import_movie)
                    if has_target:
                        self._mark_queue_item_completed(project_name, episode_name)
                    imported = True
                    result.new_episodes += 1
                else:
                    result.warnings.append(f"Cannot read subtitle for movie: {movie_title}")
            else:
                # Update existing_meta if target sub status changed on disk
                meta_changed = False
                if existing_meta.get("arr_has_target") != has_target:
                    existing_meta["arr_has_target"] = has_target
                    if has_target:
                        existing_meta["translated"] = True
                        existing_meta["translation_status"] = "completed"
                        self._mark_queue_item_completed(project_name, episode_name)
                    meta_changed = True
                
                tgt_path_on_disk = scan.target_sub_path if has_target else None
                if existing_meta.get("arr_target_path") != tgt_path_on_disk:
                    existing_meta["arr_target_path"] = tgt_path_on_disk
                    meta_changed = True

                if existing_meta.get("arr_sub_path") != scan.source_sub_path:
                    existing_meta["arr_sub_path"] = scan.source_sub_path
                    existing_meta["original_filename"] = Path(scan.source_sub_path).name
                    meta_changed = True

                # Keep the export target current when the media file moved (see series sync).
                if existing_meta.get("arr_media_path") != media_path:
                    existing_meta["arr_media_path"] = media_path
                    meta_changed = True

                if meta_changed:
                    await loop.run_in_executor(None, storage.save_episode_metadata, project_name, episode_name, existing_meta, False)
                imported = True
        else:

            # Retain the movie episode so it is visible in the UI with 'No Subtitle' status and can be extracted
            existing_meta = await loop.run_in_executor(None, storage.load_episode_metadata, project_name, episode_name)
            if not existing_meta:
                new_meta = {
                    "arr_source": True,
                    "arr_media_path": media_path,
                    "arr_radarr_id": radarr_id,
                    "episode_title": movie_title,
                    "has_source_sub": False,
                    "arr_has_target": has_target,
                    "arr_target_path": scan.target_sub_path if has_target else None,
                    "translated": has_target,
                    "line_count": 0,
                    "original_filename": Path(media_path).name,
                }
                def _init_empty_movie():
                    ep_dir = storage.episode_dir(project_name, episode_name)
                    ep_dir.mkdir(parents=True, exist_ok=True)
                    storage.save_episode(project_name, episode_name, [], new_meta)
                await loop.run_in_executor(None, _init_empty_movie)
                if has_target:
                    self._mark_queue_item_completed(project_name, episode_name)
                imported = True
                result.new_episodes += 1
            else:
                meta_changed = False
                if existing_meta.get("arr_media_path") != media_path:
                    existing_meta["arr_media_path"] = media_path
                    meta_changed = True
                if existing_meta.get("arr_has_target") != has_target:
                    existing_meta["arr_has_target"] = has_target
                    if has_target:
                        existing_meta["translated"] = True
                        existing_meta["translation_status"] = "completed"
                        self._mark_queue_item_completed(project_name, episode_name)
                    meta_changed = True
                if existing_meta.get("has_source_sub") is not False and not existing_meta.get("line_count") and not storage.original_subtitle_exists(project_name, episode_name):
                    existing_meta["has_source_sub"] = False
                    meta_changed = True
                if meta_changed:
                    await loop.run_in_executor(None, storage.save_episode_metadata, project_name, episode_name, existing_meta, False)
                imported = True
            result.skipped_no_subs += 1

        # Mirror the directory: prune the episode if its media file AND source subtitle are gone
        _seen = {episode_name}
        if migrated_sibling:
            _seen.add(migrated_sibling)


        # No sidecar (or no .ass on an ass-preferring project): look inside the container.
        # A queued extraction counts as "seen" so prune leaves the episode (and its
        # export) alone until the job lands.
        if self._wants_embedded_probe(scan, prefer_ass):
            from utils.translation_queue import PRIORITY_SYNC
            _seen.update(await self._run_embedded_probes(
                project_name,
                [{
                    "episode_name": episode_name,
                    "media_path": media_path,
                    "import_metadata": {"arr_radarr_id": radarr_id},
                }],
                PRIORITY_SYNC,
                result,
            ))
        if scan.has_source_sub:
            project_meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
            create_alt = self._create_alt_formats(project_meta, prefer_ass)

            base_extra_meta = {
                "arr_radarr_id": radarr_id,
            }
            seen_siblings = await loop.run_in_executor(
                None,
                self._sync_secondary_formats,
                project_name,
                episode_name,
                scan,
                media_path,
                base_extra_meta,
                create_alt,
                result,
                storage
            )
            _seen.update(seen_siblings)

        result.removed_episodes += await loop.run_in_executor(
            None, self._prune_removed_episodes, project_name, _seen, result
        )

        # Persist stats
        meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
        if meta:
            meta["arr_total_episodes"] = 1 if scan.has_source_sub else 0
            meta["arr_translated_episodes"] = 1 if has_target else 0
            await loop.run_in_executor(None, storage.save_project_metadata, project_name, meta)

        return {
            "title": movie_title,
            "project_name": project_name,
            "has_source_sub": scan.has_source_sub,
            "has_target_sub": has_target,
            "imported": imported,
            "is_new": is_new,
        }

    # ------------------------------------------------------------------
    # Webhook processing
    # ------------------------------------------------------------------

    async def process_sonarr_webhook(
        self, payload: Dict, storage, parse_srt,
    ) -> Optional[str]:
        """Handle a Sonarr 'Download' webhook event.

        Returns the project_name + episode_name of the imported episode,
        or None if nothing was imported.
        """
        event_type = payload.get("eventType", "")
        if event_type == "Test":
            return None

        series = payload.get("series") or {}
        episodes = payload.get("episodes") or []
        episode_file = payload.get("episodeFile") or {}

        series_title = series.get("title", "Unknown")
        raw_media_path = episode_file.get("path", "")

        if not raw_media_path or not episodes:
            return None

        loop = asyncio.get_running_loop()
        media_path = self.resolver.resolve(raw_media_path)
        scan = await loop.run_in_executor(None, self.scanner.scan_media_file, media_path)

        project_name = make_project_name(series_title)
        ep_data = episodes[0]  # Sonarr sends one episode per download event
        season = ep_data.get("seasonNumber", 0)
        episode_num = ep_data.get("episodeNumber", 0)
        episode_name = make_episode_name(season, episode_num)

        # Resolve prefer_ass from the existing project, falling back to the seriesType
        # in the payload so a brand-new anime show gets the behaviour on its very first
        # download rather than only after the next full sync.
        proj_meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name) or {}
        if not proj_meta.get("series_type"):
            proj_meta = {**proj_meta, "series_type": series.get("seriesType", "standard")}
        prefer_ass = prefer_ass_for_project(proj_meta)

        if self._wants_embedded_probe(scan, prefer_ass):
            from utils.translation_queue import PRIORITY_WEBHOOK
            new_meta = self._new_project_metadata(series_title, "series")
            new_meta["series_type"] = proj_meta.get("series_type", "standard")
            queued = await self._run_embedded_probes(
                project_name,
                [{
                    "episode_name": episode_name,
                    "media_path": media_path,
                    "import_metadata": {
                        "arr_sonarr_series_id": series.get("id"),
                        "arr_sonarr_episode_id": ep_data.get("id"),
                        "episode_title": ep_data.get("title", ""),
                        "season": season,
                        "episode": episode_num,
                    },
                }],
                PRIORITY_WEBHOOK,
                SyncResult(),
                project_metadata=new_meta,
            )
            if queued and not scan.has_source_sub:
                logger.info(f"Sonarr webhook: queued embedded extraction for {raw_media_path}")
                return None

        # INT-P1-04: Reuse existing translation if media was upgraded/renamed
        existing_meta = await loop.run_in_executor(None, storage.load_episode_metadata, project_name, episode_name)
        reused_trans = False
        if existing_meta and not scan.has_target_sub:
            reused_trans = await loop.run_in_executor(
                None,
                self._reuse_and_export_existing_translation,
                project_name, episode_name, media_path, scan, storage, existing_meta
            )

        if not scan.has_source_sub:
            if reused_trans:
                logger.info(f"Sonarr webhook: re-exported existing translation for upgraded file {raw_media_path} (no new source sub needed).")
                return None
            logger.info(f"Sonarr webhook: no source subtitle found for {raw_media_path}")
            return None

        result = SyncResult()
        await self._import_episode_file(
            project_name=project_name,
            series_title=series_title,
            episode_name=episode_name,
            media_path=media_path,
            scan=scan,
            ep_meta={
                "episode_title": ep_data.get("title", ""),
                "season": season,
                "episode": episode_num,
                "arr_sonarr_series_id": series.get("id"),
                "arr_sonarr_episode_id": ep_data.get("id"),
            },
            result=result,
            storage=storage,
            parse_srt=parse_srt,
            media_type="series",
        )

        return f"{project_name}/{episode_name}" if result.new_episodes > 0 or result.updated_episodes > 0 else None

    async def process_radarr_webhook(
        self, payload: Dict, storage, parse_srt,
    ) -> Optional[str]:
        """Handle a Radarr 'Download' webhook event.

        Returns the project_name/episode_name of the imported movie,
        or None if nothing was imported.
        """
        event_type = payload.get("eventType", "")
        if event_type == "Test":
            return None

        movie = payload.get("movie") or {}
        movie_file = payload.get("movieFile") or {}

        movie_title = movie.get("title", "Unknown")
        raw_media_path = movie_file.get("path", "")

        if not raw_media_path:
            return None

        loop = asyncio.get_running_loop()
        media_path = self.resolver.resolve(raw_media_path)
        scan = await loop.run_in_executor(None, self.scanner.scan_media_file, media_path)

        radarr_id = movie.get("id")
        adopted_name = await loop.run_in_executor(None, self._find_project_by_radarr_id, storage, radarr_id)
        project_name = movie_project_name(movie, adopted=adopted_name)
        episode_name = project_name

        proj_meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name) or {}
        prefer_ass = prefer_ass_for_project(proj_meta)

        if self._wants_embedded_probe(scan, prefer_ass):
            from utils.translation_queue import PRIORITY_WEBHOOK
            queued = await self._run_embedded_probes(
                project_name,
                [{
                    "episode_name": episode_name,
                    "media_path": media_path,
                    "import_metadata": {"arr_radarr_id": radarr_id},
                }],
                PRIORITY_WEBHOOK,
                SyncResult(),
                project_metadata=self._new_project_metadata(movie_title, "movie"),
            )
            if queued and not scan.has_source_sub:
                logger.info(f"Radarr webhook: queued embedded extraction for {raw_media_path}")
                return None

        # INT-P1-04: Reuse existing translation if media was upgraded/renamed
        existing_meta = await loop.run_in_executor(None, storage.load_episode_metadata, project_name, episode_name)
        reused_trans = False
        if existing_meta and not scan.has_target_sub:
            reused_trans = await loop.run_in_executor(
                None,
                self._reuse_and_export_existing_translation,
                project_name, episode_name, media_path, scan, storage, existing_meta
            )

        if not scan.has_source_sub:
            if reused_trans:
                logger.info(f"Radarr webhook: re-exported existing translation for upgraded file {raw_media_path} (no new source sub needed).")
                return None
            logger.info(f"Radarr webhook: no source subtitle found for {raw_media_path}")
            return None

        result = SyncResult()
        await self._import_episode_file(
            project_name=project_name,
            series_title=movie_title,
            episode_name=episode_name,
            media_path=media_path,
            scan=scan,
            ep_meta={
                "arr_radarr_id": movie.get("id"),
            },
            result=result,
            storage=storage,
            parse_srt=parse_srt,
            media_type="movie",
        )

        return f"{project_name}/{episode_name}" if result.new_episodes > 0 or result.updated_episodes > 0 else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _import_episode_file(
        self, project_name: str, series_title: str, episode_name: str,
        media_path: str, scan, ep_meta: Dict,
        result: SyncResult, storage, parse_srt, media_type: str,
    ):
        """Shared import logic for both webhook and full-sync paths."""
        loop = asyncio.get_running_loop()
        existing_projects = await loop.run_in_executor(None, storage.list_projects)
        if project_name not in existing_projects:
            await loop.run_in_executor(None, storage.create_project, project_name, {
                "show_name": series_title,
                "target_language": self.target_language,
                "type": media_type,
                "arr_source": True,
                "arr_media_type": media_type,
                "arr_disabled": False,
                "arr_last_sync": datetime.now().isoformat(),
            })
            result.new_projects += 1

        fingerprint = await loop.run_in_executor(None, self._fingerprint, scan.source_sub_path)
        existing_meta = await loop.run_in_executor(None, storage.load_episode_metadata, project_name, episode_name)

        if existing_meta:
            old_fp = existing_meta.get("arr_sub_fingerprint")
            if fingerprint is None:
                return  # Fingerprint failed. Prevent spurious update.
            if old_fp and fingerprint == old_fp:
                project_meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
                create_alt = self._create_alt_formats(project_meta, prefer_ass_for_project(project_meta))
                await loop.run_in_executor(
                    None,
                    self._sync_secondary_formats,
                    project_name,
                    episode_name,
                    scan,
                    media_path,
                    ep_meta,
                    create_alt,
                    result,
                    storage
                )
                return  # Unchanged
            if old_fp:
                result.updated_episodes += 1

            # A newly-arrived .ass outranks the .srt this episode was imported from;
            # move the old episode to its sibling name so its translations survive.
            migrated_sibling = await loop.run_in_executor(
                None, self._migrate_primary_format_flip,
                project_name, episode_name, scan, existing_meta,
            )
            if migrated_sibling:
                existing_meta = None

        srt_content = await loop.run_in_executor(None, self._read_file, scan.source_sub_path)
        if not srt_content:
            result.warnings.append(f"Cannot read: {scan.source_sub_path}")
            return
            
        target_srt_content = None
        if scan.has_target_sub and scan.target_sub_path:
            target_srt_content = await loop.run_in_executor(None, self._read_file, scan.target_sub_path)

        from utils.source_clean import import_and_clean_srt
        def _do_import_webhook():
            import_and_clean_srt(
                project_name,
                episode_name,
                srt_content,
                filename=Path(scan.source_sub_path).name,
                fingerprint=fingerprint,
                target_srt_content=target_srt_content,
                extra_metadata={
                    "arr_source": True,
                    "arr_sub_path": scan.source_sub_path,
                    "arr_media_path": media_path,
                    "arr_has_target": scan.has_target_sub,
                    "arr_translated_from_fingerprint": existing_meta.get("arr_translated_from_fingerprint") if existing_meta else None,
                    **ep_meta,
                },
            )
        await loop.run_in_executor(None, _do_import_webhook)
        if scan.has_target_sub:
            self._mark_queue_item_completed(project_name, episode_name)
        result.new_episodes += 1

        project_meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
        create_alt = self._create_alt_formats(project_meta, prefer_ass_for_project(project_meta))
        await loop.run_in_executor(
            None,
            self._sync_secondary_formats,
            project_name,
            episode_name,
            scan,
            media_path,
            ep_meta,
            create_alt,
            result,
            storage
        )

    @staticmethod
    def _find_project_by_radarr_id(storage, radarr_id) -> Optional[str]:
        """Scan existing projects for one already linked to this Radarr movie id.

        Used by single-movie paths (webhook) that don't have full_sync's
        precomputed adoption map, so a legacy-named project is still adopted
        rather than duplicated. A full project scan is fine here — this only
        runs per webhook event, not in a per-movie sync loop.
        """
        if radarr_id is None:
            return None
        try:
            for p_name in storage.list_projects():
                meta = storage.load_project_metadata(p_name)
                if not meta:
                    continue
                rid = meta.get("arr_radarr_id")
                if rid is not None and str(rid) == str(radarr_id):
                    return p_name
        except Exception as e:
            logger.warning(f"Failed to scan projects for radarr_id {radarr_id}: {e}")
        return None

    # ------------------------------------------------------------------
    # Embedded subtitle discovery (docs/PLAN_embedded_ass_extraction.md)
    # ------------------------------------------------------------------

    def _get_embedded_tools(self):
        """Resolve the ffmpeg pair once per engine instead of once per media file."""
        if not self._embedded_tools_resolved:
            from integrations import embedded_subs
            from utils import storage
            self._embedded_tools = embedded_subs.resolve_tools(storage.load_global_config())
            self._embedded_tools_resolved = True
            if not self._embedded_tools:
                logger.warning(
                    "Embedded subtitle extraction is enabled but ffmpeg/ffprobe was not found. "
                    "Set ffmpeg_path in Settings or install ffmpeg on PATH."
                )
        return self._embedded_tools

    @staticmethod
    def _create_alt_formats(project_meta: Optional[Dict], prefer_ass: bool) -> bool:
        """Whether to create dual-format sibling episodes for non-primary source formats.

        An explicit ``translate_all_source_formats`` on the project always wins. When it
        is unset, ass-preferring projects default to **on**: the point of preferring
        ``.ass`` is to get the typeset track, and the ``.srt`` that was there first is
        still worth translating so viewers keep both options.
        """
        if not project_meta:
            return False
        from utils import storage
        return bool(storage.get_project_setting(
            project_meta, "translate_all_source_formats", prefer_ass
        ))

    @staticmethod
    def _has_ass_source(scan) -> bool:
        """True when a source-language .ass/.ssa sidecar is already on disk."""
        return any(
            Path(p).suffix.lower() in _ASS_EXTENSIONS for p in (scan.source_subs or [])
        )

    def _wants_embedded_probe(self, scan, prefer_ass: bool) -> bool:
        """Should this media file be probed for a muxed ASS or SRT track?

        Two triggers:
        1. No source subtitle on disk at all (not scan.has_source_sub) -> probe for any embedded text track (ASS or SRT).
        2. prefer_ass is True and no .ass exists on disk (an .srt-only episode still needs its typeset track).
        """
        if not self.embedded_extraction:
            return False
        if not scan.has_source_sub:
            return True
        if prefer_ass and not self._has_ass_source(scan):
            return True
        return False

    @staticmethod
    def _migrate_primary_format_flip(project_name: str, episode_name: str,
                                     scan, existing_meta: Dict) -> Optional[str]:
        """Move an episode aside when its primary source format changes underneath it.

        An ``.ass`` appearing next to the ``.srt`` an episode was imported from (whether
        extracted from the container or dropped in by hand) makes the ``.ass`` the new
        primary, because ``_EXT_PRIORITY`` ranks it first. Left alone, the base episode
        keeps its name, gets the new fingerprint, and ``import_and_clean_srt`` rewrites
        its ``data.json`` — silently destroying every translation and edit made against
        the old format.

        Renaming it to ``"<episode> [srt]"`` is precisely where the dual-format sibling
        model would have put it anyway: the next ``_sync_secondary_formats`` pass finds
        it, matches its unchanged fingerprint, and leaves its translations alone.

        Returns the sibling episode name when the episode was moved (so the caller can
        import the new primary into a clean slot and protect the sibling from prune),
        or None when nothing needed to happen.
        """
        from utils import storage

        old_ext = (existing_meta.get("original_extension") or "").lstrip(".").lower()
        if not old_ext:
            old_ext = Path(existing_meta.get("arr_sub_path") or "").suffix.lstrip(".").lower()
        new_ext = Path(scan.source_sub_path or "").suffix.lstrip(".").lower()
        if not old_ext or not new_ext or old_ext == new_ext:
            return None

        # Only migrate when the old format is STILL on disk. If it's gone, there is no
        # sibling for it to become — the source genuinely changed and a re-import is right.
        if not any(Path(p).suffix.lstrip(".").lower() == old_ext for p in (scan.source_subs or [])):
            return None

        sibling = secondary_episode_name(episode_name, old_ext)
        if storage.load_episode_metadata(project_name, sibling) is not None:
            return None  # a sibling for that format already exists; never clobber it
        if not storage.rename_episode(project_name, episode_name, sibling):
            return None

        sib_meta = storage.load_episode_metadata(project_name, sibling) or {}
        sib_meta["arr_secondary_of"] = episode_name
        sib_meta["arr_source_format"] = old_ext
        storage.save_episode_metadata(project_name, sibling, sib_meta, False)
        logger.info(
            f"Primary subtitle format for {project_name}/{episode_name} changed "
            f"{old_ext} -> {new_ext}; moved the existing episode to '{sibling}' so its "
            f"translations survive."
        )
        return sibling

    def _new_project_metadata(self, title: str, media_type: str) -> Dict:
        """Metadata for a project the extraction job may have to create itself.

        Webhook paths queue an extraction for a show/movie that has no project yet.
        Creating it here and enqueuing separately would race the worker, so the
        metadata travels with the job and the worker creates the project only if it
        is still missing when the job runs.
        """
        return {
            "show_name": title,
            "target_language": self.target_language,
            "type": media_type,
            "arr_source": True,
            "arr_media_type": media_type,
            "arr_disabled": False,
            "arr_last_sync": datetime.now().isoformat(),
        }

    async def _run_embedded_probes(
        self, project_name: str, candidates: List[Dict], priority: int, result: SyncResult,
        project_metadata: Optional[Dict] = None,
    ) -> set:
        """Probe several containers concurrently; return the episode names that got a job.

        Bounded at 4: ffprobe is a header read, but on a network share a few hundred
        unbounded probes would still saturate the link.
        """
        loop = asyncio.get_running_loop()
        sem = asyncio.Semaphore(4)
        queued: set = set()

        async def probe_one(candidate: Dict):
            async with sem:
                try:
                    ok = await loop.run_in_executor(
                        None, self._probe_and_enqueue_extraction,
                        project_name, candidate["episode_name"], candidate["media_path"],
                        candidate["import_metadata"], priority, result, project_metadata,
                    )
                except Exception as e:
                    logger.warning(
                        f"Embedded probe failed for {project_name}/{candidate['episode_name']}: {e}"
                    )
                    return
            if ok:
                queued.add(candidate["episode_name"])

        await asyncio.gather(*[probe_one(c) for c in candidates])
        return queued

    def _probe_and_enqueue_extraction(
        self, project_name: str, episode_name: str, media_path: str,
        import_metadata: Dict, priority: int, result: SyncResult,
        project_metadata: Optional[Dict] = None,
        extra_options: Optional[Dict] = None,
    ) -> bool:
        """Probe a container that has no source sidecar and queue an extraction job.

        Runs during sync because ffprobe only reads container headers — cheap even
        over SMB, and cached by media fingerprint so a library full of files with no
        embedded subtitles isn't re-probed on every pass. The *extraction* (which streams
        the whole file) is deliberately not done here; it goes to the worker.

        ``extra_options`` is merged over the job options last, so a caller that needs
        a different follow-up than the library-wide default (on-demand requests
        translate immediately, with their own options) can say so per job.

        Returns True when a job was queued.
        """
        from integrations import embedded_subs
        from utils import media_probe_cache

        tools = self._get_embedded_tools()
        if not tools:
            return False

        fingerprint = self._fingerprint(media_path)
        cached = media_probe_cache.get(media_path, fingerprint)
        if cached is not None:
            tracks = [embedded_subs.SubtitleTrack.from_dict(t) for t in cached]
        else:
            tracks = embedded_subs.probe_subtitle_tracks(media_path, tools)
            media_probe_cache.put(media_path, fingerprint, embedded_subs.describe_candidates(tracks))

        if not tracks:
            return False

        track = embedded_subs.select_track(tracks, self.source_lang_code, self.embedded_keywords)
        if not track:
            # Something is in there, it's just not usable — say so once, because
            # "found 3 PGS tracks" is a very different problem from "found nothing".
            image_tracks = [t for t in tracks if t.is_image]
            if image_tracks:
                result.warnings.append(
                    f"{project_name}/{episode_name}: {len(image_tracks)} image-based subtitle "
                    f"track(s) found in the media file — these need OCR and cannot be extracted."
                )
            return False

        from services.queue_service import enqueue_extraction
        enqueue_extraction(
            project_name,
            episode_name,
            priority,
            {
                "media_path": media_path,
                "stream_index": track.index,
                "source_lang_code": self.source_lang_code,
                "output_format": track.output_format,
                "track": track.to_dict(),
                "candidates": embedded_subs.describe_candidates(tracks),
                "import_metadata": import_metadata,
                "project_metadata": project_metadata,
                "auto_translate": self.embedded_auto_translate,
                **(extra_options or {}),
            },
        )
        logger.info(
            f"Queued embedded subtitle extraction for {project_name}/{episode_name}: "
            f"stream {track.index} [{track.output_format.upper()}] ({track.title or 'untitled'}, {track.frames or '?'} events"
            f"{', deprioritized: ' + '; '.join(track.penalty_reasons) if track.penalized else ''})"
        )
        return True

    @staticmethod
    def _fingerprint(path: Optional[str]) -> Optional[str]:
        """mtime_ns_size fingerprint for change detection."""
        if not path:
            return None
        try:
            stat = Path(path).stat()
            return f"{int(stat.st_mtime_ns)}_{stat.st_size}"
        except OSError:
            return None

    @staticmethod
    def _read_file(path: Optional[str]) -> Optional[str]:
        """Read subtitle file with encoding fallback (``utils.text_repair.decode_subtitle_bytes``).

        utf-16 is only attempted when the file actually starts with a utf-16 BOM
        (\\xff\\xfe or \\xfe\\xff). Without that guard, an even-length cp1252/latin-1
        file (e.g. accented Western text with an even byte count) can "successfully"
        decode as BOM-less utf-16 into mojibake instead of falling through to the
        codec that actually matches it — silently corrupting the import.
        A legacy file is read as cp1253 when its accented bytes are Greek text (read as
        cp1252 a Greek file became mojibake that was then imported as the translation).
        """
        if not path:
            return None
        # One read, decoded in memory: on the media shares every read is a network
        # round-trip, and this runs for every subtitle on every sync. A refused read
        # is retried briefly — Plex or another scanner holding the file open over SMB
        # is the usual cause, and it clears in well under a second.
        raw = None
        for delay in (0.0, 0.3, 1.0):
            if delay:
                time.sleep(delay)
            try:
                raw = Path(path).read_bytes()
                break
            except FileNotFoundError:
                return None
            except OSError:
                continue
        if raw is None:
            return None
        try:
            text = decode_subtitle_bytes(raw)
        except Exception as e:
            logger.warning(f"Could not decode subtitle {path}: {e}")
            return None
        # Same newline handling read_text() gave callers before.
        return text.replace("\r\n", "\n").replace("\r", "\n")

    async def sync_project(self, project_name: str, progress_callback=None) -> SyncResult:
        """Sync a single project (series or movie) from Sonarr/Radarr and filesystem."""
        from utils import storage
        from utils.srt_parser import parse_srt
        from .sonarr import get_series
        from .radarr import get_movie

        result = SyncResult()

        def _progress(pct: float, msg: str):
            if progress_callback:
                progress_callback(progress=pct, message=msg)

        loop = asyncio.get_running_loop()
        meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
        if not meta:
            raise ValueError(f"Project metadata not found for {project_name}")

        existing_projects = await loop.run_in_executor(None, storage.list_projects)
        existing_projects_set = set(existing_projects)

        media_type = meta.get("arr_media_type") or meta.get("type")
        is_movie = media_type == "movie"

        if is_movie:
            if not self.radarr or not self.radarr.enabled or not self.radarr.api_key:
                raise ValueError("Radarr is not enabled or api key is missing")

            radarr_id = meta.get("arr_radarr_id")
            movie_data = None
            if radarr_id:
                _progress(20.0, f"Fetching movie '{project_name}' from Radarr...")
                movie_data = await get_movie(self.radarr, int(radarr_id))
            
            if not movie_data:
                # Fallback: search all movies. Match either the new disambiguated
                # name ("Title (year)") or the legacy bare-title name — a project
                # created before the disambiguation suffix existed (and missing
                # arr_radarr_id) is named just "Title" and would never match the
                # suffixed form.
                _progress(20.0, f"Searching for movie '{meta.get('show_name')}' in Radarr...")
                all_movies = await get_all_movies(self.radarr)
                for m in all_movies:
                    if movie_project_name(m) == project_name or make_project_name(m.get("title", "")) == project_name:
                        movie_data = m
                        break

            if not movie_data:
                raise ValueError(f"Movie '{project_name}' not found in Radarr")

            _progress(50.0, f"Scanning movie filesystem path...")
            sync_info = await self._sync_movie(movie_data, project_name, result, storage, parse_srt, existing_projects_set)
            if sync_info:
                result.synced_movies.append(sync_info)
        else:
            # Series/Show
            if not self.sonarr or not self.sonarr.enabled or not self.sonarr.api_key:
                raise ValueError("Sonarr is not enabled or api key is missing")

            sonarr_id = meta.get("arr_sonarr_id")
            series_data = None
            if sonarr_id:
                _progress(20.0, f"Fetching series '{project_name}' from Sonarr...")
                series_data = await get_series(self.sonarr, int(sonarr_id))

            if not series_data:
                # Fallback: search all series
                _progress(20.0, f"Searching for series '{meta.get('show_name')}' in Sonarr...")
                all_series = await get_all_series(self.sonarr)
                clean_target = make_project_name(meta.get("show_name", project_name))
                for s in all_series:
                    if make_project_name(s.get("title", "")) == clean_target:
                        series_data = s
                        break

            if not series_data:
                raise ValueError(f"Series '{project_name}' not found in Sonarr")

            _progress(50.0, f"Syncing series episodes and scanning filesystem...")
            sync_info = await self._sync_series(series_data, project_name, result, storage, parse_srt, existing_projects_set)
            if sync_info:
                result.synced_series.append(sync_info)

        _progress(100.0, "Sync complete")
        return result

