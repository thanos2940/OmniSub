"""First-run setup wizard status (docs/PLAN_onboarding_wizard.md).

A thin read of existing config state — the wizard itself only calls endpoints
that already exist elsewhere (settings, auth, integrations); this is just the
one bit of new state (``setup_completed``) plus a few flags so the frontend
can decide what to show.
"""
from fastapi import APIRouter

from utils import storage
from routers.settings import has_api_key

router = APIRouter()


@router.get("/api/setup/status")
async def setup_status():
    config = storage.load_global_config()
    return {
        "setup_completed": bool(config.get("setup_completed")),
        "auth_configured": bool(config.get("auth_enabled")),
        "has_gemini_key": has_api_key(),
        "arr_configured": bool(config.get("sonarr_enabled") or config.get("radarr_enabled")),
    }
