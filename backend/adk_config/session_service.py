"""
ADK Session Service Configuration

This replaces the JSON-based project storage with ADK's managed sessions.
Each project gets its own session, storing:
- Glossary state
- Context guide
- Translation cache info
"""

from google.adk.sessions import DatabaseSessionService
from pathlib import Path

# Database for session persistence
SESSION_DB_PATH = Path(__file__).parent.parent / "ombisub_sessions.db"

def get_session_service():
    """
    Get the ADK session service for OmbiSub.
    
    This uses SQLite to persist session data across restarts.
    In production, this could be switched to Cloud SQL or Firestore.
    """
    return DatabaseSessionService(
        db_url=f"sqlite:///{SESSION_DB_PATH}"
    )
