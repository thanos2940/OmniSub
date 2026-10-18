"""
Export formatting, subtitle output conformance, and SubtitleEdit integration.
"""

import copy
import logging
import os
import tempfile
from pathlib import Path
from typing import List, Dict, Optional

from utils import storage
from utils.language_codes import to_code, output_filename_from_original
from utils.subtitle_fixer import _run_se_cli, SubtitleEditUnavailable
import utils.source_clean as source_clean
import utils.subtitle_fixer as subtitle_fixer

logger = logging.getLogger(__name__)


def _apply_output_conformance(rows: List[Dict], target_lang_code: str, global_config: Dict) -> None:
    """Re-wrap over-long lines for OUTPUT only — mutates the given (copied) rows.

    Conformance wrapping is a formatting fix, so like SubtitleEdit fixes it belongs at
    export, never in the editor's 1:1 data.
    """
    try:
        from utils.subtitle_conformance import default_limits, apply_wrapping
        from utils.text_normalize import sanitize_translation_text
        limits = default_limits(global_config)
        for line in rows:
            raw_trans = (line.get("translations", {}) or {}).get(target_lang_code, "") or line.get("translated", "")
            line["translated"] = sanitize_translation_text(raw_trans, source_text=line.get("original", ""), target_lang_code=target_lang_code)
        apply_wrapping(rows, limits)
        for line in rows:
            line.setdefault("translations", {})[target_lang_code] = line.get("translated", "")
    except Exception as e:
        logger.warning(f"Output conformance wrapping failed: {e}")


def resolve_cross_share_media(
    media_path: Optional[str],
    episode_name: Optional[str] = None,
    metadata: Optional[Dict] = None,
    global_config: Optional[Dict] = None,
    project_name: Optional[str] = None,
) -> Optional[Path]:
    """Re-resolve stale or cross-share media paths when direct path mapping fails.

    When mapped_media and its parent do not exist on disk, checks project_meta["arr_path"]
    and probes alternative path_mappings roots while preserving relative path tails.
    Updates metadata["arr_media_path"] if resolved to a new valid file path.
    """
    if not media_path:
        return None
    if metadata is None:
        metadata = {}
    if global_config is None:
        from utils import storage
        global_config = storage.load_global_config()

    from integrations.path_resolver import PathResolver
    mappings = global_config.get("arr_path_mappings", [])
    resolver = PathResolver(mappings)

    # 1. Direct check
    found = resolver.find_media_file(media_path, episode_name=episode_name)
    if found:
        p = Path(found)
        if p.exists() and p.is_file():
            if str(p) != media_path:
                metadata["arr_media_path"] = str(p)
            return p

    resolved_direct = resolver.resolve(media_path)
    if resolved_direct:
        p_dir = Path(resolved_direct)
        if p_dir.exists() and p_dir.is_file():
            if str(p_dir) != media_path:
                metadata["arr_media_path"] = str(p_dir)
            return p_dir

    video_exts = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov"}
    target_filename = Path(media_path).name

    # 2. Check project_meta["arr_path"] / "path" / "media_path"
    proj_name = project_name or metadata.get("project_name")
    from utils import storage
    proj_meta = storage.load_project_metadata(proj_name) if proj_name else None

    if proj_meta:
        for pkey in ("arr_path", "path", "media_path", "root_folder_path"):
            cand_folder = proj_meta.get(pkey)
            if not cand_folder:
                continue
            resolved_folder = resolver.resolve(cand_folder)
            rf_path = Path(resolved_folder)
            if not rf_path.exists() or not rf_path.is_dir():
                continue

            parent_dir_name = Path(media_path).parent.name
            if parent_dir_name:
                cand_in_season = rf_path / parent_dir_name / target_filename
                if cand_in_season.exists() and cand_in_season.is_file():
                    metadata["arr_media_path"] = str(cand_in_season)
                    return cand_in_season

            cand_direct = rf_path / target_filename
            if cand_direct.exists() and cand_direct.is_file():
                metadata["arr_media_path"] = str(cand_direct)
                return cand_direct

            try:
                for sub in rf_path.iterdir():
                    if sub.is_file() and sub.suffix.lower() in video_exts:
                        if sub.name.lower() == target_filename.lower() or (episode_name and episode_name.lower() in sub.name.lower()):
                            metadata["arr_media_path"] = str(sub)
                            return sub
                    elif sub.is_dir():
                        for ep_file in sub.iterdir():
                            if ep_file.is_file() and ep_file.suffix.lower() in video_exts:
                                if ep_file.name.lower() == target_filename.lower() or (episode_name and episode_name.lower() in ep_file.name.lower()):
                                    metadata["arr_media_path"] = str(ep_file)
                                    return ep_file
            except OSError:
                pass

    # 3. Probe alternative path_mappings roots preserving relative tail
    norm_media = media_path.replace("\\", "/")
    tails_to_try = []
    for m in mappings:
        rem = (m.get("remote") or "").replace("\\", "/").rstrip("/")
        if rem and norm_media.lower().startswith(rem.lower()):
            tail = norm_media[len(rem):].lstrip("/")
            if tail and tail not in tails_to_try:
                tails_to_try.append(tail)

    parent_tail = Path(media_path).parent.name
    if parent_tail:
        season_tail = f"{parent_tail}/{target_filename}"
        if season_tail not in tails_to_try:
            tails_to_try.append(season_tail)
    if target_filename not in tails_to_try:
        tails_to_try.append(target_filename)

    for m in mappings:
        local_root_str = m.get("local")
        if not local_root_str:
            continue
        local_root = Path(local_root_str)
        if not local_root.exists() or not local_root.is_dir():
            continue

        for tail in tails_to_try:
            cand = local_root / tail
            try:
                if cand.exists() and cand.is_file():
                    metadata["arr_media_path"] = str(cand)
                    return cand
                if cand.parent.exists() and cand.parent.is_dir():
                    import re
                    for f in cand.parent.iterdir():
                        if f.is_file() and f.suffix.lower() in video_exts:
                            if episode_name and (re.search(rf"(?i)(?:\b|_){re.escape(episode_name)}(?:\b|_)", f.name) or episode_name.lower() in f.name.lower()):
                                metadata["arr_media_path"] = str(f)
                                return f
            except OSError:
                pass

    return None


def _current_media_file(episode_name: str, metadata: Dict, global_config: Dict, project_name: Optional[str] = None) -> Optional[Path]:
    """The episode's video file as it is named on disk *now*.

    Sonarr/Radarr upgrades and renames replace the file behind ``arr_media_path``
    ("... AAC 2.0.mkv" -> "... Opus 2.0.mkv"). Naming the subtitle after the stale
    path would leave it orphaned for Plex, so look the episode up in its folder
    and remember the new path.
    """
    media_path = metadata.get("arr_media_path")
    if not media_path:
        return None
    from integrations.path_resolver import PathResolver
    resolver = PathResolver(global_config.get("arr_path_mappings", []))
    found = resolver.find_media_file(media_path, episode_name=episode_name)
    if found:
        if found != media_path:
            logger.info(f"Media file for {episode_name} is now '{Path(found).name}' (was '{Path(media_path).name}').")
            metadata["arr_media_path"] = found
        return Path(found)

    resolved_cross = resolve_cross_share_media(
        media_path=media_path,
        episode_name=episode_name,
        metadata=metadata,
        global_config=global_config,
        project_name=project_name or metadata.get("project_name"),
    )
    if resolved_cross:
        return resolved_cross

    resolved = resolver.resolve(media_path)
    return Path(resolved) if resolved else None


def _atomic_write_text(path: Path, content: str, encoding: str = "utf-8-sig") -> None:
    """Write ``content`` to ``path`` via a temp file in the same folder + ``os.replace``.

    Readers (Plex, players) therefore see either the old file or the complete new one,
    never a half-written file. The temp file is removed on failure.
    """
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(content)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _foreign_blocked(path: Path, metadata: Dict, protect: bool, force: bool) -> bool:
    """True when ``path`` exists, is not OmbiSub's own file and must not be overwritten."""
    if force or not protect:
        return False
    try:
        if not path.exists():
            return False
    except OSError:
        return False
    from services.autopilot import ownership
    return not ownership.is_ours(path, metadata)


def _register_written(path: Path, project_name: Optional[str], episode_name: Optional[str]) -> None:
    from services.autopilot import ownership
    ownership.register_file(path, project_name, episode_name)


def _remove_superseded_target(previous: Optional[str], current: Path, metadata: Optional[Dict] = None) -> None:
    """Delete our previous export when it was written under an outdated video name."""
    if not previous:
        return
    from utils.subtitle_naming import split_subtitle_name
    old = Path(previous)
    old_stem, old_suffix = split_subtitle_name(old.name)
    new_stem, new_suffix = split_subtitle_name(current.name)
    # Same kind of file (e.g. ".el.ass") under an outdated stem — a different format
    # beside it (".el.srt") is someone else's file and stays.
    if old_suffix.lower() != new_suffix.lower() or old_stem.lower() == new_stem.lower():
        return
    try:
        if old.parent == current.parent and old.is_file():
            from services.autopilot import ownership
            if not ownership.is_ours(old, metadata):
                logger.info(f"Kept '{old.name}': it is not a file OmbiSub wrote.")
                return
            old.unlink()
            ownership.forget(old)
            logger.info(f"Removed superseded subtitle '{old.name}' (now '{current.name}').")
    except OSError as e:
        logger.warning(f"Could not remove superseded subtitle '{old}': {e}")


def _ass_rows_to_srt(rows: List[Dict], tgt_lang: str) -> str:
    """Plain SRT of the spoken dialogue of an ASS episode, for players without ASS support.

    Only translated dialogue events are included (signs, karaoke and drawings are
    typesetting and have no place in a plain-text track); stacked layers with the same
    timing and text collapse to one cue.
    """
    import re
    from utils.ass_parser import _srt_timecode_to_ms

    cues, seen = [], set()
    for r in rows:
        meta = r.get("_ass") or {}
        if meta.get("kind") != "dialogue" or meta.get("translatable") is False:
            continue
        text = (r.get("translations", {}) or {}).get(tgt_lang) or r.get("translated") or ""
        text = re.sub(r"\{[^}]*\}", "", text)
        text = re.sub(r"\\[Nn]", "\n", text).replace("\\h", " ").strip()
        if not text:
            continue
        start, end = _srt_timecode_to_ms(r.get("timecode", ""))
        key = (start, end, text)
        if key in seen:
            continue
        seen.add(key)
        cues.append((start, end, r.get("timecode"), text))
    cues.sort(key=lambda c: (c[0], c[1]))
    return "\n".join(f"{i}\n{tc}\n{t}\n" for i, (_, _, tc, t) in enumerate(cues, start=1))


def _write_plain_srt_companion(rows: List[Dict], media_file: Path, tgt_lang: str,
                               metadata: Optional[Dict] = None, project_name: Optional[str] = None,
                               episode_name: Optional[str] = None, protect: bool = True,
                               force: bool = False) -> Optional[str]:
    """Write ``<video stem>.<lang>.srt`` beside the styled ``.ass`` export.

    Atomic, guarded against foreign files and registered like the main export. A failure
    is logged as an error. Returns the path written, else None.
    """
    path = media_file.parent / output_filename_from_original(f"{media_file.stem}.srt", tgt_lang)
    try:
        content = _ass_rows_to_srt(rows, tgt_lang)
        if not content.strip():
            return None
        if _foreign_blocked(path, metadata or {}, protect, force):
            logger.warning(f"Not writing SRT companion '{path}': a subtitle OmbiSub did not write is already there.")
            return None
        _atomic_write_text(path, content, "utf-8-sig")
        _register_written(path, project_name, episode_name)
        return str(path)
    except Exception as e:
        logger.error(f"Could not write plain SRT companion '{path}': {e}")
        return None


def _has_translated_srt_sibling(project_name: str, episode_name: str, tgt_lang: str) -> bool:
    """True when the project holds a translated SRT sibling of this ASS episode.

    The dual-format sync keeps an episode's plain ``.srt`` as ``"<episode> [srt]"`` with its
    own translation; that sibling exports the real ``<video>.el.srt``, which is a better file
    than the dialogue-only companion derived from the ASS rows -- so the companion stands down.
    """
    try:
        from integrations.media_sync_engine import secondary_episode_name
        sibling = secondary_episode_name(episode_name, "srt")
        data = (storage.load_episode(project_name, sibling) or {}).get("data") or []
        return any(
            bool((row.get("translations", {}) or {}).get(tgt_lang) or row.get("translated"))
            for row in data
        )
    except Exception as e:
        logger.debug(f"SRT sibling lookup failed for {project_name}/{episode_name}: {e}")
        return False


def auto_export_translated_subtitle(project_name: str, episode_name: str, parsed_srt: List[Dict], metadata: Dict,
                                    force: bool = False) -> Optional[str]:
    """Export the translated subtitle next to its media file (and to the optional
    auto-export dir). Returns the media-adjacent target path if written, else None.

    All output-level fixes (conformance wrapping, SubtitleEdit FixCommonErrors +
    SplitLongLines) are applied HERE, on a copy / on the written file — never to the
    editor's ``parsed_srt`` / data.json, which must stay 1:1 with the source.

    Files are written atomically and registered as OmbiSub's own. With the setting
    ``autopilot_protect_foreign_subs`` (default on) an existing subtitle OmbiSub did not
    write is never overwritten unless ``force`` is true (explicit user exports); the
    episode then gets a ``translation_warning`` and None is returned.
    """
    exported_path: Optional[str] = None
    try:
        # Map target language name to code
        proj_meta = storage.load_project_metadata(project_name) or {}
        target_lang_name = proj_meta.get("target_language", "Greek")
        tgt_lang = to_code(target_lang_name)

        gc = storage.load_global_config()
        protect = bool(gc.get("autopilot_protect_foreign_subs", True))

        # 1. Export next to the media file, named after the video as it is named now
        media_file = _current_media_file(episode_name, metadata, gc, project_name=project_name)

        _, original_ext = storage.load_original_subtitle(project_name, episode_name)
        ext = original_ext.lstrip('.') if original_ext else "srt"

        export_rows = parsed_srt
        if gc.get("conformance_enabled", False) and ext not in ("ass", "ssa"):
            export_rows = copy.deepcopy(parsed_srt)
            _apply_output_conformance(export_rows, tgt_lang, gc)

        translated_srt_content = source_clean.reconstruct_cleaned_srt(project_name, episode_name, export_rows, tgt_lang)

        if gc.get("apply_subtitle_edit_fixes") and ext not in ["ass", "ssa"]:
            try:
                import services.translation_service as ts
                se_fn = getattr(ts, "_run_se_cli", subtitle_fixer._run_se_cli)
                translated_srt_content = se_fn(translated_srt_content)
            except SubtitleEditUnavailable:
                pass
            except Exception as e:
                logger.warning(f"SubtitleEdit fixes failed at export for {project_name}/{episode_name}: {e}")

        if media_file:
            target_filename = output_filename_from_original(f"{media_file.stem}.{ext}", tgt_lang)
            target_srt_path = media_file.parent / target_filename
            try:
                if not target_srt_path.parent.exists():
                    raise FileNotFoundError(f"Output directory does not exist or is unreachable: '{target_srt_path.parent}'.")
                if _foreign_blocked(target_srt_path, metadata, protect, force):
                    msg = (f"Not exported: '{target_srt_path.name}' already exists and was not written by OmbiSub "
                           f"(protected). Export manually to replace it.")
                    logger.warning(f"{msg} [{project_name}/{episode_name}]")
                    metadata["translation_warning"] = msg
                    storage.save_episode_metadata(project_name, episode_name, metadata)
                    main_written = False
                else:
                    _atomic_write_text(target_srt_path, translated_srt_content, "utf-8-sig")
                    _register_written(target_srt_path, project_name, episode_name)
                    logger.info(f"Auto-exported translated subtitle next to media: {target_srt_path}")
                    _remove_superseded_target(metadata.get("arr_target_path"), target_srt_path, metadata)
                    main_written = True

                if ext in ("ass", "ssa") and not _has_translated_srt_sibling(project_name, episode_name, tgt_lang):
                    _write_plain_srt_companion(
                        export_rows, media_file, tgt_lang, metadata, project_name, episode_name, protect, force)

                if main_written:
                    metadata["arr_has_target"] = True
                    metadata["arr_target_path"] = str(target_srt_path)
                    metadata.pop("translation_warning", None)
                    storage.save_episode_metadata(project_name, episode_name, metadata)
                    exported_path = str(target_srt_path)
            except (OSError, FileNotFoundError) as write_err:
                logger.error(f"Failed to auto-export subtitle to '{target_srt_path}': {write_err}")
                try:
                    from services.queue_service import enqueue_export_retry
                    enqueue_export_retry(
                        project_name=project_name,
                        episode_name=episode_name,
                        target_path=str(target_srt_path),
                        lang=tgt_lang,
                        error=str(write_err),
                    )
                except Exception as q_err:
                    logger.error(f"Failed to enqueue export retry from auto_export: {q_err}")
                metadata["translation_warning"] = f"Export scheduled for retry: {target_srt_path} ({write_err})"
                storage.save_episode_metadata(project_name, episode_name, metadata)
            
        # 2. Export to settings-defined auto_export_dir if enabled
        project_settings = proj_meta.get("settings", {})
        if project_settings.get("auto_export_enabled") and project_settings.get("auto_export_dir"):
            export_dir = project_settings.get("auto_export_dir")
            os.makedirs(export_dir, exist_ok=True)
            
            orig_filename = metadata.get("original_filename")
            if orig_filename:
                filename = output_filename_from_original(orig_filename, tgt_lang)
            else:
                filename = output_filename_from_original(f"{episode_name}.{ext}", tgt_lang)
                
            export_path = os.path.join(export_dir, filename)
            _atomic_write_text(Path(export_path), translated_srt_content, "utf-8")
            logger.info(f"Auto-exported translated subtitle to dir: {export_path}")
            
    except Exception as e:
        logger.error(f"Error auto-exporting translated subtitle for {project_name}/{episode_name}: {e}", exc_info=True)
    return exported_path
