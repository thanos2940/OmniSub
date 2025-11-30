from fastapi import FastAPI, UploadFile, File, HTTPException, Body, BackgroundTasks, Path as PathParam, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import sys
import uvicorn
import os
import io
import zipfile
import uuid
import json
from pathlib import Path
from dotenv import load_dotenv
from typing import List, Dict, Optional
from pydantic import BaseModel
from datetime import datetime, timedelta

# Explicitly load .env from the project root (parent of backend)
# Explicitly load .env
if getattr(sys, 'frozen', False):
    # If frozen (exe), look in the same directory as the executable
    env_path = Path(sys.executable).parent / '.env'
else:
    # If dev, look in project root (parent of backend)
    env_path = Path(__file__).resolve().parent.parent / '.env'

load_dotenv(dotenv_path=env_path)

# Config file utilities
CONFIG_FILE = Path(__file__).resolve().parent / 'config.json'

def read_config():
    """Read configuration from config.json"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading config: {e}")
            return {}
    return {}

def write_config(config):
    """Write configuration to config.json"""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"Error writing config: {e}")
        return False

def get_api_key():
    """Get API key from config.json, .env, or environment (in that order)"""
    # 1. Check config.json
    config = read_config()
    if config.get('api_key'):
        return config['api_key']
    
    # 2. Check .env / environment
    return os.environ.get("GOOGLE_API_KEY")

def has_api_key():
    """Check if API key is configured"""
    return get_api_key() is not None

def validate_api_key():
    """Validate that an API key is configured, raise HTTPException if not"""
    if not has_api_key():
        raise HTTPException(
            status_code=400,
            detail={
                "error": "api_key_missing",
                "message": "Google Gemini API key not configured. Please set your API key in Settings."
            }
        )


from utils.srt_parser import parse_srt, extract_text_only
from agents.cartographer import CartographerAgent
from agents.translator import TranslatorAgent
from utils import storage
from adk_agents.cartographer_agent import create_cartographer_agent
from adk_agents.translation_pipeline import create_translation_pipeline
from adk_config.runner_factory import OmbiSubRunnerFactory
from adk_config.session_manager import OmbiSubSessionManager

# Initialize ADK services
adk_runner_factory = OmbiSubRunnerFactory()
adk_session_manager = OmbiSubSessionManager()

app = FastAPI(title="OmbiSub API", version="5.0")

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic Models ---

class CreateProjectRequest(BaseModel):
    name: str
    target_language: str = "English"
    parent_project: Optional[str] = None
    type: str = "show" # "show" or "movie"

class ImportRequest(BaseModel):
    source_project: str
    import_glossary: bool = True
    import_context: bool = True

class ScanRequest(BaseModel):
    model: str = "gemini-flash-lite-latest"

class TranslateRequest(BaseModel):
    model: str = "gemini-flash-latest"

class SaveEpisodeRequest(BaseModel):
    data: List[Dict]

# --- Endpoints ---

class ProjectSettings(BaseModel):
    translation_model: str = "gemini-flash-latest"
    context_model: str = "gemini-flash-lite-latest"
    glossary_model: str = "gemini-flash-lite-latest"

class UpdateProjectRequest(BaseModel):
    show_name: Optional[str] = None
    target_language: Optional[str] = None
    context_guide: Optional[str] = None
    glossary: Optional[Dict] = None
    settings: Optional[ProjectSettings] = None

class SetApiKeyRequest(BaseModel):
    api_key: str

# --- API Key Management Endpoints ---

@app.get("/api/config/api-key")
async def get_api_key_status():
    """Check if API key is configured (doesn't return the actual key)"""
    return {
        "has_key": has_api_key(),
        "source": "config" if read_config().get('api_key') else "env" if os.environ.get("GOOGLE_API_KEY") else "none"
    }

@app.post("/api/config/api-key")
async def set_api_key(request: SetApiKeyRequest):
    """Set the API key in config.json"""
    if not request.api_key or not request.api_key.strip():
        raise HTTPException(status_code=400, detail="API key cannot be empty")
    
    config = read_config()
    config['api_key'] = request.api_key.strip()
    
    if write_config(config):
        # Update the environment variable for immediate use
        os.environ["GOOGLE_API_KEY"] = request.api_key.strip()
        return {"message": "API key saved successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to save API key")

@app.delete("/api/config/api-key")
async def delete_api_key():
    """Remove the API key from config.json"""
    config = read_config()
    if 'api_key' in config:
        del config['api_key']
        write_config(config)
    
    # Clear from environment if it was set from config
    if not os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY") == config.get('api_key'):
        os.environ.pop("GOOGLE_API_KEY", None)
    
    return {"message": "API key removed"}

# --- Project Endpoints ---


@app.get("/")
async def root():
    return {"message": "OmbiSub API is running"}

# Project Management

@app.get("/projects")
async def get_projects():
    return storage.list_projects()

@app.post("/projects")
async def create_project(request: CreateProjectRequest):
    import re
    
    # Validate project name
    if not request.name or not request.name.strip():
        raise HTTPException(status_code=400, detail="Project name cannot be empty")
    
    # Check for invalid characters (OS path unsafe)
    invalid_chars = r'[<>:"/\\|?*]'
    if re.search(invalid_chars, request.name):
        raise HTTPException(
            status_code=400,
            detail="Project name contains invalid characters. Avoid: < > : \" / \\ | ? *"
        )
    
    # Check length
    if len(request.name) > 200:
        raise HTTPException(status_code=400, detail="Project name too long (max 200 characters)")
    
    # Check if project already exists
    existing_projects = storage.list_projects()
    if request.name in existing_projects:
        raise HTTPException(
            status_code=409,
            detail=f"Project '{request.name}' already exists"
        )
    
    try:
        metadata = {
            "show_name": request.name,
            "target_language": request.target_language,
            "parent_project": request.parent_project,
            "type": request.type,
            "glossary": {"terms": []},
            "context_guide": "",
            "settings": {
                "translation_model": "gemini-flash-latest",
                "context_model": "gemini-flash-lite-latest",
                "glossary_model": "gemini-flash-lite-latest"
            }
        }
        
        storage.create_project(request.name, metadata)
        return metadata
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/projects/{project_name}")
async def get_project(project_name: str):
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    return metadata

@app.post("/projects/{project_name}/import")
async def import_project_data(project_name: str, request: ImportRequest):
    try:
        target_meta = storage.load_project_metadata(project_name)
        if not target_meta:
            raise HTTPException(status_code=404, detail="Target project not found")
            
        source_meta = storage.load_project_metadata(request.source_project)
        if not source_meta:
            raise HTTPException(status_code=404, detail="Source project not found")
            
        if request.import_context:
            # Append source context to target context
            current_context = target_meta.get("context_guide", "")
            source_context = source_meta.get("context_guide", "")
            if source_context:
                target_meta["context_guide"] = (current_context + "\n\n" + source_context).strip()
                
        if request.import_glossary:
            # Merge glossaries, avoiding duplicates
            target_terms = target_meta.get("glossary", {}).get("terms", [])
            source_terms = source_meta.get("glossary", {}).get("terms", [])
            
            existing_term_names = {t["term"].lower() for t in target_terms}
            
            for term in source_terms:
                if term["term"].lower() not in existing_term_names:
                    target_terms.append(term)
                    existing_term_names.add(term["term"].lower())
            
            if "glossary" not in target_meta:
                target_meta["glossary"] = {}
            target_meta["glossary"]["terms"] = target_terms
            
        storage.save_project_metadata(project_name, target_meta)
        return target_meta
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/projects/{project_name}")
async def update_project(project_name: str, request: UpdateProjectRequest):
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if request.show_name:
        metadata["show_name"] = request.show_name
    if request.target_language:
        metadata["target_language"] = request.target_language
    if request.context_guide is not None:
        metadata["context_guide"] = request.context_guide
    if request.glossary is not None:
        metadata["glossary"] = request.glossary
    if request.settings is not None:
        metadata["settings"] = request.settings.dict()
        
    # Invalidate context cache if glossary or context guide changed
    if request.context_guide is not None or request.glossary is not None:
        if "context_cache_name" in metadata:
            del metadata["context_cache_name"]
        if "context_cache_expiry" in metadata:
            del metadata["context_cache_expiry"]

    storage.save_project_metadata(project_name, metadata)
    return metadata

# AI Enhancement Endpoints

class EnhanceGlossaryRequest(BaseModel):
    episode_names: Optional[List[str]] = None

async def _gather_project_text(project_name: str, limit_lines: int = 5000, episode_names: List[str] = None) -> List[str]:
    episodes = storage.list_episodes(project_name)
    # Filter if specific episodes requested (empty list means NO episodes, None means ALL)
    if episode_names is not None:
        episodes = [ep for ep in episodes if ep in episode_names]
        
    all_lines = []
    for ep in episodes:
        data = storage.load_episode(project_name, ep)
        if data:
            lines = extract_text_only(data["data"])
            all_lines.extend(lines)
            if len(all_lines) >= limit_lines:
                break
    return all_lines[:limit_lines]

@app.post("/projects/{project_name}/glossary/enhance")
async def enhance_glossary(
    project_name: str, 
    background_tasks: BackgroundTasks,
    request: EnhanceGlossaryRequest = Body(default=None),
    model: str = Query(None),
    enable_research: bool = Query(False, description="Enable web research for glossary terms")
):
    validate_api_key()
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Determine model
    if not model:
        settings = metadata.get("settings", {})
        model = settings.get("glossary_model", "gemini-flash-lite-latest")

    episode_names = request.episode_names if request else None
    
    job = create_job("enhance_glossary")
    background_tasks.add_task(_process_enhance_glossary, job.id, project_name, episode_names, model, enable_research)
    return {"job_id": job.id}

@app.post("/projects/{project_name}/glossary/create")
async def create_glossary(
    project_name: str, 
    background_tasks: BackgroundTasks,
    request: EnhanceGlossaryRequest = Body(default=None),
    model: str = Query(None)
):
    validate_api_key()
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Determine model
    if not model:
        settings = metadata.get("settings", {})
        model = settings.get("glossary_model", "gemini-flash-lite-latest")

    episode_names = request.episode_names if request else None
    
    job = create_job("create_glossary")
    background_tasks.add_task(_process_create_glossary, job.id, project_name, episode_names, model)
    return {"job_id": job.id}

@app.post("/projects/{project_name}/context/enhance")
async def enhance_context(
    project_name: str, 
    background_tasks: BackgroundTasks,
    model: str = Query(None),
    with_research: bool = Query(False)  # NEW: Allow user to choose fresh research
):
    validate_api_key()
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if not model:
        settings = metadata.get("settings", {})
        model = settings.get("context_model", "gemini-flash-lite-latest")
        
    job = create_job("enhance_context")
    background_tasks.add_task(_process_enhance_context, job.id, project_name, model, with_research)
    return {"job_id": job.id}

@app.post("/projects/{project_name}/context/create")
async def create_context(
    project_name: str, 
    background_tasks: BackgroundTasks,
    request: EnhanceGlossaryRequest = Body(default=None),
    model: str = Query(None)
):
    validate_api_key()
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if not model:
        settings = metadata.get("settings", {})
        model = settings.get("context_model", "gemini-flash-lite-latest")
        
    episode_names = request.episode_names if request else None

    job = create_job("create_context")
    background_tasks.add_task(_process_create_context, job.id, project_name, episode_names, model)
    return {"job_id": job.id}

@app.delete("/projects/{project_name}/context")
async def delete_context(project_name: str):
    """Delete/clear the context guide for a project"""
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Clear context guide
    metadata["context_guide"] = ""
    
    # Invalidate context cache
    if "context_cache_name" in metadata:
        del metadata["context_cache_name"]
    if "context_cache_expiry" in metadata:
        del metadata["context_cache_expiry"]
    
    storage.save_project_metadata(project_name, metadata)
    return {"message": "Context guide deleted successfully"}

@app.delete("/projects/{project_name}/glossary")
async def delete_glossary(project_name: str):
    """Delete/clear the glossary for a project"""
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Reset glossary to empty
    metadata["glossary"] = {"terms": []}
    
    # Invalidate context cache since glossary is part of cached context
    if "context_cache_name" in metadata:
        del metadata["context_cache_name"]
    if "context_cache_expiry" in metadata:
        del metadata["context_cache_expiry"]
    
    storage.save_project_metadata(project_name, metadata)
    return {"message": "Glossary deleted successfully"}


@app.get("/projects/{project_name}/episodes")
async def get_episodes(project_name: str):
    episode_names = storage.list_episodes(project_name)
    results = []
    for name in episode_names:
        data = storage.load_episode(project_name, name)
        is_translated = False
        if data and "data" in data:
            # Check if any line has a translation
            for line in data["data"]:
                if line.get("translated"):
                    is_translated = True
                    break
        
        metadata = data.get("metadata", {})
        original_filename = metadata.get("original_filename")
        season = metadata.get("season")
        
        results.append({
            "name": name,
            "translated": is_translated,
            "original_filename": original_filename,
            "line_count": len(data.get("data", [])),
            "season": season
        })
    return results

@app.get("/projects/{project_name}/episodes/{episode_name}")
async def get_episode(project_name: str, episode_name: str):
    data = storage.load_episode(project_name, episode_name)
    if not data:
        raise HTTPException(status_code=404, detail="Episode not found")
    return data

@app.post("/projects/{project_name}/episodes/{episode_name}/save")
async def save_episode_data(project_name: str, episode_name: str, request: SaveEpisodeRequest):
    storage.save_episode(project_name, episode_name, request.data)
    return {"message": "Saved successfully"}

@app.delete("/projects/{project_name}/episodes/{episode_name}")
async def delete_episode(project_name: str, episode_name: str):
    storage.delete_episode(project_name, episode_name)
    return {"message": "Episode deleted successfully"}

class UpdateMetadataRequest(BaseModel):
    metadata: Dict

@app.post("/projects/{project_name}/episodes/{episode_name}/metadata")
async def update_episode_metadata(project_name: str, episode_name: str, request: UpdateMetadataRequest):
    success = storage.update_episode_metadata(project_name, episode_name, request.metadata)
    if not success:
        raise HTTPException(status_code=404, detail="Episode not found")
    return {"message": "Metadata updated successfully"}

# File Operations

@app.post("/projects/{project_name}/episodes/{episode_name}/upload")
async def upload_episode(
    project_name: str, 
    episode_name: str, 
    file: UploadFile = File(...)
):
    print(f"[OMBI-LOG] Upload request for {project_name}/{episode_name}")
    
    # Validate file size (max 5MB for SRT files)
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    content = await file.read()
    
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE / (1024*1024):.0f}MB"
        )
    
    # Validate UTF-8 encoding
    try:
        decoded_content = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="File must be UTF-8 encoded. Please re-save your SRT file as UTF-8."
        )
    
    # Validate file is not empty
    if not decoded_content.strip():
        raise HTTPException(status_code=400, detail="File is empty")
    
    # Parse and validate SRT format
    try:
        parsed_data = parse_srt(decoded_content)
        
        if not parsed_data:
            raise HTTPException(
                status_code=400,
                detail="Invalid SRT file format. Please check your file contains proper timecodes and text."
            )
        
        # Save initial state
        storage.save_episode(project_name, episode_name, parsed_data, decoded_content, metadata={"original_filename": file.filename})
        
        return {"message": "File uploaded and saved", "data": parsed_data}
        
    except HTTPException:
        # Re-raise HTTPException as-is
        raise
    except Exception as e:
        print(f"[OMBI-LOG] Error parsing file: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Error parsing file: {str(e)}")

# AI Operations

@app.post("/projects/{project_name}/episodes/{episode_name}/scan")
async def scan_episode(
    project_name: str, 
    episode_name: str, 
    background_tasks: BackgroundTasks,
    request: ScanRequest
):
    validate_api_key()
    # Determine model (request model takes precedence, else settings)
    model = request.model
    if not model or model == "gemini-flash-lite-latest": # Check if it's the default from Pydantic
        metadata = storage.load_project_metadata(project_name)
        if metadata:
            settings = metadata.get("settings", {})
            model = settings.get("glossary_model", request.model)

    job = create_job("scan_episode")
    background_tasks.add_task(_process_scan_episode, job.id, project_name, episode_name, model)
    return {"job_id": job.id}



# --- Job System ---

class Job(BaseModel):
    id: str
    type: str
    status: str  # "pending", "running", "completed", "failed"
    progress: float = 0.0
    message: str = ""
    logs: List[str] = []
    result: Optional[Dict] = None
    prompt: Optional[str] = None
    ai_response: Optional[str] = None
    created_at: str
    
jobs: Dict[str, Job] = {}

def create_job(job_type: str) -> Job:
    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        type=job_type,
        status="pending",
        created_at=datetime.now().isoformat()
    )
    jobs[job_id] = job
    return job

def update_job(job_id: str, status: str = None, progress: float = None, message: str = None, log: str = None, result: Dict = None, prompt: str = None, ai_response: str = None):
    if job_id in jobs:
        job = jobs[job_id]
        if status: job.status = status
        if progress is not None: job.progress = progress
        if message: job.message = message
        if log: job.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {log}")
        if result: job.result = result
        if prompt: job.prompt = prompt
        if ai_response: job.ai_response = ai_response

@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]

# --- Background Tasks ---

async def _process_batch_translation(job_id: str, project_name: str, episode_names: List[str], model: str, enhance_glossary_flag: bool):
    update_job(job_id, status="running", progress=0.0, message="Starting batch translation (ADK)...", log="Job started")
    
    try:
        results = {}
        
        # Get Session State
        state = await adk_session_manager.get_project_state(project_name)
        glossary = state.get("glossary", {"terms": []})
        target_lang = state.get("target_language", "English")
        
        # Create Pipeline
        pipeline = create_translation_pipeline(
            project_name=project_name,
            target_language=target_lang,
            glossary=glossary,
            cartographer_model=model,
            translator_model=model,
            skip_glossary_step=not enhance_glossary_flag
        )
        
        runner = adk_runner_factory.create_runner(
            pipeline, f"ombisub_project_{project_name}"
        )
        
        total_episodes = len(episode_names)
        
        for idx, episode_name in enumerate(episode_names):
            update_job(job_id, message=f"Processing {episode_name}...", log=f"Processing {episode_name}...")
            
            episode_data = storage.load_episode(project_name, episode_name)
            if not episode_data:
                results[episode_name] = "Episode not found"
                update_job(job_id, log=f"Episode {episode_name} not found")
                continue
                
            parsed_srt = episode_data["data"]
            text_lines = extract_text_only(parsed_srt)
            
            # Chunking logic
            CHUNK_SIZE = 100 # Safe chunk size for translation context
            chunks = [text_lines[i:i + CHUNK_SIZE] for i in range(0, len(text_lines), CHUNK_SIZE)]
            
            translated_lines_map = {}
            
            for i, chunk in enumerate(chunks):
                chunk_num = i + 1
                update_job(job_id, message=f"Translating {episode_name} (Chunk {chunk_num}/{len(chunks)})...")
                
                # Prepare numbered input for this chunk
                chunk_input = "\n".join([f"{j+1}: {line}" for j, line in enumerate(chunk)])
                
                prompt = f"Translate these lines to {target_lang}:\n{chunk_input}"
                # Pass session_id to ensure context is maintained/saved to correct session
                response = await runner.run_debug(prompt, session_id=f"ombisub_project_{project_name}")
                
                # Parse response
                for line in response.split('\n'):
                    parts = line.split(':', 1)
                    if len(parts) == 2 and parts[0].strip().isdigit():
                        local_idx = int(parts[0].strip()) - 1
                        if 0 <= local_idx < len(chunk):
                            global_idx = (i * CHUNK_SIZE) + local_idx
                            translated_lines_map[global_idx] = parts[1].strip()
            
            # Apply translations
            for i, item in enumerate(parsed_srt):
                if i in translated_lines_map:
                    item["translated"] = translated_lines_map[i]
            
            storage.save_episode(project_name, episode_name, parsed_srt)
            results[episode_name] = "Success"
            update_job(job_id, log=f"Translation completed for {episode_name}")
            
            # Update progress
            progress = ((idx + 1) / total_episodes) * 100
            update_job(job_id, progress=progress)
            
        update_job(job_id, status="completed", progress=100.0, message="Batch translation completed", log="All tasks finished", result=results)
        
    except Exception as e:
        update_job(job_id, status="failed", message=f"Critical error: {str(e)}", log=f"Critical error: {str(e)}")

async def _process_scan_episode(job_id: str, project_name: str, episode_name: str, model: str):
    update_job(job_id, status="running", progress=0.0, message=f"Scanning {episode_name} (ADK)...", log="Job started")
    try:
        project_meta = storage.load_project_metadata(project_name)
        episode_data = storage.load_episode(project_name, episode_name)
        
        if not project_meta or not episode_data:
            update_job(job_id, status="failed", message="Project or Episode not found", log="Error: Not found")
            return
        
        text_lines = extract_text_only(episode_data["data"])
        
        # Create ADK Agent & Runner
        agent = create_cartographer_agent(model_name=model)
        runner = adk_runner_factory.create_runner(
            agent, f"ombisub_project_{project_name}"
        )
        
        update_job(job_id, message="Generating glossary...", log="Analyzing text...")
        
        # Run Agent
        input_text = "\n".join(text_lines[:5000])
        prompt = f"Extract glossary terms from the following text:\n\n{input_text}"
        # Pass session_id to ensure output is saved to project session
        response = await runner.run_debug(prompt, session_id=f"ombisub_project_{project_name}")
        
        # Parse JSON result
        import json
        import re
        try:
            clean_response = response.replace("```json", "").replace("```", "").strip()
            match = re.search(r'\{.*\}', clean_response, re.DOTALL)
            if match:
                clean_response = match.group(0)
            new_glossary = json.loads(clean_response)
        except Exception as e:
            update_job(job_id, log=f"Failed to parse JSON response: {str(e)}", result={"raw": response})
            new_glossary = {"terms": [], "error": "Failed to parse model output", "raw_output": response}
        
        # Update Session (ADK)
        await adk_session_manager.update_glossary(project_name, new_glossary)
        
        # Update Legacy Storage
        project_meta["glossary"] = new_glossary
        if new_glossary.get("context_guide"):
            project_meta["context_guide"] = new_glossary["context_guide"]
        storage.save_project_metadata(project_name, project_meta)
        
        
        update_job(job_id, 
            status="completed", 
            progress=100.0, 
            message="Scan completed", 
            log=f"Updated glossary with terms from {episode_name}", 
            result=new_glossary
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {str(e)}", log=f"Error: {str(e)}")

async def _process_enhance_glossary(job_id: str, project_name: str, episode_names: Optional[List[str]], model: str, enable_research: bool = False):
    update_job(job_id, status="running", progress=0.0, message="Starting glossary enhancement (ADK)...", log="Job started")
    try:
        # Gather text
        text_lines = await _gather_project_text(project_name, episode_names=episode_names)
        update_job(job_id, message="Analyzing text...", log=f"Gathered {len(text_lines)} lines of text")
        
        # Get current state via Session Manager
        state = await adk_session_manager.get_project_state(project_name)
        
        # Create ADK Glossary Orchestrator (combines Research + Extraction agents)
        from adk_agents.glossary_orchestrator import create_glossary_orchestrator
        orchestrator = create_glossary_orchestrator(
            model_name=model,
            enable_research=enable_research  # Use parameter from endpoint
        )
        runner = adk_runner_factory.create_runner(
            orchestrator, f"ombisub_project_{project_name}"
        )
        
        # Run Agent
        # Limit context to avoid token limits, taking first 5000 lines if too large
        input_text = "\n".join(text_lines[:5000])
        
        # Check if glossary already has terms
        existing_terms = state.get("glossary", {}).get("terms", [])
        
        if not input_text.strip():
            # No text provided - Research Only Mode
            if not enable_research:
                update_job(job_id, status="failed", message="No text provided and research disabled", log="Error: No episodes selected and research is disabled.")
                return

            prompt = f"""This is a RESEARCH-ONLY glossary creation task.
            
Task: Create a comprehensive glossary for the show "{project_name}" using ONLY web research.

1. RESEARCH: Use the Google Search tool to find official information.
2. Identify key characters, locations, and terminology.
3. Provide a suggested translation for each term.
"""
        elif not existing_terms:
            # Fresh glossary creation with text
            prompt = f"""This is a FRESH glossary extraction task. Ignore any previous conversation history.

Analyze the following subtitle text and extract ALL important terms for translation consistency:
- Character names
- Location names
- Specialized terminology
- Cultural references

Text to analyze:
{input_text}
"""
        else:
            # Enhancement - find NEW terms not in existing glossary
            existing_terms_list = "\n".join([f"- {term['term']}" for term in existing_terms[:50]])  # Limit to first 50 to avoid token overflow
            
            prompt = f"""This is a glossary ENHANCEMENT task. Find NEW terms not already in the existing glossary.

EXISTING GLOSSARY (DO NOT include these again):
{existing_terms_list}

Analyze the following subtitle text and extract NEW terms that are NOT in the existing glossary above:
- Character names
- Location names
- Specialized terminology
- Cultural references

Text to analyze:
{input_text}
"""
        
        
        
        # Pass session_id to ensure output is saved to project session
        response = await runner.run_debug(prompt, session_id=f"ombisub_project_{project_name}")
        
        # With SequentialAgent orchestrator, the CartographerAgent's output 
        # is stored in session state with output_key="glossary_result"
        # We need to retrieve it from the session state
        try:
            # Get the updated session state after agent execution
            final_state = await adk_session_manager.get_project_state(project_name)
            
            # DEBUG: Log all keys in session state
            update_job(job_id, log=f"Session State Keys: {list(final_state.keys())}")
            
            # The glossary result should be in the session state
            glossary_result = final_state.get("glossary_result")
            
            if glossary_result:
                # If it's a Pydantic model, convert to dict
                if hasattr(glossary_result, 'dict'):
                    new_glossary = glossary_result.dict()
                elif hasattr(glossary_result, '__dict__'):
                    new_glossary = glossary_result.__dict__
                elif isinstance(glossary_result, dict):
                    new_glossary = glossary_result
                else:
                    update_job(job_id, log=f"Unexpected glossary_result type: {type(glossary_result)}")
                    new_glossary = {"terms": []}
            else:
                # Fallback: try to parse the response directly
                update_job(job_id, log="glossary_result not found in session state, trying direct response")
                
                if isinstance(response, list):
                    # run_debug returns a list of events/objects
                    # We need to find the one that contains our glossary data
                    found_glossary = None
                    for i, item in enumerate(response):
                        # DEBUG: Log attributes of each item
                        attrs = dir(item)
                        public_attrs = [a for a in attrs if not a.startswith('_')]
                        update_job(job_id, log=f"Item {i} attrs: {public_attrs}")
                        # Check for Pydantic model (GlossaryOutput)
                        if hasattr(item, 'terms') and isinstance(item.terms, list):
                            found_glossary = item.dict() if hasattr(item, 'dict') else item.__dict__
                            break
                        # Check for dict with terms
                        if isinstance(item, dict) and 'terms' in item and isinstance(item['terms'], list):
                            found_glossary = item
                            break
                        # Check for Event object with payload/content that might be our data
                        if hasattr(item, 'payload'):
                            payload = item.payload
                            # Case 1: Payload is dict
                            if isinstance(payload, dict) and 'terms' in payload:
                                found_glossary = payload
                                break
                            # Case 2: Payload is Pydantic model (GlossaryOutput)
                            if hasattr(payload, 'terms') and isinstance(payload.terms, list):
                                found_glossary = payload.dict() if hasattr(payload, 'dict') else payload.__dict__
                                break
                        
                        # Case 3: Check content for JSON string (Fallback)
                        if hasattr(item, 'content') and item.content:
                            content = item.content
                            # If content is a string and looks like JSON
                            if isinstance(content, str) and "terms" in content:
                                import re
                                import json
                                try:
                                    # Try to find JSON block
                                    match = re.search(r'\{.*\}', content, re.DOTALL)
                                    if match:
                                        json_str = match.group(0)
                                        parsed = json.loads(json_str)
                                        if "terms" in parsed:
                                            found_glossary = parsed
                                            update_job(job_id, log=f"Extracted glossary from content string in item {i}")
                                            break
                                except Exception as e:
                                    update_job(job_id, log=f"Failed to parse JSON from content in item {i}: {e}")

                    if found_glossary:
                        new_glossary = found_glossary
                        update_job(job_id, log=f"Found glossary in response list with {len(new_glossary.get('terms', []))} terms")
                    else:
                        update_job(job_id, log=f"Could not find glossary data in response list of length {len(response)}")
                        # Log detailed types for debugging
                        for i, item in enumerate(response):
                            item_type = str(type(item))
                            payload_info = "no_payload"
                            content_info = "no_content"
                            
                            if hasattr(item, 'payload'):
                                payload = item.payload
                                payload_type = str(type(payload))
                                payload_content = str(payload)[:100]
                                payload_info = f"payload_type={payload_type}, content={payload_content}"
                                
                            if hasattr(item, 'content'):
                                content = item.content
                                content_type = str(type(content))
                                content_preview = str(content)[:100]
                                content_info = f"content_type={content_type}, preview={content_preview}"
                                
                            update_job(job_id, log=f"Item {i}: {item_type}, {payload_info}, {content_info}")
                            
                        new_glossary = {"terms": []}
                        
                elif hasattr(response, '__dict__'):
                    new_glossary = response.dict() if hasattr(response, 'dict') else response.__dict__
                elif isinstance(response, dict):
                    new_glossary = response
                else:
                    update_job(job_id, log=f"Could not extract glossary from response type: {type(response)}")
                    new_glossary = {"terms": []}
                
        except Exception as e:
            update_job(job_id, log=f"Failed to process structured output: {str(e)}")
            import traceback
            update_job(job_id, log=f"Traceback: {traceback.format_exc()}")
            new_glossary = {"terms": [], "error": "Failed to process model output"}



        # Merge new terms with existing glossary
        if existing_terms:
            # Append new terms to existing ones
            merged_glossary = {
                "terms": existing_terms + new_glossary.get("terms", [])
            }
            update_job(job_id, log=f"Merging {len(new_glossary.get('terms', []))} new terms with {len(existing_terms)} existing terms")
        else:
            # No existing terms, use new glossary as-is
            merged_glossary = new_glossary
        
        # Update Session (ADK)
        await adk_session_manager.update_glossary(project_name, merged_glossary)
        
        # Update Legacy Storage (JSON) - Hybrid approach
        metadata = storage.load_project_metadata(project_name)
        if metadata:
            metadata["glossary"] = merged_glossary
            storage.save_project_metadata(project_name, metadata)
        
        update_job(job_id, 
            status="completed", 
            progress=100.0, 
            message="Glossary enhancement completed", 
            log=f"Total glossary now has {len(merged_glossary.get('terms', []))} terms", 
            result=new_glossary  # Return only NEW terms for UI review
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {str(e)}", log=f"Error: {str(e)}")

async def _process_create_glossary(job_id: str, project_name: str, episode_names: List[str], model: str):
    update_job(job_id, status="running", progress=0.0, message="Creating glossary...", log="Job started")
    try:
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found", log="Error: Project not found")
            return
            
        text_lines = await _gather_project_text(project_name, episode_names=episode_names)
        if not text_lines:
            update_job(job_id, message="No text found, using research mode...", log="No episodes found/selected. Using research mode.")
            
        agent = CartographerAgent(model_name=model)
        result, debug_info = await agent.generate_glossary(
            text_lines, 
            metadata.get("show_name", project_name), 
            metadata.get("target_language", "English")
        )
        
        metadata["glossary"] = result
        if result.get("context_guide"):
            metadata["context_guide"] = result["context_guide"]
        storage.save_project_metadata(project_name, metadata)
        
        update_job(job_id, 
            status="completed", 
            progress=100.0, 
            message="Glossary created", 
            log="Glossary created and saved", 
            result=result,
            prompt=debug_info.get("prompt"),
            ai_response=debug_info.get("response") or debug_info.get("error")
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {str(e)}", log=f"Error: {str(e)}")

async def _process_enhance_context(job_id: str, project_name: str, model: str, with_research: bool = False):
    update_job(job_id, status="running", progress=0.0, message="Enhancing context...", log="Job started")
    try:
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found", log="Error: Project not found")
            return
            
        current_guide = metadata.get("context_guide", "")
        if not current_guide and not with_research:
            update_job(job_id, status="failed", message="No context guide", log="Error: No context guide to enhance")
            return
            
        agent = CartographerAgent(model_name=model)
        
        # Determine input based on user choice
        if with_research:
            # User chose: Start fresh with new research (A2A workflow)
            update_job(job_id, progress=20.0, message="Running fresh research...", log="User requested fresh research")
            text_lines = await _gather_project_text(project_name)
            research_data, research_debug = await agent.research_project(
                metadata.get("show_name", project_name),
                text_lines,
                metadata.get("target_language", "English")
            )
            input_data = research_data
            update_job(job_id, progress=50.0, message="Research complete, generating instructions...", log="Research completed")
            prompt_context = f"=== RESEARCH AGENT ===\n{research_debug.get('prompt')}\n\n"
        else:
            # User chose: Use current context (default)
            input_data = current_guide
            update_job(job_id, progress=50.0, message="Using current context...", log="Enhancing existing context")
            prompt_context = ""
        
        # Prompt Engineer step (same for both modes)
        update_job(job_id, progress=60.0, message="Prompt Engineer: Creating instructions...", log="Running prompt engineer")
        enhanced_guide, enhance_debug = await agent.enhance_context_guide(
            input_data,
            metadata.get("show_name", project_name)
        )
        
        update_job(job_id, 
            status="completed", 
            progress=100.0, 
            message="Context enhanced", 
            log="Context guide enhanced", 
            result={"context_guide": enhanced_guide},
            prompt=f"{prompt_context}=== PROMPT ENGINEER ===\n{enhance_debug.get('prompt')}",
            ai_response=enhanced_guide
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {str(e)}", log=f"Error: {str(e)}")

async def _process_create_context(job_id: str, project_name: str, episode_names: List[str], model: str):
    update_job(job_id, status="running", progress=0.0, message="Starting context creation...", log="Job started")
    try:
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found", log="Error: Project not found")
            return
            
        # Gather subtitle text if episode names provided
        text_lines = await _gather_project_text(project_name, episode_names=episode_names)
        if not text_lines:
            update_job(job_id, progress=10.0, message="No episodes selected, using research-only mode...", log="No episodes found/selected. Using research mode.")
            
        agent = CartographerAgent(model_name=model)
        
        # STEP 1: Research Agent - Gather comprehensive project analysis
        update_job(job_id, progress=20.0, message="Research Agent: Analyzing project...", log="Running research agent")
        research_data, research_debug = await agent.research_project(
            metadata.get("show_name", project_name),
            text_lines,
            metadata.get("target_language", "English")
        )
        
        update_job(job_id, progress=50.0, message="Research complete. Generating instructions...", log=f"Research completed ({len(research_data)} chars)")
        
        # STEP 2: Prompt Engineer - Transform research into comprehensive instructions
        update_job(job_id, progress=60.0, message="Prompt Engineer: Creating translation instructions...", log="Running prompt engineer")
        enhanced_context, enhance_debug = await agent.enhance_context_guide(
            research_data,  # Feed research output into prompt engineer
            metadata.get("show_name", project_name)
        )
        
        update_job(job_id, progress=90.0, message="Finalizing...", log="Context guide generated successfully")
        
        # Save ONLY context (NO glossary)
        # Note: Glossary creation is a completely separate process
        update_job(job_id, 
            status="completed", 
            progress=100.0, 
            message="Context created successfully", 
            log="A2A workflow completed: Research → Prompt Engineer", 
            result={"context_guide": enhanced_context},
            prompt=f"=== RESEARCH AGENT ===\n{research_debug.get('prompt')}\n\n=== PROMPT ENGINEER ===\n{enhance_debug.get('prompt')}",
            ai_response=enhanced_context
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {str(e)}", log=f"Error: {str(e)}")


class TranslateRequest(BaseModel):
    model: str = "gemini-flash-latest"
    enhance_glossary: bool = False

@app.post("/projects/{project_name}/episodes/{episode_name}/translate")
async def translate_episode(
    project_name: str, 
    episode_name: str, 
    request: TranslateRequest,
    background_tasks: BackgroundTasks
):
    validate_api_key()
    job = create_job("translate_episode")
    background_tasks.add_task(
        _process_batch_translation, 
        job.id, 
        project_name, 
        [episode_name], 
        request.model, 
        request.enhance_glossary
    )
    return {"job_id": job.id}

class BatchTranslateRequest(BaseModel):
    episode_names: List[str]
    model: str = "gemini-flash-latest"
    enhance_glossary: bool = False

@app.post("/projects/{project_name}/batch-translate")
async def batch_translate(
    project_name: str, 
    request: BatchTranslateRequest,
    background_tasks: BackgroundTasks
):
    validate_api_key()
    job = create_job("batch_translate")
    background_tasks.add_task(
        _process_batch_translation, 
        job.id, 
        project_name, 
        request.episode_names, 
        request.model, 
        request.enhance_glossary
    )
    return {"job_id": job.id}

class BatchDownloadRequest(BaseModel):
    episodes: Optional[List[str]] = None

@app.post("/projects/{project_name}/batch-download")
async def batch_download(project_name: str, request: BatchDownloadRequest):
    # Language code mapping
    LANGUAGE_CODES = {
        "Greek": "el",
        "English": "en",
        "Spanish": "es",
        "French": "fr",
        "German": "de",
        "Italian": "it",
        "Portuguese": "pt",
        "Russian": "ru",
        "Japanese": "ja",
        "Korean": "ko",
        "Chinese": "zh"
    }
    
    project_meta = storage.load_project_metadata(project_name)
    if not project_meta:
        raise HTTPException(status_code=404, detail="Project not found")
    
    target_lang = project_meta.get("target_language", "English")
    lang_code = LANGUAGE_CODES.get(target_lang, "en")
    
    all_episodes = storage.list_episodes(project_name)
    req_episodes = request.episodes
    if req_episodes:
        target_episodes = [ep for ep in all_episodes if ep in req_episodes]
    else:
        target_episodes = all_episodes

    if not target_episodes:
        raise HTTPException(status_code=404, detail="No episodes found")
        
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for episode_name in target_episodes:
            episode_data = storage.load_episode(project_name, episode_name)
            if not episode_data:
                continue
                
            data = episode_data["data"]
            srt_content = ""
            for index, item in enumerate(data):
                index_num = index + 1
                timecode = item.get("timecode", "")
                text = item.get("translated") or item.get("original") or ""
                srt_content += f"{index_num}\n{timecode}\n{text}\n\n"
            
            # Replace .en.srt with target language code
            # Determine filename
            original_filename = episode_data.get("metadata", {}).get("original_filename")
            if original_filename:
                filename = original_filename
            else:
                filename = episode_name

            # Replace .en.srt with target language code
            if filename.endswith(".en.srt"):
                filename = filename.replace(".en.srt", f".{lang_code}.srt")
            elif filename.endswith(".srt"):
                # If no language code, add it before .srt
                filename = filename.replace(".srt", f".{lang_code}.srt")
            else:
                # If no .srt extension at all, just add it
                filename = f"{filename}.{lang_code}.srt"
                
            zip_file.writestr(filename, srt_content)
            
    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer, 
        media_type="application/zip", 
        headers={"Content-Disposition": f"attachment; filename={project_name}_subtitles.zip"}
    )

# --- Static Files (For Standalone Executable) ---
# Serve static files if they exist (bundled by PyInstaller)
if getattr(sys, 'frozen', False):
    # If running as compiled executable, use temp folder
    static_dir = os.path.join(sys._MEIPASS, "static")
    if os.path.exists(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
