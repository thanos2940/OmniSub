"""
Bazarr Sync Engine — Synchronizes Bazarr library state with OmbiSub projects.

This module is the brain of the Bazarr integration. It:
  1. Fetches all series/movies from Bazarr
  2. Filters by language profile (must include target language)
  3. Creates/updates OmbiSub projects and imports English subtitle files
  4. Marks removed entries as disabled (not deleted)
  5. Detects subtitle file changes and updates accordingly

The sync engine does NOT trigger translations — imported episodes are left
in an "awaiting translation" state for manual or scheduled translation.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .bazarr import (
    BazarrConfig,
    _sub_code,
    check_path_reachable,
    get_all_movies,
    get_all_series,
    get_language_profiles,
    get_series_episodes,
    get_subtitle_fingerprint,
    make_episode_name,
    make_project_name,
    read_subtitle_file,
    MissingSubtitleItem,
)

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Outcome of a full sync run."""
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


class BazarrSyncEngine:
    """Synchronizes Bazarr library with OmbiSub projects.

    Usage::

        engine = BazarrSyncEngine(config)
        result = await engine.full_sync(progress_callback=update_job)
    """

    def __init__(self, config: BazarrConfig):
        self.config = config

    async def full_sync(
        self,
        progress_callback=None,
    ) -> SyncResult:
        """Run a complete sync cycle.

        Steps:
            1. Fetch language profiles to identify target language
            2. Fetch all series, filter by profile, sync episodes
            3. Fetch all movies, filter by profile, sync
            4. Disable projects for entries removed from Bazarr
            5. Re-enable projects for entries that reappeared
        """
        # Lazy import to avoid circular dependency
        from utils import storage
        from utils.srt_parser import parse_srt

        result = SyncResult()

        def _progress(pct: float, msg: str):
            if progress_callback:
                progress_callback(progress=pct, message=msg)

        _progress(5.0, "Fetching language profiles from Bazarr...")

        # 1. Check which language profiles include our target language
        profiles = await get_language_profiles(self.config)
        target_lang = self.config.target_language  # e.g. "Greek"
        target_code = self.config.target_language_code2  # e.g. "el"

        # Build set of profile IDs that include the target language
        target_profile_ids = set()
        for profile in profiles:
            profile_id = profile.get("profileId")
            items = profile.get("items", [])
            for item in items:
                lang = item.get("language")
                if lang and lang.lower() in (target_code.lower(), target_lang.lower(), self.config.target_language_code3.lower()):
                    target_profile_ids.add(profile_id)
                    break

        logger.info(
            f"Found {len(target_profile_ids)} language profiles "
            f"containing {target_lang}"
        )

        # Check path reachability before heavy I/O
        _progress(8.0, "Checking path accessibility...")
        reachable_checked = set()
        for mapping in self.config.path_mappings:
            local_path = mapping.get("local", "")
            if local_path and local_path not in reachable_checked:
                reachable_checked.add(local_path)
                if not check_path_reachable(local_path):
                    result.unreachable_paths.append(local_path)
                    result.warnings.append(
                        f"Path unreachable: {local_path} — "
                        f"some subtitles may not be importable"
                    )

        # Track which bazarr projects we see during this sync
        seen_project_names = set()
        existing_projects = storage.list_projects()
        existing_bazarr_projects = set()
        for p_name in existing_projects:
            meta = storage.load_project_metadata(p_name)
            if meta and meta.get("bazarr_source"):
                existing_bazarr_projects.add(p_name)

        # 2. Sync series
        if self.config.media_types in ("series", "both"):
            _progress(10.0, "Fetching series from Bazarr...")
            all_series = await get_all_series(self.config)
            total_series = len(all_series)
            series_done = 0

            for series in all_series:
                series_id = series.get("sonarrSeriesId")
                series_title = series.get("title", "Unknown")
                series_profile = series.get("profileId")
                series_languages = series.get("languages", [])

                if not series_id:
                    continue

                # Filter: language profile must include target language
                # Check both profileId-based and languages-list-based filtering
                profile_match = series_profile in target_profile_ids
                lang_match = target_lang in series_languages
                if not profile_match and not lang_match:
                    series_done += 1
                    continue

                project_name = make_project_name(series_title)
                seen_project_names.add(project_name)

                pct = 10.0 + (series_done / max(total_series, 1)) * 40.0
                _progress(pct, f"Syncing series: {series_title}...")

                try:
                    sync_info = await self._sync_series(
                        series, project_name, result, storage, parse_srt
                    )
                    if sync_info:
                        result.synced_series.append(sync_info)
                except Exception as e:
                    err = f"Error syncing series '{series_title}': {e}"
                    logger.error(err, exc_info=True)
                    result.errors.append(err)

                series_done += 1

        # 3. Sync movies
        if self.config.media_types in ("movies", "both"):
            _progress(55.0, "Fetching movies from Bazarr...")
            all_movies = await get_all_movies(self.config)
            total_movies = len(all_movies)
            movies_done = 0

            for movie in all_movies:
                movie_title = movie.get("title", "Unknown")
                movie_profile = movie.get("profileId")
                movie_languages = movie.get("languages", [])

                profile_match = movie_profile in target_profile_ids
                lang_match = target_lang in movie_languages
                if not profile_match and not lang_match:
                    movies_done += 1
                    continue

                project_name = make_project_name(movie_title)
                seen_project_names.add(project_name)

                pct = 55.0 + (movies_done / max(total_movies, 1)) * 30.0
                _progress(pct, f"Syncing movie: {movie_title}...")

                try:
                    sync_info = await self._sync_movie(
                        movie, project_name, result, storage, parse_srt
                    )
                    if sync_info:
                        result.synced_movies.append(sync_info)
                except Exception as e:
                    err = f"Error syncing movie '{movie_title}': {e}"
                    logger.error(err, exc_info=True)
                    result.errors.append(err)

                movies_done += 1

        # 4. Disable projects that are no longer in Bazarr
        _progress(88.0, "Checking for removed entries...")
        for p_name in existing_bazarr_projects:
            if p_name not in seen_project_names:
                meta = storage.load_project_metadata(p_name)
                if meta and not meta.get("bazarr_disabled"):
                    meta["bazarr_disabled"] = True
                    meta["bazarr_disabled_at"] = datetime.now().isoformat()
                    storage.save_project_metadata(p_name, meta)
                    result.disabled_projects += 1
                    logger.info(f"Disabled project (removed from Bazarr): {p_name}")

        # 5. Re-enable projects that reappeared
        for p_name in seen_project_names:
            if p_name in existing_bazarr_projects:
                meta = storage.load_project_metadata(p_name)
                if meta and meta.get("bazarr_disabled"):
                    meta["bazarr_disabled"] = False
                    meta.pop("bazarr_disabled_at", None)
                    storage.save_project_metadata(p_name, meta)
                    result.re_enabled_projects += 1
                    logger.info(f"Re-enabled project (reappeared in Bazarr): {p_name}")

        _progress(95.0, "Finalizing sync...")

        logger.info(
            f"Sync complete: {result.new_projects} new projects, "
            f"{result.new_episodes} new episodes, "
            f"{result.updated_episodes} updated, "
            f"{result.disabled_projects} disabled, "
            f"{result.re_enabled_projects} re-enabled, "
            f"{len(result.errors)} errors"
        )

        return result

    async def _sync_series(
        self, series_data: Dict, project_name: str,
        result: SyncResult, storage, parse_srt,
    ) -> Optional[Dict]:
        """Sync a single series — create project and import episodes."""
        series_id = series_data["sonarrSeriesId"]
        series_title = series_data.get("title", "Unknown")
        series_type = series_data.get("seriesType", "standard")

        # Create or load project
        is_new = project_name not in storage.list_projects()
        if is_new:
            storage.create_project(project_name, {
                "show_name": series_title,
                "target_language": self.config.target_language,
                "type": "show",
                "bazarr_source": True,
                "bazarr_disabled": False,
                "bazarr_series_id": series_id,
                "bazarr_profile_id": series_data.get("profileId"),
                "bazarr_media_type": "series",
                "bazarr_last_sync": datetime.now().isoformat(),
                "series_type": series_type,
            })
            result.new_projects += 1
            logger.info(f"Created project from Bazarr: {project_name}")
        else:
            # Update sync timestamp and enforce correct types
            meta = storage.load_project_metadata(project_name)
            if meta:
                meta["bazarr_last_sync"] = datetime.now().isoformat()
                meta["bazarr_series_id"] = series_id
                meta["bazarr_profile_id"] = series_data.get("profileId")
                meta["bazarr_media_type"] = "series"
                meta["type"] = "show"
                storage.save_project_metadata(project_name, meta)

        # Fetch episodes from Bazarr
        episodes = await get_series_episodes(self.config, series_id)
        target_code = self.config.target_language_code2
        source_code = self.config.source_language_code

        imported_count = 0
        total_with_english = 0
        translated_count = 0

        # Track which episodes Bazarr reports so we can detect removals
        bazarr_episode_names = set()

        for ep in episodes:
            subs = ep.get("subtitles", [])
            media_path = ep.get("path", "")
            season = ep.get("season", 0)
            episode_num = ep.get("episode", 0)
            ep_title = ep.get("title", "")

            # Find English sub
            english_sub = None
            has_target = False
            for sub in subs:
                code = _sub_code(sub)
                if code == source_code and sub.get("path"):
                    english_sub = sub
                if code == target_code:
                    has_target = True

            episode_name = f"S{season:02d}E{episode_num:02d}"
            bazarr_episode_names.add(episode_name)

            if not english_sub or not media_path:
                continue

            total_with_english += 1
            if has_target:
                translated_count += 1

            # Import episode even if it has a target sub in Bazarr, but tag it
            # so we can show it as completed/translated in the UI.

            english_sub_path = self.config.translate_path(english_sub["path"])
            local_media_path = self.config.translate_path(media_path)

            # Check fingerprint for change detection
            fingerprint = get_subtitle_fingerprint(english_sub_path)
            existing_meta = storage.load_episode_metadata(
                project_name, episode_name
            )

            if existing_meta:
                old_fingerprint = existing_meta.get("bazarr_sub_fingerprint")
                if old_fingerprint and fingerprint == old_fingerprint:
                    # No change — skip
                    imported_count += 1
                    continue
                elif old_fingerprint and fingerprint != old_fingerprint:
                    # File changed — re-import
                    logger.info(
                        f"Subtitle changed for {project_name}/{episode_name}, "
                        f"re-importing"
                    )
                    result.updated_episodes += 1
                else:
                    imported_count += 1
                    continue

            # Read and import the subtitle file
            srt_content = read_subtitle_file(english_sub_path)
            if not srt_content:
                if fingerprint is None:
                    result.warnings.append(
                        f"Cannot read subtitle: {english_sub_path}"
                    )
                continue

            parsed = parse_srt(srt_content)
            if not parsed:
                result.warnings.append(
                    f"Failed to parse SRT: {english_sub_path}"
                )
                continue

            # Save episode data
            storage.save_original_srt(project_name, episode_name, srt_content)
            storage.save_episode(project_name, episode_name, parsed, {
                "original_filename": Path(english_sub_path).name,
                "line_count": len(parsed),
                "bazarr_source": True,
                "bazarr_sub_path": english_sub_path,
                "bazarr_sub_fingerprint": fingerprint,
                "bazarr_media_path": local_media_path,
                "bazarr_episode_id": ep.get("sonarrEpisodeId"),
                "episode_title": ep_title,
                "season": season,
                "episode": episode_num,
                "bazarr_has_target": has_target,
            })
            imported_count += 1
            result.new_episodes += 1

        # Update project stats in metadata
        meta = storage.load_project_metadata(project_name)
        if meta:
            meta["bazarr_total_episodes"] = total_with_english
            meta["bazarr_translated_episodes"] = translated_count
            storage.save_project_metadata(project_name, meta)

        return {
            "title": series_title,
            "project_name": project_name,
            "total_episodes": len(episodes),
            "episodes_with_english": total_with_english,
            "imported_episodes": imported_count,
            "translated_in_bazarr": translated_count,
            "is_new": is_new,
        }

    async def _sync_movie(
        self, movie_data: Dict, project_name: str,
        result: SyncResult, storage, parse_srt,
    ) -> Optional[Dict]:
        """Sync a single movie — create project and import subtitle."""
        movie_title = movie_data.get("title", "Unknown")
        radarr_id = movie_data.get("radarrId")
        subs = movie_data.get("subtitles", [])
        media_path = movie_data.get("path", "")

        target_code = self.config.target_language_code2
        source_code = self.config.source_language_code

        english_sub = None
        has_target = False
        for sub in subs:
            code = _sub_code(sub)
            if code == source_code and sub.get("path"):
                english_sub = sub
            if code == target_code:
                has_target = True

        # Create or load project
        is_new = project_name not in storage.list_projects()
        if is_new:
            storage.create_project(project_name, {
                "show_name": movie_title,
                "target_language": self.config.target_language,
                "type": "movie",
                "bazarr_source": True,
                "bazarr_disabled": False,
                "bazarr_radarr_id": radarr_id,
                "bazarr_profile_id": movie_data.get("profileId"),
                "bazarr_media_type": "movie",
                "bazarr_last_sync": datetime.now().isoformat(),
            })
            result.new_projects += 1
        else:
            meta = storage.load_project_metadata(project_name)
            if meta:
                meta["bazarr_last_sync"] = datetime.now().isoformat()
                meta["bazarr_radarr_id"] = radarr_id
                meta["bazarr_profile_id"] = movie_data.get("profileId")
                meta["bazarr_media_type"] = "movie"
                meta["type"] = "movie"
                storage.save_project_metadata(project_name, meta)

        has_english = False
        imported = False

        if english_sub and media_path:
            has_english = True
            english_sub_path = self.config.translate_path(english_sub["path"])
            local_media_path = self.config.translate_path(media_path)

            episode_name = make_project_name(movie_title)
            fingerprint = get_subtitle_fingerprint(english_sub_path)
            existing_meta = storage.load_episode_metadata(
                project_name, episode_name
            )

            should_import = True
            if existing_meta:
                old_fp = existing_meta.get("bazarr_sub_fingerprint")
                if old_fp and fingerprint == old_fp:
                    should_import = False
                    imported = True
                elif old_fp and fingerprint != old_fp:
                    result.updated_episodes += 1

            if should_import:
                srt_content = read_subtitle_file(english_sub_path)
                if srt_content:
                    parsed = parse_srt(srt_content)
                    if parsed:
                        storage.save_original_srt(
                            project_name, episode_name, srt_content
                        )
                        storage.save_episode(
                            project_name, episode_name, parsed, {
                                "original_filename": Path(english_sub_path).name,
                                "line_count": len(parsed),
                                "bazarr_source": True,
                                "bazarr_sub_path": english_sub_path,
                                "bazarr_sub_fingerprint": fingerprint,
                                "bazarr_media_path": local_media_path,
                                "bazarr_radarr_id": radarr_id,
                                "bazarr_has_target": has_target,
                            }
                        )
                        imported = True
                        result.new_episodes += 1
                    else:
                        result.warnings.append(
                            f"Failed to parse SRT for movie: {movie_title}"
                        )
                else:
                    result.warnings.append(
                        f"Cannot read subtitle for movie: {movie_title}"
                    )
        elif not english_sub:
            result.skipped_no_subs += 1

        # Update project stats in metadata
        meta = storage.load_project_metadata(project_name)
        if meta:
            meta["bazarr_total_episodes"] = 1 if has_english else 0
            meta["bazarr_translated_episodes"] = 1 if has_target else 0
            storage.save_project_metadata(project_name, meta)

        return {
            "title": movie_title,
            "project_name": project_name,
            "has_english_sub": has_english,
            "imported": imported,
            "has_target_sub": has_target,
            "is_new": is_new,
        }
