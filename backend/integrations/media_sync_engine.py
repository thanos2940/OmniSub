"""
MediaSyncEngine — Sonarr + Radarr library synchronization.

Replaces BazarrSyncEngine. Uses Sonarr (/api/v3/series, /api/v3/episode)
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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .sonarr import SonarrConfig, get_all_series, get_episodes, get_episode_file, get_episode_files
from .radarr import RadarrConfig, get_all_movies
from .subtitle_scanner import SubtitleScannerService
from .path_resolver import PathResolver

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sanitization helpers (previously in bazarr.py)
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
    ):
        self.sonarr = sonarr_config
        self.radarr = radarr_config
        self.resolver = resolver or PathResolver()
        self.scanner = SubtitleScannerService(source_lang_code, target_lang_code)
        self.source_lang_code = source_lang_code
        self.target_lang_code = target_lang_code
        self.target_language = target_language

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
            if meta:
                rid = meta.get("arr_radarr_id") or meta.get("bazarr_movie_id")
                if rid is not None:
                    radarr_id_to_project[str(rid)] = p_name
        await asyncio.gather(*[load_meta(p) for p in existing_projects])

        # --- Sonarr sync ---
        sonarr_failed = False
        if self.sonarr and self.sonarr.enabled and self.sonarr.api_key:
            _progress(5.0, "Fetching series from Sonarr...")
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
        radarr_failed = False
        if self.radarr and self.radarr.enabled and self.radarr.api_key:
            _progress(52.0, "Fetching movies from Radarr...")
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
        async def disable_project(p_name):
            try:
                meta = await loop.run_in_executor(None, storage.load_project_metadata, p_name)
            except Exception as e:
                logger.warning(f"Failed to load metadata for project '{p_name}' during disable check: {e}")
                meta = None
            if meta and not meta.get("arr_disabled"):
                media_type = meta.get("arr_media_type")
                if media_type == "series" and sonarr_failed:
                    return
                if media_type == "movie" and radarr_failed:
                    return
                meta["arr_disabled"] = True
                meta["arr_disabled_at"] = datetime.now().isoformat()
                await loop.run_in_executor(None, storage.save_project_metadata, p_name, meta)
                result.disabled_projects += 1

        await asyncio.gather(*[disable_project(p) for p in existing_arr_projects if p not in seen_project_names])

        # --- Re-enable projects that reappeared ---
        async def re_enable_project(p_name):
            try:
                meta = await loop.run_in_executor(None, storage.load_project_metadata, p_name)
            except Exception as e:
                logger.warning(f"Failed to load metadata for project '{p_name}' during re-enable check: {e}")
                meta = None
            if meta and meta.get("arr_disabled"):
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
        result: SyncResult, storage, parse_srt, existing_projects_set: set
    ) -> Optional[Dict]:
        """Create/update project and import episodes for one series."""
        series_id = series_data.get("id")
        series_title = series_data.get("title", "Unknown")
        series_type = series_data.get("seriesType", "standard")
        series_path = series_data.get("path", "")

        loop = asyncio.get_running_loop()

        is_new = project_name not in existing_projects_set
        if is_new:
            # Sibling project inheritance check (run in executor to avoid blocking)
            def get_inherited_data(existing_set):
                inherited_glossary = {"terms": []}
                inherited_context = ""
                show_name_lower = series_title.lower()
                for existing in existing_set:
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

            inherited_glossary, inherited_context = await loop.run_in_executor(None, get_inherited_data, existing_projects_set)

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
            existing_projects_set.add(project_name)
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
                # Migrate old bazarr_source projects
                if meta.get("bazarr_source") and not meta.get("arr_source"):
                    meta["arr_source"] = True
                await loop.run_in_executor(None, storage.save_project_metadata, project_name, meta)

        episodes = await get_episodes(self.sonarr, series_id)
        # Fetch all episode files for the series in a single API call to avoid N+1 requests
        episode_files = await get_episode_files(self.sonarr, series_id)
        episode_file_map = {ef.get("id"): ef.get("path", "") for ef in episode_files if ef.get("id")}

        imported_count = 0
        total_with_file = 0
        translated_count = 0
        bazarr_episode_names: set = set()
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
            bazarr_episode_names.add(episode_name)

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

        # Step 2: Scan unique directories in parallel
        scan_tasks = []
        for d_path in parent_dirs:
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
        dir_indices = dict(zip(parent_dirs, scan_results))

        print(f"Step 3: Processing {len(resolved_episodes)} resolved episodes...")
        for item in resolved_episodes:
            ep = item["ep"]
            episode_name = item["episode_name"]
            media_path = item["media_path"]
            season = item["season"]
            episode_num = item["episode_num"]
            ep_title = item["ep_title"]

            p_dir = str(Path(media_path).parent)
            dir_index = dir_indices.get(p_dir)

            print(f"  [{episode_name}] Scanning media file...")
            scan = await loop.run_in_executor(None, self.scanner.scan_media_file, media_path, dir_index)
            print(f"  [{episode_name}] Scan complete: has_source={scan.has_source_sub}, has_target={scan.has_target_sub}")

            total_with_file += 1
            if scan.has_target_sub:
                translated_count += 1

            if not scan.has_source_sub:
                result.skipped_no_subs += 1
                continue

            seen_episode_names.add(episode_name)
            item["scan"] = scan

            print(f"  [{episode_name}] Generating fingerprint for {scan.source_sub_path}...")
            fingerprint = await loop.run_in_executor(None, self._fingerprint, scan.source_sub_path)
            print(f"  [{episode_name}] Loading episode metadata...")
            existing_meta = await loop.run_in_executor(None, storage.load_episode_metadata, project_name, episode_name)

            should_import = True
            if existing_meta:
                data_file = storage.PROJECTS_DIR / project_name / "episodes" / episode_name / "data.json"
                old_fp = existing_meta.get("arr_sub_fingerprint")
                if not data_file.exists() or not existing_meta.get("arr_media_path"):
                    should_import = True
                elif fingerprint is None:
                    should_import = False
                    imported_count += 1
                elif old_fp and fingerprint == old_fp:
                    imported_count += 1
                    should_import = False
                elif old_fp and fingerprint != old_fp:
                    result.updated_episodes += 1
                else:
                    imported_count += 1
                    should_import = False

            if should_import:
                print(f"  [{episode_name}] Reading subtitle file...")
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
                    print(f"  [{episode_name}] Running import_and_clean_srt...")
                    res = import_and_clean_srt(
                        project_name, 
                        episode_name, 
                        srt_content, 
                        filename=Path(scan.source_sub_path).name, 
                        fingerprint=fingerprint,
                        target_srt_content=target_srt_content
                    )
                    meta = res["metadata"]
                    meta.update({
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
                    })
                    print(f"  [{episode_name}] Saving episode metadata...")
                    storage.save_episode_metadata(project_name, episode_name, meta, update_stats=False)
                await loop.run_in_executor(None, _do_import)
                imported_count += 1
                result.new_episodes += 1
            else:
                print(f"  [{episode_name}] Updating existing metadata/target status...")
                # Update existing_meta if target sub status changed on disk
                meta_changed = False
                if existing_meta.get("arr_has_target") != scan.has_target_sub:
                    existing_meta["arr_has_target"] = scan.has_target_sub
                    meta_changed = True
                
                tgt_path_on_disk = scan.target_sub_path if scan.has_target_sub else None
                if existing_meta.get("arr_target_path") != tgt_path_on_disk:
                    existing_meta["arr_target_path"] = tgt_path_on_disk
                    meta_changed = True

                if existing_meta.get("bazarr_has_target") != scan.has_target_sub:
                    existing_meta["bazarr_has_target"] = scan.has_target_sub
                    meta_changed = True

                # Source renamed on disk (same content → same fingerprint, but new path):
                # keep the stored source path/filename current so the dashboard reflects it.
                if existing_meta.get("arr_sub_path") != scan.source_sub_path:
                    existing_meta["arr_sub_path"] = scan.source_sub_path
                    existing_meta["original_filename"] = Path(scan.source_sub_path).name
                    meta_changed = True

                if meta_changed:
                    await loop.run_in_executor(None, storage.save_episode_metadata, project_name, episode_name, existing_meta, False)
                imported_count += 1

        # Sync secondary formats
        project_meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
        create_alt = False
        if project_meta:
            create_alt = bool(storage.get_project_setting(project_meta, "translate_all_source_formats", False))

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
        # dashboard mirrors the directory. Their exported translation files are removed too.
        removed = await loop.run_in_executor(
            None, self._prune_removed_episodes, project_name, seen_episode_names, result
        )
        result.removed_episodes += removed

        # Persist stats
        meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
        if meta:
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

            target_paths = [em.get("arr_target_path")]
            for t in (em.get("arr_targets") or {}).values():
                if isinstance(t, dict) and t.get("target_path"):
                    target_paths.append(t["target_path"])
            for tp in target_paths:
                if tp and os.path.exists(tp):
                    try:
                        os.remove(tp)
                    except Exception as e:
                        logger.warning(f"Failed to delete orphaned target {tp}: {e}")
            storage.delete_episode(project_name, ep_name)
            removed += 1
            logger.info(f"Sync: removed {project_name}/{ep_name} (source no longer on disk).")
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
                data_file = storage.PROJECTS_DIR / project_name / "episodes" / sib_name / "data.json"
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
                res = import_and_clean_srt(
                    project_name,
                    sib_name,
                    srt_content,
                    filename=Path(source_path).name,
                    fingerprint=fp,
                    target_srt_content=target_srt_content
                )
                meta = res["metadata"]
                # Clean/merge extra metadata
                extra = dict(base_extra_meta)
                extra.pop("arr_target_path", None)
                extra.pop("arr_has_target", None)
                extra.pop("arr_sub_path", None)
                extra.pop("arr_sub_fingerprint", None)

                meta.update({
                    "arr_source": True,
                    "arr_sub_path": source_path,
                    "arr_media_path": media_path,
                    "arr_secondary_of": base_episode_name,
                    "arr_source_format": ext,
                    "arr_has_target": has_target,
                    "arr_target_path": tgt_path if has_target else None,
                    "arr_translated_from_fingerprint": existing_meta.get("arr_translated_from_fingerprint") if existing_meta else None,
                    **extra,
                })
                storage.save_episode_metadata(project_name, sib_name, meta, update_stats=False)
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

                if existing_meta.get("bazarr_has_target") != has_target:
                    existing_meta["bazarr_has_target"] = has_target
                    meta_changed = True

                if existing_meta.get("arr_sub_path") != source_path:
                    existing_meta["arr_sub_path"] = source_path
                    existing_meta["original_filename"] = Path(source_path).name
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
            existing_projects_set.add(project_name)
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
                if meta.get("bazarr_source") and not meta.get("arr_source"):
                    meta["arr_source"] = True
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

        has_target = scan.has_target_sub
        imported = False

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
                data_file = storage.PROJECTS_DIR / project_name / "episodes" / episode_name / "data.json"
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

            if should_import:
                srt_content = await loop.run_in_executor(None, self._read_file, scan.source_sub_path)
                if srt_content:
                    target_srt_content = None
                    if has_target and scan.target_sub_path:
                        target_srt_content = await loop.run_in_executor(None, self._read_file, scan.target_sub_path)

                    from utils.source_clean import import_and_clean_srt
                    def _do_import_movie():
                        res = import_and_clean_srt(
                            project_name, 
                            episode_name, 
                            srt_content, 
                            filename=Path(scan.source_sub_path).name, 
                            fingerprint=fingerprint,
                            target_srt_content=target_srt_content
                        )
                        meta = res["metadata"]
                        meta.update({
                            "arr_source": True,
                            "arr_sub_path": scan.source_sub_path,
                            "arr_media_path": media_path,
                            "arr_radarr_id": radarr_id,
                            "arr_has_target": has_target,
                            "arr_target_path": scan.target_sub_path if has_target else None,
                            "arr_translated_from_fingerprint": existing_meta.get("arr_translated_from_fingerprint") if existing_meta else None,
                        })
                        storage.save_episode_metadata(project_name, episode_name, meta, update_stats=False)
                    await loop.run_in_executor(None, _do_import_movie)
                    imported = True
                    result.new_episodes += 1
                else:
                    result.warnings.append(f"Cannot read subtitle for movie: {movie_title}")
            else:
                # Update existing_meta if target sub status changed on disk
                meta_changed = False
                if existing_meta.get("arr_has_target") != has_target:
                    existing_meta["arr_has_target"] = has_target
                    meta_changed = True
                
                tgt_path_on_disk = scan.target_sub_path if has_target else None
                if existing_meta.get("arr_target_path") != tgt_path_on_disk:
                    existing_meta["arr_target_path"] = tgt_path_on_disk
                    meta_changed = True

                if existing_meta.get("bazarr_has_target") != has_target:
                    existing_meta["bazarr_has_target"] = has_target
                    meta_changed = True

                if meta_changed:
                    await loop.run_in_executor(None, storage.save_episode_metadata, project_name, episode_name, existing_meta, False)
                imported = True
        else:
            result.skipped_no_subs += 1

        # Mirror the directory: prune the episode if its source subtitle is gone (the
        # safety valve in _prune_removed_episodes skips when nothing was seen, so a
        # transient scan failure won't wipe the movie).
        _seen = {episode_name} if scan.has_source_sub else set()
        if scan.has_source_sub:
            project_meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
            create_alt = False
            if project_meta:
                create_alt = bool(storage.get_project_setting(project_meta, "translate_all_source_formats", False))

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

        if not scan.has_source_sub:
            logger.info(f"Sonarr webhook: no .en.srt found for {raw_media_path}")
            return None

        project_name = make_project_name(series_title)
        ep_data = episodes[0]  # Sonarr sends one episode per download event
        season = ep_data.get("seasonNumber", 0)
        episode_num = ep_data.get("episodeNumber", 0)
        episode_name = make_episode_name(season, episode_num)

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

        if not scan.has_source_sub:
            logger.info(f"Radarr webhook: no .en.srt found for {raw_media_path}")
            return None

        radarr_id = movie.get("id")
        adopted_name = await loop.run_in_executor(None, self._find_project_by_radarr_id, storage, radarr_id)
        project_name = movie_project_name(movie, adopted=adopted_name)
        episode_name = project_name

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
    # Migration helper
    # ------------------------------------------------------------------

    @staticmethod
    def migrate_old_episode_names(storage) -> Dict[str, int]:
        """Rename messy Bazarr-imported movie episode names to clean titles.

        Old format: "Snow Queen (2002) DVD x265 AAC 2.0 Radarr.en.srt"
        New format: "Snow Queen"

        Returns dict of {project_name: episodes_renamed}.
        """
        fixed: Dict[str, int] = {}
        for p_name in storage.list_projects():
            meta = storage.load_project_metadata(p_name)
            if not meta or meta.get("type") != "movie":
                continue

            episodes = storage.list_episodes(p_name)
            clean_name = make_project_name(meta.get("show_name", p_name))

            renames = 0
            for ep_name in episodes:
                # Episode names containing spaces + dots = likely old filename
                if ep_name == clean_name:
                    continue
                if "." in ep_name and " " in ep_name:
                    # Old messy name → rename to clean title
                    from pathlib import Path as _Path
                    old_dir = _Path(storage.PROJECTS_DIR) / p_name / "episodes" / ep_name
                    new_dir = _Path(storage.PROJECTS_DIR) / p_name / "episodes" / clean_name
                    if old_dir.exists() and not new_dir.exists():
                        old_dir.rename(new_dir)
                        renames += 1
                        logger.info(f"Migration: renamed {p_name}/{ep_name} → {clean_name}")

            if renames:
                fixed[p_name] = renames

        return fixed

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
                create_alt = False
                if project_meta:
                    create_alt = bool(storage.get_project_setting(project_meta, "translate_all_source_formats", False))
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

        srt_content = await loop.run_in_executor(None, self._read_file, scan.source_sub_path)
        if not srt_content:
            result.warnings.append(f"Cannot read: {scan.source_sub_path}")
            return
            
        target_srt_content = None
        if scan.has_target_sub and scan.target_sub_path:
            target_srt_content = await loop.run_in_executor(None, self._read_file, scan.target_sub_path)

        from utils.source_clean import import_and_clean_srt
        def _do_import_webhook():
            res = import_and_clean_srt(
                project_name, 
                episode_name, 
                srt_content, 
                filename=Path(scan.source_sub_path).name, 
                fingerprint=fingerprint,
                target_srt_content=target_srt_content
            )
            meta = res["metadata"]
            meta.update({
                "arr_source": True,
                "arr_sub_path": scan.source_sub_path,
                "arr_media_path": media_path,
                "arr_has_target": scan.has_target_sub,
                "arr_translated_from_fingerprint": existing_meta.get("arr_translated_from_fingerprint") if existing_meta else None,
                **ep_meta,
            })
            storage.save_episode_metadata(project_name, episode_name, meta)
        await loop.run_in_executor(None, _do_import_webhook)
        result.new_episodes += 1

        project_meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
        create_alt = False
        if project_meta:
            create_alt = bool(storage.get_project_setting(project_meta, "translate_all_source_formats", False))
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
                rid = meta.get("arr_radarr_id") or meta.get("bazarr_movie_id")
                if rid is not None and str(rid) == str(radarr_id):
                    return p_name
        except Exception as e:
            logger.warning(f"Failed to scan projects for radarr_id {radarr_id}: {e}")
        return None

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
        """Read subtitle file with encoding fallback.

        utf-16 is only attempted when the file actually starts with a utf-16 BOM
        (\\xff\\xfe or \\xfe\\xff). Without that guard, an even-length cp1252/latin-1
        file (e.g. accented Western text with an even byte count) can "successfully"
        decode as BOM-less utf-16 into mojibake instead of falling through to the
        codec that actually matches it — silently corrupting the import.
        """
        if not path:
            return None
        try:
            head = Path(path).read_bytes()[:2]
        except OSError:
            return None
        has_utf16_bom = head in (b"\xff\xfe", b"\xfe\xff")
        encodings = ["utf-8-sig", "utf-8"]
        if has_utf16_bom:
            encodings.append("utf-16")
        encodings += ["cp1252", "latin-1"]
        for enc in encodings:
            try:
                return Path(path).read_text(encoding=enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
        return None

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

            radarr_id = meta.get("arr_radarr_id") or meta.get("bazarr_movie_id")
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

            sonarr_id = meta.get("arr_sonarr_id") or meta.get("bazarr_series_id")
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

