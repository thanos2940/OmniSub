"""
OmbiSub API - Subtitle Translation Platform

FastAPI backend providing endpoints for project management, glossary creation,
and AI-powered subtitle translation using ADK agents.
"""

import os
import re
import json
import asyncio
from io import BytesIO
from zipfile import ZipFile
from datetime import datetime
from uuid import uuid4
from typing import List, Dict, Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env')

from utils.srt_parser import parse_srt, extract_text_only, reconstruct_srt
from utils import storage
from utils.cache_manager import invalidate_cache as _invalidate_translation_cache
from adk_agents import (
    create_cartographer_agent,
    create_research_agent,
    create_glossary_orchestrator,
    create_translation_pipeline,
    generate_glossary_adk,
    research_project_adk,
    translate_batch_adk,
    enhance_context_guide_adk
)
from adk_config import OmbiSubRunnerFactory, OmbiSubSessionManager
from google.adk.runners import types as adk_types


def get_api_key() -> Optional[str]:
    return os.environ.get("GOOGLE_API_KEY")


def has_api_key() -> bool:
    return get_api_key() is not None


def validate_api_key():
    if not has_api_key():
        raise HTTPException(status_code=400, detail={
            "error": "api_key_missing",
            "message": "Google Gemini API key not configured."
        })


# Initialize ADK services
adk_runner_factory = OmbiSubRunnerFactory()
adk_session_manager = OmbiSubSessionManager()

app = FastAPI(title="OmbiSub API", version="5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request Models

class CreateProjectRequest(BaseModel):
    name: str
    target_language: str = "English"
    parent_project: Optional[str] = None
    type: str = "show"


class ImportRequest(BaseModel):
    source_project: str
    import_glossary: bool = True
    import_context: bool = True


class ScanRequest(BaseModel):
    model: str = "gemini-flash-lite-latest"


class TranslateRequest(BaseModel):
    model: str = "gemini-flash-latest"
    enhance_glossary: bool = False


class BatchTranslateRequest(BaseModel):
    episode_names: List[str]
    model: str = "gemini-flash-latest"
    enhance_glossary: bool = False


class EnhanceGlossaryRequest(BaseModel):
    episode_names: Optional[List[str]] = None


class SaveEpisodeRequest(BaseModel):
    data: List[Dict]


class BatchDownloadRequest(BaseModel):
    episodes: Optional[List[str]] = None


class PipelineRequest(BaseModel):
    mode: str = "auto"  # "auto" or "step"
    skip_context: bool = False
    skip_glossary: bool = False
    episode_names: Optional[List[str]] = None
    model: str = "gemini-2.5-flash"
    context_model: Optional[str] = None
    glossary_model: Optional[str] = None
    translation_model: Optional[str] = None


class SettingsRequest(BaseModel):
    default_target_language: Optional[str] = None
    default_scan_model: Optional[str] = None
    default_translation_model: Optional[str] = None
    local_llm_base_url: Optional[str] = None


class ApiKeyRequest(BaseModel):
    api_key: str

class SimplePipelineRequest(BaseModel):
    model: str = "gemini-flash-latest"

class ConfirmContextRequest(BaseModel):
    context_guide: str

class ConfirmGlossaryRequest(BaseModel):
    glossary: Dict


class JobStatus(BaseModel):
    id: str
    status: str  # pending, running, completed, failed, paused, awaiting_review, cancelled
    progress: float
    message: str
    logs: List[str]
    result: Optional[Dict] = None
    prompt: Optional[str] = None
    ai_response: Optional[str] = None
    pipeline_stage: Optional[str] = None  # context, glossary, translate
    pipeline_mode: Optional[str] = None   # auto, step
    sub_jobs: Optional[List[str]] = None
    cancelled: bool = False


jobs: Dict[str, JobStatus] = {}


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring and container orchestration."""
    return {
        "status": "healthy",
        "service": "OmbiSub API",
        "version": "5.0",
        "adk_enabled": True,
        "api_key_configured": has_api_key()
    }


def create_job(job_type: str) -> str:
    job_id = f"{job_type}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    jobs[job_id] = JobStatus(
        id=job_id, status="pending", progress=0.0, 
        message="Initializing...", logs=[]
    )
    return job_id


def update_job(
    job_id: str,
    status: str = None,
    progress: float = None,
    message: str = None,
    log: str = None,
    result: Dict = None,
    prompt: str = None,
    ai_response: str = None
):
    if job_id not in jobs:
        return
    job = jobs[job_id]
    if status:
        job.status = status
    if progress is not None:
        job.progress = progress
    if message:
        job.message = message
    if log:
        job.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {log}")
    if result:
        job.result = result
    if prompt:
        job.prompt = prompt
    if ai_response:
        job.ai_response = ai_response


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]


@app.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[job_id]
    job.cancelled = True
    if job.status in ("running", "pending", "paused", "awaiting_review"):
        job.status = "cancelled"
        job.message = "Cancelled by user"
        job.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] Cancelled by user")
    return {"status": "cancelled"}


# API Key Management

@app.get("/api/config/api-key")
async def get_api_key_status():
    return {"has_key": has_api_key()}


@app.post("/api/config/api-key")
async def set_api_key(request: ApiKeyRequest):
    os.environ["GOOGLE_API_KEY"] = request.api_key
    return {"status": "success"}


@app.delete("/api/config/api-key")
async def delete_api_key():
    if "GOOGLE_API_KEY" in os.environ:
        del os.environ["GOOGLE_API_KEY"]
    return {"status": "success"}


# Settings

@app.get("/settings")
async def get_settings():
    return storage.load_global_config()


@app.post("/settings")
async def update_settings(settings: SettingsRequest):
    storage.save_global_config(settings.dict(exclude_unset=True))
    return {"status": "success"}


# Project Management

@app.get("/projects")
async def list_projects():
    return storage.list_projects()


@app.post("/projects")
async def create_project(request: CreateProjectRequest):
    metadata = {
        "show_name": request.name,
        "target_language": request.target_language,
        "parent_project": request.parent_project,
        "type": request.type,
        "glossary": {"terms": []},
        "context_guide": ""
    }
    return storage.create_project(request.name, metadata)


@app.get("/projects/{project_name}")
async def get_project(project_name: str):
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    return metadata


@app.put("/projects/{project_name}")
async def update_project(project_name: str, data: Dict):
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    # Detect if glossary or context_guide changed
    glossary_changed = "glossary" in data
    context_changed = "context_guide" in data
    metadata.update(data)
    if glossary_changed or context_changed:
        _invalidate_translation_cache(metadata)
    storage.save_project_metadata(project_name, metadata)
    await adk_session_manager.update_glossary(project_name, metadata.get("glossary", {"terms": []}))
    return metadata


@app.delete("/projects/{project_name}")
async def delete_project(project_name: str):
    if storage.delete_project(project_name):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Project not found")


@app.post("/projects/{project_name}/import")
async def import_project_data(project_name: str, request: ImportRequest):
    target = storage.load_project_metadata(project_name)
    source = storage.load_project_metadata(request.source_project)
    
    if not target or not source:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if request.import_glossary:
        target["glossary"] = source.get("glossary", {"terms": []})
    if request.import_context:
        target["context_guide"] = source.get("context_guide", "")
    
    _invalidate_translation_cache(target)
    storage.save_project_metadata(project_name, target)
    return target


# Episode Management

@app.get("/projects/{project_name}/episodes")
async def list_episodes(project_name: str):
    """List all episodes with metadata."""
    episode_names = storage.list_episodes(project_name)
    episodes = []

    for ep_name in episode_names:
        metadata = storage.load_episode_metadata(project_name, ep_name) or {}
        episodes.append({
            "name": ep_name,
            "season": metadata.get("season"),
            "line_count": metadata.get("line_count", 0),
            "translated": metadata.get("translated", False),
            "metadata": metadata
        })

    return episodes


@app.get("/projects/{project_name}/episodes/{episode_name}")
async def get_episode(project_name: str, episode_name: str):
    try:
        data = storage.load_episode(project_name, episode_name)
        if not data:
            raise HTTPException(status_code=404, detail="Episode not found")
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load episode: {str(e)}")


@app.post("/projects/{project_name}/episodes/{episode_name}/upload")
async def upload_episode(project_name: str, episode_name: str, file: UploadFile):
    validate_api_key()
    content = (await file.read()).decode('utf-8')
    parsed = parse_srt(content)
    
    if not parsed:
        raise HTTPException(status_code=400, detail="Invalid SRT file")
    
    storage.save_original_srt(project_name, episode_name, content)
    storage.save_episode(project_name, episode_name, parsed, {
        "original_filename": file.filename,
        "line_count": len(parsed)
    })
    
    return {"status": "success", "line_count": len(parsed)}


@app.post("/projects/{project_name}/episodes/{episode_name}/save")
async def save_episode(project_name: str, episode_name: str, request: SaveEpisodeRequest):
    storage.save_episode(project_name, episode_name, request.data)
    return {"status": "success"}


@app.delete("/projects/{project_name}/episodes/{episode_name}")
async def delete_episode(project_name: str, episode_name: str):
    if storage.delete_episode(project_name, episode_name):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Episode not found")


# Glossary Operations

@app.delete("/projects/{project_name}/glossary")
async def delete_glossary(project_name: str):
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    metadata["glossary"] = {"terms": []}
    storage.save_project_metadata(project_name, metadata)
    await adk_session_manager.update_glossary(project_name, {"terms": []})
    return {"status": "deleted"}


@app.post("/projects/{project_name}/glossary/create")
async def create_glossary(
    project_name: str,
    background_tasks: BackgroundTasks,
    model: str = "gemini-flash-lite-latest"
):
    validate_api_key()
    job_id = create_job("create_glossary")
    background_tasks.add_task(_process_create_glossary, job_id, project_name, [], model)
    return {"job_id": job_id}


@app.post("/projects/{project_name}/glossary/enhance")
async def enhance_glossary(
    project_name: str,
    background_tasks: BackgroundTasks,
    request: EnhanceGlossaryRequest = None,
    model: str = "gemini-flash-latest",
    enable_research: bool = False
):
    validate_api_key()
    episode_names = request.episode_names if request else None
    job_id = create_job("enhance_glossary")
    background_tasks.add_task(
        _process_enhance_glossary, job_id, project_name, 
        episode_names, model, enable_research
    )
    return {"job_id": job_id}


# Context Operations

@app.delete("/projects/{project_name}/context")
async def delete_context(project_name: str):
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    metadata["context_guide"] = ""
    storage.save_project_metadata(project_name, metadata)
    await adk_session_manager.update_context(project_name, "")
    return {"status": "deleted"}


@app.post("/projects/{project_name}/context/create")
async def create_context(
    project_name: str,
    background_tasks: BackgroundTasks,
    model: str = "gemini-flash-latest"
):
    validate_api_key()
    job_id = create_job("create_context")
    background_tasks.add_task(_process_create_context, job_id, project_name, [], model)
    return {"job_id": job_id}


@app.post("/projects/{project_name}/context/enhance")
async def enhance_context(
    project_name: str,
    background_tasks: BackgroundTasks,
    model: str = "gemini-flash-latest",
    with_research: bool = False
):
    validate_api_key()
    job_id = create_job("enhance_context")
    background_tasks.add_task(_process_enhance_context, job_id, project_name, model, with_research)
    return {"job_id": job_id}


# Translation Operations

@app.post("/projects/{project_name}/episodes/{episode_name}/scan")
async def scan_episode(
    project_name: str,
    episode_name: str,
    background_tasks: BackgroundTasks,
    request: ScanRequest
):
    validate_api_key()
    job_id = create_job("scan_episode")
    background_tasks.add_task(_process_scan_episode, job_id, project_name, episode_name, request.model)
    return {"job_id": job_id}


@app.post("/projects/{project_name}/episodes/{episode_name}/translate")
async def translate_episode(
    project_name: str,
    episode_name: str,
    background_tasks: BackgroundTasks,
    request: TranslateRequest
):
    validate_api_key()
    job_id = create_job("translate_episode")
    background_tasks.add_task(
        _process_batch_translation, job_id, project_name,
        [episode_name], request.model, request.enhance_glossary, False
    )
    return {"job_id": job_id}


@app.post("/projects/{project_name}/batch-translate")
async def batch_translate(
    project_name: str,
    background_tasks: BackgroundTasks,
    request: BatchTranslateRequest
):
    validate_api_key()
    job_id = create_job("batch_translate")
    background_tasks.add_task(
        _process_batch_translation, job_id, project_name,
        request.episode_names, request.model, request.enhance_glossary, False
    )
    return {"job_id": job_id}


# Pipeline Operations

@app.post("/projects/{project_name}/pipeline/start")
async def start_pipeline(
    project_name: str,
    background_tasks: BackgroundTasks,
    request: PipelineRequest
):
    validate_api_key()
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")

    job_id = create_job("pipeline")
    jobs[job_id].pipeline_mode = request.mode
    jobs[job_id].pipeline_stage = "context"
    jobs[job_id].sub_jobs = []

    background_tasks.add_task(
        _process_pipeline, job_id, project_name, request
    )
    return {"job_id": job_id}


@app.post("/projects/{project_name}/pipeline/{job_id}/continue")
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


@app.get("/api/config/models/local")
async def list_local_models(base_url: Optional[str] = None):
    """Fetch available models from a local OpenAI-compatible server."""
    from adk_agents.llm_factory import _resolve_local_base_url
    
    # Resolve URL from provided arg, then global config, then default
    if not base_url:
        global_config = storage.load_global_config()
        base_url = global_config.get("local_llm_base_url")
    
    url = _resolve_local_base_url(base_url)
    
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            root_url = f"{parsed.scheme}://{parsed.netloc}"
            
            # Alternative: if netloc is 'localhost', also try '127.0.0.1'
            alt_root_url = None
            if parsed.netloc.startswith('localhost:'):
                alt_root_url = f"{parsed.scheme}://127.0.0.1:{parsed.netloc.split(':')[1]}"
            elif parsed.netloc == 'localhost':
                alt_root_url = f"{parsed.scheme}://127.0.0.1"
            
            # Endpoints to try with different strategies
            check_urls = []
            
            base_urls = [root_url]
            if alt_root_url: base_urls.append(alt_root_url)
            
            # Add base-relative URL
            if url.endswith('/v1') or url.endswith('/v1/'):
                check_urls.append(url.rstrip('/') + "/models")
                if alt_root_url:
                    alt_url = url.replace(parsed.netloc, alt_root_url.split('://')[1])
                    check_urls.append(alt_url.rstrip('/') + "/models")
            else:
                check_urls.append(url.rstrip('/') + "/v1/models")
                
            for b in base_urls:
                check_urls.append(b + "/v1/models")
                check_urls.append(b + "/api/v1/models")
            
            # De-duplicate
            check_urls = list(dict.fromkeys(check_urls))
            
            all_errors = []
            for target_url in check_urls:
                try:
                    response = await client.get(target_url, timeout=2.0)
                    if response.status_code == 200:
                        data = response.json()
                        models = [
                            {"value": f"local/{m['id']}", "label": m['id']} 
                            for m in data.get("data", [])
                        ]
                        if models:
                            return {"models": models, "active_endpoint": target_url}
                except Exception as e:
                    all_errors.append(f"{target_url}: {str(e)}")
            
            return {
                "models": [], 
                "error": "Models not found at any common endpoint",
                "diagnostics": {
                    "tried_urls": check_urls,
                    "errors": all_errors
                }
            }
    except Exception as e:
        return {"models": [], "error": str(e)}


@app.post("/projects/{project_name}/simple-pipeline/start")
async def start_simple_pipeline(
    project_name: str,
    background_tasks: BackgroundTasks,
    request: SimplePipelineRequest
):
    validate_api_key()
    job_id = create_job("simple_pipeline")
    jobs[job_id].pipeline_mode = "simple"
    jobs[job_id].pipeline_stage = "analyze"
    background_tasks.add_task(_process_simple_pipeline, job_id, project_name, request.model)
    return {"job_id": job_id}


@app.post("/projects/{project_name}/simple-pipeline/{job_id}/confirm-context")
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


@app.post("/projects/{project_name}/simple-pipeline/{job_id}/confirm-glossary")
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


@app.get("/projects/{project_name}/episodes/{episode_name}/download")
async def download_single_episode(project_name: str, episode_name: str):
    metadata = storage.load_project_metadata(project_name)
    ep_data = storage.load_episode(project_name, episode_name)
    if not metadata or not ep_data:
        raise HTTPException(status_code=404, detail="Project or episode not found")
    
    target_lang = metadata.get("target_language", "English")
    lang_codes = {"Greek": "el", "English": "en", "Spanish": "es", "French": "fr"}
    lang_code = lang_codes.get(target_lang, "tr")
    
    srt_content = reconstruct_srt(ep_data["data"])
    filename = f"{episode_name}.{lang_code}.srt"
    
    return StreamingResponse(
        BytesIO(srt_content.encode('utf-8')),
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.post("/projects/{project_name}/batch-download")
async def batch_download(project_name: str, request: BatchDownloadRequest):
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    
    episodes = request.episodes or storage.list_episodes(project_name)
    target_lang = metadata.get("target_language", "en")
    
    lang_codes = {
        "Greek": "el", "English": "en", "Spanish": "es", "French": "fr",
        "German": "de", "Italian": "it", "Portuguese": "pt", "Russian": "ru",
        "Japanese": "ja", "Korean": "ko", "Chinese": "zh"
    }
    lang_code = lang_codes.get(target_lang, "en")
    
    buffer = BytesIO()
    with ZipFile(buffer, 'w') as zf:
        for ep_name in episodes:
            ep_data = storage.load_episode(project_name, ep_name)
            if not ep_data:
                continue
            srt_content = reconstruct_srt(ep_data["data"])
            filename = f"{ep_name}.{lang_code}.srt"
            zf.writestr(filename, srt_content)
    
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={project_name}_export.zip"}
    )


# Background Task Helpers

async def _gather_project_text(project_name: str, episode_names: List[str] = None) -> List[str]:
    """Gather all subtitle text from specified or all episodes."""
    episodes = episode_names or storage.list_episodes(project_name)
    all_text = []

    for ep_name in episodes:
        ep_data = storage.load_episode(project_name, ep_name)
        if ep_data and isinstance(ep_data, dict) and "data" in ep_data:
            data = ep_data["data"]
            if isinstance(data, list):
                all_text.extend(extract_text_only(data))

    return all_text


def _parse_json_response(response: str) -> Dict:
    """Parse JSON from model response, handling markdown code blocks."""
    clean = response.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    if clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    
    match = re.search(r'\{.*\}', clean, re.DOTALL)
    if match:
        clean = match.group(0)
    
    return json.loads(clean)


# Background Tasks

async def _process_create_glossary(job_id: str, project_name: str, episode_names: List[str], model: str):
    update_job(job_id, status="running", progress=0.0, message="Creating glossary...", log="Job started")
    try:
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return

        text_lines = await _gather_project_text(project_name, episode_names)
        enable_research = bool(not text_lines and metadata.get("show_name"))

        if not text_lines:
            update_job(job_id, message="No episodes, using research mode...", log="Research mode")

        result, debug_info = await generate_glossary_adk(
            text_lines,
            metadata.get("show_name", project_name),
            metadata.get("target_language", "English"),
            existing_glossary=None,
            model_name=model,
            enable_research=enable_research
        )

        metadata["glossary"] = result
        _invalidate_translation_cache(metadata)
        storage.save_project_metadata(project_name, metadata)
        await adk_session_manager.update_glossary(project_name, result)

        update_job(
            job_id, status="completed", progress=100.0,
            message="Glossary created", result=result,
            prompt=debug_info.get("prompt"),
            ai_response=debug_info.get("response")
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {str(e)}", log=str(e))


async def _process_enhance_glossary(
    job_id: str, project_name: str, episode_names: Optional[List[str]], 
    model: str, enable_research: bool
):
    update_job(job_id, status="running", progress=0.0, message="Enhancing glossary...", log="Job started")
    try:
        # 1. Load Project Metadata (Source of Truth)
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return
            
        target_language = metadata.get("target_language", "English")
        show_name = metadata.get("show_name", project_name)
        existing_terms = metadata.get("glossary", {}).get("terms", [])

        # 2. Gather Source Text (Subtitles)
        if episode_names:
            text_lines = await _gather_project_text(project_name, episode_names)
            update_job(job_id, message="Analyzing text...", log=f"Gathered {len(text_lines)} lines")
            # Take a larger sample for extraction to ensure context
            input_text = "\n".join(text_lines[:8000]) 
        else:
            # Research Mode (No files)
            text_lines = []
            enable_research = True # Force research if no text
            update_job(job_id, message="Researching...", log="Research mode (no files selected)")
            input_text = ""

        # 3. Run Research Step (Optional / Side-Channel)
        research_context = ""
        if enable_research:
            update_job(job_id, progress=20.0, message="Performing Research...", log=f"Researching '{show_name}'")
            
            # Create a dedicated runner for research
            research_agent = create_research_agent(model_name=model)
            research_session_id = f"research_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
            research_runner = adk_runner_factory.create_runner(research_agent, research_session_id)
            
            await adk_session_manager.session_service.create_session(
                session_id=research_session_id, user_id="default_user", app_name=f"OmbiSub_{research_session_id}"
            )

            research_prompt = f"Research the show '{show_name}'. Focus on official character names, locations, and specific terminology. Target Language for translation is: {target_language}."
            
            async for event in research_runner.run_async(
                user_id="default_user", session_id=research_session_id,
                new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=research_prompt)])
            ):
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            research_context += part.text
            
            update_job(job_id, progress=40.0, message="Research complete", log="Research data acquired")

        # 4. Run Extraction Step (Cartographer)
        update_job(job_id, progress=50.0, message="Extracting terms...", log=f"Target Language: {target_language}")
        
        cartographer = create_cartographer_agent(
            model_name=model, 
            target_language=target_language
        )
        
        session_id_unique = f"enhance_glossary_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        runner = adk_runner_factory.create_runner(cartographer, session_id_unique)
        
        await adk_session_manager.session_service.create_session(
            session_id=session_id_unique, user_id="default_user", app_name=f"OmbiSub_{session_id_unique}"
        )
        
        # Construct a robust prompt that injects research as context, but keeps source text primary
        if input_text:
            prompt = f"""TASK: Extract glossary terms from the SOURCE TEXT below.
            
TARGET LANGUAGE: {target_language}

RESEARCH CONTEXT (Use this for accurate names/definitions):
{research_context}

SOURCE TEXT (Subtitle Sample):
{input_text}

INSTRUCTIONS:
1. Extract terms from SOURCE TEXT.
2. Use RESEARCH CONTEXT to verify names and lore.
3. Translate/Transliterate strictly into {target_language}.
"""
        else:
            # Pure Research Mode
            prompt = f"""TASK: Create a glossary for '{show_name}' based on the research below.
            
TARGET LANGUAGE: {target_language}

RESEARCH FINDINGS:
{research_context}

INSTRUCTIONS:
1. Extract key terms from the research.
2. Translate/Transliterate strictly into {target_language}.
"""
        
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
        
        # 5. Parse and Merge Results
        try:
            new_glossary = _parse_json_response(response_text)
        except Exception as e:
            print(f"DEBUG: JSON parse error: {str(e)}")
            new_glossary = {"terms": [], "raw_output": response_text}
        
        # Deduplicate
        existing_names = {t.get("term", "").lower() for t in existing_terms}
        new_terms = [t for t in new_glossary.get("terms", []) if t.get("term", "").lower() not in existing_names]
        merged = {"terms": existing_terms + new_terms}
        
        # Save
        await adk_session_manager.update_glossary(project_name, merged)
        metadata["glossary"] = merged
        _invalidate_translation_cache(metadata)
        storage.save_project_metadata(project_name, metadata)
        
        update_job(
            job_id, status="completed", progress=100.0,
            message="Glossary enhanced",
            log=f"Added {len(new_terms)} new terms for {target_language}",
            result={"terms": new_terms}
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {str(e)}", log=str(e))


async def _process_create_context(job_id: str, project_name: str, episode_names: List[str], model: str):
    update_job(job_id, status="running", progress=0.0, message="Creating context...", log="Job started")
    try:
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return
        
        if episode_names:
            text_lines = await _gather_project_text(project_name, episode_names)
        else:
            text_lines = []
            update_job(job_id, message="Researching...", log="Research mode (no files selected)")

        update_job(job_id, progress=20.0, message="Researching...", log="Running research agent")
        research_data, research_debug = await research_project_adk(
            metadata.get("show_name", project_name),
            text_lines,
            metadata.get("target_language", "English"),
            model_name=model
        )
        
        update_job(job_id, progress=60.0, message="Generating instructions...", log="Running prompt engineer")
        enhanced_context, enhance_debug = await enhance_context_guide_adk(
            research_data.get("findings", ""),
            metadata.get("show_name", project_name),
            model_name=model
        )
        
        metadata["context_guide"] = enhanced_context
        _invalidate_translation_cache(metadata)
        storage.save_project_metadata(project_name, metadata)
        await adk_session_manager.update_context(project_name, enhanced_context)
        
        update_job(
            job_id, status="completed", progress=100.0,
            message="Context created",
            result={"context_guide": enhanced_context},
            ai_response=enhanced_context
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {str(e)}", log=str(e))


async def _process_enhance_context(job_id: str, project_name: str, model: str, with_research: bool):
    update_job(job_id, status="running", progress=0.0, message="Enhancing context...", log="Job started")
    try:
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return
        
        current_guide = metadata.get("context_guide", "")
        if not current_guide and not with_research:
            update_job(job_id, status="failed", message="No context to enhance")
            return

        if with_research:
            update_job(job_id, progress=20.0, message="Researching...", log="Fresh research")
            text_lines = await _gather_project_text(project_name)
            research_data, _ = await research_project_adk(
                metadata.get("show_name", project_name),
                text_lines,
                metadata.get("target_language", "English"),
                model_name=model
            )
            input_data = research_data.get("findings", "")
        else:
            input_data = current_guide

        update_job(job_id, progress=60.0, message="Generating instructions...")
        enhanced_guide, _ = await enhance_context_guide_adk(
            input_data,
            metadata.get("show_name", project_name),
            model_name=model
        )
        
        print(f"DEBUG: Enhanced guide length: {len(enhanced_guide)}")
        if not enhanced_guide:
            print("DEBUG: Enhanced guide is empty!")
        
        metadata["context_guide"] = enhanced_guide
        _invalidate_translation_cache(metadata)
        storage.save_project_metadata(project_name, metadata)
        await adk_session_manager.update_context(project_name, enhanced_guide)
        
        update_job(
            job_id, status="completed", progress=100.0,
            message="Context enhanced",
            result={"context_guide": enhanced_guide},
            ai_response=enhanced_guide
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {str(e)}", log=str(e))


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
        runner = adk_runner_factory.create_runner(agent, session_id_unique)
        
        # Create session explicitly
        await adk_session_manager.session_service.create_session(
            session_id=session_id_unique,
            user_id="default_user",
            app_name=f"OmbiSub_{session_id_unique}"
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
        
        try:
            new_glossary = _parse_json_response(response_text)
        except Exception:
            new_glossary = {"terms": [], "raw_output": response_text}
        
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


async def _process_simple_pipeline(job_id: str, project_name: str, model: str):
    try:
        update_job(job_id, status="running", progress=5.0, message="Analyzing project context...")
        metadata = storage.load_project_metadata(project_name)
        target_lang = metadata.get("target_language", "English")
        
        # Step 1: Analyze Context
        update_job(job_id, pipeline_stage="analyze", log="Starting context analysis")
        
        from adk_agents.llm_factory import is_local_model
        is_local = is_local_model(model)
        
        # Get a small text sample for context if needed
        episodes = storage.list_episodes(project_name)
        sample_text = []
        if episodes:
            first_ep = storage.load_episode(project_name, episodes[0])
            if first_ep:
                sample_text = extract_text_only(first_ep["data"])[:100]

        if not is_local:
            # Research + Enhance
            research_data, _ = await research_project_adk(project_name, sample_text, target_lang, model)
            context_guide, _ = await enhance_context_guide_adk(research_data.get("findings", ""), project_name, model)
        else:
            # Local model: Skip research, just generate guide from project name + sample
            prompt = f"Analyze the following project and create a translation style guide.\\nProject: {project_name}\\nTarget Language: {target_lang}\\n\\nSample Text:\\n" + "\\n".join(sample_text[:50])
            context_guide, _ = await enhance_context_guide_adk(prompt, project_name, model)

        update_job(job_id, progress=20.0, result={"context_guide": context_guide}, message="Awaiting context review...")
        await _pipeline_pause(job_id, "context", "Please review and confirm the generated context guide.")
        
        if jobs[job_id].cancelled: return

        # Step 2: Glossary Scan
        update_job(job_id, status="running", progress=25.0, pipeline_stage="glossary", message="Scanning episodes for glossary terms...")
        
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
            model_name=model, enable_research=not is_local
        )
        
        update_job(job_id, progress=50.0, result={"glossary": glossary_dict}, message="Awaiting glossary review...")
        await _pipeline_pause(job_id, "glossary", "Please review and confirm the generated glossary terms.")
        
        if jobs[job_id].cancelled: return

        # Step 3: Batch Translation
        update_job(job_id, status="running", progress=55.0, pipeline_stage="translate", message="Translating episodes...")
        
        # Reload metadata to get confirmed changes
        metadata = storage.load_project_metadata(project_name)
        
        # Reuse existing batch translation logic
        await _process_batch_translation(
            job_id, project_name, episodes, model, enhance_glossary_flag=False, is_simple_pipeline=True
        )

    except Exception as e:
        update_job(job_id, status="failed", message=f"Pipeline error: {str(e)}", log=str(e))


async def _process_batch_translation(
    job_id: str, project_name: str, episode_names: List[str], 
    model: str, enhance_glossary_flag: bool, is_simple_pipeline: bool = False
):
    update_job(job_id, status="running", progress=0.0, message="Starting translation...", log="Job started")
    try:
        # Load metadata for source of truth
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return

        glossary = metadata.get("glossary", {"terms": []})
        target_lang = metadata.get("target_language", "English")
        context_guide = metadata.get("context_guide", "")
        
        pipeline = create_translation_pipeline(
            project_name=project_name,
            target_language=target_lang,
            glossary=glossary,
            context_guide=context_guide,
            cartographer_model=model,
            translator_model=model,
            skip_glossary_step=not enhance_glossary_flag
        )
        
        session_id_unique = f"batch_translate_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        runner = adk_runner_factory.create_runner(pipeline, session_id_unique)
        
        # Create session explicitly (once per batch job)
        await adk_session_manager.session_service.create_session(
            session_id=session_id_unique,
            user_id="default_user",
            app_name=f"OmbiSub_{session_id_unique}"
        )
        
        results = {}
        total = len(episode_names)
        chunk_size = 200
        
        for idx, episode_name in enumerate(episode_names):
            # Check for cancellation before each episode
            if jobs[job_id].cancelled:
                update_job(job_id, status="cancelled", message="Cancelled by user")
                return

            update_job(job_id, message=f"Translating {episode_name}...", log=f"Processing {episode_name}")
            
            episode_data = storage.load_episode(project_name, episode_name)
            if not episode_data:
                results[episode_name] = "Not found"
                continue
            
            parsed_srt = episode_data["data"]
            text_lines = extract_text_only(parsed_srt)
            
            chunks = [text_lines[i:i + chunk_size] for i in range(0, len(text_lines), chunk_size)]
            translated_map = {}
            
            for chunk_idx, chunk in enumerate(chunks):
                # Check for cancellation before each chunk
                if jobs[job_id].cancelled:
                    update_job(job_id, status="cancelled", message="Cancelled by user")
                    return

                # Flatten multi-line subtitles: replace \n with <br> so each entry stays on one line
                chunk_input = "\n".join([f"{j+1}: {line.replace(chr(10), '<br>')}" for j, line in enumerate(chunk)])
                prompt = f"Translate these subtitles to {target_lang}:\n\n{chunk_input}"
                
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
                
                # Parse numbered output with regex for robustness
                for line in response_text.split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    match = re.match(r'^(\d+)\s*[:.]\s*(.*)', line)
                    if match:
                        local_idx = int(match.group(1)) - 1
                        if 0 <= local_idx < len(chunk):
                            global_idx = (chunk_idx * chunk_size) + local_idx
                            translated_text = match.group(2).strip()
                            # Restore <br> placeholders to real newlines
                            translated_text = translated_text.replace("<br>", "\n")
                            translated_map[global_idx] = translated_text
            
            for i, item in enumerate(parsed_srt):
                if i in translated_map:
                    item["translated"] = translated_map[i]
            
            storage.save_episode(project_name, episode_name, parsed_srt)
            results[episode_name] = "Success"
            
            progress = ((idx + 1) / total) * 100
            update_job(job_id, progress=progress, log=f"Completed {episode_name}")
        
        update_job(
            job_id, status="completed", progress=100.0,
            message="Translation completed",
            result=results
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {str(e)}", log=str(e))


# Pipeline Background Tasks

# Shared state for pipeline pause/resume coordination
_pipeline_resume_events: Dict[str, asyncio.Event] = {}


async def _process_pipeline(job_id: str, project_name: str, request: PipelineRequest):
    """Run the full translation pipeline: context → glossary → translate."""
    update_job(job_id, status="running", progress=0.0, message="Starting pipeline...", log="Pipeline started")

    try:
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return

        ctx_model = request.context_model or request.model
        gls_model = request.glossary_model or request.model
        tr_model = request.translation_model or request.model
        is_step = request.mode == "step"

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
            update_job(job_id, progress=25.0, log="Skipping context (already exists)")

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
            update_job(job_id, progress=50.0, log="Skipping glossary (already exists)")

        # --- Stage 3: Translation ---
        if jobs[job_id].cancelled:
            return

        jobs[job_id].pipeline_stage = "translate"
        episode_names = request.episode_names or storage.list_episodes(project_name)

        if not episode_names:
            update_job(job_id, status="completed", progress=100.0, message="Pipeline complete (no episodes to translate)")
            return

        update_job(job_id, progress=55.0, message=f"Translating {len(episode_names)} episodes...", log=f"Stage 3/3: Translate ({len(episode_names)} episodes)")

        # Reuse the existing batch translation logic
        glossary = metadata.get("glossary", {"terms": []})
        target_lang = metadata.get("target_language", "English")
        context_guide = metadata.get("context_guide", "")

        pipeline = create_translation_pipeline(
            project_name=project_name,
            target_language=target_lang,
            glossary=glossary,
            context_guide=context_guide,
            cartographer_model=tr_model,
            translator_model=tr_model,
            skip_glossary_step=True  # Already built glossary in stage 2
        )

        session_id_unique = f"pipeline_translate_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        runner = adk_runner_factory.create_runner(pipeline, session_id_unique)
        await adk_session_manager.session_service.create_session(
            session_id=session_id_unique, user_id="default_user",
            app_name=f"OmbiSub_{session_id_unique}"
        )

        chunk_size = 200
        total = len(episode_names)

        for idx, episode_name in enumerate(episode_names):
            if jobs[job_id].cancelled:
                return

            update_job(job_id, message=f"Translating {episode_name}...", log=f"Translating {episode_name}")

            episode_data = storage.load_episode(project_name, episode_name)
            if not episode_data:
                continue

            parsed_srt = episode_data["data"]
            text_lines_ep = extract_text_only(parsed_srt)
            chunks = [text_lines_ep[i:i + chunk_size] for i in range(0, len(text_lines_ep), chunk_size)]
            translated_map = {}

            for chunk_idx, chunk in enumerate(chunks):
                if jobs[job_id].cancelled:
                    return

                chunk_input = "\n".join([f"{j+1}: {line.replace(chr(10), '<br>')}" for j, line in enumerate(chunk)])
                prompt = f"Translate these subtitles to {target_lang}:\n\n{chunk_input}"

                response_text = ""
                async for event in runner.run_async(
                    user_id="default_user", session_id=session_id_unique,
                    new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=prompt)])
                ):
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text:
                                response_text += part.text

                for line in response_text.split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    match = re.match(r'^(\d+)\s*[:.]\s*(.*)', line)
                    if match:
                        local_idx = int(match.group(1)) - 1
                        if 0 <= local_idx < len(chunk):
                            global_idx = (chunk_idx * chunk_size) + local_idx
                            translated_text = match.group(2).strip().replace("<br>", "\n")
                            translated_map[global_idx] = translated_text

            for i, item in enumerate(parsed_srt):
                if i in translated_map:
                    item["translated"] = translated_map[i]

            storage.save_episode(project_name, episode_name, parsed_srt)

            # Map progress from 55% to 100%
            progress = 55.0 + ((idx + 1) / total) * 45.0
            update_job(job_id, progress=progress, log=f"Completed {episode_name}")

        update_job(
            job_id, status="completed", progress=100.0,
            message="Pipeline complete — all episodes translated"
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

def _select_golden_ratio_files(episodes: List[str]) -> List[str]:
    """
    Select a few representative episodes from a list using golden ratio offsets.
    Always includes first and last.
    """
    if not episodes:
        return []
    if len(episodes) <= 5:
        return episodes
        
    n = len(episodes)
    phi = 0.61803398875
    
    # Indices to pick
    indices = {0, n - 1}
    
    # Primary golden sections
    indices.add(round((n - 1) * (1 - phi))) # ~0.382
    indices.add(round((n - 1) * phi))       # ~0.618
    
    # For very large series, add secondary golden sections
    if n > 12:
        indices.add(round((n - 1) * (1 - phi**2))) # ~0.146
        indices.add(round((n - 1) * (1 - (1-phi)**2))) # ~0.854

    sorted_indices = sorted(list(indices))
    return [episodes[i] for i in sorted_indices if 0 <= i < n]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
