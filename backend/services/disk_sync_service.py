"""
Disk Sync Service
=================
Inspects physical disk directories for actual subtitle files (.el.ass, .el.srt, .en.ass, etc.)
and synchronizes Omnisub project status and translation queue items to reflect real filesystem state.

Capabilities:
1. Detects new or existing target translations (.el.ass / .el.srt / .greek.srt / etc.) on disk:
   - Updates episode metadata (arr_has_target=True, translated=True, status="completed")
   - Ingests translated cues into episode storage so the UI immediately shows them
   - Preserves source text integrity (never clobbers original English text with Greek text)
   - Marks translation queue items, agent manifests, and in-memory jobs as COMPLETED
2. Detects removed translations:
   - If a target subtitle file was deleted by the user from disk:
     - Updates episode metadata (arr_has_target=False, translated=False, status="untranslated")
     - Clears translated cues while preserving original source dialogue
     - Re-queues the episode into the translation queue so it can be re-translated
3. Detects new, updated, or removed source subtitles (.en.ass / .en.srt).
   - Ingests source cues via canonical import_and_clean_srt
4. Discovers new episode files added to media/season folders on disk.
5. Broadcasts real-time events to all connected clients.
"""

import os
import re
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Set, Tuple
from datetime import datetime, timezone

from utils import storage
from utils.paths import DATA_DIR
from utils.language_codes import to_code, matches_language, variants_for
from utils.translation_queue import TranslationQueue, KIND_TRANSLATION, PRIORITY_SYNC
from utils.metadata_index import upsert as index_upsert
from utils.event_broadcaster import broadcast_event
from utils.source_clean import import_and_clean_srt
from utils.subtitle_io import parse_subtitle
from utils.text_repair import decode_subtitle_bytes, repair_mojibake
from services.agent_task_service import agent_task_service

logger = logging.getLogger("omnisub.disk_sync")

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov"}
SUB_EXTS = {".ass", ".srt", ".ssa"}

# Season folder name regex
SEASON_DIR_PATTERN = re.compile(
    r"^(?:season[._ -]?\d+|s\d+|specials|subs?|subtitles?|staffel[._ -]?\d+|part[._ -]?\d+|cour[._ -]?\d+|series[._ -]?\d+)$",
    re.IGNORECASE
)


def _clean_path(p: Path) -> Path:
    """Normalize path without slow network RPC calls on UNC shares."""
    return Path(os.path.normpath(str(p)))


class DiskSyncService:
    """Synchronizes Omnisub project status with actual files on disk."""

    def _get_project_directories(self, project_name: str, proj_meta: Dict[str, Any], episodes: List[str]) -> List[Path]:
        """Find all candidate disk directories where media or subtitle files might reside."""
        seen_dirs: Set[str] = set()
        result_dirs: List[Path] = []

        def add_dir(d_path: Path):
            normalized = str(d_path).lower()
            if normalized not in seen_dirs:
                seen_dirs.add(normalized)
                result_dirs.append(d_path)

        from integrations.path_resolver import PathResolver
        from services.translation.export_formatter import resolve_cross_share_media
        gc = storage.load_global_config()
        resolver = PathResolver(gc.get("arr_path_mappings", []))

        # 1. Project-level path metadata
        for key in ("path", "media_path", "root_folder_path", "arr_path", "series_path", "folder"):
            p_val = proj_meta.get(key)
            if p_val:
                try:
                    resolved_p_val = resolver.resolve(p_val)
                    p = Path(resolved_p_val) if resolved_p_val else Path(p_val)
                    if p.exists() and p.is_dir():
                        add_dir(p)
                        # Look for season and media subdirectories
                        for sub in p.iterdir():
                            if not sub.is_dir() or sub.name.startswith(".") or sub.name in ("__pycache__", "node_modules"):
                                continue
                            if SEASON_DIR_PATTERN.search(sub.name) or re.search(r"season\s*\d+|specials", sub.name, re.IGNORECASE):
                                add_dir(sub)
                                for sub2 in sub.iterdir():
                                    if sub2.is_dir() and not sub2.name.startswith("."):
                                        add_dir(sub2)
                            else:
                                add_dir(sub)
                except Exception as e:
                    logger.debug(f"Error checking project path {p_val}: {e}")

        # 2. Episode-level metadata paths (avoid redundant network calls)
        for ep_name in episodes:
            ep_meta = storage.load_episode_metadata(project_name, ep_name) or {}
            for path_key in ("arr_media_path", "arr_sub_path", "arr_target_path", "media_path"):
                val = ep_meta.get(path_key)
                if val:
                    try:
                        resolved_val = resolver.resolve(val)
                        p = Path(resolved_val) if resolved_val else Path(val)
                        if not p.parent.exists() and path_key == "arr_media_path":
                            re_resolved = resolve_cross_share_media(
                                val,
                                episode_name=ep_name,
                                metadata=ep_meta,
                                global_config=gc,
                                project_name=project_name,
                            )
                            if re_resolved:
                                p = re_resolved
                        parent = p.parent
                        if parent.exists():
                            add_dir(parent)
                        else:
                            add_dir(parent)
                    except Exception:
                        pass

        # 3. Internal episode directories under DATA_DIR / projects
        proj_dir = storage.project_dir(project_name)
        if proj_dir.exists():
            add_dir(proj_dir)

        return result_dirs

    @staticmethod
    def _dir_reachable(d: Path) -> bool:
        try:
            return d.is_dir()
        except OSError:
            return False

    def _extract_episode_tag(self, text: str) -> Optional[str]:
        """Extract standard SxxExx or Exx from filename or string."""
        m = re.search(r"(?:S(\d{1,3}))?[._ -]?E(\d{1,4})", text, re.IGNORECASE)
        if m:
            s_val = m.group(1)
            e_val = int(m.group(2))
            if s_val:
                return f"S{int(s_val):02d}E{e_val:02d}"
            return f"E{e_val:02d}"
        return None

    def _format_priority(self, suffix: str) -> int:
        """Format priority: .ass is richest (0), .ssa (1), .srt (2)."""
        s = suffix.lower()
        if s == ".ass":
            return 0
        if s == ".ssa":
            return 1
        return 2

    def _find_matching_target_sub(
        self,
        ep_name: str,
        ep_meta: Dict[str, Any],
        candidate_files: List[Path],
        target_code: str,
    ) -> Optional[Path]:
        """Find a target language subtitle file matching this episode."""
        target_variants = variants_for(target_code)

        # Normalize episode name tag (e.g. S01E02)
        ep_tag = self._extract_episode_tag(ep_name)

        # Candidate stem to match against
        media_path = ep_meta.get("arr_media_path")
        media_stem = Path(media_path).stem if media_path else None
        if media_stem:
            media_stem = re.sub(r'\.[a-z]{2,3}$', '', media_stem, flags=re.IGNORECASE).lower()

        matched_files: List[Tuple[int, Path]] = []  # (score, path)

        for f in candidate_files:
            if f.suffix.lower() not in SUB_EXTS:
                continue
            try:
                if f.stat().st_size == 0:
                    continue
            except Exception:
                continue

            f_name_lower = f.name.lower()
            f_stem = f.stem.lower()

            # Check if file has target language variant tag
            is_media_lang = bool(media_stem and matches_language(f.name, media_stem, target_code))
            has_lang = is_media_lang or any(
                f".{v}." in f_name_lower
                or f"_{v}." in f_name_lower
                or f"-{v}." in f_name_lower
                or f_name_lower.endswith(f".{v}{f.suffix.lower()}")
                or f_stem.endswith(f".{v}")
                or f_stem.endswith(f"_{v}")
                for v in target_variants
            )

            if not has_lang:
                continue

            fmt_prio = self._format_priority(f.suffix)

            # Priority 0: Exact match with media stem
            if media_stem and media_stem in f_stem:
                matched_files.append((0 + fmt_prio, f))
                continue

            # Priority 1: Exact match with episode tag (SxxExx)
            if ep_tag and ep_tag.lower() in f_name_lower:
                matched_files.append((10 + fmt_prio, f))
                continue

            # Priority 2: Episode name in filename
            if ep_name.lower() in f_name_lower:
                matched_files.append((20 + fmt_prio, f))
                continue

        if matched_files:
            matched_files.sort(key=lambda x: x[0])
            best_candidate = _clean_path(matched_files[0][1])

            # If existing arr_target_path exists on disk, check if candidate has better format
            existing_tgt = ep_meta.get("arr_target_path")
            if existing_tgt:
                try:
                    p = Path(existing_tgt)
                    if p.is_file() and p.stat().st_size > 0:
                        p_resolved = _clean_path(p)
                        if p_resolved == best_candidate:
                            return best_candidate
                        # If existing is .srt and candidate is .ass, upgrade to candidate
                        p_prio = self._format_priority(p.suffix)
                        cand_prio = self._format_priority(best_candidate.suffix)
                        if cand_prio < p_prio:
                            return best_candidate
                        return p_resolved
                except Exception:
                    pass

            return best_candidate

        # Check existing arr_target_path if no new candidates matched
        existing_tgt = ep_meta.get("arr_target_path")
        if existing_tgt:
            try:
                p = Path(existing_tgt)
                if p.is_file() and p.stat().st_size > 0:
                    p_name_lower = p.name.lower()
                    p_has_lang = any(
                        f".{v}." in p_name_lower or f"_{v}." in p_name_lower or p_name_lower.endswith(f".{v}{p.suffix.lower()}")
                        for v in target_variants
                    )
                    if p_has_lang:
                        return _clean_path(p)
            except Exception:
                pass

        return None

    def _find_matching_source_sub(
        self,
        ep_name: str,
        ep_meta: Dict[str, Any],
        candidate_files: List[Path],
        target_code: str = "el",
        source_code: str = "en",
    ) -> Optional[Path]:
        """Find a source language subtitle file matching this episode."""
        source_variants = variants_for(source_code)
        target_variants = variants_for(target_code)

        ep_tag = self._extract_episode_tag(ep_name)
        media_path = ep_meta.get("arr_media_path")
        media_stem = Path(media_path).stem if media_path else None
        if media_stem:
            media_stem = re.sub(r'\.[a-z]{2,3}$', '', media_stem, flags=re.IGNORECASE).lower()

        matched_files: List[Tuple[int, Path]] = []

        for f in candidate_files:
            if f.suffix.lower() not in SUB_EXTS:
                continue
            try:
                if f.stat().st_size == 0:
                    continue
            except Exception:
                continue

            f_name_lower = f.name.lower()
            f_stem = f.stem.lower()

            # Reject target subtitles
            is_target_sub = any(
                f".{v}." in f_name_lower
                or f"_{v}." in f_name_lower
                or f"-{v}." in f_name_lower
                or f_name_lower.endswith(f".{v}{f.suffix.lower()}")
                for v in target_variants
            )
            if is_target_sub:
                continue

            is_media_lang = bool(media_stem and matches_language(f.name, media_stem, source_code))
            has_lang = is_media_lang or any(
                f".{v}." in f_name_lower
                or f"_{v}." in f_name_lower
                or f"-{v}." in f_name_lower
                or f_name_lower.endswith(f".{v}{f.suffix.lower()}")
                or f_stem.endswith(f".{v}")
                or f_stem.endswith(f"_{v}")
                for v in source_variants
            )

            # Bare subtitle without language tag matching media stem or episode tag
            if not has_lang:
                if media_stem and f_stem == media_stem:
                    has_lang = True
                elif ep_tag and (ep_tag.lower() == f_stem or ep_tag.lower() in f_name_lower):
                    has_lang = True

            if not has_lang:
                continue

            fmt_prio = self._format_priority(f.suffix)

            if media_stem and media_stem in f_stem:
                matched_files.append((0 + fmt_prio, f))
            elif ep_tag and ep_tag.lower() in f_name_lower:
                matched_files.append((10 + fmt_prio, f))
            elif ep_name.lower() in f_name_lower:
                matched_files.append((20 + fmt_prio, f))

        if matched_files:
            matched_files.sort(key=lambda x: x[0])
            best_candidate = _clean_path(matched_files[0][1])

            existing_src = ep_meta.get("arr_sub_path")
            if existing_src:
                try:
                    p = Path(existing_src)
                    if p.is_file() and p.stat().st_size > 0:
                        p_resolved = _clean_path(p)
                        if p_resolved == best_candidate:
                            return best_candidate
                        p_prio = self._format_priority(p.suffix)
                        cand_prio = self._format_priority(best_candidate.suffix)
                        if cand_prio < p_prio:
                            return best_candidate
                        return p_resolved
                except Exception:
                    pass

            return best_candidate

        existing_src = ep_meta.get("arr_sub_path")
        if existing_src:
            try:
                p = Path(existing_src)
                if p.is_file() and p.stat().st_size > 0:
                    return _clean_path(p)
            except Exception:
                pass

        return None

    def _import_cues_from_file(self, project_name: str, episode_name: str, sub_file: Path, target_code: str) -> int:
        """Parse subtitle file and merge translations into episode data."""
        try:
            try:
                content = decode_subtitle_bytes(sub_file.read_bytes())
            except Exception:
                content = sub_file.read_text(encoding="utf-8", errors="replace")

            cues = parse_subtitle(content, filename=sub_file.name)
            if not cues:
                return 0
            if target_code == "el":
                # Safety net for Greek mojibake the whole-file repair left (a few lines of a mostly fine file).
                for c in cues:
                    for key in ("translated", "original"):
                        fixed = repair_mojibake(c.get(key) or "")
                        if fixed is not None:
                            c[key] = fixed

            existing_cues = storage.load_episode_data(project_name, episode_name) or []
            if existing_cues and len(existing_cues) == len(cues):
                for i, c in enumerate(existing_cues):
                    trans_text = cues[i].get("translated") or cues[i].get("original", "").strip()
                    c["translated"] = trans_text
                    c.setdefault("translations", {})[target_code] = trans_text
                storage.save_episode(project_name, episode_name, existing_cues)
                return len(existing_cues)
            elif existing_cues:
                for i, c in enumerate(existing_cues):
                    if i < len(cues):
                        trans_text = cues[i].get("translated") or cues[i].get("original", "").strip()
                        c["translated"] = trans_text
                        c.setdefault("translations", {})[target_code] = trans_text
                storage.save_episode(project_name, episode_name, existing_cues)
                return len(existing_cues)
            else:
                for c in cues:
                    t = c.get("translated") or c.get("original", "").strip()
                    c["translated"] = t
                    c.setdefault("translations", {})[target_code] = t
                storage.save_episode(project_name, episode_name, cues)
                return len(cues)
        except Exception as e:
            logger.warning(f"Could not import cues from {sub_file}: {e}")
            return 0

    def sync_project_disk(
        self,
        project_name: str,
        re_queue_removed: bool = False,
        import_cues: bool = True,
        auto_add_new_episodes: bool = True,
        auto_rename_matching_media: bool = True,
    ) -> Dict[str, Any]:
        """Perform disk-level synchronization for a single project.
        Scans all files on disk, updates translation status, handles additions and removals,
        and synchronizes queue items.

        A translation whose subtitle was deleted from disk is queued again only when the
        caller asks (``re_queue_removed``, the explicit sync buttons): a sync that runs on its
        own never mass-enqueues.
        """
        proj_meta = storage.load_project_metadata(project_name)
        if not proj_meta:
            return {"success": False, "error": f"Project '{project_name}' not found."}

        # 0. Automatically reconcile and rename subtitles to match current video file stems
        renamed_count = 0
        mismatched: List[Dict[str, str]] = []
        try:
            rename_res = self.rename_subtitles_to_match_media(project_name, apply=auto_rename_matching_media)
            renamed_count = rename_res.get("renamed_count", 0)
            if not auto_rename_matching_media:
                mismatched = rename_res.get("mismatched", [])
        except Exception as ren_e:
            logger.debug(f"Error checking subtitle names for {project_name}: {ren_e}")

        target_lang = proj_meta.get("target_language", "Greek")
        target_code = to_code(target_lang) or "el"
        source_code = "en"
        proj_disabled = bool(proj_meta.get("arr_disabled", False))
        gc = storage.load_global_config()
        from services.translation.export_formatter import resolve_cross_share_media

        episodes = storage.list_episodes(project_name)
        candidate_dirs = self._get_project_directories(project_name, proj_meta, episodes)

        # Collect all files across candidate directories without duplicates
        all_disk_files: List[Path] = []
        seen_paths: Set[str] = set()

        for d in candidate_dirs:
            if not d.exists() or not d.is_dir():
                continue
            try:
                for item in d.iterdir():
                    if item.is_file():
                        p_str = str(item).lower()
                        if p_str not in seen_paths:
                            seen_paths.add(p_str)
                            all_disk_files.append(item)
            except Exception as e:
                logger.debug(f"Error listing files in {d}: {e}")

        translations_found = 0
        translations_removed = 0
        queue_completed = 0
        queue_requeued = 0
        cues_imported_total = 0
        new_episodes_added = 0

        q = TranslationQueue()

        for ep_name in episodes:
            ep_meta = storage.load_episode_metadata(project_name, ep_name) or {}
            ep_dir = storage.episode_dir(project_name, ep_name)

            existing_cues = storage.load_episode_data(project_name, ep_name) or []
            meta_changed = False

            # Check if arr_media_path is stale or missing on disk, and re-resolve it
            if ep_meta.get("arr_media_path"):
                p_media = Path(ep_meta["arr_media_path"])
                if not p_media.exists():
                    resolved_media = resolve_cross_share_media(
                        ep_meta["arr_media_path"],
                        episode_name=ep_name,
                        metadata=ep_meta,
                        global_config=gc,
                        project_name=project_name,
                    )
                    if resolved_media and str(resolved_media) != ep_meta.get("arr_media_path"):
                        ep_meta["arr_media_path"] = str(resolved_media)
                        meta_changed = True

            ep_local_files = all_disk_files
            if ep_dir.exists():
                ep_local_files = list(all_disk_files)
                for f in ep_dir.iterdir():
                    if f.is_file() and str(f).lower() not in seen_paths:
                        ep_local_files.append(f)

            # Discover Subtitles on Disk
            source_sub = self._find_matching_source_sub(ep_name, ep_meta, ep_local_files, target_code=target_code, source_code=source_code)
            target_sub = self._find_matching_target_sub(ep_name, ep_meta, ep_local_files, target_code)

            # 1. Source Subtitle Handling
            if source_sub and source_sub.is_file() and source_sub.stat().st_size > 0:
                new_src = str(source_sub)
                if ep_meta.get("arr_sub_path") != new_src or not ep_meta.get("has_source_sub"):
                    ep_meta["arr_sub_path"] = new_src
                    ep_meta["has_source_sub"] = True
                    meta_changed = True

                # If episode has no stored cues, import source subtitle properly
                if import_cues and not existing_cues:
                    try:
                        src_content = source_sub.read_text(encoding="utf-8", errors="replace")
                        tgt_content = None
                        if target_sub and target_sub.is_file() and target_sub.stat().st_size > 0:
                            tgt_content = target_sub.read_text(encoding="utf-8", errors="replace")
                        import_and_clean_srt(
                            project_name,
                            ep_name,
                            src_content,
                            filename=source_sub.name,
                            target_srt_content=tgt_content,
                            extra_metadata={
                                "arr_sub_path": str(source_sub),
                                "has_source_sub": True,
                            }
                        )
                        existing_cues = storage.load_episode_data(project_name, ep_name) or []
                        cues_imported_total += len(existing_cues)
                        meta_changed = True
                    except Exception as e:
                        logger.warning(f"Failed to import source sub {source_sub}: {e}")
            else:
                if ep_meta.get("arr_sub_path"):
                    p = Path(ep_meta["arr_sub_path"])
                    if not p.is_file():
                        ep_meta["arr_sub_path"] = None
                        ep_meta["has_source_sub"] = False
                        meta_changed = True

            # 2. Target Subtitle Handling
            if target_sub and target_sub.is_file() and target_sub.stat().st_size > 0:
                was_completed = bool(
                    ep_meta.get("arr_has_target")
                    and ep_meta.get("translated")
                    and ep_meta.get("translation_status") == "completed"
                )
                new_tgt = str(target_sub)
                if not was_completed or ep_meta.get("arr_target_path") != new_tgt:
                    ep_meta["arr_has_target"] = True
                    ep_meta["arr_target_path"] = new_tgt
                    ep_meta["translated"] = True
                    ep_meta["translation_status"] = "completed"
                    meta_changed = True
                    if not was_completed:
                        translations_found += 1

                # Ingest cues if missing
                if import_cues:
                    has_translations = existing_cues and any(
                        (c.get("translated") or (c.get("translations") and c.get("translations").get(target_code)))
                        for c in existing_cues if isinstance(c, dict)
                    )
                    if not has_translations:
                        c_count = self._import_cues_from_file(project_name, ep_name, target_sub, target_code)
                        cues_imported_total += c_count

                if meta_changed:
                    storage.save_episode_metadata(project_name, ep_name, ep_meta, update_stats=False)
                    index_upsert(project_name, ep_name, ep_meta, original_exists=True, disabled=proj_disabled)

                # Complete queue items the file satisfies (explicit agent/user requests
                # such as gap-filling are left alone — a file existing isn't their goal).
                queue_completed += agent_task_service.mark_task_completed(project_name, ep_name)

            else:
                # Target DOES NOT exist on disk
                was_marked_translated = bool(
                    ep_meta.get("arr_has_target")
                    or ep_meta.get("arr_target_path")
                    or ep_meta.get("translated")
                    or ep_meta.get("translation_status") == "completed"
                )
                # Only a folder we can actually see can prove a subtitle was deleted; an
                # unreachable share must never wipe stored translations.
                expected_dir = next((Path(p).parent for p in (ep_meta.get("arr_target_path"), ep_meta.get("arr_media_path")) if p), None)
                folder_visible = bool(expected_dir) and self._dir_reachable(expected_dir)

                if was_marked_translated and folder_visible:
                    logger.info(f"Target subtitle for {project_name} - {ep_name} was removed from disk.")
                    ep_meta["arr_has_target"] = False
                    ep_meta["arr_target_path"] = None
                    ep_meta["translated"] = False
                    ep_meta["translation_status"] = "untranslated"

                    # Clear translated cues while preserving original source dialogue
                    if existing_cues:
                        for c in existing_cues:
                            if isinstance(c, dict):
                                c["translated"] = ""
                                if "translations" in c and target_code in c["translations"]:
                                    c["translations"][target_code] = ""
                        storage.save_episode(project_name, ep_name, existing_cues)

                    storage.save_episode_metadata(project_name, ep_name, ep_meta, update_stats=False)
                    index_upsert(project_name, ep_name, ep_meta, original_exists=True, disabled=proj_disabled)
                    translations_removed += 1

                    # Re-queue in translation queue if requested
                    if re_queue_removed:
                        row = None
                        with q._conn() as conn:
                            row = conn.execute("""
                                SELECT id, status FROM translation_queue
                                WHERE project_name = ? AND episode_name = ? AND kind = ?
                            """, (project_name, ep_name, KIND_TRANSLATION)).fetchone()

                            if row:
                                conn.execute("""
                                    UPDATE translation_queue
                                    SET status = 'pending', completed_at = NULL, last_error = NULL
                                    WHERE id = ?
                                """, (row["id"],))
                                conn.commit()

                        if row:
                            queue_requeued += 1
                        else:
                            from services.queue_service import enqueue_translation
                            enqueue_translation(project_name, ep_name, priority=PRIORITY_SYNC)
                            queue_requeued += 1
                elif meta_changed:
                    storage.save_episode_metadata(project_name, ep_name, ep_meta, update_stats=False)

        # 3. Discover untracked episodes on disk
        if auto_add_new_episodes:
            existing_ep_set = set(episodes)
            for f in all_disk_files:
                is_video = f.suffix.lower() in VIDEO_EXTS
                is_sub = f.suffix.lower() in SUB_EXTS
                if is_video or is_sub:
                    tag = self._extract_episode_tag(f.name)
                    if tag and tag not in existing_ep_set:
                        storage.create_project(project_name, {"target_language": target_lang})
                        new_meta = {
                            "episode_name": tag,
                            "original_filename": f.name,
                            "arr_media_path": str(f) if is_video else None,
                            "arr_sub_path": str(f) if is_sub else None,
                            "has_source_sub": is_sub,
                            "arr_has_target": False,
                            "translated": False,
                            "translation_status": "untranslated",
                        }
                        storage.save_episode_metadata(project_name, tag, new_meta, update_stats=False)
                        existing_ep_set.add(tag)
                        new_episodes_added += 1

        # Re-index project and refresh stats
        try:
            from utils.storage_modules.stats_and_index import update_project_stats_on_change
            update_project_stats_on_change(project_name)
        except Exception:
            pass

        result = {
            "success": True,
            "project_name": project_name,
            "scanned_episodes": len(episodes),
            "translations_found": translations_found,
            "translations_removed": translations_removed,
            "queue_completed": queue_completed,
            "queue_requeued": queue_requeued,
            "cues_imported": cues_imported_total,
            "new_episodes_added": new_episodes_added,
            "subtitles_renamed": renamed_count,
            # Subtitles named after an old video file (reported when renaming is off).
            "subtitles_mismatched": mismatched,
        }

        # Broadcast sync result to UI clients
        broadcast_event("sync_completed", result)
        broadcast_event("queue_updated", {"project_name": project_name, "reason": "disk_sync"})

        logger.info(
            f"Disk sync completed for '{project_name}': {translations_found} translations found, "
            f"{translations_removed} removed, {queue_requeued} re-queued, {new_episodes_added} new eps."
        )

        return result

    def sync_all_projects_disk(self, re_queue_removed: bool = False) -> Dict[str, Any]:
        """Perform disk sync across all projects."""
        projects = storage.list_projects()
        results: Dict[str, Any] = {}
        total_found = 0
        total_removed = 0
        total_requeued = 0

        for p_name in projects:
            res = self.sync_project_disk(p_name, re_queue_removed=re_queue_removed)
            results[p_name] = res
            total_found += res.get("translations_found", 0)
            total_removed += res.get("translations_removed", 0)
            total_requeued += res.get("queue_requeued", 0)

        summary = {
            "success": True,
            "total_projects": len(projects),
            "total_translations_found": total_found,
            "total_translations_removed": total_removed,
            "total_queue_requeued": total_requeued,
            "project_results": results,
        }

        broadcast_event("sync_completed", summary)
        broadcast_event("queue_updated", {"reason": "library_disk_sync"})

        return summary


    def rename_subtitles_to_match_media(self, project_name: str, apply: bool = True) -> Dict[str, Any]:
        """Find subtitles in the project's media folders whose name no longer matches
        their video (e.g. after a Sonarr upgrade renamed "... AAC 2.0.mkv" to
        "... Opus 2.0.mkv") and, with ``apply``, rename them to the current video
        stem, keeping language/forced/SDH tags. Never overwrites an existing file.
        Episode metadata pointing at a renamed file is updated.
        """
        from utils.subtitle_naming import plan_directory, apply_plan

        proj_meta = storage.load_project_metadata(project_name)
        if not proj_meta:
            return {"success": False, "error": f"Project '{project_name}' not found."}

        episodes = storage.list_episodes(project_name)
        internal = str(storage.project_dir(project_name)).lower()
        plan: List[Dict[str, str]] = []
        for d in self._get_project_directories(project_name, proj_meta, episodes):
            if str(d).lower().startswith(internal):
                continue
            try:
                if d.is_dir():
                    plan.extend(plan_directory(d))
            except OSError as e:
                logger.debug(f"Cannot inspect {d} for subtitle names: {e}")

        mismatched = [e for e in plan if e["status"] == "rename"]
        renamed: List[Dict[str, str]] = []
        errors: List[str] = []
        if apply and mismatched:
            renamed, errors = apply_plan(mismatched)
            moved = {r["old_path"].lower(): r["path"] for r in renamed}
            for ep_name in episodes:
                ep_meta = storage.load_episode_metadata(project_name, ep_name)
                if not ep_meta:
                    continue
                changed = False
                for key in ("arr_target_path", "arr_sub_path"):
                    new_path = moved.get(str(ep_meta.get(key) or "").lower())
                    if new_path:
                        ep_meta[key] = new_path
                        changed = True
                if changed:
                    storage.save_episode_metadata(project_name, ep_name, ep_meta, update_stats=False)
            for r in renamed:
                logger.info(f"Renamed subtitle to match its video: '{r['old_name']}' -> '{r['new_name']}'")

        res = {
            "success": True,
            "project_name": project_name,
            "applied": apply,
            "mismatched_count": len(mismatched),
            "mismatched": [{"old_name": Path(e["old"]).name, "new_name": Path(e["new"]).name} for e in mismatched],
            "renamed_count": len(renamed),
            "renamed_files": renamed,
            "conflicts": [Path(e["old"]).name for e in plan if e["status"] == "conflict"],
            "unmatched": [Path(e["old"]).name for e in plan if e["status"] in ("unmatched", "ambiguous")],
            "errors": errors,
        }
        if renamed:
            broadcast_event("subtitles_renamed", {k: res[k] for k in ("project_name", "renamed_count")})
        return res


disk_sync_service = DiskSyncService()

