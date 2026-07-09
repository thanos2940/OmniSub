from typing import Dict
from fastapi import APIRouter, HTTPException, BackgroundTasks

from utils import storage
from utils.episode_summaries import EpisodeSummaryManager
from utils.jobs_manager import create_job, update_job
from routers.settings import validate_api_key

router = APIRouter()


@router.get("/projects/{project_name}/summaries")
async def get_episode_summaries(project_name: str):
    """Get all episode summaries for a project.

    Returns a dict of {episode_name: summary_text} sorted by episode name.
    """
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")

    mgr = EpisodeSummaryManager(project_name)
    all_summaries = mgr.load_all()
    # Return sorted for frontend display
    return {k: all_summaries[k] for k in sorted(all_summaries)}


@router.put("/projects/{project_name}/summaries/{episode_name}")
async def update_episode_summary(
    project_name: str,
    episode_name: str,
    body: Dict,
):
    """Create or update an episode summary.

    Expects JSON body: {"summary": "text here"}
    Users can manually edit summaries to correct LLM inaccuracies or
    add terminology notes that will propagate to future translations.
    """
    summary_text = body.get("summary", "").strip()
    if not summary_text:
        raise HTTPException(status_code=400, detail="summary field is required and must not be empty")

    mgr = EpisodeSummaryManager(project_name)
    mgr.save_summary(episode_name, summary_text)
    return {"status": "updated", "episode": episode_name}


@router.delete("/projects/{project_name}/summaries/{episode_name}")
async def delete_episode_summary(project_name: str, episode_name: str):
    """Delete an episode's summary."""
    mgr = EpisodeSummaryManager(project_name)
    if mgr.delete_summary(episode_name):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail=f"No summary found for '{episode_name}'")


@router.post("/projects/{project_name}/summaries/{episode_name}/generate")
async def generate_summary_for_episode(
    project_name: str,
    episode_name: str,
    background_tasks: BackgroundTasks,
    model: str = "gemini-flash-lite-latest",
):
    """Manually trigger summary generation for a specific translated episode.

    Useful when summaries were not auto-generated (e.g. feature was disabled
    at translation time) or when a summary needs to be regenerated.
    """
    validate_api_key()

    data = storage.load_episode(project_name, episode_name)
    if not data:
        raise HTTPException(status_code=404, detail="Episode not found")

    job_id = create_job("generate_summary", project_name=project_name, episode_name=episode_name)
    background_tasks.add_task(
        _process_summary_generation, job_id, project_name, episode_name, model
    )
    return {"job_id": job_id}


async def _process_summary_generation(
    job_id: str,
    project_name: str,
    episode_name: str,
    model: str,
):
    """Background task: generate a summary for one episode."""
    try:
        update_job(job_id, status="running", progress=20.0, message=f"Generating summary for {episode_name}...")

        data = storage.load_episode(project_name, episode_name)
        if not data:
            update_job(job_id, status="failed", message="Episode not found")
            return

        proj_meta = storage.load_project_metadata(project_name)
        show_name = proj_meta.get("show_name", project_name) if proj_meta else project_name

        from adk_agents.operations import generate_episode_summary_adk
        summary = await generate_episode_summary_adk(
            data["data"],
            episode_name,
            show_name=show_name,
            model_name=model,
        )

        if not summary:
            update_job(job_id, status="failed", message="LLM returned empty summary")
            return

        mgr = EpisodeSummaryManager(project_name)
        mgr.save_summary(episode_name, summary)

        update_job(
            job_id,
            status="completed",
            progress=100.0,
            message="Summary generated",
            result={"episode": episode_name, "summary": summary},
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {e}", log=str(e))
