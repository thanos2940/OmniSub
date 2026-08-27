"""
ADK Session Service Configuration

Two session services:
- DatabaseSessionService  — persistent SQLite, used for project-level state
  (glossary, context guide, settings) that must survive server restarts.
- InMemorySessionService  — ephemeral, used for per-scene translation runners
  that are completely stateless. Avoids a SQLite write for every scene chunk.
"""

from pathlib import Path
from google.adk.sessions import DatabaseSessionService, InMemorySessionService

from utils.paths import SESSIONS_DB_FILE as SESSION_DB_PATH

_session_service = None
_memory_session_service = None


def get_session_service() -> DatabaseSessionService:
    """Persistent SQLite session service for project-level state."""
    global _session_service
    if _session_service is None:
        db_path_posix = Path(SESSION_DB_PATH).resolve().as_posix()
        _session_service = DatabaseSessionService(
            db_url=f"sqlite+aiosqlite:///{db_path_posix}"
        )
    return _session_service


def get_ephemeral_session_service() -> InMemorySessionService:
    """In-memory session service for throwaway per-scene translation runners.

    These sessions are never read back — they exist only to satisfy the ADK
    Runner API.  Using InMemorySessionService eliminates the SQLite write that
    would otherwise happen for every scene chunk in a batch job.
    """
    global _memory_session_service
    if _memory_session_service is None:
        _memory_session_service = InMemorySessionService()
    return _memory_session_service
