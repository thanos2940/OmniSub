from typing import List
from fastapi import APIRouter, HTTPException

from utils.translation_memory import TranslationMemory

router = APIRouter()


@router.get("/projects/{project_name}/tm/stats")
async def tm_stats(project_name: str):
    """Get Translation Memory statistics for a project."""
    try:
        tm = TranslationMemory(project_name)
        return tm.get_stats()
    except Exception as e:
        return {"error": str(e), "total_records": 0}


@router.get("/projects/{project_name}/edit-stats")
async def get_edit_stats(project_name: str):
    """Show how many user edits have been recorded and their impact."""
    tm = TranslationMemory(project_name)
    stats = tm.get_stats()
    return {
        "total_records": stats.get("total_records", 0),
        "user_edited_records": stats.get("user_edited_count", 0),
        "edit_ratio": stats.get("user_edited_count", 0) / max(stats.get("total_records", 1), 1),
    }


@router.delete("/projects/{project_name}/tm")
async def tm_clear(project_name: str):
    """Clear all Translation Memory records for a project."""
    try:
        tm = TranslationMemory(project_name)
        tm.clear()
        return {"status": "cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear TM: {e}")


@router.post("/projects/{project_name}/tm/search")
async def tm_search(project_name: str, lines: List[str], top_k: int = 3, threshold: float = 0.80):
    """Search Translation Memory for similar translations."""
    try:
        tm = TranslationMemory(project_name)
        results = tm.search(lines, top_k=top_k, similarity_threshold=threshold)
        return {"results": {str(k): v for k, v in results.items()}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TM search failed: {e}")
