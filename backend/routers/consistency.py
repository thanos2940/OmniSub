"""Cross-episode consistency endpoints (Plan 12)."""

from typing import List, Optional
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from utils import storage, consistency
from utils.jobs_manager import create_job

router = APIRouter()


class ConsistencyFix(BaseModel):
    terms: List[str]               # source terms to re-canonicalize
    model: Optional[str] = None


@router.get("/projects/{project}/consistency")
async def get_consistency(project: str):
    return consistency.build_report(project)


from utils.model_resolver import resolve_model

@router.post("/projects/{project}/consistency/fix")
async def fix_consistency(project: str, body: ConsistencyFix, background_tasks: BackgroundTasks):
    """Targeted retranslation of scenes containing the drifting terms.

    The glossary already holds the canonical target, so re-translating the affected
    scenes enforces it.
    """
    metadata = storage.load_project_metadata(project) or {}
    model = body.model or resolve_model("translation", metadata)
    job_id = create_job("consistency_fix", project_name=project)
    from services.translation_service import _process_targeted_retranslation
    background_tasks.add_task(_process_targeted_retranslation, job_id, project, body.terms, model)
    return {"job_id": job_id}
