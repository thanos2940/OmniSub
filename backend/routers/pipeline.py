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
from utils.jobs_manager import create_job, update_job, jobs
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


async def _monitor_batch_queue_progress(job_id: str, item_ids: List[str]):
    import sqlite3
    from utils.translation_queue import TranslationQueue
    queue = TranslationQueue()
    
    total = len(item_ids)
    if total == 0:
        update_job(job_id, status="completed", progress=100.0, message="Batch complete (0 items enqueued).")
        return
        
    update_job(job_id, status="running", progress=0.0, message=f"Enqueued {total} episodes in the translation queue.", log=f"Monitoring progress for {total} enqueued items.")
        
    while True:
        await asyncio.sleep(2.0)
        
        if job_id not in jobs:
            break
            
        if jobs[job_id].cancelled:
            # Cancel all pending/running enqueued items in queue
            for item_id in item_ids:
                queue.cancel(item_id)
            update_job(job_id, status="cancelled", message="Batch cancelled by user.", log="Batch translation cancelled by user.")
            return
            
        # Get all queue items and status
        all_items = queue.get_all(limit=1000)
        items_by_id = {item["id"]: item for item in all_items}
        
        completed = 0
        failed = 0
        running = 0
        pending = 0
        cancelled = 0
        paused = 0
        
        completed_names = []
        failed_names = []
        
        for item_id in item_ids:
            item = items_by_id.get(item_id)
            if not item:
                try:
                    conn = sqlite3.connect(queue.db_path)
                    conn.row_factory = sqlite3.Row
                    cursor = conn.execute("SELECT status, episode_name FROM translation_queue WHERE id = ?", (item_id,))
                    db_row = cursor.fetchone()
                    if db_row:
                        item = dict(db_row)
                except Exception:
                    pass
            
            if not item:
                completed += 1
                continue
                
            status = item.get("status", "pending")
            ep_name = item.get("episode_name", "")
            
            if status == "completed":
                completed += 1
                completed_names.append(ep_name)
            elif status == "failed":
                failed += 1
                failed_names.append(ep_name)
            elif status == "running":
                running += 1
            elif status == "paused_daily_limit":
                paused += 1
            elif status == "cancelled":
                cancelled += 1
            else:
                pending += 1
                
        progress = ((completed + failed + cancelled) / total) * 100.0
        
        msg_parts = [f"{completed}/{total} complete"]
        if running > 0:
            msg_parts.append(f"{running} running")
        if pending > 0:
            msg_parts.append(f"{pending} pending")
        if failed > 0:
            msg_parts.append(f"{failed} failed")
        if paused > 0:
            msg_parts.append(f"{paused} paused (limits)")
        if cancelled > 0:
            msg_parts.append(f"{cancelled} cancelled")
            
        message = "Batch progress: " + ", ".join(msg_parts)
        
        update_job(
            job_id, 
            progress=progress, 
            message=message, 
            completed_episodes=completed_names,
            failed_episodes=failed_names
        )
        
        if completed + failed + cancelled == total:
            final_status = "completed"
            if failed == total:
                final_status = "failed"
            elif failed > 0:
                final_status = "partial"
                
            update_job(
                job_id, 
                status=final_status, 
                progress=100.0,
                message=f"Batch complete. {completed} completed, {failed} failed.", 
                log=f"Batch complete: {completed} success, {failed} fail."
            )
            return


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
    
    options = {
        "model": model,
        "enhance_glossary": request.enhance_glossary,
        "force": request.force,
        "translation_type": request.translation_type,
        "temperature": storage.get_project_setting(metadata, "temperature") or global_config.get("temperature"),
        "top_k": storage.get_project_setting(metadata, "top_k") or global_config.get("top_k"),
        "top_p": storage.get_project_setting(metadata, "top_p") or global_config.get("top_p")
    }
    
    from services.queue_service import enqueue_translation, PRIORITY_MANUAL
    item_id = enqueue_translation(project_name, episode_name, PRIORITY_MANUAL, options)
    
    return {"job_id": f"bg_{item_id}"}


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
    
    from services.queue_service import enqueue_translation, PRIORITY_MANUAL
    enqueued_ids = []
    for ep_name in request.episode_names:
        item_id = enqueue_translation(project_name, ep_name, PRIORITY_MANUAL, options)
        enqueued_ids.append(item_id)
        
    job_id = create_job("batch_translate", project_name=project_name)
    background_tasks.add_task(_monitor_batch_queue_progress, job_id, enqueued_ids)
    
    return {"job_id": job_id}


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
    is_local = (model or "").startswith("local/")

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

        use_whole = (not is_local) and mode != "scenes" and (mode == "whole" or n_lines <= whole_cap)
        if use_whole:
            calls = 1
        else:
            calls = max(1, len(build_scene_ast(rows, max_lines_per_scene=max_lines)))
        total_calls += calls
        total_in_tokens += calls * bible_tokens + src_tokens
        total_out_tokens += out_tokens

    # Cost (cloud only; thinking is disabled for translation by default — D2).
    from utils.telemetry import _price_for
    pin, pout = _price_for(model or "")
    cost = (total_in_tokens * pin + total_out_tokens * pout) / 1_000_000.0
    batch_cost = cost * 0.5  # Batch API discount

    estimate = translation_rate_limiter.estimate_requests_from_scenes(total_calls)
    return {
        "total_episodes": len(episodes),
        "total_lines": total_lines,
        "estimated_api_calls": total_calls,
        "request_mode": "whole-episode" if (not is_local and mode != "scenes") else "scenes",
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

    global_config = storage.load_global_config()
    translation_model = request.model or resolve_model("translation", metadata)
    context_model = request.context_model or resolve_model("context", metadata)
    glossary_model = request.glossary_model or resolve_model("glossary", metadata)

    job_id = create_job("auto_translate", project_name=project_name)
    jobs[job_id].pipeline_mode = "auto"
    jobs[job_id].pipeline_stage = "context"
    background_tasks.add_task(
        _process_auto_translate,
        job_id, project_name, 
        translation_model, context_model, glossary_model,
        request.episode_names, request.skip_context, request.skip_glossary,
        request.enhance_glossary,
    )
    return {"job_id": job_id}


@router.post("/projects/{project_name}/simple-pipeline/start")
async def start_simple_pipeline(
    project_name: str,
    background_tasks: BackgroundTasks,
    request: SimplePipelineRequest
):
    validate_api_key()
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
        glossary_model
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
):
    """Fully automatic pipeline: context → glossary → translate, no pauses."""
    update_job(job_id, status="running", progress=0.0, message="Starting auto-translate...", log="Auto pipeline started")

    try:
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return

        from adk_agents.llm_factory import is_local_model

        # ── Stage 1: Context guide ────────────────────────────────────────
        has_context = bool(metadata.get("context_guide", "").strip())
        if not skip_context and not has_context:
            update_job(job_id, progress=5.0, message="Generating context guide...", log="Stage 1/3: context (missing, creating)")
            jobs[job_id].pipeline_stage = "context"

            text_lines = await _gather_project_text(project_name, episode_names)
            enable_research = not is_local_model(context_model)

            if enable_research:
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
            else:
                # Local model: derive minimal context from show name only
                context_guide, _ = await enhance_context_guide_adk(
                    f"Show: {metadata.get('show_name', project_name)}",
                    metadata.get("show_name", project_name),
                    target_language=metadata.get("target_language", "English"),
                    model_name=context_model,
                )

            metadata["context_guide"] = context_guide
            _invalidate_translation_cache(metadata)
            storage.save_project_metadata(project_name, metadata)
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
            enable_research = not is_local_model(glossary_model)

            result, _ = await generate_glossary_adk(
                text_lines,
                metadata.get("show_name", project_name),
                metadata.get("target_language", "English"),
                existing_glossary=metadata.get("glossary"),
                model_name=glossary_model,
                enable_research=enable_research,
            )
            metadata["glossary"] = result
            _invalidate_translation_cache(metadata)
            storage.save_project_metadata(project_name, metadata)
            update_job(job_id, progress=50.0, log=f"Glossary: {len(result.get('terms', []))} terms")
        else:
            reason = "already exists" if has_glossary else "skipped by request"
            update_job(job_id, progress=50.0, log=f"Glossary: {reason}")

        if jobs[job_id].cancelled:
            update_job(job_id, status="cancelled", message="Cancelled")
            return

        # ── Stage 3: Translate ───────────────────────────────────────────
        jobs[job_id].pipeline_stage = "translate"
        episodes = episode_names or storage.list_episodes(project_name)
        if not episodes:
            update_job(job_id, status="completed", progress=100.0, message="No episodes to translate")
            return

        update_job(job_id, progress=52.0, message=f"Translating {len(episodes)} episodes...", log=f"Stage 3/3: {len(episodes)} episodes")

        # Reload metadata so translation sees any freshly-saved glossary/context
        metadata = storage.load_project_metadata(project_name)
        options = {
            "model": translation_model,
            "enhance_glossary": enhance_glossary,
            "context_model": context_model,
            "glossary_model": glossary_model
        }
        from services.queue_service import enqueue_translation, PRIORITY_MANUAL
        for ep_name in episodes:
            enqueue_translation(project_name, ep_name, PRIORITY_MANUAL, options)
            
        update_job(job_id, status="completed", progress=100.0, message=f"Enqueued {len(episodes)} episodes for translation in the queue.")

    except Exception as e:
        update_job(job_id, status="failed", message=f"Auto-translate error: {str(e)}", log=str(e))


async def _process_simple_pipeline(
    job_id: str, 
    project_name: str, 
    translation_model: str,
    context_model: str,
    glossary_model: str
):
    try:
        update_job(job_id, status="running", progress=5.0, message="Analyzing project context...")
        metadata = storage.load_project_metadata(project_name)
        target_lang = metadata.get("target_language", "English")
        
        # Step 1: Analyze Context
        jobs[job_id].pipeline_stage = "analyze"
        update_job(job_id, log="Starting context analysis")
        
        from adk_agents.llm_factory import is_local_model
        is_local = is_local_model(context_model)
        
        # Get a small text sample for context if needed
        episodes = storage.list_episodes(project_name)
        sample_text = []
        if episodes:
            first_ep = storage.load_episode(project_name, episodes[0])
            if first_ep:
                sample_text = extract_text_only(first_ep["data"])[:100]

        if not is_local:
            # Research + Enhance
            research_data, _ = await research_project_adk(project_name, sample_text, target_lang, context_model)
            context_guide, _ = await enhance_context_guide_adk(
                research_data.get("findings", ""),
                project_name,
                target_language=target_lang,
                model_name=context_model
            )
        else:
            # Local model: Skip research, just generate guide from project name + sample
            prompt = f"Analyze the following project and create a translation style guide.\nProject: {project_name}\nTarget Language: {target_lang}\n\nSample Text:\n" + "\n".join(sample_text[:50])
            context_guide, _ = await enhance_context_guide_adk(
                prompt,
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
            model_name=glossary_model, enable_research=not is_local_model(glossary_model)
        )
        
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
            "enhance_glossary": False,
            "context_model": context_model,
            "glossary_model": glossary_model
        }
        from services.queue_service import enqueue_translation, PRIORITY_MANUAL
        for ep_name in episodes:
            enqueue_translation(project_name, ep_name, PRIORITY_MANUAL, options)
            
        update_job(job_id, status="completed", progress=100.0, message=f"Enqueued {len(episodes)} episodes for translation in the queue.")

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
                await _pipeline_pause(job_id, "context", "Review the context guide before continuing.")
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

            metadata["glossary"] = result
            _invalidate_translation_cache(metadata)
            storage.save_project_metadata(project_name, metadata)
            await adk_session_manager.update_glossary(project_name, result)

            update_job(job_id, progress=50.0, message="Glossary built", log=f"Glossary: {len(result.get('terms', []))} terms")

            if is_step:
                await _pipeline_pause(job_id, "glossary", "Review the glossary before continuing.")
                if jobs[job_id].cancelled:
                    return
                metadata = storage.load_project_metadata(project_name)
        else:
            update_job(job_id, progress=50.0, log="Skipping glossary stage (skipped by request)")

        # --- Stage 3: Translation ---
        if jobs[job_id].cancelled:
            return

        jobs[job_id].pipeline_stage = "translate"
        episode_names = request.episode_names or storage.list_episodes(project_name)

        if not episode_names:
            update_job(job_id, status="completed", progress=100.0, message="Pipeline complete (no episodes to translate)")
            return

        update_job(job_id, progress=55.0, message=f"Translating {len(episode_names)} episodes...", log=f"Stage 3/3: Translate ({len(episode_names)} episodes)")

        options = {
            "model": tr_model,
            "enhance_glossary": False,
            "context_model": ctx_model,
            "glossary_model": gls_model
        }
        from services.queue_service import enqueue_translation, PRIORITY_MANUAL
        for ep_name in episode_names:
            enqueue_translation(project_name, ep_name, PRIORITY_MANUAL, options)
            
        update_job(job_id, status="completed", progress=100.0, message=f"Enqueued {len(episode_names)} episodes for translation in the queue.")

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

        # Stage 1: Probe containers and extract embedded ASS tracks
        update_job(job_id, progress=10.0, message="Stage 1/4: Checking video containers for embedded ASS tracks...", log="Checking media files for ASS streams")
        from integrations.embedded_subs import probe_subtitle_tracks, select_track, extract_subtitle_track, format_ass_sidecar_path, parse_keywords
        from utils.source_clean import import_and_clean_srt

        ep_names = storage.list_episodes(project_name)
        extracted_count = 0

        for ep_name in ep_names:
            if jobs[job_id].cancelled:
                return
            ep_meta = storage.load_episode_metadata(project_name, ep_name) or {}
            orig_fmt = (ep_meta.get("original_format") or ep_meta.get("original_extension") or "").lower()
            media_path = ep_meta.get("arr_media_path") or ep_meta.get("bazarr_media_path")

            if media_path and orig_fmt not in ("ass", "ssa") and os.path.isfile(media_path):
                tracks = probe_subtitle_tracks(media_path, config=global_config)
                if tracks:
                    proj_settings = metadata.get("settings", {})
                    keywords = parse_keywords(proj_settings.get("embedded_deprioritize_keywords") or global_config.get("embedded_deprioritize_keywords"))
                    chosen_track = select_track(tracks, "ja", keywords=keywords)
                    if chosen_track and chosen_track.is_ass:
                        out_path = format_ass_sidecar_path(media_path)
                        res = extract_subtitle_track(media_path, chosen_track.index, out_path, config=global_config)
                        if res.success and res.extracted_content:
                            import_and_clean_srt(
                                project_name,
                                ep_name,
                                res.extracted_content,
                                filename=os.path.basename(out_path),
                                fingerprint=res.fingerprint,
                                extra_metadata={
                                    "embedded_extracted": True,
                                    "embedded_track": chosen_track.to_dict(),
                                    "original_format": "ass"
                                }
                            )
                            extracted_count += 1

        update_job(job_id, progress=30.0, message=f"Stage 1 complete: Demuxed {extracted_count} ASS track(s).", log=f"Demuxed {extracted_count} ASS track(s)")

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
        untranslated = []
        for ep_name in ep_names:
            ep_meta = storage.load_episode_metadata(project_name, ep_name) or {}
            is_trans = ep_meta.get("translated", False)
            if not is_trans:
                untranslated.append(ep_name)
                enqueue_translation(
                    project_name,
                    ep_name,
                    PRIORITY_MANUAL,
                    {"model": tr_model, "context_model": ctx_model, "glossary_model": gls_model}
                )

        update_job(
            job_id,
            status="completed",
            progress=100.0,
            message=f"Full Ingest complete. Demuxed {extracted_count} ASS track(s), enqueued {len(untranslated)} episode(s) for translation.",
            log=f"Pipeline complete: {len(untranslated)} enqueued"
        )
    except Exception as e:
        logger.error(f"Full Ingest pipeline failed: {e}", exc_info=True)
        update_job(job_id, status="failed", message=f"Full Ingest error: {str(e)}", log=str(e))
