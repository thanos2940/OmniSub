import os
import re
import json
import asyncio
import logging
from datetime import datetime
from uuid import uuid4
from typing import List, Dict, Optional, Any
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from utils import storage
from utils.srt_parser import extract_text_only, build_scene_ast
from utils.rate_limiter import translation_rate_limiter, active_model_var
from utils.jobs_manager import create_job, update_job, jobs, JobStatus

import threading

if not hasattr(JobStatus, "target_episodes"):
    JobStatus.target_episodes = property(
        lambda self: self.__dict__.get("target_episodes"),
        lambda self, val: self.__dict__.__setitem__("target_episodes", val),
    )
_batch_target_episodes: Dict[str, List[str]] = {}
_pipeline_thread_lock = threading.Lock()
_pipeline_async_locks: Dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}

def _get_pipeline_async_lock() -> asyncio.Lock:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.Lock()
    lock = _pipeline_async_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _pipeline_async_locks[loop] = lock
    return lock

from services.translation_service import (
    _process_batch_translation, 
    _select_golden_ratio_files, 
    _merge_glossaries,
    _gather_project_text
)

from adk_agents import (
    create_cartographer_agent,
    create_translation_pipeline,
    generate_glossary_adk,
    research_project_adk,
    enhance_context_guide_adk
)
from adk_config import adk_runner_factory, adk_session_manager, get_ephemeral_session_service
from google.adk.runners import Runner, types as adk_types
from utils.cache_manager import invalidate_cache as _invalidate_translation_cache
from utils.llm_utils import parse_glossary_from_text

from routers.schemas import (
    ScanRequest,
    TranslateRequest,
    BatchTranslateRequest,
    PipelineRequest,
    SimplePipelineRequest,
    ConfirmContextRequest,
    ConfirmGlossaryRequest,
    AutoPipelineRequest
)
from routers.settings import validate_api_key

router = APIRouter()

# Global dict to track resume events
_pipeline_resume_events: Dict[str, asyncio.Event] = {}


# --- Endpoints ---

from utils.model_resolver import resolve_model

@router.post("/projects/{project_name}/episodes/{episode_name}/scan")
async def scan_episode(
    project_name: str,
    episode_name: str,
    background_tasks: BackgroundTasks,
    request: ScanRequest
):
    validate_api_key()
    metadata = storage.load_project_metadata(project_name) or {}
    model = request.model or resolve_model("scan", metadata)
    job_id = create_job("scan_episode", project_name=project_name, episode_name=episode_name)
    background_tasks.add_task(_process_scan_episode, job_id, project_name, episode_name, model)
    return {"job_id": job_id}


async def _monitor_stage3_queue_progress(
    job_id: str,
    item_ids: List[str],
    base_progress: float = 50.0,
    max_progress: float = 100.0,
    stage_name: str = "translate",
    project_name: Optional[str] = None,
    poll_interval: float = 1.5,
) -> None:
    """
    Monitors child queue items during Stage 3 of multi-stage wizards and batch translations.
    Maintains parent job in 'running' (or 'paused'), aggregates live scene-level progress,
    cues translated, and dynamic ETA, and cascades cancellations.
    """
    import time
    import sqlite3
    from utils.translation_queue import TranslationQueue

    queue = TranslationQueue()
    total_items = len(item_ids)

    if total_items == 0:
        update_job(
            job_id,
            status="completed",
            progress=max_progress,
            pipeline_stage=stage_name,
            sub_jobs=[],
            message="Translation completed (0 items enqueued).",
            log="Stage 3: 0 episodes enqueued.",
        )
        return

    start_time = time.time()
    cues_per_item: Dict[str, int] = {}

    if not project_name and job_id in jobs:
        project_name = jobs[job_id].project_name

    update_job(
        job_id,
        status="running",
        pipeline_stage=stage_name,
        sub_jobs=item_ids,
        progress=base_progress,
        message=f"Enqueued {total_items} episode(s). Waiting for translation worker...",
        log=f"Stage 3: Actively monitoring {total_items} child queue item(s).",
    )

    last_log_time = 0.0
    missing_item_counts: Dict[str, int] = {}

    while True:
        await asyncio.sleep(poll_interval)

        if job_id not in jobs:
            break

        parent_job = jobs[job_id]

        # 1. Cascade cancellation if parent was cancelled
        if parent_job.cancelled or parent_job.status == "cancelled":
            from services.queue_service import cancel_item
            for item_id in item_ids:
                await asyncio.to_thread(cancel_item, item_id)
                child_job = jobs.get(f"bg_{item_id}")
                if child_job:
                    child_job.cancelled = True
                    child_job.status = "cancelled"
            update_job(
                job_id,
                status="cancelled",
                message="Translation cancelled by user.",
                log="Stage 3 cancelled: all child queue items cancelled.",
            )
            return

        # 2. Inspect child queue items
        all_items = queue.get_all(limit=1000)
        items_by_id = {item["id"]: item for item in all_items}

        completed_count = 0
        failed_count = 0
        cancelled_count = 0
        running_count = 0
        paused_count = 0
        pending_count = 0

        completed_names: List[str] = []
        failed_names: List[str] = []
        running_summaries: List[str] = []
        active_scene_statuses: Dict[str, str] = {}

        sum_ep_progress = 0.0
        total_cues_acc = 0
        translated_cues_acc = 0

        for item_id in item_ids:
            item = items_by_id.get(item_id)
            if not item:
                try:
                    item = queue.get_item(item_id)
                except Exception:
                    pass
            if not item:
                try:
                    conn = sqlite3.connect(queue.db_path)
                    conn.row_factory = sqlite3.Row
                    cursor = conn.execute(
                        "SELECT status, episode_name, project_name FROM translation_queue WHERE id = ?",
                        (item_id,)
                    )
                    db_row = cursor.fetchone()
                    if db_row:
                        item = dict(db_row)
                    conn.close()
                except Exception:
                    pass

            # Missing or unreadable child item handling
            if not item:
                missing_item_counts[item_id] = missing_item_counts.get(item_id, 0) + 1
                if missing_item_counts[item_id] <= 5:
                    # Transient SQLite lock or queue lag: treat as pending and retry
                    pending_count += 1
                    continue
                # Permanently unfound after multiple polls: fail gracefully, never mark completed
                failed_count += 1
                failed_names.append(f"unfound_{item_id}")
                sum_ep_progress += 1.0
                continue

            missing_item_counts.pop(item_id, None)

            status = item.get("status", "pending")
            ep_name = item.get("episode_name", "")
            proj = item.get("project_name") or project_name

            if item_id not in cues_per_item and proj:
                try:
                    ep_meta = storage.load_episode_metadata(proj, ep_name) or {}
                    cues_per_item[item_id] = ep_meta.get("line_count", 0)
                except Exception:
                    cues_per_item[item_id] = 0

            ep_cues = cues_per_item.get(item_id, 0)
            total_cues_acc += ep_cues

            if status == "completed":
                completed_count += 1
                completed_names.append(ep_name)
                sum_ep_progress += 1.0
                translated_cues_acc += ep_cues
            elif status == "failed":
                failed_count += 1
                failed_names.append(ep_name)
                sum_ep_progress += 1.0
                child_job = jobs.get(f"bg_{item_id}")
                if child_job and child_job.cues_translated is not None:
                    translated_cues_acc += child_job.cues_translated
            elif status == "cancelled":
                cancelled_count += 1
                sum_ep_progress += 1.0
                child_job = jobs.get(f"bg_{item_id}")
                if child_job and child_job.cues_translated is not None:
                    translated_cues_acc += child_job.cues_translated
            elif status == "paused_daily_limit":
                paused_count += 1
            elif status == "running":
                running_count += 1
                child_job = jobs.get(f"bg_{item_id}")
                ep_prog = 0.0
                if child_job:
                    scenes = child_job.scene_status or {}
                    total_scenes = len(scenes)
                    done_scenes = sum(1 for v in scenes.values() if v == "completed")
                    if total_scenes > 0:
                        ep_prog = done_scenes / total_scenes
                        running_summaries.append(f"{ep_name} (Scene {done_scenes}/{total_scenes})")
                    else:
                        ep_prog = max(0.0, min(1.0, child_job.progress / 100.0))
                        running_summaries.append(f"{ep_name} ({int(child_job.progress)}%)")

                    for s_k, s_v in scenes.items():
                        active_scene_statuses[f"{ep_name} {s_k}"] = s_v
                else:
                    running_summaries.append(f"{ep_name}")

                sum_ep_progress += ep_prog
                if child_job and child_job.cues_translated is not None:
                    translated_cues_acc += child_job.cues_translated
                else:
                    translated_cues_acc += int(round(ep_prog * ep_cues))
            else:
                pending_count += 1

        terminal_count = completed_count + failed_count + cancelled_count

        # 3. Calculate Stage 3 progress
        stage3_fraction = sum_ep_progress / max(1, total_items)
        calc_progress = base_progress + (max_progress - base_progress) * stage3_fraction
        if terminal_count < total_items:
            progress = min(max_progress - 0.1, max(base_progress, round(calc_progress, 1)))
        else:
            progress = max_progress

        # 4. Calculate dynamic ETA
        now = time.time()
        elapsed = now - start_time
        eta_seconds = None
        eta_str = ""
        if stage3_fraction > 0.02 and elapsed > 3.0:
            eta_seconds = max(0.0, (elapsed / stage3_fraction) - elapsed)
            if eta_seconds < 60:
                eta_str = f" | ETA: {int(eta_seconds)}s"
            elif eta_seconds < 3600:
                eta_str = f" | ETA: {int(eta_seconds // 60)}m {int(eta_seconds % 60):02d}s"
            else:
                eta_str = f" | ETA: {int(eta_seconds // 3600)}h {int((eta_seconds % 3600) // 60):02d}m"
        elif running_count > 0:
            eta_str = " | ETA: calculating..."

        # 5. Format message
        if running_count > 0:
            running_str = ", ".join(running_summaries[:2])
            if len(running_summaries) > 2:
                running_str += f" +{len(running_summaries) - 2} more"
            cues_str = f" · {translated_cues_acc}/{total_cues_acc} cues" if total_cues_acc > 0 else ""
            msg = f"Translating {completed_count}/{total_items} episodes: {running_str}{cues_str}{eta_str}"
        elif paused_count > 0 and pending_count == 0 and running_count == 0:
            msg = f"Paused: Daily API limit reached ({completed_count}/{total_items} completed). Resumes on reset."
        elif pending_count > 0:
            msg = f"Queued {completed_count}/{total_items} complete ({pending_count} pending in queue)..."
        else:
            msg = f"Finishing translation ({completed_count}/{total_items} complete)..."

        is_all_paused = (paused_count > 0 and running_count == 0 and pending_count == 0)
        parent_status = "paused" if is_all_paused else "running"

        log_entry = None
        if now - last_log_time >= 10.0:
            log_entry = f"Stage 3 progress: {progress}% ({completed_count}/{total_items} done, {running_count} running, {pending_count} pending){eta_str}"
            last_log_time = now

        update_job(
            job_id,
            status=parent_status,
            progress=progress,
            message=msg,
            log=log_entry,
            pipeline_stage=stage_name,
            scene_status=active_scene_statuses if active_scene_statuses else None,
            completed_episodes=completed_names,
            failed_episodes=failed_names,
            daily_limit_reached=(paused_count > 0),
            eta_seconds=eta_seconds,
            cues_translated=translated_cues_acc,
            cues_total=total_cues_acc,
            scenes_done=sum(1 for v in active_scene_statuses.values() if v == "completed") if active_scene_statuses else None,
            scenes_total=len(active_scene_statuses) if active_scene_statuses else None,
        )

        # 6. Terminal state check
        if terminal_count == total_items:
            if failed_count == total_items:
                final_status = "failed"
                final_msg = f"Pipeline failed: all {total_items} episodes failed translation."
            elif failed_count > 0 or cancelled_count > 0:
                final_status = "partial"
                final_msg = f"Pipeline complete with warnings: {completed_count} completed, {failed_count} failed, {cancelled_count} cancelled."
            else:
                final_status = "completed"
                final_msg = f"Pipeline complete: all {completed_count} episode(s) successfully translated."

            update_job(
                job_id,
                status=final_status,
                progress=max_progress,
                message=final_msg,
                log=f"Stage 3 finished: {completed_count} completed, {failed_count} failed, {cancelled_count} cancelled.",
                pipeline_stage=stage_name,
                completed_episodes=completed_names,
                failed_episodes=failed_names,
                daily_limit_reached=False,
                eta_seconds=0.0,
                cues_translated=translated_cues_acc,
                cues_total=total_cues_acc,
            )
            return


async def _monitor_batch_queue_progress(job_id: str, item_ids: List[str]):
    await _monitor_stage3_queue_progress(
        job_id=job_id,
        item_ids=item_ids,
        base_progress=0.0,
        max_progress=100.0,
        stage_name="translate",
    )


@router.post("/projects/{project_name}/episodes/{episode_name}/translate")
async def translate_episode(
    project_name: str,
    episode_name: str,
    request: TranslateRequest
):
    validate_api_key()
    metadata = storage.load_project_metadata(project_name) or {}
    global_config = storage.load_global_config()
    model = (
        request.model
        or resolve_model("translation", metadata)
    )
    
    from utils.language_codes import to_code
    target_lang_name = request.target_language_name or metadata.get("target_language", "English")
    primary_code = to_code(metadata.get("target_language", "English"))
    requested_code = request.lang or to_code(target_lang_name)
    is_primary = (requested_code == primary_code or requested_code == "")
    lang_param = "" if is_primary else requested_code

    options = {
        "model": model,
        "target_language_name": target_lang_name,
        "enhance_glossary": request.enhance_glossary,
        "force": request.force,
        "translation_type": request.translation_type,
        "temperature": storage.get_project_setting(metadata, "temperature") or global_config.get("temperature"),
        "top_k": storage.get_project_setting(metadata, "top_k") or global_config.get("top_k"),
        "top_p": storage.get_project_setting(metadata, "top_p") or global_config.get("top_p")
    }
    
    # Deduplicate against translation_queue for identical project/episode/lang in pending or running state
    from utils.translation_queue import TranslationQueue, KIND_TRANSLATION
    tq = TranslationQueue()
    with _pipeline_thread_lock:
        with tq._conn() as conn:
            cursor = conn.execute(
                """
                SELECT id FROM translation_queue
                WHERE LOWER(project_name) = LOWER(?) AND LOWER(episode_name) = LOWER(?) AND lang = ? AND kind = ?
                  AND status IN ('pending', 'running')
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (project_name.strip(), episode_name.strip(), lang_param, KIND_TRANSLATION)
            )
            row = cursor.fetchone()
            if row:
                existing_item_id = row["id"]
                job_key = f"bg_{existing_item_id}"
                if job_key not in jobs or not getattr(jobs[job_key], "cancelled", False):
                    return {"job_id": job_key, "item_id": existing_item_id}

        from services.queue_service import enqueue_translation, PRIORITY_MANUAL
        item_id = enqueue_translation(project_name, episode_name, PRIORITY_MANUAL, options, lang=lang_param)
        
        return {"job_id": f"bg_{item_id}", "item_id": item_id}



@router.post("/projects/{project_name}/batch-translate")
async def batch_translate(
    project_name: str,
    background_tasks: BackgroundTasks,
    request: BatchTranslateRequest
):
    validate_api_key()
    metadata = storage.load_project_metadata(project_name) or {}
    global_config = storage.load_global_config()
    model = (
        request.model
        or resolve_model("translation", metadata)
    )
    
    options = {
        "model": model,
        "enhance_glossary": request.enhance_glossary,
        "force": request.force,
        "translation_type": request.translation_type,
        "temperature": storage.get_project_setting(metadata, "temperature") or global_config.get("temperature"),
        "top_k": storage.get_project_setting(metadata, "top_k") or global_config.get("top_k"),
        "top_p": storage.get_project_setting(metadata, "top_p") or global_config.get("top_p")
    }
    
    req_episodes = sorted(list({str(e).strip() for e in (request.episode_names or []) if e and str(e).strip()}))
    req_episodes_folded = {e.casefold() for e in req_episodes}

    async with _get_pipeline_async_lock():
        with _pipeline_thread_lock:
            # Deduplicate: check if identical batch translation is already in flight for this project
            for existing_id, existing_job in list(jobs.items()):
                if (
                    (existing_job.project_name or "").casefold() == project_name.casefold()
                    and existing_id.startswith("batch_translate_")
                    and existing_job.status in ("pending", "running")
                    and not getattr(existing_job, "cancelled", False)
                ):
                    existing_eps = getattr(existing_job, "target_episodes", None)
                    if existing_eps is None:
                        existing_eps = _batch_target_episodes.get(existing_id)
                    if existing_eps is not None:
                        existing_folded = {str(e).strip().casefold() for e in existing_eps}
                        if existing_folded == req_episodes_folded:
                            existing_sub_jobs = getattr(existing_job, "sub_jobs", None) or []
                            return {
                                "job_id": existing_id,
                                "sub_jobs": existing_sub_jobs,
                                "item_ids": existing_sub_jobs
                            }

            job_id = create_job("batch_translate", project_name=project_name)
            jobs[job_id].target_episodes = req_episodes
            _batch_target_episodes[job_id] = req_episodes

            options_with_parent = dict(options)
            options_with_parent["parent_job_id"] = job_id

            from services.queue_service import enqueue_translation, PRIORITY_MANUAL
            enqueued_ids = []
            for ep_name in req_episodes:
                item_id = enqueue_translation(project_name, ep_name, PRIORITY_MANUAL, options_with_parent)
                enqueued_ids.append(item_id)
                
            jobs[job_id].sub_jobs = enqueued_ids
            background_tasks.add_task(_monitor_batch_queue_progress, job_id, enqueued_ids)
            
            return {
                "job_id": job_id,
                "sub_jobs": enqueued_ids,
                "item_ids": enqueued_ids
            }


@router.post("/projects/{project_name}/batch-translate/estimate")
async def estimate_batch(project_name: str, request: BatchTranslateRequest):
    """Token-based cost & quota preflight before starting a batch (v2 D8).

    Counts real lines/characters per episode and models both request modes:
    whole-episode (Gemini default) vs scene-chunked.
    """
    episodes = request.episode_names

    global_config = storage.load_global_config()
    max_lines = global_config.get("max_lines_per_scene", 200)
    mode = global_config.get("episode_request_mode", "auto")
    whole_cap = global_config.get("whole_episode_max_cues", 1200)

    metadata = storage.load_project_metadata(project_name) or {}
    model = request.model or resolve_model("translation", metadata)

    # Bible size (system instruction repeated per request).
    from services.prompt_assembler import compile_bible
    try:
        bible_tokens = len(compile_bible(project_name, metadata, model).system_instruction) // 4
    except Exception:
        bible_tokens = 1200

    # Filter episodes that already have a target-language subtitle
    filtered_episodes = []
    for ep_name in episodes:
        ep_meta = storage.load_episode_metadata(project_name, ep_name)
        if not storage.episode_has_target(ep_meta):
            filtered_episodes.append(ep_name)
    episodes = filtered_episodes

    total_calls = 0
    total_in_tokens = 0
    total_out_tokens = 0
    total_lines = 0
    for ep_name in episodes:
        data = storage.load_episode(project_name, ep_name)
        if not data or not data.get("data"):
            continue
        rows = data["data"]
        n_lines = len(rows)
        src_chars = sum(len(r.get("original", "")) for r in rows)
        src_tokens = max(1, src_chars // 4)
        out_tokens = int(src_tokens * 1.15) + n_lines * 4  # target slightly longer + JSON overhead
        total_lines += n_lines

        use_whole = mode != "scenes" and (mode == "whole" or n_lines <= whole_cap)
        if use_whole:
            calls = 1
        else:
            calls = max(1, len(build_scene_ast(rows, max_lines_per_scene=max_lines)))
        total_calls += calls
        total_in_tokens += calls * bible_tokens + src_tokens
        total_out_tokens += out_tokens

    # Cost (thinking is disabled for translation by default — D2).
    from utils.telemetry import _price_for
    pin, pout = _price_for(model or "")
    cost = (total_in_tokens * pin + total_out_tokens * pout) / 1_000_000.0
    batch_cost = cost * 0.5  # Batch API discount

    estimate = translation_rate_limiter.estimate_requests_from_scenes(total_calls)
    return {
        "total_episodes": len(episodes),
        "total_lines": total_lines,
        "estimated_api_calls": total_calls,
        "request_mode": "whole-episode" if mode != "scenes" else "scenes",
        "estimated_minutes": round(estimate["estimated_minutes"], 1),
        "exceeds_daily_limit": estimate["exceeds_daily"],
        "daily_remaining": estimate["remaining_daily"],
        "estimated_input_tokens": total_in_tokens,
        "estimated_output_tokens": total_out_tokens,
        "estimated_cost_usd": round(cost, 4),
        "estimated_cost_usd_batch_lane": round(batch_cost, 4),
        "model": model,
    }


@router.post("/projects/{project_name}/pipeline/start")
async def start_pipeline(
    project_name: str,
    background_tasks: BackgroundTasks,
    request: PipelineRequest
):
    validate_api_key()
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")

    async with _get_pipeline_async_lock():
        with _pipeline_thread_lock:
            # Deduplicate concurrent pipeline runs for this project
            for existing_id, existing_job in list(jobs.items()):
                if (
                    (existing_job.project_name or "").casefold() == project_name.casefold()
                    and (
                        existing_id.startswith("pipeline_")
                        or existing_id.startswith("auto_translate_")
                        or existing_id.startswith("simple_pipeline_")
                        or getattr(existing_job, "pipeline_mode", None) in ("auto", "simple", getattr(request, "mode", None))
                    )
                    and existing_job.status in ("pending", "running", "awaiting_review")
                    and not getattr(existing_job, "cancelled", False)
                ):
                    return {"job_id": existing_id}

            job_id = create_job("pipeline", project_name=project_name)
            jobs[job_id].pipeline_mode = request.mode
            jobs[job_id].pipeline_stage = "context"
            jobs[job_id].sub_jobs = []

            background_tasks.add_task(
                _process_pipeline, job_id, project_name, request
            )
            return {"job_id": job_id}


@router.post("/projects/{project_name}/pipeline/{job_id}/continue")
async def continue_pipeline(
    project_name: str,
    job_id: str,
    background_tasks: BackgroundTasks
):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[job_id]
    if job.status != "awaiting_review":
        raise HTTPException(status_code=400, detail="Pipeline is not awaiting review")
    job.status = "running"
    job.message = "Resuming pipeline..."
    background_tasks.add_task(_resume_pipeline, job_id, project_name)
    return {"status": "resumed"}


@router.post("/projects/{project_name}/auto-translate")
async def start_auto_translate(
    project_name: str,
    background_tasks: BackgroundTasks,
    request: AutoPipelineRequest = AutoPipelineRequest(),
):
    """Fire-and-forget translation pipeline — no review gates.

    Steps (all automatic):
    1. Generate context guide if missing (or skip_context=True)
    2. Generate glossary if missing (or skip_glossary=True)
    3. Translate all episodes (or the provided subset)

    Uses the project's default model from settings, overridable via ``model``.
    """
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")

    async with _get_pipeline_async_lock():
        with _pipeline_thread_lock:
            # Deduplicate: check if an active auto_translate or simple_pipeline job is already in flight
            for existing_id, existing_job in list(jobs.items()):
                if (
                    (existing_job.project_name or "").casefold() == project_name.casefold()
                    and (
                        existing_id.startswith("auto_translate_")
                        or existing_id.startswith("simple_pipeline_")
                        or existing_id.startswith("pipeline_")
                        or getattr(existing_job, "pipeline_mode", None) in ("auto", "simple")
                    )
                    and existing_job.status in ("pending", "running", "awaiting_review")
                    and not getattr(existing_job, "cancelled", False)
                ):
                    return {"job_id": existing_id}

            global_config = storage.load_global_config()
            translation_model = request.model or request.model_name or request.translation_model or resolve_model("translation", metadata)
            context_model = request.context_model or resolve_model("context", metadata)
            glossary_model = request.glossary_model or resolve_model("glossary", metadata)
            req_episodes = request.episode_names or request.episodes
            if req_episodes:
                req_episodes = [e for e in req_episodes if e and str(e).strip()]
                if not req_episodes:
                    req_episodes = None

            job_id = create_job("auto_translate", project_name=project_name)
            jobs[job_id].pipeline_mode = "auto"
            jobs[job_id].pipeline_stage = "context"
            background_tasks.add_task(
                _process_auto_translate,
                job_id, project_name, 
                translation_model, context_model, glossary_model,
                req_episodes, request.skip_context, request.skip_glossary,
                request.enhance_glossary, request.force, request.translation_type,
            )
            return {"job_id": job_id}




@router.post("/projects/{project_name}/simple-pipeline/start")
async def start_simple_pipeline(
    project_name: str,
    background_tasks: BackgroundTasks,
    request: SimplePipelineRequest
):
    validate_api_key()

    async with _get_pipeline_async_lock():
        with _pipeline_thread_lock:
            # Re-use existing active or paused pipeline job if one is already running
            for existing_id, existing_job in list(jobs.items()):
                if (
                    (existing_job.project_name or "").casefold() == project_name.casefold()
                    and (
                        existing_id.startswith("simple_pipeline_")
                        or existing_id.startswith("auto_translate_")
                        or existing_id.startswith("pipeline_")
                        or getattr(existing_job, "pipeline_mode", None) in ("simple", "auto")
                    )
                    and existing_job.status in ("pending", "running", "awaiting_review")
                    and not getattr(existing_job, "cancelled", False)
                ):
                    return {"job_id": existing_id}

            job_id = create_job("simple_pipeline", project_name=project_name)
            jobs[job_id].pipeline_mode = "simple"
            jobs[job_id].pipeline_stage = "analyze"
            metadata = storage.load_project_metadata(project_name) or {}
            global_config = storage.load_global_config()
            translation_model = request.translation_model or request.model or resolve_model("translation", metadata)
            context_model = request.context_model or request.model or resolve_model("context", metadata)
            glossary_model = request.glossary_model or request.model or resolve_model("glossary", metadata)

            background_tasks.add_task(
                _process_simple_pipeline, 
                job_id, 
                project_name, 
                translation_model, 
                context_model, 
                glossary_model,
                request.enhance_glossary,
            )
            return {"job_id": job_id}


@router.post("/projects/{project_name}/simple-pipeline/{job_id}/confirm-context")
async def confirm_pipeline_context(
    project_name: str,
    job_id: str,
    request: ConfirmContextRequest
):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Save the edited context to project metadata
    metadata = storage.load_project_metadata(project_name)
    metadata["context_guide"] = request.context_guide
    storage.save_project_metadata(project_name, metadata)
    await adk_session_manager.update_context(project_name, request.context_guide)
    
    # Signal the background task to continue
    event = _pipeline_resume_events.get(job_id)
    if event:
        event.set()
    return {"status": "confirmed"}


@router.post("/projects/{project_name}/simple-pipeline/{job_id}/confirm-glossary")
async def confirm_pipeline_glossary(
    project_name: str,
    job_id: str,
    request: ConfirmGlossaryRequest
):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Save the edited glossary to project metadata
    metadata = storage.load_project_metadata(project_name)
    metadata["glossary"] = request.glossary
    storage.save_project_metadata(project_name, metadata)
    await adk_session_manager.update_glossary(project_name, request.glossary)
    
    # Signal the background task to continue
    event = _pipeline_resume_events.get(job_id)
    if event:
        event.set()
    return {"status": "confirmed"}


# --- Background Tasks ---

async def _process_scan_episode(job_id: str, project_name: str, episode_name: str, model: str):
    update_job(job_id, status="running", progress=0.0, message=f"Scanning {episode_name}...", log="Job started")
    try:
        metadata = storage.load_project_metadata(project_name)
        episode_data = storage.load_episode(project_name, episode_name)
        
        if not metadata or not episode_data:
            update_job(job_id, status="failed", message="Not found")
            return
        
        target_language = metadata.get("target_language", "English")
        existing_terms = metadata.get("glossary", {}).get("terms", [])
        text_lines = extract_text_only(episode_data["data"])
        
        agent = create_cartographer_agent(model_name=model, target_language=target_language)
        session_id_unique = f"scan_episode_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        eph_svc = get_ephemeral_session_service()
        runner = Runner(
            agent=agent,
            app_name=f"Omnisub_{session_id_unique}",
            session_service=eph_svc,
        )
        
        # Create session explicitly
        await eph_svc.create_session(
            session_id=session_id_unique,
            user_id="default_user",
            app_name=f"Omnisub_{session_id_unique}"
        )
        
        update_job(job_id, progress=30.0, message="Analyzing...", log="Running extraction")
        
        # Include existing terms to avoid duplicates
        existing_names = ", ".join(t.get("term", "") for t in existing_terms) if existing_terms else "None"
        prompt = f"""Extract glossary terms from the following subtitle text.
Target Language: {target_language}
Existing terms (DO NOT duplicate): {existing_names}

{chr(10).join(text_lines[:5000])}"""
        
        response_text = ""
        async for event in runner.run_async(
            user_id="default_user",
            session_id=session_id_unique,
            new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=prompt)])
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_text += part.text
        
        new_glossary = parse_glossary_from_text(response_text)
        
        # Merge with existing glossary instead of replacing
        existing_names_set = {t.get("term", "").lower() for t in existing_terms}
        new_terms = [
            t for t in new_glossary.get("terms", [])
            if t.get("term", "").lower() not in existing_names_set
        ]
        merged = {"terms": existing_terms + new_terms}
        
        await adk_session_manager.update_glossary(project_name, merged)
        metadata["glossary"] = merged
        _invalidate_translation_cache(metadata)
        storage.save_project_metadata(project_name, metadata)
        
        update_job(
            job_id, status="completed", progress=100.0,
            message=f"Scan completed — found {len(new_terms)} new terms",
            log=f"Added {len(new_terms)} new terms (skipped {len(new_glossary.get('terms', [])) - len(new_terms)} duplicates)",
            result={"new_terms": new_terms, "total_terms": len(merged["terms"])}
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {str(e)}", log=str(e))


async def _process_auto_translate(
    job_id: str,
    project_name: str,
    translation_model: str,
    context_model: str,
    glossary_model: str,
    episode_names: Optional[List[str]],
    skip_context: bool,
    skip_glossary: bool,
    enhance_glossary: bool,
    force: bool = False,
    translation_type: str = "saved",
):
    """Fully automatic pipeline: context → glossary → translate, no pauses."""
    update_job(job_id, status="running", progress=0.0, message="Starting auto-translate...", log="Auto pipeline started")

    try:
        if episode_names:
            episode_names = [e for e in episode_names if e and str(e).strip()]
            if not episode_names:
                episode_names = None

        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return

        # Auto-extract embedded subtitles for any episodes with media files lacking subtitles
        from services.embedded_extractor import auto_extract_embedded_subs_for_episodes
        n_extracted = await auto_extract_embedded_subs_for_episodes(
            project_name=project_name,
            episode_names=episode_names,
            progress_callback=lambda msg: update_job(job_id, log=msg)
        )
        if n_extracted > 0:
            update_job(job_id, log=f"Auto-extracted embedded subtitles for {n_extracted} episode(s).")
            metadata = storage.load_project_metadata(project_name) or metadata

        # ── Stage 1: Context guide ────────────────────────────────────────

        has_context = bool(metadata.get("context_guide", "").strip())
        if not skip_context and not has_context:
            update_job(job_id, progress=5.0, message="Generating context guide...", log="Stage 1/3: context (missing, creating)")
            jobs[job_id].pipeline_stage = "context"

            text_lines = await _gather_project_text(project_name, episode_names)
            research_data, _ = await research_project_adk(
                metadata.get("show_name", project_name),
                text_lines,
                metadata.get("target_language", "English"),
                model_name=context_model,
            )
            context_guide, _ = await enhance_context_guide_adk(
                research_data.get("findings", ""),
                metadata.get("show_name", project_name),
                target_language=metadata.get("target_language", "English"),
                model_name=context_model,
            )

            metadata["context_guide"] = context_guide
            _invalidate_translation_cache(metadata)
            storage.save_project_metadata(project_name, metadata)
            await adk_session_manager.update_context(project_name, context_guide)
            update_job(job_id, progress=25.0, log="Context guide saved")
        else:
            reason = "already exists" if has_context else "skipped by request"
            update_job(job_id, progress=25.0, log=f"Context: {reason}")

        if jobs[job_id].cancelled:
            update_job(job_id, status="cancelled", message="Cancelled")
            return

        # ── Stage 2: Glossary ────────────────────────────────────────────
        has_glossary = bool(metadata.get("glossary", {}).get("terms"))
        if not skip_glossary and not has_glossary:
            update_job(job_id, progress=28.0, message="Building glossary...", log="Stage 2/3: glossary (missing, creating)")
            jobs[job_id].pipeline_stage = "glossary"

            text_lines = await _gather_project_text(project_name, episode_names)

            result, _ = await generate_glossary_adk(
                text_lines,
                metadata.get("show_name", project_name),
                metadata.get("target_language", "English"),
                existing_glossary=metadata.get("glossary"),
                model_name=glossary_model,
                enable_research=True,
            )

            # Sanitize Japanese names for Greek if applicable
            target_lang = metadata.get("target_language", "English")
            if (target_lang or "").strip().lower() in ("greek", "el", "ελληνικά") and result.get("terms"):
                from utils.text_normalize import fix_japanese_name_greek_tonos
                for term_obj in result["terms"]:
                    if isinstance(term_obj, dict) and "translation" in term_obj:
                        term_obj["translation"] = fix_japanese_name_greek_tonos(term_obj["translation"])

            metadata["glossary"] = result
            _invalidate_translation_cache(metadata)
            storage.save_project_metadata(project_name, metadata)
            await adk_session_manager.update_glossary(project_name, result)
            update_job(job_id, progress=50.0, log=f"Glossary: {len(result.get('terms', []))} terms")
        else:
            reason = "already exists" if has_glossary else "skipped by request"
            update_job(job_id, progress=50.0, log=f"Glossary: {reason}")

        if jobs[job_id].cancelled:
            update_job(job_id, status="cancelled", message="Cancelled")
            return

        # ── Stage 3: Translate ───────────────────────────────────────────
        jobs[job_id].pipeline_stage = "translate"
        if episode_names:
            raw_candidates = [str(e).strip() for e in episode_names if e and str(e).strip()]
        else:
            raw_candidates = [str(e).strip() for e in storage.list_episodes(project_name) if e and str(e).strip()]
        candidate_episodes = [e for e in raw_candidates if e and str(e).strip()]

        # Filter out episodes that do NOT have source subtitles, cues, or media files
        episodes = []
        for ep_name in candidate_episodes:
            if not ep_name or not str(ep_name).strip():
                continue
            ep_meta = storage.load_episode_metadata(project_name, ep_name) or {}
            orig_ex = storage.original_subtitle_exists(project_name, ep_name)
            has_source = ep_meta.get("has_source_sub", True)
            if ep_meta.get("line_count", 0) > 0 or ep_meta.get("arr_sub_path") or orig_ex or ep_meta.get("arr_media_path"):
                has_source = True
            elif ep_meta.get("line_count", 0) == 0 and not ep_meta.get("arr_sub_path") and not orig_ex and not ep_meta.get("arr_media_path"):
                has_source = False
            if has_source:
                episodes.append(ep_name)

        if not episodes:
            update_job(job_id, status="completed", progress=100.0, message="No episodes with subtitles to translate")
            return



        update_job(job_id, progress=52.0, message=f"Translating {len(episodes)} episodes...", log=f"Stage 3/3: {len(episodes)} episodes")

        # Reload metadata so translation sees any freshly-saved glossary/context
        metadata = storage.load_project_metadata(project_name)
        options = {
            "model": translation_model,
            "enhance_glossary": enhance_glossary,
            "context_model": context_model,
            "glossary_model": glossary_model,
            "force": force,
            "translation_type": translation_type,
            "parent_job_id": job_id,
        }
        from services.queue_service import enqueue_translation, PRIORITY_MANUAL
        enqueued_ids = []
        for ep_name in episodes:
            clean_ep = str(ep_name).strip() if ep_name else ""
            if clean_ep:
                item_id = enqueue_translation(project_name, clean_ep, PRIORITY_MANUAL, options)
                if item_id:
                    enqueued_ids.append(item_id)

        update_job(
            job_id,
            status="running",
            pipeline_stage="translate",
            sub_jobs=enqueued_ids,
            progress=52.0,
            message=f"Enqueued {len(enqueued_ids)} episode(s) in the translation queue.",
            log=f"Enqueued {len(enqueued_ids)} episode(s) in translation queue. Beginning Stage 3 monitoring.",
        )
        await _monitor_stage3_queue_progress(
            job_id=job_id,
            item_ids=enqueued_ids,
            base_progress=50.0,
            project_name=project_name,
        )

    except Exception as e:
        update_job(job_id, status="failed", message=f"Auto-translate error: {str(e)}", log=str(e))




async def _process_simple_pipeline(
    job_id: str, 
    project_name: str, 
    translation_model: str,
    context_model: str,
    glossary_model: str,
    enhance_glossary: bool = True,
):
    try:
        update_job(job_id, status="running", progress=5.0, message="Analyzing project context...")
        metadata = storage.load_project_metadata(project_name)
        target_lang = metadata.get("target_language", "English")
        
        # Auto-extract embedded subtitles for any episodes with media files lacking subtitles
        from services.embedded_extractor import auto_extract_embedded_subs_for_episodes
        n_extracted = await auto_extract_embedded_subs_for_episodes(
            project_name=project_name,
            progress_callback=lambda msg: update_job(job_id, log=msg)
        )
        if n_extracted > 0:
            update_job(job_id, log=f"Auto-extracted embedded subtitles for {n_extracted} episode(s).")
            metadata = storage.load_project_metadata(project_name) or metadata

        # Step 1: Analyze Context
        jobs[job_id].pipeline_stage = "analyze"
        update_job(job_id, log="Starting context analysis")
        
        # Get a small text sample for context if needed
        episodes = storage.list_episodes(project_name)
        sample_text = []
        if episodes:
            first_ep = storage.load_episode(project_name, episodes[0])
            if first_ep:
                sample_text = extract_text_only(first_ep["data"])[:100]


        # Research + Enhance
        research_data, _ = await research_project_adk(project_name, sample_text, target_lang, context_model)
        context_guide, _ = await enhance_context_guide_adk(
            research_data.get("findings", ""),
            project_name,
            target_language=target_lang,
            model_name=context_model
        )

        update_job(job_id, progress=20.0, result={"context_guide": context_guide}, message="Awaiting context review...")
        await _pipeline_pause(job_id, "context", "Please review and confirm the generated context guide.")
        
        if jobs[job_id].cancelled: return

        # Step 2: Glossary Scan
        jobs[job_id].pipeline_stage = "glossary"
        update_job(job_id, status="running", progress=25.0, message="Scanning episodes for glossary terms...")
        
        golden_files = _select_golden_ratio_files(episodes)
        update_job(job_id, log=f"Selected {len(golden_files)} episodes for glossary extraction")
        
        combined_text = []
        for ep_name in golden_files:
            ep_data = storage.load_episode(project_name, ep_name)
            if ep_data:
                combined_text.extend(extract_text_only(ep_data["data"]))
        
        # Limit text for glossary extraction
        glossary_dict, _ = await generate_glossary_adk(
            combined_text[:2000], project_name, target_lang, 
            existing_glossary=metadata.get("glossary"),
            model_name=glossary_model, enable_research=True
        )

        if (target_lang or "").strip().lower() in ("greek", "el", "ελληνικά") and glossary_dict.get("terms"):
            from utils.text_normalize import fix_japanese_name_greek_tonos
            for term_obj in glossary_dict["terms"]:
                if isinstance(term_obj, dict) and "translation" in term_obj:
                    term_obj["translation"] = fix_japanese_name_greek_tonos(term_obj["translation"])
        
        update_job(job_id, progress=50.0, result={"glossary": glossary_dict}, message="Awaiting glossary review...")
        await _pipeline_pause(job_id, "glossary", "Please review and confirm the generated glossary terms.")
        
        if jobs[job_id].cancelled: return

        # Step 3: Batch Translation
        jobs[job_id].pipeline_stage = "translate"
        update_job(job_id, status="running", progress=55.0, message="Translating episodes...")
        
        # Reload metadata so translation sees any freshly-saved glossary/context
        metadata = storage.load_project_metadata(project_name)
        options = {
            "model": translation_model,
            "enhance_glossary": enhance_glossary,
            "context_model": context_model,
            "glossary_model": glossary_model,
            "parent_job_id": job_id,
        }
        from services.queue_service import enqueue_translation, PRIORITY_MANUAL
        valid_episodes = [str(e).strip() for e in episodes if e and str(e).strip()]
        enqueued_ids = []
        for ep_name in valid_episodes:
            item_id = enqueue_translation(project_name, ep_name, PRIORITY_MANUAL, options)
            if item_id:
                enqueued_ids.append(item_id)

        update_job(
            job_id,
            status="running",
            pipeline_stage="translate",
            sub_jobs=enqueued_ids,
            progress=55.0,
            message=f"Enqueued {len(enqueued_ids)} episode(s) in the translation queue.",
            log=f"Enqueued {len(enqueued_ids)} episode(s) in translation queue. Beginning Step 3 monitoring.",
        )
        await _monitor_stage3_queue_progress(
            job_id=job_id,
            item_ids=enqueued_ids,
            base_progress=50.0,
            project_name=project_name,
        )

    except Exception as e:
        update_job(job_id, status="failed", message=f"Pipeline error: {str(e)}", log=str(e))


async def _process_pipeline(job_id: str, project_name: str, request: PipelineRequest):
    """Run the full translation pipeline: context → glossary → translate."""
    update_job(job_id, status="running", progress=0.0, message="Starting pipeline...", log="Pipeline started")

    try:
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return

        global_config = storage.load_global_config()
        ctx_model = request.context_model or request.model or resolve_model("context", metadata)
        gls_model = request.glossary_model or request.model or resolve_model("glossary", metadata)
        tr_model = request.translation_model or request.model or resolve_model("translation", metadata)
        is_step = request.mode == "step"
        active_model_var.set(tr_model)

        # --- Stage 1: Context Guide ---
        if not request.skip_context:
            if jobs[job_id].cancelled:
                return

            jobs[job_id].pipeline_stage = "context"
            update_job(job_id, progress=5.0, message="Creating context guide...", log="Stage 1/3: Context")

            text_lines = await _gather_project_text(project_name, request.episode_names)

            research_data, _ = await research_project_adk(
                metadata.get("show_name", project_name),
                text_lines,
                metadata.get("target_language", "English"),
                model_name=ctx_model
            )

            if jobs[job_id].cancelled:
                return

            update_job(job_id, progress=15.0, message="Generating context instructions...", log="Research complete, generating guide")
            enhanced_context, _ = await enhance_context_guide_adk(
                research_data.get("findings", ""),
                metadata.get("show_name", project_name),
                target_language=metadata.get("target_language", "English"),
                model_name=ctx_model
            )

            metadata["context_guide"] = enhanced_context
            _invalidate_translation_cache(metadata)
            storage.save_project_metadata(project_name, metadata)
            await adk_session_manager.update_context(project_name, enhanced_context)

            update_job(job_id, progress=25.0, message="Context guide created", log="Context guide saved")

            if is_step:
                await _pipeline_pause(job_id, "context", "Review context guide")
                if jobs[job_id].cancelled:
                    return
                # Reload metadata in case user edited during review
                metadata = storage.load_project_metadata(project_name)
        else:
            update_job(job_id, progress=25.0, log="Skipping context stage (skipped by request)")

        # --- Stage 2: Glossary ---
        if not request.skip_glossary:
            if jobs[job_id].cancelled:
                return

            jobs[job_id].pipeline_stage = "glossary"
            update_job(job_id, progress=30.0, message="Building glossary...", log="Stage 2/3: Glossary")

            text_lines = await _gather_project_text(project_name, request.episode_names)
            enable_research = bool(not text_lines and metadata.get("show_name"))

            result, _ = await generate_glossary_adk(
                text_lines,
                metadata.get("show_name", project_name),
                metadata.get("target_language", "English"),
                existing_glossary=metadata.get("glossary"),
                model_name=gls_model,
                enable_research=enable_research
            )

            # Sanitize Japanese names for Greek if applicable
            target_lang = metadata.get("target_language", "English")
            if (target_lang or "").strip().lower() in ("greek", "el", "ελληνικά") and result.get("terms"):
                from utils.text_normalize import fix_japanese_name_greek_tonos
                for term_obj in result["terms"]:
                    if isinstance(term_obj, dict) and "translation" in term_obj:
                        term_obj["translation"] = fix_japanese_name_greek_tonos(term_obj["translation"])

            metadata["glossary"] = result
            _invalidate_translation_cache(metadata)
            storage.save_project_metadata(project_name, metadata)
            await adk_session_manager.update_glossary(project_name, result)

            update_job(job_id, progress=50.0, message="Glossary built", log=f"Glossary saved ({len(result.get('terms', []))} terms)")

            if is_step:
                await _pipeline_pause(job_id, "glossary", "Review glossary")
                if jobs[job_id].cancelled:
                    return
                metadata = storage.load_project_metadata(project_name)
        else:
            update_job(job_id, progress=50.0, log="Skipping glossary stage (skipped by request)")

        # --- Stage 3: Translation ---
        if jobs[job_id].cancelled:
            return

        jobs[job_id].pipeline_stage = "translate"
        raw_eps = request.episode_names if request.episode_names is not None else storage.list_episodes(project_name)
        episode_names = [str(e).strip() for e in raw_eps if e and str(e).strip()]

        if not episode_names:
            update_job(job_id, status="completed", progress=100.0, message="Pipeline complete (no episodes to translate)")
            return

        update_job(job_id, progress=55.0, message=f"Translating {len(episode_names)} episodes...", log=f"Stage 3/3: Translate ({len(episode_names)} episodes)")

        options = {
            "model": tr_model,
            "enhance_glossary": request.enhance_glossary,
            "context_model": ctx_model,
            "glossary_model": gls_model,
            "parent_job_id": job_id,
        }
        from services.queue_service import enqueue_translation, PRIORITY_MANUAL
        enqueued_ids = []
        for ep_name in episode_names:
            item_id = enqueue_translation(project_name, ep_name, PRIORITY_MANUAL, options)
            if item_id:
                enqueued_ids.append(item_id)

        update_job(
            job_id,
            status="running",
            pipeline_stage="translate",
            sub_jobs=enqueued_ids,
            progress=55.0,
            message=f"Enqueued {len(enqueued_ids)} episode(s) in the translation queue.",
            log=f"Enqueued {len(enqueued_ids)} episode(s) in translation queue. Beginning Stage 3 monitoring.",
        )
        await _monitor_stage3_queue_progress(
            job_id=job_id,
            item_ids=enqueued_ids,
            base_progress=50.0,
            project_name=project_name,
        )

    except Exception as e:
        update_job(job_id, status="failed", message=f"Pipeline error: {str(e)}", log=str(e))
    finally:
        # Clean up resume event
        _pipeline_resume_events.pop(job_id, None)


async def _pipeline_pause(job_id: str, stage: str, message: str):
    """Pause pipeline and wait for user to call /pipeline/continue."""
    event = asyncio.Event()
    _pipeline_resume_events[job_id] = event
    jobs[job_id].status = "awaiting_review"
    jobs[job_id].message = message
    jobs[job_id].logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] Paused for review ({stage})")
    # Wait until the continue endpoint sets the event, or job is cancelled
    while not event.is_set():
        if jobs[job_id].cancelled:
            return
        await asyncio.sleep(0.5)


async def _resume_pipeline(job_id: str, project_name: str):
    """Signal a paused pipeline to continue."""
    event = _pipeline_resume_events.get(job_id)
    if event:
        event.set()


@router.post("/projects/{project_name}/pipeline/full-ingest")
async def start_full_ingest_pipeline(
    project_name: str,
    background_tasks: BackgroundTasks,
    request: Optional[AutoPipelineRequest] = None
):
    validate_api_key()
    job_id = create_job("full_ingest_pipeline", project_name=project_name)
    background_tasks.add_task(_process_full_ingest, job_id, project_name, request or AutoPipelineRequest())
    return {"job_id": job_id}


async def _process_full_ingest(job_id: str, project_name: str, request: AutoPipelineRequest):
    update_job(job_id, status="running", progress=0.0, message="Starting Full Ingest Pipeline...", log="Initialized One-Click Ingest Pipeline")
    try:
        metadata = storage.load_project_metadata(project_name) or {}
        global_config = storage.load_global_config()

        ctx_model = resolve_model("context", metadata)
        gls_model = resolve_model("glossary", metadata)
        tr_model = resolve_model("translation", metadata)

        # Stage 1: Probe containers and extract embedded tracks
        update_job(job_id, progress=10.0, message="Stage 1/4: Checking video containers for embedded subtitle tracks...", log="Checking media files for embedded streams")
        from services.embedded_extractor import auto_extract_embedded_subs_for_episodes
        extracted_count = await auto_extract_embedded_subs_for_episodes(
            project_name=project_name,
            progress_callback=lambda msg: update_job(job_id, log=msg)
        )
        if jobs[job_id].cancelled:
            return
        if extracted_count > 0:
            metadata = storage.load_project_metadata(project_name) or metadata

        update_job(job_id, progress=30.0, message=f"Stage 1 complete: Demuxed {extracted_count} track(s).", log=f"Demuxed {extracted_count} track(s)")

        # Read after Stage 1: extraction can turn a subtitle-less placeholder into a
        # translatable episode, and the later stages must see it.
        ep_names = storage.list_episodes(project_name)

        # Stage 2: Context Guide
        if not metadata.get("context_guide"):
            update_job(job_id, progress=40.0, message="Stage 2/4: Generating project context guide...", log="Researching project context")
            text_lines = await _gather_project_text(project_name, ep_names)
            research_data, _ = await research_project_adk(
                metadata.get("show_name", project_name),
                text_lines,
                metadata.get("target_language", "English"),
                model_name=ctx_model
            )
            if jobs[job_id].cancelled:
                return
            enhanced_context, _ = await enhance_context_guide_adk(
                research_data.get("findings", ""),
                metadata.get("show_name", project_name),
                target_language=metadata.get("target_language", "English"),
                model_name=ctx_model
            )
            metadata["context_guide"] = enhanced_context
            _invalidate_translation_cache(metadata)
            storage.save_project_metadata(project_name, metadata)
            await adk_session_manager.update_context(project_name, enhanced_context)

        # Stage 3: Glossary
        if not metadata.get("glossary") or len(metadata.get("glossary", {}).get("terms", [])) == 0:
            update_job(job_id, progress=60.0, message="Stage 3/4: Building initial terminology glossary...", log="Generating glossary")
            text_lines = await _gather_project_text(project_name, ep_names)
            result, _ = await generate_glossary_adk(
                text_lines,
                metadata.get("show_name", project_name),
                metadata.get("target_language", "English"),
                model_name=gls_model,
                enable_research=bool(not text_lines and metadata.get("show_name"))
            )
            metadata["glossary"] = result
            _invalidate_translation_cache(metadata)
            storage.save_project_metadata(project_name, metadata)
            await adk_session_manager.update_glossary(project_name, result)

        # Stage 4: Enqueue Untranslated Episodes
        update_job(job_id, progress=80.0, message="Stage 4/4: Enqueuing pending episodes for translation...", log="Enqueuing batch translations")
        from services.queue_service import enqueue_translation, PRIORITY_MANUAL
        untranslated_ids = []
        for ep_name in ep_names:
            # An episode with no source subtitle (nothing on disk, nothing muxed) can
            # only fail in the worker; queueing it just produced retried errors.
            if not storage.original_subtitle_exists(project_name, ep_name):
                continue
            ep_meta = storage.load_episode_metadata(project_name, ep_name) or {}
            if storage.episode_has_target(ep_meta) and not storage.episode_translation_is_stale(ep_meta):
                continue
            if not ep_meta.get("translated", False) or storage.episode_translation_is_stale(ep_meta):
                item_id = enqueue_translation(
                    project_name,
                    ep_name,
                    PRIORITY_MANUAL,
                    {"model": tr_model, "context_model": ctx_model, "glossary_model": gls_model, "parent_job_id": job_id}
                )
                if item_id:
                    untranslated_ids.append(item_id)

        update_job(
            job_id,
            status="running",
            pipeline_stage="translate",
            sub_jobs=untranslated_ids,
            progress=80.0,
            message=f"Full Ingest: Demuxed {extracted_count} ASS track(s). Enqueued {len(untranslated_ids)} episode(s) for translation.",
            log=f"Enqueued {len(untranslated_ids)} episode(s) in translation queue. Beginning Stage 4 monitoring.",
        )
        await _monitor_stage3_queue_progress(
            job_id=job_id,
            item_ids=untranslated_ids,
            base_progress=80.0,
            max_progress=100.0,
            stage_name="translate",
            project_name=project_name,
        )
    except Exception as e:
        logger.error(f"Full Ingest pipeline failed: {e}", exc_info=True)
        update_job(job_id, status="failed", message=f"Full Ingest error: {str(e)}", log=str(e))
