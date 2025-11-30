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
from adk_agents import (
    create_cartographer_agent,
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


class ApiKeyRequest(BaseModel):
    api_key: str


# Job Management

class JobStatus(BaseModel):
    id: str
    status: str
    progress: float
    message: str
    logs: List[str]
    result: Optional[Dict] = None
    prompt: Optional[str] = None
    ai_response: Optional[str] = None


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
    metadata.update(data)
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
        [episode_name], request.model, request.enhance_glossary
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
        request.episode_names, request.model, request.enhance_glossary
    )
    return {"job_id": job_id}


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
        if episode_names:
            text_lines = await _gather_project_text(project_name, episode_names)
            update_job(job_id, message="Analyzing text...", log=f"Gathered {len(text_lines)} lines")
            input_text = "\n".join(text_lines[:5000])
        else:
            # Research mode (no files selected)
            text_lines = []
            enable_research = True
            update_job(job_id, message="Researching...", log="Research mode (no files selected)")
            input_text = ""
        
        state = await adk_session_manager.get_project_state(project_name)
        existing_terms = state.get("glossary", {}).get("terms", [])
        target_language = state.get("target_language", "English")
        
        orchestrator = create_glossary_orchestrator(
            model_name=model, 
            enable_research=enable_research,
            target_language=target_language
        )
        session_id_unique = f"enhance_glossary_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        runner = adk_runner_factory.create_runner(orchestrator, session_id_unique)
        
        # Create session explicitly
        await adk_session_manager.session_service.create_session(
            session_id=session_id_unique,
            user_id="default_user",
            app_name=f"OmbiSub_{session_id_unique}"
        )
        
        if input_text:
            prompt = f"Extract glossary terms from the text below and translate them to {target_language}:\n\n{input_text}"
        else:
            prompt = f"Research the show '{project_name}' and create a glossary. Translate all terms to {target_language}."
        
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
        
        # Extract text from response object
        # response_text is already the text
        
        try:
            print(f"DEBUG: Response text type: {type(response_text)}")
            print(f"DEBUG: Response text content: {response_text[:200]}...")
            new_glossary = _parse_json_response(response_text)
            print(f"DEBUG: Parsed glossary type: {type(new_glossary)}")
        except Exception as e:
            print(f"DEBUG: JSON parse error: {str(e)}")
            new_glossary = {"terms": [], "raw_output": response_text}
        
        existing_names = {t.get("term", "").lower() for t in existing_terms}
        new_terms = [t for t in new_glossary.get("terms", []) if t.get("term", "").lower() not in existing_names]
        merged = {"terms": existing_terms + new_terms}
        
        await adk_session_manager.update_glossary(project_name, merged)
        
        metadata = storage.load_project_metadata(project_name)
        if metadata:
            metadata["glossary"] = merged
            storage.save_project_metadata(project_name, metadata)
        
        update_job(
            job_id, status="completed", progress=100.0,
            message="Glossary enhanced",
            log=f"Added {len(new_terms)} new terms",
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
        
        text_lines = extract_text_only(episode_data["data"])
        
        agent = create_cartographer_agent(model_name=model)
        session_id_unique = f"scan_episode_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        runner = adk_runner_factory.create_runner(agent, session_id_unique)
        
        # Create session explicitly
        await adk_session_manager.session_service.create_session(
            session_id=session_id_unique,
            user_id="default_user",
            app_name=f"OmbiSub_{session_id_unique}"
        )
        
        update_job(job_id, progress=30.0, message="Analyzing...", log="Running extraction")
        
        prompt = f"Extract glossary terms from:\n\n{chr(10).join(text_lines[:5000])}"
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
        except:
            new_glossary = {"terms": [], "raw_output": response.text if hasattr(response, 'text') else str(response)}
        
        await adk_session_manager.update_glossary(project_name, new_glossary)
        metadata["glossary"] = new_glossary
        storage.save_project_metadata(project_name, metadata)
        
        update_job(
            job_id, status="completed", progress=100.0,
            message="Scan completed",
            result=new_glossary
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {str(e)}", log=str(e))


async def _process_batch_translation(
    job_id: str, project_name: str, episode_names: List[str], 
    model: str, enhance_glossary_flag: bool
):
    update_job(job_id, status="running", progress=0.0, message="Starting translation...", log="Job started")
    try:
        state = await adk_session_manager.get_project_state(project_name)
        glossary = state.get("glossary", {"terms": []})
        target_lang = state.get("target_language", "English")
        
        pipeline = create_translation_pipeline(
            project_name=project_name,
            target_language=target_lang,
            glossary=glossary,
            cartographer_model=model,
            translator_model=model,
            skip_glossary_step=not enhance_glossary_flag
        )
        
        session_id_unique = f"batch_translate_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        runner = adk_runner_factory.create_runner(pipeline, session_id_unique)
        
        results = {}
        total = len(episode_names)
        chunk_size = 100
        
        for idx, episode_name in enumerate(episode_names):
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
                chunk_input = "\n".join([f"{j+1}: {line}" for j, line in enumerate(chunk)])
                prompt = f"Translate to {target_lang}:\n{chunk_input}"
                
                # Create session explicitly
                await adk_session_manager.session_service.create_session(
                    session_id=session_id_unique,
                    user_id="default_user",
                    app_name=f"OmbiSub_{session_id_unique}"
                )
                
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
                
                for line in response_text.split('\n'):
                    parts = line.split(':', 1)
                    if len(parts) == 2 and parts[0].strip().isdigit():
                        local_idx = int(parts[0].strip()) - 1
                        if 0 <= local_idx < len(chunk):
                            global_idx = (chunk_idx * chunk_size) + local_idx
                            translated_map[global_idx] = parts[1].strip()
            
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
