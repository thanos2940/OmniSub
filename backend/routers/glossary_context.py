import re
import json
from uuid import uuid4
from datetime import datetime
from typing import List, Dict, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from utils import storage
from utils.srt_parser import extract_text_only
from utils.cache_manager import invalidate_cache as _invalidate_translation_cache
from utils.jobs_manager import create_job, update_job
from utils.llm_utils import parse_glossary_from_text
from adk_config import adk_runner_factory, adk_session_manager, get_ephemeral_session_service
from adk_agents import (
    create_research_agent,
    create_cartographer_agent,
    generate_glossary_adk,
    research_project_adk,
    enhance_context_guide_adk
)
from google.adk.runners import Runner, types as adk_types
from routers.schemas import EnhanceGlossaryRequest, SyncImportRequest
from utils.character_profiles import CharacterProfileManager, CharacterProfile
from routers.settings import validate_llm_access
from services.translation_service import _gather_project_text, _merge_glossaries

router = APIRouter()


async def _ask_agy_for_context(project_name: str, *, want_guide: bool, want_terms: bool,
                               improve_guide: bool = False, episodes: Optional[List[str]] = None) -> Dict:
    """Agent mode: one request to the agent CLI for a project's context guide and/or glossary terms (the shared
    brief prompt of services/project_bootstrap.py). Returns {guide, terms, prompt, text}."""
    import asyncio
    from services import agy_llm, project_bootstrap

    def ask(prompt: str) -> str:
        return agy_llm.complete(prompt, purpose="context_guide" if want_guide else "glossary", project=project_name)

    return await asyncio.to_thread(project_bootstrap.ask_for_context, project_name, ask, want_guide=want_guide,
                                   want_terms=want_terms, improve_guide=improve_guide, episodes=episodes)


@router.delete("/projects/{project_name}/glossary")
async def delete_glossary(project_name: str):
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    metadata["glossary"] = {"terms": []}
    storage.save_project_metadata(project_name, metadata)
    await adk_session_manager.update_glossary(project_name, {"terms": []})
    return {"status": "deleted"}


@router.post("/projects/{project_name}/glossary/create")
async def create_glossary(
    project_name: str,
    background_tasks: BackgroundTasks,
    model: str = "gemini-flash-lite-latest"
):
    validate_llm_access()
    job_id = create_job("create_glossary", project_name=project_name)
    background_tasks.add_task(_process_create_glossary, job_id, project_name, [], model)
    return {"job_id": job_id}


@router.post("/projects/{project_name}/glossary/enhance")
async def enhance_glossary(
    project_name: str,
    background_tasks: BackgroundTasks,
    request: EnhanceGlossaryRequest = None,
    model: str = "gemini-flash-lite-latest",
    enable_research: bool = False
):
    validate_llm_access()
    episode_names = request.episode_names if request else None
    job_id = create_job("enhance_glossary", project_name=project_name)
    background_tasks.add_task(
        _process_enhance_glossary, job_id, project_name, 
        episode_names, model, enable_research
    )
    return {"job_id": job_id}


@router.delete("/projects/{project_name}/context")
async def delete_context(project_name: str):
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    metadata["context_guide"] = ""
    storage.save_project_metadata(project_name, metadata)
    await adk_session_manager.update_context(project_name, "")
    return {"status": "deleted"}


@router.post("/projects/{project_name}/context/create")
async def create_context(
    project_name: str,
    background_tasks: BackgroundTasks,
    model: str = "gemini-flash-lite-latest"
):
    validate_llm_access()
    job_id = create_job("create_context", project_name=project_name)
    background_tasks.add_task(_process_create_context, job_id, project_name, [], model)
    return {"job_id": job_id}


@router.post("/projects/{project_name}/context/enhance")
async def enhance_context(
    project_name: str,
    background_tasks: BackgroundTasks,
    model: str = "gemini-flash-lite-latest",
    with_research: bool = False
):
    validate_llm_access()
    job_id = create_job("enhance_context", project_name=project_name)
    background_tasks.add_task(_process_enhance_context, job_id, project_name, model, with_research)
    return {"job_id": job_id}


# Background Tasks for Glossary and Context

async def _process_create_glossary(job_id: str, project_name: str, episode_names: List[str], model: str):
    update_job(job_id, status="running", progress=0.0, message="Creating glossary...", log="Job started")
    try:
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return

        # In a child project, resolve parent metadata so we have full universe context
        resolved_meta = storage.load_resolved_project_metadata(project_name)
        parent_name = metadata.get("parent_project")
        if parent_name:
            parent_meta = storage.load_project_metadata(parent_name) or {}
            parent_show = parent_meta.get("show_name") or parent_name
            current_show = metadata.get("show_name") or project_name
            if current_show.lower().startswith("season") or current_show.lower().startswith("s0"):
                effective_show_name = f"{parent_show} - {current_show}"
            else:
                effective_show_name = current_show or parent_show
            existing_glossary = resolved_meta.get("glossary")
        else:
            effective_show_name = metadata.get("show_name", project_name)
            existing_glossary = metadata.get("glossary")

        text_lines = await _gather_project_text(project_name, episode_names)
        if not text_lines:
            from services.embedded_extractor import auto_extract_embedded_subs_for_episodes
            await auto_extract_embedded_subs_for_episodes(project_name, episode_names)
            text_lines = await _gather_project_text(project_name, episode_names)

        enable_research = bool(not text_lines and effective_show_name)

        if not text_lines:
            update_job(job_id, message="No episodes, using research mode...", log="Research mode")

        from services import agy_llm
        if agy_llm.agent_mode():
            # Agent mode: the agent CLI proposes the terms; they join the glossary through the same merge an
            # episode's submit uses (everyday words are left out, genders reach the character profiles).
            from services.agent_task_service import agent_task_service
            from utils.language_codes import to_code
            update_job(job_id, progress=30.0, message="Asking the agent CLI...", log="Agent CLI glossary request")
            found = await _ask_agy_for_context(project_name, want_guide=False, want_terms=True)
            if not found["terms"]:
                raise RuntimeError(f"The agent CLI returned no glossary terms: {found['text'][:200]!r}")
            agent_task_service._merge_terms(project_name, found["terms"], to_code(metadata.get("target_language", "Greek")))
            metadata = storage.load_project_metadata(project_name) or metadata
            result = metadata.get("glossary") or {"terms": []}
            debug_info = {"prompt": found["prompt"], "response": found["text"]}
        else:
            result, debug_info = await generate_glossary_adk(
                text_lines,
                effective_show_name,
                metadata.get("target_language", "English"),
                existing_glossary=existing_glossary,
                model_name=model,
                enable_research=enable_research
            )

        metadata["glossary"] = result
        _invalidate_translation_cache(metadata)
        storage.save_project_metadata(project_name, metadata)
        await adk_session_manager.update_glossary(project_name, result)

        update_job(
            job_id, status="completed", progress=100.0,
            message="Glossary created", result={"terms": result.get("terms", [])},
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
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return
            
        target_language = metadata.get("target_language", "English")
        show_name = metadata.get("show_name", project_name)
        existing_terms = metadata.get("glossary", {}).get("terms", [])

        from services import agy_llm
        if agy_llm.agent_mode():
            # Agent mode: candidate terms from the agent CLI (research is the CLI model's own knowledge of the show).
            from services.agent_task_service import agent_task_service
            from utils.language_codes import to_code
            update_job(job_id, progress=30.0, message="Asking the agent CLI...", log="Agent CLI glossary request")
            found = await _ask_agy_for_context(project_name, want_guide=False, want_terms=True,
                                               episodes=episode_names or None)
            from utils.glossary_judgment import accept_new_term
            known = {t.get("term", "").lower() for t in existing_terms}
            vocab = agent_task_service._vocab(project_name)
            new_terms = [t for t in agent_task_service._normalize_harvested_terms(found["terms"], to_code(target_language))
                         if t["term"].lower() not in known and accept_new_term(t, vocab)]
            update_job(job_id, status="completed", progress=100.0, message="Glossary enhanced",
                       log=f"Found {len(new_terms)} new candidate terms for {target_language}",
                       result={"terms": new_terms})
            return

        if episode_names:
            text_lines = await _gather_project_text(project_name, episode_names)
            update_job(job_id, message="Analyzing text...", log=f"Gathered {len(text_lines)} lines")
            input_text = "\n".join(text_lines[:8000]) 
        else:
            text_lines = []
            enable_research = True
            update_job(job_id, message="Researching...", log="Research mode (no files selected)")
            input_text = ""

        research_context = ""
        if enable_research:
            update_job(job_id, progress=20.0, message="Performing Research...", log=f"Researching '{show_name}'")
            
            research_agent = create_research_agent(model_name=model)
            research_session_id = f"research_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
            eph_service = get_ephemeral_session_service()
            research_runner = Runner(
                agent=research_agent,
                app_name=f"Omnisub_{research_session_id}",
                session_service=eph_service
            )
            
            await eph_service.create_session(
                session_id=research_session_id, user_id="default_user", app_name=f"Omnisub_{research_session_id}"
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

        update_job(job_id, progress=50.0, message="Extracting terms...", log=f"Target Language: {target_language}")
        
        cartographer = create_cartographer_agent(
            model_name=model, 
            target_language=target_language
        )
        
        session_id_unique = f"enhance_glossary_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        eph_service = get_ephemeral_session_service()
        runner = Runner(
            agent=cartographer,
            app_name=f"Omnisub_{session_id_unique}",
            session_service=eph_service
        )
        
        await eph_service.create_session(
            session_id=session_id_unique, user_id="default_user", app_name=f"Omnisub_{session_id_unique}"
        )
        
        existing_names_str = ", ".join(t.get("term", "") for t in existing_terms) if existing_terms else "None"

        if input_text:
            prompt = f"""TASK: Build a COMPREHENSIVE glossary for '{show_name}'.

TARGET LANGUAGE: {target_language}
EXISTING TERMS (DO NOT duplicate): {existing_names_str}

RESEARCH CONTEXT (Use this for accurate names/definitions):
{research_context}

SOURCE TEXT (Subtitle Sample):
{input_text}

INSTRUCTIONS:
1. Extract every named entity and special term from SOURCE TEXT.
2. Use RESEARCH CONTEXT to verify names and lore.
3. ALSO use your own knowledge of '{show_name}' to add ALL important characters, locations, organizations, items, and concepts from the ENTIRE show/movie — even those not in the text sample or research.
4. Translate/Transliterate strictly into {target_language}.
5. Aim for 40-80+ NEW terms. Be thorough — cover main characters, recurring characters, all locations, factions, special items, techniques, and unique terminology.
"""
        else:
            prompt = f"""TASK: Create a COMPREHENSIVE glossary for '{show_name}' using research and your knowledge.

TARGET LANGUAGE: {target_language}
EXISTING TERMS (DO NOT duplicate): {existing_names_str}

RESEARCH FINDINGS:
{research_context}

INSTRUCTIONS:
1. Extract key terms from the research.
2. ALSO use your own knowledge of '{show_name}' to add ALL important characters, locations, organizations, items, and concepts from the ENTIRE show/movie.
3. Translate/Transliterate strictly into {target_language}.
4. Aim for 40-80+ NEW terms. Cover: all named characters (main + recurring + notable minor), all locations, organizations/factions, special items/weapons, unique concepts/techniques, titles, and invented terminology.
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
        
        new_glossary = parse_glossary_from_text(response_text)
        
        existing_names = {t.get("term", "").lower() for t in existing_terms}
        new_terms = [t for t in new_glossary.get("terms", []) if t.get("term", "").lower() not in existing_names]
        
        update_job(
            job_id, status="completed", progress=100.0,
            message="Glossary enhanced",
            log=f"Found {len(new_terms)} new candidate terms for {target_language}",
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
        
        # In a child project, resolve parent metadata so we have full universe context
        resolved_meta = storage.load_resolved_project_metadata(project_name)
        parent_name = metadata.get("parent_project")
        if parent_name:
            parent_meta = storage.load_project_metadata(parent_name) or {}
            parent_show = parent_meta.get("show_name") or parent_name
            current_show = metadata.get("show_name") or project_name
            if current_show.lower().startswith("season") or current_show.lower().startswith("s0"):
                effective_show_name = f"{parent_show} - {current_show}"
            else:
                effective_show_name = current_show or parent_show
        else:
            effective_show_name = metadata.get("show_name", project_name)

        if episode_names:
            text_lines = await _gather_project_text(project_name, episode_names)
        else:
            text_lines = []

        if not text_lines:
            from services.embedded_extractor import auto_extract_embedded_subs_for_episodes
            await auto_extract_embedded_subs_for_episodes(project_name, episode_names)
            text_lines = await _gather_project_text(project_name, episode_names)

        if not text_lines:
            update_job(job_id, message="Researching...", log="Research mode (no files selected)")

        from services import agy_llm
        if agy_llm.agent_mode():
            update_job(job_id, progress=30.0, message="Asking the agent CLI...", log="Agent CLI context guide request")
            found = await _ask_agy_for_context(project_name, want_guide=True, want_terms=False)
            if not found["guide"]:
                raise RuntimeError(f"The agent CLI returned no context guide: {found['text'][:200]!r}")
            enhanced_context = found["guide"]
        else:
            update_job(job_id, progress=20.0, message="Researching...", log="Running research agent")
            research_data, research_debug = await research_project_adk(
                effective_show_name,
                text_lines,
                metadata.get("target_language", "English"),
                model_name=model
            )

            update_job(job_id, progress=60.0, message="Generating instructions...", log="Running prompt engineer")
            enhanced_context, enhance_debug = await enhance_context_guide_adk(
                research_data.get("findings", ""),
                effective_show_name,
                target_language=metadata.get("target_language", "English"),
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
 
        from services import agy_llm
        if agy_llm.agent_mode():
            # Agent mode: the agent CLI revises the current guide (or writes one when there is none) from the
            # show's facts and a spread of its dialogue; "research" is the CLI model's own knowledge of the show.
            update_job(job_id, progress=30.0, message="Asking the agent CLI...", log="Agent CLI context guide request")
            found = await _ask_agy_for_context(project_name, want_guide=True, want_terms=False,
                                               improve_guide=bool(current_guide))
            if not found["guide"]:
                raise RuntimeError(f"The agent CLI returned no context guide: {found['text'][:200]!r}")
            enhanced_guide = found["guide"]
        else:
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
                target_language=metadata.get("target_language", "English"),
                model_name=model
            )

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


@router.get("/projects/{project_name}/sync-candidates")
async def get_sync_candidates(project_name: str):
    """Scan all child projects pointing to this parent.
    Aggregates and groups glossary terms and character profiles present in children but missing in parent,
    detecting translation or attribute conflicts across sibling shows.
    """
    parent_metadata = storage.load_project_metadata(project_name)
    if not parent_metadata:
        raise HTTPException(status_code=404, detail="Parent project not found")
        
    parent_terms = {t.get("term", "").lower().strip() for t in parent_metadata.get("glossary", {}).get("terms", []) if t.get("term")}
    
    parent_char_mgr = CharacterProfileManager(project_name)
    parent_chars = {name.lower().strip() for name in parent_char_mgr.load_all().keys()}
    
    glossary_by_term: Dict[str, Dict] = {}
    chars_by_name: Dict[str, Dict] = {}
    
    all_projects = storage.list_projects()
    for child in all_projects:
        child_name = child.get("name") if isinstance(child, dict) else child
        if not child_name or child_name == project_name:
            continue
        child_meta = storage.load_project_metadata(child_name)
        if not child_meta:
            continue
        
        if child_meta.get("parent_project") == project_name:
            # glossary terms
            child_terms = child_meta.get("glossary", {}).get("terms", [])
            for t in child_terms:
                term_name = (t.get("term") or "").strip()
                if not term_name or term_name.lower() in parent_terms:
                    continue
                    
                key = term_name.lower()
                variant = {
                    **t,
                    "project": child_name,
                    "inherited": False,
                }
                
                if key not in glossary_by_term:
                    glossary_by_term[key] = {
                        **t,
                        "project": child_name,
                        "projects": [child_name],
                        "inherited": False,
                        "has_conflict": False,
                        "variants": [variant],
                    }
                else:
                    existing = glossary_by_term[key]
                    if child_name not in existing["projects"]:
                        existing["projects"].append(child_name)
                    existing["variants"].append(variant)
                    
                    # Conflict if translations, types, or genders differ
                    if (existing.get("translation", "").strip() != t.get("translation", "").strip() or
                        existing.get("type", "") != t.get("type", "") or
                        existing.get("gender", "") != t.get("gender", "")):
                        existing["has_conflict"] = True

            # character profiles
            child_char_mgr = CharacterProfileManager(child_name)
            child_chars = child_char_mgr.load_all()
            for name, profile in child_chars.items():
                if not name or name.lower().strip() in parent_chars:
                    continue
                c_key = name.lower().strip()
                p_dict = profile.to_dict()
                p_dict["project"] = child_name
                p_dict["inherited"] = False
                
                if c_key not in chars_by_name:
                    chars_by_name[c_key] = {
                        **p_dict,
                        "projects": [child_name],
                        "has_conflict": False,
                        "variants": [p_dict],
                    }
                else:
                    existing_char = chars_by_name[c_key]
                    if child_name not in existing_char["projects"]:
                        existing_char["projects"].append(child_name)
                    existing_char["variants"].append(p_dict)
                    if (existing_char.get("gender") != p_dict.get("gender") or
                        existing_char.get("formality") != p_dict.get("formality")):
                        existing_char["has_conflict"] = True
                        
    return {
        "glossary": list(glossary_by_term.values()),
        "characters": list(chars_by_name.values()),
    }


@router.post("/projects/{project_name}/sync-import")
async def sync_import(project_name: str, request: SyncImportRequest):
    """Import selected terms and character profiles into the parent project."""
    parent_metadata = storage.load_project_metadata(project_name)
    if not parent_metadata:
        raise HTTPException(status_code=404, detail="Parent project not found")
        
    # Import glossary terms
    added_count = 0
    char_added_count = 0
    if request.terms:
        parent_glossary = parent_metadata.get("glossary", {})
        parent_terms = parent_glossary.get("terms", [])
        parent_term_map = {t.get("term", "").lower().strip(): t for t in parent_terms if t.get("term")}
        
        for term_entry in request.terms:
            term_name = term_entry.get("term", "")
            if not term_name:
                continue
            term_lower = term_name.lower().strip()
            if term_lower not in parent_term_map:
                t_copy = dict(term_entry)
                for drop_k in ("project", "projects", "inherited", "inherited_from", "has_conflict", "variants", "_uid"):
                    t_copy.pop(drop_k, None)
                parent_terms.append(t_copy)
                parent_term_map[term_lower] = t_copy
                added_count += 1
                
        parent_metadata["glossary"] = {"terms": parent_terms}
        _invalidate_translation_cache(parent_metadata)
        storage.save_project_metadata(project_name, parent_metadata)
        await adk_session_manager.update_glossary(project_name, parent_metadata["glossary"])
        
        # Cascade cache invalidation to all descendants
        for desc in storage.get_descendant_projects(project_name):
            desc_name = desc.get("name") if isinstance(desc, dict) else desc
            if not desc_name:
                continue
            desc_meta = storage.load_project_metadata(desc_name)
            if desc_meta:
                _invalidate_translation_cache(desc_meta)
        
    # Import character profiles
    if request.characters:
        parent_char_mgr = CharacterProfileManager(project_name)
        parent_chars = parent_char_mgr.load_all()

        for char_data in request.characters:
            char_name = char_data.get("name", "")
            if not char_name:
                continue
            if char_name.lower().strip() not in {n.lower().strip() for n in parent_chars.keys()}:
                c_copy = dict(char_data)
                for drop_k in ("project", "projects", "inherited", "inherited_from", "has_conflict", "variants", "_uid"):
                    c_copy.pop(drop_k, None)
                
                parent_chars[char_name] = CharacterProfile.from_dict(c_copy)
                char_added_count += 1
                
        parent_char_mgr.save_all(parent_chars)
        
    return {
        "status": "imported",
        "imported_terms": added_count,
        "imported_characters": char_added_count,
        "requested_terms": len(request.terms),
        "requested_characters": len(request.characters),
    }
