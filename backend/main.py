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

from utils.srt_parser import parse_srt, extract_text_only, reconstruct_srt, build_scene_ast, get_scene_context_hint
from utils.subtitle_fixer import fix_subtitles_with_se
from utils.llm_utils import strip_reasoning_blocks, parse_glossary_from_text, parse_translations_from_text

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
from adk_agents.translator_agent import build_translation_prompt
from adk_config import OmbiSubRunnerFactory, OmbiSubSessionManager, get_ephemeral_session_service
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
    concurrent_scenes: Optional[int] = None
    max_lines_per_scene: Optional[int] = None
    temperature: Optional[float] = None
    top_k: Optional[int] = None
    top_p: Optional[float] = None


class ApiKeyRequest(BaseModel):
    api_key: str

class SimplePipelineRequest(BaseModel):
    model: str = "gemini-flash-latest"

class ConfirmContextRequest(BaseModel):
    context_guide: str

class ConfirmGlossaryRequest(BaseModel):
    glossary: Dict


class MergeTranslationRequest(BaseModel):
    selected_lines: Dict[str, str]  # global_index -> new translation text

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
    scene_status: Optional[Dict[str, str]] = None  # {scene_id: status}
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
    ai_response: str = None,
    scene_status: Dict[str, str] = None
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
    if scene_status:
        if not job.scene_status:
            job.scene_status = {}
        job.scene_status.update(scene_status)


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


from utils.token_helper import estimate_tokens, estimate_glossary_tokens, get_base_instruction_tokens

@app.get("/projects/{project_name}/token-summary")
async def get_project_token_summary(project_name: str):
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    
    glossary = metadata.get("glossary", {"terms": []})
    context = metadata.get("context_guide", "")
    
    glossary_tokens = estimate_glossary_tokens(glossary)
    context_tokens = estimate_tokens(context)
    base_instructions = get_base_instruction_tokens()
    
    return {
        "glossary_tokens": glossary_tokens,
        "context_tokens": context_tokens,
        "base_instruction_tokens": base_instructions,
        "total_static_tokens": glossary_tokens + context_tokens + base_instructions
    }


@app.get("/projects/{project_name}")
async def get_project(project_name: str):
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    return metadata


class TestTranslationRequest(BaseModel):
    lines: List[str]
    temperature: Optional[float] = None
    top_k: Optional[int] = None
    top_p: Optional[float] = None
    model_name: Optional[str] = None

@app.post("/projects/{project_name}/test-translation")
async def test_project_translation(project_name: str, request: TestTranslationRequest):
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    
    glossary = metadata.get("glossary", {"terms": []})
    target_lang = metadata.get("target_language", "English")
    context_guide = metadata.get("context_guide", "")
    
    # Use specified model or default from settings
    model_name = request.model_name or metadata.get("settings", {}).get("translation_model", "gemini-2.5-flash-lite-latest")
    
    from adk_agents.translator_agent import create_translator_agent
    from google.adk.runners import types as adk_types
    import re
    
    agent = create_translator_agent(
        model_name=model_name,
        glossary=glossary,
        target_language=target_lang,
        context_guide=context_guide,
        temperature=request.temperature,
        top_k=request.top_k,
        top_p=request.top_p
    )
    
    session_id = f"test_{uuid4().hex[:8]}"
    runner = adk_runner_factory.create_runner(agent, session_id)
    
    # app_name must match what the runner factory uses: f"OmbiSub_{session_id}"
    app_name = f"OmbiSub_{session_id}"
    await adk_session_manager.session_service.create_session(
        session_id=session_id, user_id="test_user", app_name=app_name
    )
    
    # Format same as subtitle scene
    chunk_input = "\n".join([f"{j+1}: {line}" for j, line in enumerate(request.lines)])
    prompt = f"Translate these subtitles to {target_lang}:\n\n{chunk_input}"
    
    response_text = ""
    async for event in runner.run_async(
        user_id="test_user",
        session_id=session_id,
        new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=prompt)])
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    response_text += part.text

    # Extract reasoning (Support standard tags OR Gemma <|channel>thought format)
    reasoning = ""
    reasoning_match = re.search(
        r'(?:<internal_reasoning>|<\|channel>thought\s*)(.*?)(?:</internal_reasoning>|<channel\|>)',
        response_text,
        re.DOTALL
    )
    if reasoning_match:
        reasoning = reasoning_match.group(1).strip()
    
    # Extract lines
    lines_out = []
    line_matches = re.finditer(r'<line\s+index=["\']?(\d+)["\']?>\s*(.*?)\s*(?:</line>|(?=<line)|$)', response_text, re.DOTALL)
    for match in line_matches:
        lines_out.append({
            "index": int(match.group(1)),
            "text": match.group(2).strip()
        })
        
    return {
        "reasoning": reasoning,
        "translations": lines_out,
        "raw": response_text if not lines_out else None
    }


@app.put("/projects/{project_name}")
async def update_project(project_name: str, data: Dict, background_tasks: BackgroundTasks):
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
        
    old_glossary = metadata.get("glossary", {}).get("terms", [])
    
    # Detect if glossary or context_guide changed
    glossary_changed = "glossary" in data
    context_changed = "context_guide" in data
    metadata.update(data)
    
    affected_terms = set()
    if glossary_changed:
        new_glossary = metadata.get("glossary", {}).get("terms", [])
        old_map = {t.get("term", "").lower(): t for t in old_glossary if t.get("term")}
        
        for nt in new_glossary:
            term = nt.get("term", "")
            if not term:
                continue
            key = term.lower()
            if key not in old_map:
                affected_terms.add(term)
            else:
                ot = old_map[key]
                if (ot.get("translation") != nt.get("translation") or 
                    ot.get("gender") != nt.get("gender") or
                    ot.get("case_sensitive") != nt.get("case_sensitive") or
                    ot.get("keep_original") != nt.get("keep_original")):
                    affected_terms.add(term)
                    
    if glossary_changed or context_changed:
        _invalidate_translation_cache(metadata)
        
    storage.save_project_metadata(project_name, metadata)
    await adk_session_manager.update_glossary(project_name, metadata.get("glossary", {"terms": []}))
    
    if affected_terms:
        # Prioritize project-specific translation model, then fallback to global default
        project_settings = metadata.get("settings", {})
        model = project_settings.get("translation_model")
        
        if not model:
            global_config = storage.load_global_config()
            model = global_config.get("default_translation_model", "gemini-flash-latest")
            
        job_id = create_job("retranslate_invalidated")
        background_tasks.add_task(
            _process_targeted_retranslation,
            job_id,
            project_name,
            list(affected_terms),
            model
        )
        metadata["job_id"] = job_id
        
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


@app.post("/projects/{project_name}/episodes/{episode_name}/clear")
async def clear_episode_translation(project_name: str, episode_name: str):
    data = storage.load_episode(project_name, episode_name)
    if not data:
        raise HTTPException(status_code=404, detail="Episode not found")
    
    for line in data["data"]:
        line["translated"] = ""
    
    metadata = storage.load_episode_metadata(project_name, episode_name) or {}
    metadata["translated"] = False
    
    storage.save_episode(project_name, episode_name, data["data"], metadata)
    return {"status": "cleared"}


@app.post("/projects/{project_name}/episodes/{episode_name}/retranslate")
async def retranslate_episode(
    project_name: str, 
    episode_name: str, 
    background_tasks: BackgroundTasks,
    request: TranslateRequest
):
    validate_api_key()
    job_id = create_job("retranslate_compare")
    background_tasks.add_task(
        _process_retranslation_for_comparison, job_id, project_name,
        episode_name, request.model
    )
    return {"job_id": job_id}


@app.post("/projects/{project_name}/episodes/{episode_name}/merge")
async def merge_episode_translation(project_name: str, episode_name: str, request: MergeTranslationRequest):
    data = storage.load_episode(project_name, episode_name)
    if not data:
        raise HTTPException(status_code=404, detail="Episode not found")
    
    for idx_str, text in request.selected_lines.items():
        try:
            idx = int(idx_str)
            if 0 <= idx < len(data["data"]):
                data["data"][idx]["translated"] = text
        except (ValueError, TypeError):
            continue
    
    metadata = storage.load_episode_metadata(project_name, episode_name) or {}
    metadata["translated"] = True
    
    storage.save_episode(project_name, episode_name, data["data"], metadata)
    
    # Auto-export if enabled
    project_metadata = storage.load_project_metadata(project_name)
    if project_metadata:
        project_settings = project_metadata.get("settings", {})
        if project_settings.get("auto_export_enabled") and project_settings.get("auto_export_dir"):
            export_dir = project_settings.get("auto_export_dir")
            try:
                os.makedirs(export_dir, exist_ok=True)
                target_lang = project_metadata.get("target_language", "English")
                lang_codes = {
                    "Greek": "el", "English": "en", "Spanish": "es", 
                    "French": "fr", "German": "de", "Italian": "it", 
                    "Portuguese": "pt", "Russian": "ru", "Japanese": "ja", 
                    "Korean": "ko", "Chinese": "zh"
                }
                lang_code = lang_codes.get(target_lang, "en")
                
                orig_filename = data.get("metadata", {}).get("original_filename")
                if orig_filename:
                    if orig_filename.endswith(".en.srt"):
                        filename = orig_filename.replace(".en.srt", f".{lang_code}.srt")
                    elif orig_filename.endswith(".srt"):
                        filename = orig_filename.replace(".srt", f".{lang_code}.srt")
                    else:
                        filename = f"{orig_filename}.{lang_code}.srt"
                else:
                    filename = f"{episode_name}.{lang_code}.srt"
                    
                export_path = os.path.join(export_dir, filename)
                srt_content = reconstruct_srt(data["data"])
                with open(export_path, 'w', encoding='utf-8') as f:
                    f.write(srt_content)
            except Exception as export_err:
                print(f"Failed to auto-export after merge: {export_err}")

    return {"status": "merged"}


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


@app.get("/api/models")
async def list_all_models(base_url: Optional[str] = None):
    """Unified model registry — returns Gemini cloud models and discovered local models.

    Response shape:
    {
      "gemini": [{"value": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "group": "gemini"}, ...],
      "local":  [{"value": "local/my-model", "label": "my-model", "group": "local"}, ...],
      "local_endpoint": "http://localhost:1234/v1",
      "local_online": true | false
    }
    """
    gemini_models = [
        {"value": "gemini-2.5-flash",       "label": "Gemini 2.5 Flash",       "group": "gemini"},
        {"value": "gemini-2.5-flash-lite",   "label": "Gemini 2.5 Flash Lite",  "group": "gemini"},
        {"value": "gemini-2.5-pro",          "label": "Gemini 2.5 Pro",         "group": "gemini"},
        {"value": "gemini-flash-latest",     "label": "Gemini Flash (latest)",  "group": "gemini"},
        {"value": "gemini-flash-lite-latest","label": "Gemini Flash Lite",      "group": "gemini"},
    ]

    from adk_agents.llm_factory import _resolve_local_base_url
    global_config = storage.load_global_config()
    resolved_url = _resolve_local_base_url(base_url or global_config.get("local_llm_base_url"))

    local_models: list = []
    local_online = False

    try:
        import httpx
        from urllib.parse import urlparse
        parsed = urlparse(resolved_url)
        candidates = [resolved_url.rstrip("/") + "/models"]
        if not resolved_url.rstrip("/").endswith("/v1"):
            candidates.append(resolved_url.rstrip("/") + "/v1/models")

        async with httpx.AsyncClient(timeout=2.0) as client:
            for target in candidates:
                try:
                    resp = await client.get(target)
                    if resp.status_code == 200:
                        data = resp.json()
                        local_models = [
                            {"value": f"local/{m['id']}", "label": m["id"], "group": "local"}
                            for m in data.get("data", [])
                        ]
                        local_online = True
                        break
                except Exception:
                    pass
    except Exception:
        pass

    return {
        "gemini": gemini_models,
        "local": local_models,
        "local_endpoint": resolved_url,
        "local_online": local_online,
    }


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


class AutoPipelineRequest(BaseModel):
    model: Optional[str] = None          # overrides project default
    episode_names: Optional[List[str]] = None  # None = all episodes
    skip_context: bool = False
    skip_glossary: bool = False


@app.post("/projects/{project_name}/auto-translate")
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
    model = request.model or global_config.get("default_translation_model", "gemini-flash-latest")

    job_id = create_job("auto_translate")
    jobs[job_id].pipeline_mode = "auto"
    jobs[job_id].pipeline_stage = "context"
    background_tasks.add_task(
        _process_auto_translate,
        job_id, project_name, model,
        request.episode_names, request.skip_context, request.skip_glossary,
    )
    return {"job_id": job_id}


async def _process_auto_translate(
    job_id: str,
    project_name: str,
    model: str,
    episode_names: Optional[List[str]],
    skip_context: bool,
    skip_glossary: bool,
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
            update_job(job_id, progress=5.0, message="Generating context guide...", log="Stage 1/3: context")
            jobs[job_id].pipeline_stage = "context"

            text_lines = await _gather_project_text(project_name, episode_names)
            enable_research = not is_local_model(model)

            if enable_research:
                research_data, _ = await research_project_adk(
                    metadata.get("show_name", project_name),
                    text_lines,
                    metadata.get("target_language", "English"),
                    model_name=model,
                )
                context_guide, _ = await enhance_context_guide_adk(
                    research_data.get("findings", ""),
                    metadata.get("show_name", project_name),
                    model_name=model,
                )
            else:
                # Local model: derive minimal context from show name only
                context_guide, _ = await enhance_context_guide_adk(
                    f"Show: {metadata.get('show_name', project_name)}",
                    metadata.get("show_name", project_name),
                    model_name=model,
                )

            metadata["context_guide"] = context_guide
            _invalidate_translation_cache(metadata)
            storage.save_project_metadata(project_name, metadata)
            update_job(job_id, progress=25.0, log="Context guide saved")
        else:
            update_job(job_id, progress=25.0, log="Context: skipped (already exists)" if has_context else "Context: skipped by request")

        if jobs[job_id].cancelled:
            update_job(job_id, status="cancelled", message="Cancelled")
            return

        # ── Stage 2: Glossary ────────────────────────────────────────────
        has_glossary = bool(metadata.get("glossary", {}).get("terms"))
        if not skip_glossary and not has_glossary:
            update_job(job_id, progress=28.0, message="Building glossary...", log="Stage 2/3: glossary")
            jobs[job_id].pipeline_stage = "glossary"

            text_lines = await _gather_project_text(project_name, episode_names)
            enable_research = not is_local_model(model) and not text_lines

            result, _ = await generate_glossary_adk(
                text_lines,
                metadata.get("show_name", project_name),
                metadata.get("target_language", "English"),
                existing_glossary=metadata.get("glossary"),
                model_name=model,
                enable_research=enable_research,
            )
            metadata["glossary"] = result
            _invalidate_translation_cache(metadata)
            storage.save_project_metadata(project_name, metadata)
            update_job(job_id, progress=50.0, log=f"Glossary: {len(result.get('terms', []))} terms")
        else:
            update_job(job_id, progress=50.0, log="Glossary: skipped (already exists)" if has_glossary else "Glossary: skipped by request")

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
        await _process_batch_translation(job_id, project_name, episodes, model, enhance_glossary_flag=False)

    except Exception as e:
        update_job(job_id, status="failed", message=f"Auto-translate error: {str(e)}", log=str(e))


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
        new_glossary = parse_glossary_from_text(response_text)
        
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


async def _process_simple_pipeline(job_id: str, project_name: str, model: str):
    try:
        update_job(job_id, status="running", progress=5.0, message="Analyzing project context...")
        metadata = storage.load_project_metadata(project_name)
        target_lang = metadata.get("target_language", "English")
        
        # Step 1: Analyze Context
        jobs[job_id].pipeline_stage = "analyze"
        update_job(job_id, log="Starting context analysis")
        
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
            model_name=model, enable_research=not is_local
        )
        
        update_job(job_id, progress=50.0, result={"glossary": glossary_dict}, message="Awaiting glossary review...")
        await _pipeline_pause(job_id, "glossary", "Please review and confirm the generated glossary terms.")
        
        if jobs[job_id].cancelled: return

        # Step 3: Batch Translation
        jobs[job_id].pipeline_stage = "translate"
        update_job(job_id, status="running", progress=55.0, message="Translating episodes...")
        
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

        global_config = storage.load_global_config()
        concurrent_limit = global_config.get("concurrent_scenes", 3)
        semaphore = asyncio.Semaphore(concurrent_limit)

        # Build the agent ONCE per batch job — all scenes share the same instruction
        # string, so the local LLM server can reuse its KV-cached system prefix.
        shared_agent = create_translation_pipeline(
            project_name=project_name,
            target_language=target_lang,
            glossary=glossary,
            context_guide=context_guide,
            cartographer_model=model,
            translator_model=model,
            skip_glossary_step=not enhance_glossary_flag,
            temperature=global_config.get("temperature"),
            top_k=global_config.get("top_k"),
            top_p=global_config.get("top_p"),
        )
        _eph_svc = get_ephemeral_session_service()
        from google.adk.runners import Runner as _Runner

        # Track total progress across multiple episodes
        progress = 0.0
        total = len(episode_names)

        async def _translate_scene_task(scene: Dict, target_lang: str, translated_map: Dict, parsed_srt: List[Dict], total_scenes: int, episode_name: str, prev_scene_lines: List[Dict] = None):
            nonlocal progress
            scene_label = f"Scene {scene['scene_id']}"
            update_job(job_id, scene_status={scene_label: "pending"})

            async with semaphore:
                if jobs[job_id].cancelled:
                    return

                update_job(job_id, scene_status={scene_label: "running"})
                completed_scenes = [s for s in jobs[job_id].scene_status.values() if s == "completed"]
                update_job(job_id, message=f"Translating {episode_name} ({len(completed_scenes)}/{total_scenes} scenes done)...")

                # Each scene gets its own ephemeral session on the shared agent
                session_id = f"scene_{uuid4().hex[:8]}"
                runner = _Runner(agent=shared_agent, app_name=f"OmbiSub_{session_id}", session_service=_eph_svc)
                await _eph_svc.create_session(
                    session_id=session_id, user_id="default_user", app_name=f"OmbiSub_{session_id}"
                )

                lines = scene.get("lines", [])
                if not lines:
                    return

                # One-line context hint from the end of the previous scene — zero overlap waste
                context_hint = get_scene_context_hint(prev_scene_lines) if prev_scene_lines else ""
                source_texts = [line.get("original", "") for line in lines]
                prompt = build_translation_prompt(source_texts, target_lang, context_hint)

                response_text = ""
                async for event in runner.run_async(
                    user_id="default_user",
                    session_id=session_id,
                    new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=prompt)])
                ):
                    if jobs[job_id].cancelled:
                        update_job(job_id, scene_status={scene_label: "cancelled"})
                        return

                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text:
                                response_text += part.text

                # Parse structured JSON output
                # Extract and log internal reasoning for transparency
                reasoning_match = re.search(r'<internal_reasoning>(.*?)</internal_reasoning>', response_text, re.DOTALL)
                if reasoning_match:
                    reasoning_text = reasoning_match.group(1).strip()
                    update_job(job_id, log=f"Scene {scene['scene_id']} Reasoning: {reasoning_text[:300]}...")
                
                # Strip reasoning blocks just in case any escaped the schema wrapper
                response_text = strip_reasoning_blocks(response_text)
                
                # Robust parsing using shared utility
                parsed_lines = parse_translations_from_text(response_text)

                for item in parsed_lines:
                    # Output indices are 1-based, no overlap offset needed
                    actual_idx = item["index"] - 1
                    if 0 <= actual_idx < len(lines):
                        global_idx = scene["start_index"] + actual_idx
                        translated_map[global_idx] = item["text"]
                        if global_idx < len(parsed_srt):
                            parsed_srt[global_idx]["translated"] = item["text"]
                
                # Incrementally save the episode for the frontend Live View
                storage.save_episode(project_name, episode_name, parsed_srt)
                update_job(job_id, scene_status={scene_label: "completed"})
                
                # Update main job message after completion
                completed_count = len([s for s in jobs[job_id].scene_status.values() if s == "completed"])
                update_job(job_id, message=f"Translating {episode_name} ({completed_count}/{total_scenes} scenes done)...", progress=progress + ((completed_count/total_scenes) * (100/total)))

        results = {}
        total = len(episode_names)
        
        for idx, episode_name in enumerate(episode_names):
            if jobs[job_id].cancelled:
                update_job(job_id, status="cancelled", message="Cancelled by user")
                return

            update_job(job_id, message=f"Translating {episode_name}...", log=f"Processing {episode_name}")
            
            episode_data = storage.load_episode(project_name, episode_name)
            if not episode_data:
                results[episode_name] = "Not found"
                continue
            
            parsed_srt = episode_data["data"]
            
            # 1. Semantic Chunking (AST)
            max_lines = global_config.get("max_lines_per_scene", 200)
            scenes = build_scene_ast(parsed_srt, gap_threshold_ms=3000, max_lines_per_scene=max_lines)
            update_job(job_id, log=f"Split episode into {len(scenes)} Scenes based on temporal gaps.", scene_status={f"Scene {s['scene_id']}": "pending" for s in scenes})
            
            translated_map = {}
            
            # 2. Parallel Scene Translation
            # Skip scenes that are already fully translated (resume support)
            def _scene_complete(scene: Dict) -> bool:
                return all(line.get("translated", "").strip() for line in scene.get("lines", []))

            pending_scenes = [(i, s) for i, s in enumerate(scenes) if not _scene_complete(s)]
            skipped = len(scenes) - len(pending_scenes)
            if skipped:
                update_job(job_id, log=f"Resuming {episode_name}: skipping {skipped} already-translated scene(s)")

            tasks = []
            total_scenes = len(scenes)
            for i, scene in pending_scenes:
                prev_scene_lines = scenes[i - 1]["lines"] if i > 0 else None
                tasks.append(_translate_scene_task(scene, target_lang, translated_map, parsed_srt, total_scenes, episode_name, prev_scene_lines))

            await asyncio.gather(*tasks)
            
            if jobs[job_id].cancelled:
                update_job(job_id, status="cancelled", message="Cancelled by user")
                return
            
            # 3. Final Reconstruct (Already handled progressively, but ensure clean merge)
            for i, item in enumerate(parsed_srt):
                if i in translated_map:
                    item["translated"] = translated_map[i]
            
            # Apply SubtitleEdit fixes if configured
            project_settings = metadata.get("settings", {})
            if project_settings.get("apply_subtitle_edit_fixes", global_config.get("apply_subtitle_edit_fixes")):
                update_job(job_id, log=f"Running SubtitleEdit fixes for {episode_name}...")
                parsed_srt = fix_subtitles_with_se(parsed_srt)
            
            storage.save_episode(project_name, episode_name, parsed_srt)
            
            # Auto-export if enabled
            project_settings = metadata.get("settings", {})
            if project_settings.get("auto_export_enabled") and project_settings.get("auto_export_dir"):
                export_dir = project_settings.get("auto_export_dir")
                try:
                    os.makedirs(export_dir, exist_ok=True)
                    lang_codes = {
                        "Greek": "el", "English": "en", "Spanish": "es", 
                        "French": "fr", "German": "de", "Italian": "it", 
                        "Portuguese": "pt", "Russian": "ru", "Japanese": "ja", 
                        "Korean": "ko", "Chinese": "zh"
                    }
                    lang_code = lang_codes.get(target_lang, "en")
                    
                    orig_filename = episode_data.get("metadata", {}).get("original_filename")
                    if orig_filename:
                        if orig_filename.endswith(".en.srt"):
                            filename = orig_filename.replace(".en.srt", f".{lang_code}.srt")
                        elif orig_filename.endswith(".srt"):
                            filename = orig_filename.replace(".srt", f".{lang_code}.srt")
                        else:
                            filename = f"{orig_filename}.{lang_code}.srt"
                    else:
                        filename = f"{episode_name}.{lang_code}.srt"
                        
                    export_path = os.path.join(export_dir, filename)
                    srt_content = reconstruct_srt(parsed_srt)
                    with open(export_path, 'w', encoding='utf-8') as f:
                        f.write(srt_content)
                    update_job(job_id, log=f"Auto-exported to {export_path}")
                except Exception as export_err:
                    update_job(job_id, log=f"Failed to auto-export: {export_err}")
            
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


async def _process_targeted_retranslation(job_id: str, project_name: str, affected_terms: List[str], model: str):
    """
    Surgically re-translates only the scenes that contain the affected glossary terms.
    Preserves all other manual edits and unchanged scenes.
    """
    update_job(job_id, status="running", progress=0.0, message="Scanning for affected scenes...", log="Targeted retranslation started")
    try:
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return

        glossary = metadata.get("glossary", {"terms": []})
        target_lang = metadata.get("target_language", "English")
        context_guide = metadata.get("context_guide", "")
        
        episode_names = storage.list_episodes(project_name)
        if not episode_names:
            update_job(job_id, status="completed", progress=100.0, message="No episodes to process")
            return

        pipeline = create_translation_pipeline(
            project_name=project_name,
            target_language=target_lang,
            glossary=glossary,
            context_guide=context_guide,
            cartographer_model=model,
            translator_model=model,
            skip_glossary_step=True
        )
        
        # Compile a case-insensitive regex for the affected English terms
        # Need to safely escape terms to form word boundary matching
        import re
        escaped_terms = [re.escape(term) for term in affected_terms if term.strip()]
        if not escaped_terms:
            update_job(job_id, status="completed", progress=100.0, message="No valid terms updated")
            return
            
        term_pattern = re.compile(rf"\b(?:{'|'.join(escaped_terms)})\b", re.IGNORECASE)

        global_config = storage.load_global_config()
        concurrent_limit = global_config.get("concurrent_scenes", 3)
        semaphore = asyncio.Semaphore(concurrent_limit)

        # Build the agent once for the whole retranslation job
        _retrans_agent = create_translation_pipeline(
            project_name=project_name,
            target_language=target_lang,
            glossary=glossary,
            context_guide=context_guide,
            cartographer_model=model,
            translator_model=model,
            skip_glossary_step=True,
        )
        _eph_svc_rt = get_ephemeral_session_service()
        from google.adk.runners import Runner as _Runner

        async def _translate_scene_task(scene: Dict, translated_map: Dict):
            scene_label = f"Ep {current_ep_idx} - Scene {scene['scene_id']}"
            update_job(job_id, scene_status={scene_label: "pending"})

            async with semaphore:
                if jobs[job_id].cancelled:
                    return

                update_job(job_id, scene_status={scene_label: "running"})

                session_id = f"scene_{uuid4().hex[:8]}"
                runner = _Runner(agent=_retrans_agent, app_name=f"OmbiSub_{session_id}", session_service=_eph_svc_rt)
                await _eph_svc_rt.create_session(
                    session_id=session_id, user_id="default_user", app_name=f"OmbiSub_{session_id}"
                )

                lines = scene.get("lines", [])
                source_texts = [line.get("original", "") for line in lines]
                prompt = build_translation_prompt(source_texts, target_lang)

                response_text = ""
                async for event in runner.run_async(
                    user_id="default_user",
                    session_id=session_id,
                    new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=prompt)])
                ):
                    if jobs[job_id].cancelled:
                        update_job(job_id, scene_status={scene_label: "cancelled"})
                        return
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text:
                                response_text += part.text

                # Extract and log internal reasoning
                reasoning_match = re.search(r'<internal_reasoning>(.*?)</internal_reasoning>', response_text, re.DOTALL)
                if reasoning_match:
                    reasoning_text = reasoning_match.group(1).strip()
                    update_job(job_id, log=f"{scene_label} Reasoning: {reasoning_text[:300]}...")

                # Robust parsing using shared utility
                parsed_lines = parse_translations_from_text(response_text)
                for item in parsed_lines:
                    local_idx = item["index"] - 1
                    if 0 <= local_idx < len(lines):
                        global_idx = scene["start_index"] + local_idx
                        translated_map[global_idx] = item["text"]
                
                update_job(job_id, scene_status={scene_label: "completed"})

        results = {}
        total = len(episode_names)
        total_invalidated_scenes = 0
        
        for idx, episode_name in enumerate(episode_names):
            if jobs[job_id].cancelled:
                return

            current_ep_idx = idx + 1
            episode_data = storage.load_episode(project_name, episode_name)
            if not episode_data:
                continue
            
            parsed_srt = episode_data["data"]
            scenes = build_scene_ast(parsed_srt, gap_threshold_ms=3000, max_lines_per_scene=200)
            
            # Find invalidated scenes
            invalidated_scenes = []
            for scene in scenes:
                # Check if any original text in the scene matches the pattern
                scene_text = " ".join([line.get("original", "") for line in scene.get("lines", [])])
                if term_pattern.search(scene_text):
                    invalidated_scenes.append(scene)
            
            if not invalidated_scenes:
                update_job(job_id, progress=((idx + 1) / total) * 100, log=f"{episode_name}: No affected terms found")
                continue

            update_job(job_id, message=f"Updating {episode_name}...", 
                      log=f"Found {len(invalidated_scenes)} affected scenes in {episode_name}",
                      scene_status={f"Ep {current_ep_idx} - Scene {s['scene_id']}": "pending" for s in invalidated_scenes})
            
            translated_map = {}
            tasks = [_translate_scene_task(s, translated_map) for s in invalidated_scenes]
            await asyncio.gather(*tasks)
            
            if jobs[job_id].cancelled:
                return
            
            for i, item in enumerate(parsed_srt):
                if i in translated_map:
                    item["translated"] = translated_map[i]
            
            storage.save_episode(project_name, episode_name, parsed_srt)
            total_invalidated_scenes += len(invalidated_scenes)
            
            progress = ((idx + 1) / total) * 100
            update_job(job_id, progress=progress, log=f"Saved targeted updates for {episode_name}")
        
        update_job(
            job_id, status="completed", progress=100.0,
            message="Targeted retranslation completed",
            log=f"Fixed {total_invalidated_scenes} scenes across {total} episodes.",
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {str(e)}", log=str(e))

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
            project_name=project_name, target_language=target_lang,
            glossary=glossary, context_guide=context_guide,
            cartographer_model=tr_model, translator_model=tr_model,
            skip_glossary_step=True
        )

        # Determine concurrency limit
        from adk_agents.llm_factory import is_local_model, get_recommended_chunk_size
        is_local = is_local_model(tr_model)
        max_parallel = 2 if is_local else 5
        semaphore = asyncio.Semaphore(max_parallel)
        processed_count = 0
        total = len(episode_names)
        # Feature 4: use model-aware chunk sizing instead of hardcoded 200
        chunk_size = get_recommended_chunk_size(tr_model)
        update_job(job_id, log=f"Chunk size: {chunk_size} lines/request (model: {tr_model})")

        async def _translate_episode_task(ep_name):
            nonlocal processed_count
            async with semaphore:
                if jobs[job_id].cancelled: return
                
                # Dedicated ephemeral runner per episode — no SQLite write needed
                ep_session_id = f"ep_{uuid4().hex[:6]}"
                _eph = get_ephemeral_session_service()
                from google.adk.runners import Runner as _Runner
                ep_runner = _Runner(agent=pipeline, app_name=f"OmbiSub_{ep_name}", session_service=_eph)
                await _eph.create_session(
                    session_id=ep_session_id, user_id="default_user",
                    app_name=f"OmbiSub_{ep_name}"
                )

                try:
                    update_job(job_id, log=f"Translating: {ep_name}")
                    ep_data = storage.load_episode(project_name, ep_name)
                    if not ep_data: return

                    parsed_srt = ep_data["data"]
                    text_lines_ep = extract_text_only(parsed_srt)
                    chunks = [text_lines_ep[i:i + chunk_size] for i in range(0, len(text_lines_ep), chunk_size)]
                    translated_lines_all = []

                    for chunk_offset in range(0, len(text_lines_ep), chunk_size):
                        if jobs[job_id].cancelled:
                            return

                        chunk = text_lines_ep[chunk_offset:chunk_offset + chunk_size]
                        base_prompt = build_translation_prompt(chunk, target_lang)

                        # Index-based retry: track which 1-based indices are still missing
                        results_map: Dict[int, str] = {}
                        MAX_RETRIES = 2

                        for attempt in range(MAX_RETRIES + 1):
                            # On retry, build a prompt containing only the missing indices
                            if attempt > 0:
                                missing = [i for i in range(1, len(chunk) + 1) if not results_map.get(i)]
                                if not missing:
                                    break
                                retry_lines = [(missing[k], chunk[missing[k] - 1]) for k in range(len(missing))]
                                retry_input = "\n".join(f"{idx}| {ln.replace(chr(10), '<br>')}" for idx, ln in retry_lines)
                                current_prompt = f"Translate ONLY these missing lines to {target_lang}:\n\n{retry_input}"
                                update_job(job_id, log=f"Retry {attempt}/{MAX_RETRIES}: {len(missing)} missing lines in {ep_name}")
                            else:
                                current_prompt = base_prompt

                            response_text = ""
                            async for event in ep_runner.run_async(
                                user_id="default_user", session_id=ep_session_id,
                                new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=current_prompt)])
                            ):
                                if event.content and event.content.parts:
                                    for part in event.content.parts:
                                        if part.text:
                                            response_text += part.text

                            for item in parse_translations_from_text(response_text):
                                idx = item["index"]
                                if 1 <= idx <= len(chunk) and item["text"]:
                                    results_map[idx] = item["text"]

                            if len(results_map) >= len(chunk):
                                break

                        chunk_translated = [results_map.get(i, "") for i in range(1, len(chunk) + 1)]
                        translated_lines_all.extend(chunk_translated)

                    # Feature 6: Two-pass glossary enforcement (CPU-side, zero API cost)
                    from utils.glossary_enforcer import enforce_glossary
                    corrected_lines, correction_count = enforce_glossary(
                        translated_lines_all,
                        metadata.get("glossary", {}),
                        text_lines_ep,
                    )
                    if correction_count > 0:
                        update_job(job_id, log=f"Glossary enforced: {correction_count} correction(s) in {ep_name}")

                    # Update and save
                    for i, translated_text in enumerate(corrected_lines):
                        if i < len(parsed_srt):
                            parsed_srt[i]["translated"] = translated_text
                    
                    storage.save_episode(project_name, ep_name, parsed_srt)
                    processed_count += 1
                    prog = 55.0 + (processed_count / total) * 45.0
                    update_job(job_id, progress=prog, message=f"Translated {processed_count}/{total} episodes", log=f"Success: {ep_name}")
                except Exception as task_err:
                    update_job(job_id, log=f"Error in {ep_name}: {str(task_err)}")
                    # We don't raise here so other episodes can continue

        # Execute all tasks in parallel
        await asyncio.gather(*[_translate_episode_task(name) for name in episode_names])

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

async def _process_retranslation_for_comparison(
    job_id: str, project_name: str, episode_name: str, model: str
):
    """Retranslate an episode without overwriting, storing results in job state for review."""
    update_job(job_id, status="running", progress=0.0, message=f"Retranslating {episode_name}...", log="Job started")
    try:
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return

        glossary = metadata.get("glossary", {"terms": []})
        target_lang = metadata.get("target_language", "English")
        context_guide = metadata.get("context_guide", "")

        global_config = storage.load_global_config()
        
        ep_data = storage.load_episode(project_name, episode_name)
        if not ep_data:
             update_job(job_id, status="failed", message="Episode not found")
             return
             
        parsed_srt = ep_data["data"]
        scenes = build_scene_ast(parsed_srt)
        total_scenes = len(scenes)
        
        shared_agent = create_translation_pipeline(
            project_name=project_name,
            target_language=target_lang,
            glossary=glossary,
            context_guide=context_guide,
            cartographer_model=model,
            translator_model=model,
            skip_glossary_step=True,
            temperature=global_config.get("temperature"),
            top_k=global_config.get("top_k"),
            top_p=global_config.get("top_p"),
        )
        _eph_svc = get_ephemeral_session_service()
        from google.adk.runners import Runner as _Runner

        new_translations = {} 
        semaphore = asyncio.Semaphore(3)

        async def _translate_scene_task(scene: Dict, prev_scene_lines: List[Dict] = None):
            scene_label = f"Scene {scene['scene_id']}"
            update_job(job_id, scene_status={scene_label: "pending"})
            
            async with semaphore:
                if jobs[job_id].cancelled: return
                update_job(job_id, scene_status={scene_label: "running"})
                
                session_id = f"retrans_{uuid4().hex[:8]}"
                runner = _Runner(agent=shared_agent, app_name=f"OmbiSub_{session_id}", session_service=_eph_svc)
                await _eph_svc.create_session(session_id=session_id, user_id="default_user", app_name=f"OmbiSub_{session_id}")
                
                context_hint = get_scene_context_hint(prev_scene_lines) if prev_scene_lines else ""
                source_texts = [line.get("original", "") for line in scene.get("lines", [])]
                prompt = build_translation_prompt(source_texts, target_lang, context_hint)
                
                response_text = ""
                async for event in runner.run_async(
                    user_id="default_user",
                    session_id=session_id,
                    new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=prompt)])
                ):
                    if jobs[job_id].cancelled: return
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text: response_text += part.text

                response_text = strip_reasoning_blocks(response_text)
                parsed_lines = parse_translations_from_text(response_text)
                
                for item in parsed_lines:
                    actual_idx = item["index"] - 1
                    if 0 <= actual_idx < len(scene.get("lines", [])):
                        global_idx = scene["start_index"] + actual_idx
                        if global_idx < len(parsed_srt):
                            new_translations[str(global_idx)] = item["text"]
                
                update_job(job_id, scene_status={scene_label: "completed"})

        # Sequential processing to maintain context_hint flow
        prev_lines = None
        for i, scene in enumerate(scenes):
            if jobs[job_id].cancelled: break
            await _translate_scene_task(scene, prev_lines)
            prev_lines = scene.get("lines")
            update_job(job_id, progress=(i+1)/total_scenes * 100.0)

        if not jobs[job_id].cancelled:
            update_job(
                job_id, 
                status="completed", 
                progress=100.0, 
                message="Retranslation complete", 
                result={"new_translations": new_translations, "episode_name": episode_name}
            )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Retranslation error: {str(e)}", log=str(e))


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
