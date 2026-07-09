import asyncio
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from utils.translation_queue import TranslationQueue
from utils.rate_limiter import per_model_rate_limiter, active_model_var, DailyLimitExhausted
from utils import storage
from utils.language_codes import to_code as get_lang_code
from integrations.subtitle_scanner import SubtitleScannerService
from services.discord_notifier import DiscordNotifier

logger = logging.getLogger(__name__)


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


class BackgroundTranslationWorker:
    def __init__(self):
        self.queue = TranslationQueue()
        self.running_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self._new_item_event = asyncio.Event()

    def start(self):
        if not self.running_task or self.running_task.done():
            self._shutdown_event.clear()
            self._new_item_event.clear()
            self.running_task = asyncio.create_task(self._run_loop())
            logger.info("BackgroundTranslationWorker started.")

    def stop(self):
        if self.running_task:
            self._shutdown_event.set()
            self._new_item_event.set()
            self.running_task.cancel()
            logger.info("BackgroundTranslationWorker stopped.")

    def trigger(self):
        """Wake up the worker loop immediately to process a new task."""
        self._new_item_event.set()

    @staticmethod
    def _within_window(now, start_str: str, end_str: str) -> bool:
        """True if local time ``now`` falls within [start, end) (handles midnight wrap)."""
        try:
            sh, sm = (int(x) for x in start_str.split(":"))
            eh, em = (int(x) for x in end_str.split(":"))
        except Exception:
            return True  # malformed config -> impose no restriction
        cur = now.hour * 60 + now.minute
        start, end = sh * 60 + sm, eh * 60 + em
        if start == end:
            return True
        if start < end:
            return start <= cur < end
        return cur >= start or cur < end  # window wraps past midnight

    def _schedule_ceiling(self, config) -> Optional[int]:
        """Off-peak scheduling: max claimable priority right now, or None for unrestricted."""
        if not config.get("worker_schedule_enabled", False):
            return None
        now = datetime.now()
        if self._within_window(now, config.get("worker_window_start", "01:00"),
                                config.get("worker_window_end", "08:00")):
            return None
        return config.get("worker_schedule_priority_cutoff", 1)

    async def _run_loop(self):
        active_tasks = set()
        while not self._shutdown_event.is_set():
            try:
                global_config = storage.load_global_config()

                # Adaptive concurrency (Plan 10): effective parallel-episode count.
                from utils.concurrency_controller import concurrency_manager
                from utils.model_resolver import resolve_model
                concurrency_manager.set_defaults(
                    enabled=global_config.get("adaptive_concurrency_enabled", False),
                    base=global_config.get("concurrent_episodes", 2),
                    c_max=global_config.get("concurrency_max", 6),
                )
                model_name = resolve_model("translation")
                concurrent_episodes = concurrency_manager.current(model_name)

                # 1. Recover daily-exhausted models, then unpause their items.
                # (a) Actively probe the API for any model whose recheck window is
                #     due — lifts the latch early if the quota came back before the
                #     local midnight boundary.
                try:
                    from utils.daily_limit_probe import recheck_daily_limits
                    # Bound it so a slow/hung probe can't freeze queue claiming.
                    await asyncio.wait_for(recheck_daily_limits(force=False), timeout=30.0)
                except asyncio.TimeoutError:
                    logger.warning("Auto daily-limit recheck timed out; will retry next loop.")
                except Exception as probe_err:
                    logger.warning(f"Auto daily-limit recheck failed: {probe_err}")
                # (b) maybe_reset_daily() also lifts the latch at the local reset
                #     boundary even when no acquire() runs (everything paused).
                def _is_exhausted(m):
                    return per_model_rate_limiter.get_limiter(m).maybe_reset_daily()
                self.queue.unpause_daily_limit_items(_is_exhausted)


                # Check global pause switch
                if global_config.get("queue_worker_paused", False):
                    try:
                        await asyncio.wait_for(self._new_item_event.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        pass
                    self._new_item_event.clear()
                    # Clean up completed tasks
                    completed = {t for t in active_tasks if t.done()}
                    active_tasks.difference_update(completed)
                    continue

                # Off-peak scheduling (Plan 09): outside the window, only claim items at
                # or above the priority cutoff (webhook/manual); defer sync/backlog.
                max_priority = self._schedule_ceiling(global_config)

                # If we are at max concurrency, wait for some task to finish
                if len(active_tasks) >= concurrent_episodes:
                    done, _ = await asyncio.wait(active_tasks, return_when=asyncio.FIRST_COMPLETED)
                    active_tasks.difference_update(done)
                    continue

                # 2. Get next pending item and claim it atomically
                item = self.queue.claim_next(max_priority=max_priority)
                if not item:
                    # No items to process. Wait for a trigger event or a task completion or timeout
                    wait_coros = [self._new_item_event.wait()]
                    if active_tasks:
                        wait_coros.append(asyncio.wait(active_tasks, return_when=asyncio.FIRST_COMPLETED))
                    
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*wait_coros, return_exceptions=True) if len(wait_coros) > 1 else wait_coros[0],
                            timeout=15.0
                        )
                    except asyncio.TimeoutError:
                        pass
                        
                    self._new_item_event.clear()
                    completed = {t for t in active_tasks if t.done()}
                    active_tasks.difference_update(completed)
                    continue

                logger.info(f"Background worker processing task {item['id']}: {item['project_name']} - {item['episode_name']} (active tasks: {len(active_tasks) + 1})")
                
                # Start processing the item in parallel
                task = asyncio.create_task(self._process_item(item, global_config))
                active_tasks.add(task)
                
                def clean_task(t):
                    active_tasks.discard(t)
                    self.trigger()
                task.add_done_callback(clean_task)

            except asyncio.CancelledError:
                # Cancel all running tasks on shutdown
                for t in active_tasks:
                    t.cancel()
                if active_tasks:
                    await asyncio.gather(*active_tasks, return_exceptions=True)
                break
            except Exception as e:
                logger.error(f"Error in background worker loop: {e}", exc_info=True)
                await asyncio.sleep(30)

    async def _process_item(self, item: dict, global_config: dict):
        item_id = item["id"]
        project_name = item["project_name"]
        episode_name = item["episode_name"]
        attempts = item["attempts"]
        job_id = f"bg_{item_id}"

        # Import modular components early for immediate tracking
        from utils.jobs_manager import JobStatus, jobs, update_job
        from services.translation_service import _translate_episode_atomic, create_translation_pipeline

        # Setup job registry to allow tracking immediately
        jobs[job_id] = JobStatus(
            id=job_id, status="running", progress=0.0,
            message=f"Background translation of {episode_name}...", logs=[],
            project_name=project_name, episode_name=episode_name
        )

        update_job(job_id, log=f"Starting background translation task for {episode_name}...")

        # Telemetry context (v2 D8): every LLM call inside this item records under it.
        try:
            from utils.telemetry import usage_ctx
            usage_ctx.set({"project": project_name, "episode": episode_name, "lane": "live"})
        except Exception:
            pass

        # Initialize notifier
        webhook_url = global_config.get("discord_webhook_url")
        notifier = DiscordNotifier(webhook_url)

        try:
            # 0. Load options, media path, and metadata
            import json
            options = {}
            if item.get("options"):
                try:
                    options = json.loads(item["options"])
                except Exception:
                    pass

            translation_type = options.get("translation_type", "saved")
            force_new = (translation_type == "original" or options.get("force", False))

            ep_meta = storage.load_episode_metadata(project_name, episode_name)
            if not ep_meta:
                raise ValueError("Episode metadata not found in storage.")

            media_path = ep_meta.get("arr_media_path")
            if not media_path:
                raise ValueError("Episode metadata is missing media file path (arr_media_path).")

            project_meta = storage.load_project_metadata(project_name) or {}

            # Target language for THIS item (Plan 15). item.lang='' => project primary.
            req_lang_code = item.get("lang") or ""
            primary_code = get_lang_code(project_meta.get("target_language", "Greek"))
            is_primary_lang = (req_lang_code == "" or req_lang_code == primary_code)
            target_lang_name = options.get("target_language_name") or project_meta.get("target_language", "Greek")
            tgt_lang = primary_code if is_primary_lang else req_lang_code
            lang_meta_key = None if is_primary_lang else tgt_lang  # for per-language metadata helpers

            # Detect a stale prior translation: the source subtitle was re-imported
            # (new fingerprint) after the existing target was produced. In that case
            # we must re-translate even though an (old) target file is still on disk.
            current_src_fp = ep_meta.get("arr_sub_fingerprint")
            is_stale = storage.episode_translation_is_stale(ep_meta, lang_meta_key)
            if is_stale:
                update_job(job_id, log=f"Source subtitle changed since last translation ({tgt_lang}) — re-translating instead of reusing the stale target.")

            # 1. Handle "saved" translation reuse (re-export)
            if translation_type == "saved" and not is_stale:
                update_job(job_id, log="Checking for saved translation in storage...")
                ep_data = storage.load_episode(project_name, episode_name)
                if ep_data and ep_data.get("data") and any(line.get("translations", {}).get(tgt_lang) or (is_primary_lang and line.get("translated")) for line in ep_data["data"]):
                    update_job(job_id, log="Found saved translation. Re-exporting to disk...")
                    from utils.source_clean import reconstruct_cleaned_srt
                    from utils.language_codes import output_filename_from_original
                    translated_srt_content = reconstruct_cleaned_srt(project_name, episode_name, ep_data["data"], tgt_lang)
                    
                    media_file = Path(media_path)
                    orig_filename = ep_meta.get("original_filename") or ""
                    ext = Path(orig_filename).suffix
                    if not ext:
                        _, ext_sub = storage.load_original_subtitle(project_name, episode_name)
                        ext = f".{ext_sub}" if ext_sub else ".srt"
                    target_filename = output_filename_from_original(f"{media_file.stem}{ext}", tgt_lang)
                    target_srt_path = media_file.parent / target_filename
                    write_success = True
                    try:
                        target_srt_path.write_text(translated_srt_content, encoding="utf-8-sig")
                    except OSError as write_err:
                        logger.error(f"Failed to re-export saved translation to '{target_srt_path}': {write_err}")
                        update_job(job_id, log=f"Warning: Failed to re-export subtitle to unreachable path: {target_srt_path}")
                        write_success = False
                    
                    logger.info(f"Re-exported saved translation for {episode_name} to {target_srt_path} (success={write_success})")
                    completion_msg = (
                        "Translation completed (re-exported saved translation)."
                        if write_success
                        else "Translation completed (re-export failed: destination unreachable)."
                    )
                    update_job(job_id, status="completed", progress=100.0, message=completion_msg)
                    
                    self.queue.update_status(
                        item_id, 
                        status="completed", 
                        completed_at=datetime.now(timezone.utc).isoformat(),
                        logs=jobs[job_id].logs
                    )
                    
                    if write_success:
                        _record_target(ep_meta, is_primary_lang, tgt_lang, target_srt_path, current_src_fp)
                        ep_meta.pop("translation_warning", None)
                    else:
                        ep_meta["translation_warning"] = f"Failed to write target subtitle to unreachable path: {target_srt_path}"
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
                
                # Delete checkpoint
                checkpoint_path = storage.PROJECTS_DIR / project_name / "episodes" / episode_name / "checkpoint.json"
                if checkpoint_path.exists():
                    try:
                        checkpoint_path.unlink()
                    except Exception:
                        pass

            # 3. Check if target subtitle already exists on disk (only if not forcing a new translation)
            if not force_new:
                update_job(job_id, log="Checking if target subtitle already exists on disk...")
                src_lang = global_config.get("arr_source_language", "en")
                scanner = SubtitleScannerService(source_lang_code=src_lang, target_lang_code=tgt_lang)
                scan_res = scanner.scan_media_file(media_path)

                if scan_res.has_target_sub and not is_stale:
                    logger.info(f"Target subtitle already exists for {episode_name}. Skipping.")
                    update_job(job_id, log=f"Target subtitle already exists at: {scan_res.target_sub_path}. Skipping translation.")
                    update_job(job_id, status="completed", progress=100.0, message="Translation completed (skipped: target exists).")

                    self.queue.update_status(
                        item_id,
                        status="completed",
                        completed_at=datetime.now(timezone.utc).isoformat(),
                        logs=jobs[job_id].logs
                    )

                    _record_target(ep_meta, is_primary_lang, tgt_lang, scan_res.target_sub_path, current_src_fp)
                    storage.save_episode_metadata(project_name, episode_name, ep_meta)
                    return

            # 4. Resolve the model. Webhook/sync items carry no options, so without
            # this they would silently fall back to the cloud default even for projects
            # configured to use a local (or hybrid) provider. resolve_model() honors the
            # ai_provider switch and any per-project/per-role override (v2 D12).
            from utils.model_resolver import resolve_model
            from adk_agents.llm_factory import is_local_model
            from utils.rate_limiter import no_op_rate_limiter
            model_name = options.get("model") or resolve_model("translation", project_meta) \
                or global_config.get("default_translation_model", "gemini-flash-lite-latest")

            # Local models are unmetered: bypass the Gemini rate limiter and the
            # daily-quota latch entirely (a concrete limiter would throttle them at the
            # default 15 RPM and pollute rate_limiter_state.json).
            is_local = is_local_model(model_name)
            limiter = no_op_rate_limiter if is_local else per_model_rate_limiter.get_limiter(model_name)

            # Manual/webhook priority tasks bypass proactive daily limit checks
            if not is_local and item.get("priority", 3) <= 1:
                if hasattr(limiter, "clear_daily_exhausted_latch"):
                    limiter.clear_daily_exhausted_latch()

            # Check daily limit before starting (cloud only).
            if not is_local and limiter.maybe_reset_daily():
                logger.info(f"Daily rate limit exhausted for model {model_name}. Pausing task.")
                update_job(job_id, log=f"Daily rate limit exhausted for model {model_name}. Pausing task.")
                update_job(job_id, status="paused", message=f"Paused: Daily API limit reached for model {model_name}.")
                
                self.queue.update_status(
                    item_id,
                    status="paused_daily_limit",
                    error=f"Daily limit {limiter.daily_limit} exhausted.",
                    logs=jobs[job_id].logs,
                    attempts=max(0, attempts - 1),  # a daily-limit pause is not a failed attempt
                )
                
                await notifier.notify("daily_limit_reached", {
                    "message": f"Omnisub paused background worker because daily limit has been reached for model: {model_name}.",
                    "model": model_name,
                    "daily_limit": limiter.daily_limit
                })
                return

            # Build pipeline agent
            glossary = project_meta.get("glossary", {"terms": []})
            context_guide = project_meta.get("context_guide", "")
            
            # Seed glossary if empty
            if not glossary.get("terms") or len(glossary.get("terms", [])) == 0:
                logger.info(f"Glossary empty for project {project_name}. Attempting to auto-seed from episode text.")
                first_ep_text = ""
                # Load first 200 lines
                ep_data = storage.load_episode(project_name, episode_name)
                if ep_data and ep_data.get("data"):
                    first_ep_text = "\n".join([line.get("original", "") for line in ep_data["data"][:200]])
                
                from adk_agents.operations import generate_glossary_adk
                glossary, _ = await generate_glossary_adk(
                    first_ep_text, project_name, target_lang_name,
                    model_name=global_config.get("default_glossary_model", "gemini-flash-lite-latest")
                )
                project_meta["glossary"] = glossary
                storage.save_project_metadata(project_name, project_meta)
                logger.info(f"Auto-seeded glossary with {len(glossary.get('terms', []))} terms.")

            concurrent_limit = global_config.get("concurrent_scenes", 3)
            semaphore = asyncio.Semaphore(concurrent_limit)

            shared_agent = create_translation_pipeline(
                project_name=project_name,
                target_language=target_lang_name,
                glossary=glossary,
                context_guide=context_guide,
                cartographer_model=model_name,
                translator_model=model_name,
                skip_glossary_step=not options.get("enhance_glossary", False),
                temperature=options.get("temperature") or global_config.get("temperature"),
                top_k=options.get("top_k") or global_config.get("top_k"),
                top_p=options.get("top_p") or global_config.get("top_p"),
            )

            # Set model context
            active_model_var.set(model_name)

            success = await _translate_episode_atomic(
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

            # 4. Handle success/failure
            if success:
                # Retrieve the translated lines, write output srt file to disk
                translated_data = storage.load_episode(project_name, episode_name)
                if translated_data and translated_data.get("data"):
                    # Reconstruct translated SRT content
                    from utils.source_clean import reconstruct_cleaned_srt
                    from utils.language_codes import output_filename_from_original
                    translated_srt_content = reconstruct_cleaned_srt(project_name, episode_name, translated_data["data"], tgt_lang)
                    
                    # Target path
                    media_file = Path(media_path)
                    orig_filename = ep_meta.get("original_filename") or ""
                    ext = Path(orig_filename).suffix
                    if not ext:
                        _, ext_sub = storage.load_original_subtitle(project_name, episode_name)
                        ext = f".{ext_sub}" if ext_sub else ".srt"
                    target_filename = output_filename_from_original(f"{media_file.stem}{ext}", tgt_lang)
                    target_srt_path = media_file.parent / target_filename
                    write_success = True
                    try:
                        if not target_srt_path.parent.exists():
                            raise FileNotFoundError(
                                f"Output directory does not exist or is unreachable: '{target_srt_path.parent}'."
                            )
                        target_srt_path.write_text(translated_srt_content, encoding="utf-8-sig")
                        logger.info(f"Successfully wrote target subtitle: {target_srt_path}")
                    except (OSError, FileNotFoundError) as write_err:
                        logger.error(f"Failed to write target subtitle to '{target_srt_path}': {write_err}")
                        update_job(job_id, log=f"Warning: Failed to write output subtitle to unreachable path: {target_srt_path}. The translation is saved in internal storage.")
                        write_success = False

                    # Reload metadata so we preserve the flags the translation step
                    # just wrote (translated=True, etc.) instead of clobbering them.
                    ep_meta = storage.load_episode_metadata(project_name, episode_name) or ep_meta
                    if write_success:
                        _record_target(ep_meta, is_primary_lang, tgt_lang, target_srt_path, current_src_fp)
                        ep_meta.pop("translation_warning", None)
                    else:
                        ep_meta["translation_warning"] = f"Failed to write target subtitle to unreachable path: {target_srt_path}"
                    storage.save_episode_metadata(project_name, episode_name, ep_meta)

                    # Surface gap/quality flags so the queue UI can distinguish a clean
                    # completion from "completed but N lines need review" (v2 D6/UX).
                    review_count = sum(1 for l in translated_data["data"] if l.get("needs_review"))

                    self.queue.update_status(
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
                    
                    # Notify success
                    await notifier.notify("translation_completed", {
                        "message": f"Successfully translated subtitle for episode: {episode_name} of series {project_name}.",
                        "series": project_name,
                        "episode": episode_name,
                        "output_file": target_srt_path.name
                    })
                else:
                    raise ValueError("Translated data not found in storage after successful run.")
            else:
                # Partial failure/errors handled by raising exception or checking success flag
                raise ValueError("Translation failed to complete successfully.")

        except asyncio.CancelledError:
            # Either the user cancelled this job, or the worker is shutting down.
            if job_id in jobs and jobs[job_id].cancelled:
                self.queue.update_status(item_id, status="cancelled", logs=jobs[job_id].logs)
                update_job(job_id, status="cancelled", message="Cancelled by user")
                return
            # Shutdown: leave the item claimable again on next start.
            self.queue.update_status(item_id, status="pending", started_at=None)
            raise

        except Exception as e:
            e_name = type(e).__name__
            is_daily = (
                e_name == "DailyLimitExhausted" or
                "Daily limit" in str(e)
            )

            if is_daily:
                logger.warning(f"Daily rate limit exhausted for task {item_id} ({project_name} - {episode_name}): {e}")
                job_logs = jobs[job_id].logs if 'job_id' in locals() and job_id in jobs else None
                
                self.queue.update_status(
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
                await notifier.notify("daily_limit_reached", {
                    "message": f"Daily API quota exceeded while translating: {episode_name}.",
                    "series": project_name,
                    "episode": episode_name
                })
                return

            tb = traceback.format_exc()
            logger.error(f"Error processing item {item_id}: {e}\n{tb}")

            # Honor a user cancellation instead of treating it as a failure to retry.
            if job_id in jobs and jobs[job_id].cancelled:
                self.queue.update_status(item_id, status="cancelled", logs=jobs[job_id].logs)
                update_job(job_id, status="cancelled", message="Cancelled by user")
                return

            if 'job_id' in locals() and job_id in jobs:
                update_job(job_id, log=f"Error occurred: {str(e)}\n{tb}")

            job_logs = jobs[job_id].logs if 'job_id' in locals() and job_id in jobs else None

            # Backoff calculation: 5m, 30m, 2h, 12h
            delays = [300, 1800, 7200, 43200]
            if attempts >= 5:
                self.queue.update_status(item_id, status="failed", error=f"{str(e)}\n\n{tb}", logs=job_logs)
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
                await notifier.notify("translation_failed", {
                    "message": f"Subtitle translation failed permanently for {episode_name} after {attempts} attempts.",
                    "series": project_name,
                    "episode": episode_name,
                    "error": str(e)
                })
            else:
                delay = delays[min(attempts - 1, len(delays) - 1)]
                next_retry = datetime.now(timezone.utc).timestamp() + delay
                next_retry_str = datetime.fromtimestamp(next_retry, timezone.utc).isoformat()
                
                self.queue.update_status(item_id, status="pending", error=f"{str(e)}\n\n{tb}", next_retry_at=next_retry_str, logs=job_logs)
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

# Singleton worker instance
worker = BackgroundTranslationWorker()
