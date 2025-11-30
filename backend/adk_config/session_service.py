"""
ADK Session Service Configuration

Manages persistent session storage using SQLite for project state.
Each project maps to a unique session containing glossary, context, and settings.
"""

from google.adk.sessions import DatabaseSessionService
from pathlib import Path

SESSION_DB_PATH = Path(__file__).parent.parent / "ombisub_sessions.db"

_session_service = None


def get_session_service() -> DatabaseSessionService:
    """
    Get singleton instance of the ADK session service.
    
    Uses SQLite for persistence across server restarts.
    Production deployments can switch to Cloud SQL or Firestore.
    """
    global _session_service
    if _session_service is None:
        # Use sqlite+aiosqlite for async SQLite support (required by ADK)
        _session_service = DatabaseSessionService(
            db_url=f"sqlite+aiosqlite:///{SESSION_DB_PATH}"
        )
    return _session_service
