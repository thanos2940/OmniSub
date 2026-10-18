"""
Translation Queue Item Processor.
"""

import asyncio
import json
import logging
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from utils import storage
from utils.language_codes import to_code as get_lang_code
from utils.rate_limiter import per_model_rate_limiter, active_model_var
from integrations.subtitle_scanner import SubtitleScannerService
from services.worker.notification_handler import WorkerNotifier
from services.translation_service import _translate_episode_atomic, create_translation_pipeline
from integrations.embedded_subs import write_sidecar

# Alias for external callers and tests
translate_episode_atomic = _translate_episode_atomic

logger = logging.getLogger(__name__)


class UnrecoverableTranslationError(ValueError):
    """A failure no retry can fix: the episode is gone, or there is nothing to
    translate from. Failing it at once spares the queue days of doomed retries
    and the log a traceback per attempt."""


_glossary_save_locks: Dict[str, threading.RLock] = {}
_glossary_registry_lock = threading.Lock()

def _get_project_glossary_lock(project_name: str) -> threading.RLock:
    with _glossary_registry_lock:
        if project_name not in _glossary_save_locks:
            _glossary_save_locks[project_name] = threading.RLock()
        return _glossary_save_locks[project_name]


def _record_target(ep_meta: dict, is_primary: bool, code: str, target_path, src_fp) -> None:
    """Record a produced target subtitle: flat fields for the primary language,
    or the per-language ``arr_targets[code]`` record for additional languages (Plan 15)."""
    if is_primary:
        ep_meta["arr_has_target"] = True
        ep_meta["arr_target_path"] = str(target_path)
        ep_meta["arr_translated_from_fingerprint"] = src_fp
    else:
        targets = ep_meta.setdefault("arr_targets", {})
        targets[code] = {
            "has_target": True,
            "target_path": str(target_path),
            "translated_from_fingerprint": src_fp,
        }


async def process_translation_item(*args, **kwargs) -> None:
    queue = kwargs.get("queue")
    item = kwargs.get("item")
    global_config = kwargs.get("global_config")

    if args:
        if queue is None and item is None:
            if len(args) == 1:
                if isinstance(args[0], dict):
                    item = args[0]
                else:
                    queue = args[0]
            elif len(args) >= 2:
                queue = args[0]
                item = args[1]
                if len(args) >= 3:
                    global_config = args[2]
        elif queue is not None and item is None:
            item = args[0]
        elif item is not None and queue is None:
            queue = args[0]

    if isinstance(queue, dict) and item is not None and not isinstance(item, dict):
        queue, item = item, queue
    elif isinstance(queue, dict) and item is None:
        item = queue
        queue = None

    if queue is None:
        from utils.translation_queue import TranslationQueue
        queue = TranslationQueue()

    if global_config is None:
        global_config = storage.load_global_config()

    item_id = item["id"]
    project_name = item["project_name"]
    episode_name = item["episode_name"]
    attempts = item.get("attempts", 0) if isinstance(item, dict) else 0
    job_id = f"bg_{item_id}"

    from utils.jobs_manager import JobStatus, jobs, update_job

    item_opts = item.get("options") or {}
    if isinstance(item_opts, str):
        try:
            item_opts = json.loads(item_opts)
        except Exception:
            item_opts = {}
    parent_job_id = item_opts.get("parent_job_id") if isinstance(item_opts, dict) else None

    jobs[job_id] = JobStatus(
        id=job_id, status="running", progress=0.0,
        message=f"Background translation of {episode_name}...", logs=[],
        project_name=project_name, episode_name=episode_name,
        parent_id=parent_job_id
    )

    update_job(job_id, log=f"Starting background translation task for {episode_name}...")

    def _is_task_cancelled() -> bool:
        if job_id in jobs and jobs[job_id].cancelled:
            return True
        try:
            db_item = queue.get_item(item_id)
            if db_item and db_item.get("status") == "cancelled":
                if job_id in jobs:
                    jobs[job_id].cancelled = True
                return True
        except Exception:
            pass
        return False

    try:
        from utils.telemetry import usage_ctx
        usage_ctx.set({"project": project_name, "episode": episode_name, "lane": "live"})
    except Exception:
        pass

    webhook_url = global_config.get("discord_webhook_url")
    notifier = WorkerNotifier(webhook_url)

    try:
        options = {}
        if item.get("options"):
            if isinstance(item["options"], dict):
                options = item["options"]
            else:
                try:
                    options = json.loads(item["options"])
                except Exception:
                    pass

        translation_type = options.get("translation_type", "saved")
        force_new = (translation_type == "original" or options.get("force", False))

        ep_meta = storage.load_episode_metadata(project_name, episode_name)
        if not ep_meta:
            raise UnrecoverableTranslationError("Episode metadata not found in storage.")

        media_path = ep_meta.get("arr_media_path")
        media_file = None
        from integrations.path_resolver import PathResolver
        path_resolver = PathResolver(global_config.get("arr_path_mappings", []))
        if media_path:
            found_media = path_resolver.find_media_file(media_path, episode_name=episode_name)
            if found_media:
                media_path = found_media
                media_file = Path(found_media)
                if ep_meta.get("arr_media_path") != found_media:
                    ep_meta["arr_media_path"] = found_media
                    storage.save_episode_metadata(project_name, episode_name, ep_meta, update_stats=False)
            else:
                resolved = path_resolver.resolve(media_path)
                media_file = Path(resolved) if resolved else None

        project_meta = storage.load_project_metadata(project_name) or {}

        req_lang_code = item.get("lang") or ""
        primary_code = get_lang_code(project_meta.get("target_language", "Greek"))
        is_primary_lang = (req_lang_code == "" or req_lang_code == primary_code)
        target_lang_name = options.get("target_language_name") or project_meta.get("target_language", "Greek")
        tgt_lang = primary_code if is_primary_lang else req_lang_code
        lang_meta_key = None if is_primary_lang else tgt_lang

        current_src_fp = ep_meta.get("arr_sub_fingerprint")
        is_stale = storage.episode_translation_is_stale(ep_meta, lang_meta_key)
        if is_stale:
            update_job(job_id, log=f"Source subtitle changed since last translation ({tgt_lang}) — re-translating instead of reusing the stale target.")

        # Zero-token check: If Bazarr or an external source already supplied the target subtitle on disk, skip translation
        if not force_new:
            target_already_present = False
            found_target_path = None
            if media_path:
                src_lang = global_config.get("arr_source_language", "en")
                scanner = SubtitleScannerService(source_lang_code=src_lang, target_lang_code=tgt_lang)
                scan_res = scanner.scan_media_file(media_path)
                if scan_res.has_target_sub and not is_stale:
                    target_already_present = True
                    found_target_path = scan_res.target_sub_path
            elif not is_stale and storage.target_subtitle_exists(project_name, episode_name, lang_code=tgt_lang):
                target_already_present = True
                found_target_path = str(storage.episode_dir(project_name, episode_name) / f"target.{tgt_lang}.srt")

            if target_already_present:
                logger.info(f"Target subtitle already exists for {episode_name} at {found_target_path}. Skipping translation (0 tokens used).")
                update_job(job_id, log=f"Target subtitle already exists at: {found_target_path} (from Bazarr or storage). Skipping translation.")
                update_job(job_id, status="completed", progress=100.0, message="Translation completed (skipped: target exists).")
                queue.update_status(
                    item_id,
                    status="completed",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    logs=jobs[job_id].logs if job_id in jobs else []
                )
                if found_target_path:
                    _record_target(ep_meta, is_primary_lang, tgt_lang, found_target_path, current_src_fp)
                storage.save_episode_metadata(project_name, episode_name, ep_meta)
                return

        # Auto-extract embedded track if episode has no source subtitle yet
        has_sub = (ep_meta.get("has_source_sub", True) and ep_meta.get("line_count", 0) > 0) or storage.original_subtitle_exists(project_name, episode_name)
        extract_error = None
        if not has_sub:
            update_job(job_id, log=f"No source subtitle on disk for {episode_name}. Checking video files / auto-extracting embedded track...")
            from services.embedded_extractor import extract_embedded_for_episode
            try:
                await extract_embedded_for_episode(project_name, episode_name, force=False, migrate_srt=True)
                ep_meta = storage.load_episode_metadata(project_name, episode_name) or ep_meta
                has_sub = (ep_meta.get("has_source_sub", True) and ep_meta.get("line_count", 0) > 0) or storage.original_subtitle_exists(project_name, episode_name)
                if ep_meta.get("arr_media_path"):
                    media_path = ep_meta.get("arr_media_path")
                    found_media = path_resolver.find_media_file(media_path, episode_name=episode_name)
                    media_file = Path(found_media or path_resolver.resolve(media_path))
            except Exception as e:
                extract_error = e
                logger.warning(f"Could not auto-extract embedded track for {episode_name}: {e}")

        if not has_sub:
            message = f"Episode '{episode_name}' has no source subtitle cues to translate."
            # A share that dropped out is worth retrying; a file with no usable
            # subtitle track will still have none tomorrow.
            if (
                extract_error is not None
                and ep_meta.get("arr_media_path")
                and "reachable" in str(extract_error).lower()
            ):
                raise ValueError(f"{message} The media file is not reachable right now: {extract_error}")
            raise UnrecoverableTranslationError(
                message + (f" {extract_error}" if extract_error is not None else "")
            )


        # Comparison mode handling: side-by-side comparison without overwriting primary translation or exporting to disk
        is_comparison = bool(options.get("comparison_mode") or translation_type == "comparison")
        if is_comparison:
            from utils.model_resolver import resolve_model
            comparison_key = options.get("comparison_key") or f"compare_{int(datetime.now(timezone.utc).timestamp())}"
            model_name = options.get("model") or resolve_model("translation", project_meta)
            update_job(job_id, log=f"Running comparison translation for key '{comparison_key}' using model '{model_name}'")
            from services.translation.batch_runner import _process_retranslation_for_comparison
            await _process_retranslation_for_comparison(
                job_id=job_id,
                project_name=project_name,
                episode_name=episode_name,
                target_lang=target_lang_name,
                model_name=model_name,
                comparison_key=comparison_key
            )
            queue.update_status(
                item_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                logs=jobs[job_id].logs if job_id in jobs else []
            )
            return

        # 1. Handle "saved" translation reuse (re-export)
        if translation_type == "saved" and not is_stale:
            update_job(job_id, log="Checking for saved translation in storage...")
            ep_data = storage.load_episode(project_name, episode_name)
            if ep_data and ep_data.get("data") and any(line.get("translations", {}).get(tgt_lang) or (is_primary_lang and line.get("translated")) for line in ep_data["data"]):
                update_job(job_id, log="Found saved translation. Re-exporting to disk...")
                from services.translation.export_formatter import auto_export_translated_subtitle
                ep_meta = storage.load_episode_metadata(project_name, episode_name) or ep_meta
                exported_path = await asyncio.to_thread(auto_export_translated_subtitle, project_name, episode_name, ep_data["data"], ep_meta)
                target_srt_path = Path(exported_path) if exported_path else (Path(ep_meta.get("arr_target_path")) if ep_meta.get("arr_target_path") else None)
                write_success = bool(target_srt_path and target_srt_path.exists())
                write_error_msg = ep_meta.get("translation_warning", "")

                if not write_success:
                    if not target_srt_path:
                        if media_file:
                            orig_filename = ep_meta.get("original_filename") or ""
                            ext = Path(orig_filename).suffix
                            if not ext:
                                _, ext_sub = storage.load_original_subtitle(project_name, episode_name)
                                ext = f".{ext_sub}" if ext_sub else ".srt"
                            from utils.language_codes import output_filename_from_original
                            target_filename = output_filename_from_original(f"{media_file.stem}{ext}", tgt_lang)
                            target_srt_path = media_file.parent / target_filename
                        else:
                            target_srt_path = storage.episode_dir(project_name, episode_name) / f"{episode_name}.{tgt_lang}.srt"
                    logger.error(f"Failed to re-export saved translation to '{target_srt_path}': {write_error_msg}")
                    update_job(job_id, log=f"Warning: Failed to re-export subtitle to unreachable path: {target_srt_path}. Queuing for export retry.")
                    try:
                        from services.queue_service import enqueue_export_retry
                        enqueue_export_retry(
                            project_name=project_name,
                            episode_name=episode_name,
                            target_path=str(target_srt_path),
                            lang=tgt_lang,
                            error=write_error_msg or "Export failed",
                            queue=queue,
                        )
                    except Exception as q_err:
                        logger.error(f"Failed to enqueue export retry: {q_err}")
                else:
                    logger.info(f"Successfully re-exported saved translation to: {target_srt_path}")
                
                logger.info(f"Re-exported saved translation for {episode_name} to {target_srt_path} (success={write_success})")
                completion_msg = (
                    "Translation completed (re-exported saved translation)."
                    if write_success
                    else "Translation completed (re-export failed: destination unreachable, queued for retry)."
                )
                update_job(job_id, status="completed", progress=100.0, message=completion_msg)
                
                queue.update_status(
                    item_id, 
                    status="completed", 
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    logs=jobs[job_id].logs
                )
                
                if write_success:
                    _record_target(ep_meta, is_primary_lang, tgt_lang, target_srt_path, current_src_fp)
                    ep_meta.pop("translation_warning", None)
                else:
                    ep_meta["translation_warning"] = f"Export scheduled for retry: {target_srt_path} ({write_error_msg})"
                storage.save_episode_metadata(project_name, episode_name, ep_meta)
                return
            else:
                update_job(job_id, log="No saved translation found. Performing a new translation.")

        # 2. Handle "original" translation or force-retranslation resetting
        if force_new:
            update_job(job_id, log="Original translation requested. Clearing existing translations and checkpoint...")
            ep_data = storage.load_episode(project_name, episode_name)
            if ep_data and ep_data.get("data"):
                for line in ep_data["data"]:
                    line.pop("translated", None)
                    line.pop("from_tm", None)
                    line.pop("tm_user_edited", None)
                    line.pop("needs_review", None)
                    line.pop("review_issues", None)
                    line.pop("review_scores", None)
                ep_meta["translated"] = False
                ep_meta["translation_status"] = "pending"
                ep_meta.pop("translation_failed", None)
                ep_meta.pop("translation_error", None)
                storage.save_episode(project_name, episode_name, ep_data["data"], ep_meta)
            
            checkpoint_path = storage.episode_dir(project_name, episode_name) / "checkpoint.json"
            if checkpoint_path.exists():
                try:
                    checkpoint_path.unlink()
                except Exception:
                    pass

        # 3. Check if target subtitle already exists on disk
        if not force_new:
            update_job(job_id, log="Checking if target subtitle already exists on disk...")
            src_lang = global_config.get("arr_source_language", "en")
            scanner = SubtitleScannerService(source_lang_code=src_lang, target_lang_code=tgt_lang)
            scan_res = scanner.scan_media_file(media_path)

            if scan_res.has_target_sub and not is_stale:
                logger.info(f"Target subtitle already exists for {episode_name}. Skipping.")
                update_job(job_id, log=f"Target subtitle already exists at: {scan_res.target_sub_path}. Skipping translation.")
                update_job(job_id, status="completed", progress=100.0, message="Translation completed (skipped: target exists).")

                queue.update_status(
                    item_id,
                    status="completed",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    logs=jobs[job_id].logs
                )

                _record_target(ep_meta, is_primary_lang, tgt_lang, scan_res.target_sub_path, current_src_fp)
                storage.save_episode_metadata(project_name, episode_name, ep_meta)
                return

        # 4. Resolve the model
        from utils.model_resolver import resolve_model, get_daily_limit_policy
        daily_limit_policy = get_daily_limit_policy(project_meta)
        model_name = resolve_model(
            "translation",
            project_meta,
            model_override=options.get("model"),
            daily_limit_policy=daily_limit_policy,
        )

        limiter = per_model_rate_limiter.get_limiter(model_name)

        is_exhausted = (
            (hasattr(limiter, "is_daily_limit_exhausted") and limiter.is_daily_limit_exhausted()) or
            limiter.maybe_reset_daily()
        )

        if is_exhausted:
            if daily_limit_policy == "auto_fallback":
                limiter.trigger_daily_limit()
                fallback_model = resolve_model(
                    "translation",
                    project_meta,
                    check_exhaustion=True,
                    daily_limit_policy="auto_fallback",
                )
                fb_limiter = per_model_rate_limiter.get_limiter(fallback_model)
                fb_is_exhausted = (
                    (hasattr(fb_limiter, "is_daily_limit_exhausted") and fb_limiter.is_daily_limit_exhausted()) or
                    fb_limiter.maybe_reset_daily()
                )
                if fallback_model and fallback_model != model_name and not fb_is_exhausted:
                    logger.info(f"Primary model {model_name} is daily exhausted at start. Auto-falling back to {fallback_model}.")
                    update_job(job_id, log=f"Primary model {model_name} daily quota exhausted. Auto-falling back to fallback model {fallback_model}.")
                    model_name = fallback_model
                    limiter = fb_limiter
                    is_exhausted = False

        if is_exhausted:
            policy_suffix = " (policy: pause_and_wait)" if daily_limit_policy == "pause_and_wait" else ""
            logger.info(f"Daily rate limit exhausted for model {model_name}. Pausing task{policy_suffix}.")
            update_job(job_id, log=f"Daily rate limit exhausted for model {model_name}. Pausing task{policy_suffix}.")
            update_job(job_id, status="paused", message=f"Paused: Daily API limit reached for model {model_name}.")
            
            queue.update_status(
                item_id,
                status="paused_daily_limit",
                error=f"Daily limit {limiter.daily_limit} exhausted.",
                logs=jobs[job_id].logs,
                attempts=max(0, attempts - 1),
            )
            
            await notifier.notify_daily_limit_reached(model_name, limiter.daily_limit)
            return

        from services.project_bootstrap import ensure_project_context
        glossary, context_guide = await ensure_project_context(
            project_name,
            episode_name,
            target_lang_name,
            want_context=bool(options.get("ensure_context")),
            log=lambda msg: update_job(job_id, log=msg),
        )

        concurrent_limit = global_config.get("concurrent_scenes", 3)
        semaphore = asyncio.Semaphore(concurrent_limit)

        shared_agent = create_translation_pipeline(
            project_name=project_name,
            target_language=target_lang_name,
            glossary=glossary,
            context_guide=context_guide,
            cartographer_model=model_name,
            translator_model=model_name,
            skip_glossary_step=not options.get("live_cartographer", False),
            temperature=options.get("temperature") or global_config.get("temperature"),
            top_k=options.get("top_k") or global_config.get("top_k"),
                        top_p=options.get("top_p") or global_config.get("top_p"),
        )

        active_model_var.set(model_name)

        success = await translate_episode_atomic(
            job_id=job_id,
            project_name=project_name,
            episode_name=episode_name,
            target_lang=target_lang_name,
            shared_agent=shared_agent,
            rate_limiter=limiter,
            semaphore=semaphore,
            global_config=global_config,
            total_episodes=1,
            episode_idx=0,
            start_progress=0.0,
            model_name=model_name,
            lang_suffix="" if is_primary_lang else tgt_lang,
            disable_tm=False,
        )

        if success:
            if _is_task_cancelled():
                queue.update_status(item_id, status="cancelled", logs=jobs[job_id].logs if job_id in jobs else None)
                if job_id in jobs:
                    update_job(job_id, status="cancelled", message="Cancelled by user")
                return

            translated_data = storage.load_episode(project_name, episode_name)
            if translated_data and translated_data.get("data"):
                from services.translation.export_formatter import auto_export_translated_subtitle
                ep_meta = storage.load_episode_metadata(project_name, episode_name) or ep_meta
                exported_path = await asyncio.to_thread(auto_export_translated_subtitle, project_name, episode_name, translated_data["data"], ep_meta)
                target_srt_path = Path(exported_path) if exported_path else (Path(ep_meta.get("arr_target_path")) if ep_meta.get("arr_target_path") else None)
                write_success = bool(target_srt_path and target_srt_path.exists())
                write_error_msg = ep_meta.get("translation_warning", "")

                if write_success:
                    logger.info(f"Successfully exported target subtitle: {target_srt_path}")
                    _record_target(ep_meta, is_primary_lang, tgt_lang, target_srt_path, current_src_fp)
                    ep_meta.pop("translation_warning", None)
                else:
                    if not target_srt_path:
                        if media_file:
                            orig_filename = ep_meta.get("original_filename") or f"{episode_name}.srt"
                            ext = Path(orig_filename).suffix
                            if not ext:
                                _, ext_sub = storage.load_original_subtitle(project_name, episode_name)
                                ext = f".{ext_sub}" if ext_sub else ".srt"
                            from utils.language_codes import output_filename_from_original
                            target_filename = output_filename_from_original(f"{media_file.stem}{ext}", tgt_lang)
                            target_srt_path = media_file.parent / target_filename
                        else:
                            target_srt_path = storage.episode_dir(project_name, episode_name) / f"{episode_name}.{tgt_lang}.srt"
                    logger.error(f"Failed to write target subtitle to '{target_srt_path}': {write_error_msg}")
                    update_job(job_id, log=f"Warning: Failed to write output subtitle to unreachable path: {target_srt_path}. Queuing for export retry.")
                    try:
                        from services.queue_service import enqueue_export_retry
                        enqueue_export_retry(
                            project_name=project_name,
                            episode_name=episode_name,
                            target_path=str(target_srt_path),
                            lang=tgt_lang,
                            error=write_error_msg or "Export failed",
                            queue=queue,
                        )
                    except Exception as q_err:
                        logger.error(f"Failed to enqueue export retry: {q_err}")
                    ep_meta["translation_warning"] = f"Export scheduled for retry: {target_srt_path} ({write_error_msg})"

                storage.save_episode_metadata(project_name, episode_name, ep_meta)

                # Enhance project glossary with newly discovered terms from this episode
                if options.get("enhance_glossary", True):
                    try:
                        from services.term_harvester import harvest_terms_for_episode
                        update_job(job_id, log=f"Harvesting newly discovered terms from {episode_name} to enhance project glossary...")
                        new_terms = await harvest_terms_for_episode(project_name, episode_name, model_name=model_name)
                        if new_terms:
                            with _get_project_glossary_lock(project_name):
                                cur_proj_meta = storage.load_project_metadata(project_name) or project_meta
                                existing_terms = cur_proj_meta.get("glossary", {}).get("terms", [])
                                existing_terms_map = {
                                    (t.get("term") or t.get("source") or "").strip().lower(): t
                                    for t in existing_terms
                                    if isinstance(t, dict) and (t.get("term") or t.get("source"))
                                }
                                added_count = 0
                                for term_obj in new_terms:
                                    k = (term_obj.get("term") or term_obj.get("source") or "").strip().lower()
                                    if k and k not in existing_terms_map:
                                        existing_terms.append(term_obj)
                                        existing_terms_map[k] = term_obj
                                        added_count += 1
                                if added_count > 0:
                                    cur_proj_meta.setdefault("glossary", {})["terms"] = existing_terms
                                    storage.save_project_metadata(project_name, cur_proj_meta)
                                    storage.invalidate_project_cache(project_name)
                                    try:
                                        from utils.cache_manager import invalidate_cache as _inv_trans_cache
                                        _inv_trans_cache(cur_proj_meta)
                                    except Exception:
                                        pass
                                    try:
                                        from adk_config import adk_session_manager
                                        await adk_session_manager.update_glossary(project_name, cur_proj_meta["glossary"])
                                    except Exception:
                                        pass
                                    update_job(job_id, log=f"Glossary enhanced: added {added_count} new term(s) from {episode_name}.")
                                    logger.info(f"Enhanced glossary for {project_name} with {added_count} terms from {episode_name}.")
                    except Exception as harvest_err:
                        logger.warning(f"Glossary auto-enhancement after {episode_name} failed (non-fatal): {harvest_err}")

                review_count = sum(1 for l in translated_data["data"] if l.get("needs_review"))

                queue.update_status(
                    item_id,
                    status="completed",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    logs=jobs[job_id].logs if job_id in jobs else None,
                    review_count=review_count,
                )

                completion_msg = "Translation completed."
                if review_count:
                    completion_msg = f"Translation completed — {review_count} line(s) need review."
                update_job(job_id, status="completed", progress=100.0, message=completion_msg)
                
                await notifier.notify_translation_completed(project_name, episode_name, target_srt_path.name)
            else:
                raise ValueError("Translated data not found in storage after successful run.")
        else:
            job = jobs.get(job_id)
            reason = ((job.result or {}).get("failure_reason") if job else None)
            raise ValueError(f"Translation failed: {reason}" if reason else "Translation failed to complete successfully.")

    except asyncio.CancelledError:
        logger.info(f"Task {item_id} aborted via CancelledError.")
        if job_id in jobs:
            jobs[job_id].cancelled = True
        queue.update_status(item_id, status="cancelled", logs=jobs[job_id].logs if job_id in jobs else None)
        if job_id in jobs:
            update_job(job_id, status="cancelled", message="Cancelled by user")
        return

    except Exception as e:
        e_name = type(e).__name__
        is_daily = (
            e_name == "DailyLimitExhausted" or
            "Daily limit" in str(e)
        )

        if is_daily:
            logger.warning(f"Daily rate limit exhausted for task {item_id} ({project_name} - {episode_name}): {e}")
            job_logs = jobs[job_id].logs if 'job_id' in locals() and job_id in jobs else None
            
            queue.update_status(
                item_id, 
                status="paused_daily_limit", 
                error=str(e), 
                logs=job_logs,
                attempts=max(0, attempts - 1)
            )
            if 'job_id' in locals() and job_id in jobs:
                update_job(job_id, status="paused", message=f"Paused daily limit: {str(e)}")
            try:
                ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}
                ep_meta["translation_failed"] = True
                ep_meta["translation_error"] = f"Daily API limit reached: {str(e)}"
                ep_meta["last_translation_attempt"] = datetime.now().isoformat()
                storage.save_episode_metadata(project_name, episode_name, ep_meta)
            except Exception as meta_err:
                logger.error(f"Failed to update metadata: {meta_err}")
            await notifier.notify_daily_limit_reached(model_name if 'model_name' in locals() else 'default', getattr(limiter, 'daily_limit', 1500) if 'limiter' in locals() else 1500, episode_name, project_name)
            return

        tb = traceback.format_exc()
        permanent = isinstance(e, UnrecoverableTranslationError)
        final_attempt = permanent or attempts >= 5
        if permanent:
            logger.warning(f"Not translating {project_name}/{episode_name}: {e}")
        elif final_attempt:
            logger.error(f"Error processing item {item_id}: {e}\n{tb}")
        else:
            # It is retried automatically; a full traceback on every attempt buried
            # the failures that actually needed attention.
            logger.warning(
                f"Translation of {project_name}/{episode_name} failed on attempt {attempts}; will retry: {e}"
            )
            logger.debug(tb)
        error_text = str(e) if permanent else f"{str(e)}\n\n{tb}"

        if _is_task_cancelled():
            queue.update_status(item_id, status="cancelled", logs=jobs[job_id].logs if job_id in jobs else None)
            if job_id in jobs:
                update_job(job_id, status="cancelled", message="Cancelled by user")
            return

        if 'job_id' in locals() and job_id in jobs:
            update_job(job_id, log=f"Error occurred: {error_text}")

        job_logs = jobs[job_id].logs if 'job_id' in locals() and job_id in jobs else None

        delays = [300, 1800, 7200, 43200]
        if final_attempt:
            queue.update_status(item_id, status="failed", error=error_text, logs=job_logs)
            if 'job_id' in locals() and job_id in jobs:
                update_job(job_id, status="failed", message=f"Failed permanently: {str(e)}")
            try:
                ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}
                ep_meta["translation_failed"] = True
                ep_meta["translation_error"] = str(e)
                ep_meta["last_translation_attempt"] = datetime.now().isoformat()
                storage.save_episode_metadata(project_name, episode_name, ep_meta)
            except Exception as meta_err:
                logger.error(f"Failed to update metadata: {meta_err}")
            await notifier.notify_translation_failed(project_name, episode_name, str(e), attempts)
        else:
            delay = delays[max(0, min(attempts - 1, len(delays) - 1))]
            next_retry = datetime.now(timezone.utc).timestamp() + delay
            next_retry_str = datetime.fromtimestamp(next_retry, timezone.utc).isoformat()
            
            queue.update_status(item_id, status="pending", error=error_text, next_retry_at=next_retry_str, logs=job_logs)
            if 'job_id' in locals() and job_id in jobs:
                update_job(job_id, status="pending", message=f"Failed, retry at {next_retry_str}: {str(e)}")
            try:
                ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}
                ep_meta["translation_failed"] = True
                ep_meta["translation_error"] = str(e)
                ep_meta["last_translation_attempt"] = datetime.now().isoformat()
                storage.save_episode_metadata(project_name, episode_name, ep_meta)
            except Exception as meta_err:
                logger.error(f"Failed to update metadata: {meta_err}")
