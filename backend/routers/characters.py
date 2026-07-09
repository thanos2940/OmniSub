import json
from typing import Dict
from fastapi import APIRouter, HTTPException, BackgroundTasks

from utils import storage
from utils.character_profiles import CharacterProfileManager
from utils.rate_limiter import active_model_var, translation_rate_limiter
from utils.api_call_wrapper import rate_limited_call
from utils.jobs_manager import create_job, update_job
from routers.settings import validate_api_key

router = APIRouter()


@router.get("/projects/{project_name}/characters")
async def get_character_profiles(project_name: str):
    """Get all character profiles for a project."""
    mgr = CharacterProfileManager(project_name)
    return mgr.get_all_as_dicts()


@router.put("/projects/{project_name}/characters/{character_name}")
async def update_character_profile(project_name: str, character_name: str, updates: Dict, global_save: bool = False):
    """Create or update a character profile."""
    target_project = project_name
    if global_save:
        meta = storage.load_project_metadata(project_name)
        if meta and meta.get("parent_project"):
            target_project = meta["parent_project"]
            
    mgr = CharacterProfileManager(target_project)
    mgr.update_profile(character_name, updates)
    profile = mgr.get_profile(character_name)
    return profile.to_dict() if profile else {"status": "updated"}


@router.delete("/projects/{project_name}/characters/{character_name}")
async def delete_character_profile(project_name: str, character_name: str):
    """Delete a character profile."""
    mgr = CharacterProfileManager(project_name)
    if mgr.delete_profile(character_name):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail=f"Character '{character_name}' not found")


@router.post("/projects/{project_name}/characters/generate")
async def generate_character_profiles(
    project_name: str,
    background_tasks: BackgroundTasks,
    model: str = "gemini-flash-lite-latest",
):
    """Auto-generate character profiles from existing episodes and glossary.

    Uses an LLM to analyze subtitle text and determine character gender,
    formality, speech patterns, and verbal tics. Results are merged with
    any existing profiles (existing profiles are not overwritten).
    """
    validate_api_key()
    job_id = create_job("generate_profiles", project_name=project_name)
    background_tasks.add_task(
        _process_profile_generation, job_id, project_name, model
    )
    return {"job_id": job_id}


async def _process_profile_generation(job_id: str, project_name: str, model: str):
    """Background task: generate character profiles from episode text."""
    active_model_var.set(model)
    try:
        update_job(job_id, status="running", progress=10.0, message="Analyzing characters...")

        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return

        # Collect text from first few episodes for analysis
        episodes = storage.list_episodes(project_name)
        if not episodes:
            update_job(job_id, status="failed", message="No episodes found")
            return

        # Use first 2-3 episodes for profile generation
        sample_episodes = episodes[:3]
        combined_lines = []
        for ep_name in sample_episodes:
            ep_data = storage.load_episode(project_name, ep_name)
            if ep_data:
                combined_lines.extend(
                    entry.get("original", "") for entry in ep_data["data"]
                )

        if not combined_lines:
            update_job(job_id, status="failed", message="No subtitle text found")
            return

        # Stratified sample for analysis
        from adk_agents.operations import _stratified_sample
        sampled = _stratified_sample(combined_lines, total=300)
        subtitle_text = "\n".join(sampled)

        # Get character names from glossary
        glossary = metadata.get("glossary", {"terms": []})
        character_names = [
            t.get("term", "")
            for t in glossary.get("terms", [])
            if t.get("type") == "person" and t.get("term")
        ]

        update_job(job_id, progress=30.0, message="Running character analysis...",
                   log=f"Analyzing {len(sampled)} lines across {len(sample_episodes)} episodes, {len(character_names)} known characters")

        # Create and run the profile agent
        from adk_agents.profile_agent import create_profile_agent, build_profile_prompt
        from adk_agents.operations import _create_session_and_runner, _collect_response_text

        agent = create_profile_agent(
            model_name=model,
            target_language=metadata.get("target_language", "Greek"),
        )
        runner, session_id = await _create_session_and_runner(agent, "profiles")
        prompt = build_profile_prompt(
            subtitle_text,
            character_names,
            show_name=metadata.get("show_name", project_name),
        )

        response_text = await rate_limited_call(
            lambda: _collect_response_text(runner, session_id, prompt),
            rate_limiter=translation_rate_limiter,
        )

        update_job(job_id, progress=70.0, message="Parsing profiles...")

        # Parse JSON response
        import re as _re
        json_match = _re.search(r'\[.*\]', response_text, _re.DOTALL)
        if not json_match:
            update_job(job_id, status="failed", message="Failed to parse profile response",
                       log=f"Response: {response_text[:500]}")
            return

        try:
            profiles_data = json.loads(json_match.group())
        except json.JSONDecodeError as e:
            update_job(job_id, status="failed", message=f"Invalid JSON in response: {e}",
                       log=f"Response: {response_text[:500]}")
            return

        # Merge with existing profiles (don't overwrite user edits)
        mgr = CharacterProfileManager(project_name)
        existing = mgr.load_all()
        new_count = 0
        updated_count = 0

        for profile_dict in profiles_data:
            name = profile_dict.get("name", "")
            if not name:
                continue

            if name in existing:
                # Only fill in fields that are currently empty/unknown
                existing_profile = existing[name]
                if existing_profile.gender == "unknown" and profile_dict.get("gender", "unknown") != "unknown":
                    existing_profile.gender = profile_dict["gender"]
                    updated_count += 1
                if not existing_profile.speech_patterns and profile_dict.get("speech_patterns"):
                    existing_profile.speech_patterns = profile_dict["speech_patterns"]
                if not existing_profile.verbal_tics and profile_dict.get("verbal_tics"):
                    existing_profile.verbal_tics = profile_dict["verbal_tics"]
                if not existing_profile.formality and profile_dict.get("formality"):
                    existing_profile.formality = profile_dict["formality"]
            else:
                from utils.character_profiles import CharacterProfile
                existing[name] = CharacterProfile(
                    name=name,
                    gender=profile_dict.get("gender", "unknown"),
                    formality=profile_dict.get("formality", "informal"),
                    speech_patterns=profile_dict.get("speech_patterns", ""),
                    verbal_tics=profile_dict.get("verbal_tics", []),
                    episode_first_seen=sample_episodes[0] if sample_episodes else "",
                )
                new_count += 1

        mgr.save_all(existing)

        update_job(
            job_id, status="completed", progress=100.0,
            message=f"Generated {new_count} new profiles, updated {updated_count} existing",
            result={"profiles": mgr.get_all_as_dicts()},
            log=f"Total profiles: {len(existing)}",
        )

    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {str(e)}", log=str(e))
