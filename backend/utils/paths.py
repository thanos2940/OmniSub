"""Single source of truth for where Omnisub's mutable state lives on disk.

Defaults to the backend root (unchanged dev/test behavior). Set OMNISUB_DATA_DIR
(the Dockerfile does this — see docs/PLAN_docker_deployment.md) to relocate
everything under one directory so a single volume mount survives image rebuilds.
"""
import os
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("OMNISUB_DATA_DIR") or BACKEND_ROOT).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

PROJECTS_DIR = DATA_DIR / "projects"
CONFIG_FILE = DATA_DIR / "config.json"
DB_FILE = DATA_DIR / "omnisub.db"
SESSIONS_DB_FILE = DATA_DIR / "omnisub_sessions.db"
RATE_LIMITER_STATE_FILE = DATA_DIR / "rate_limiter_state.json"
TRANSLATION_MEMORY_DIR = DATA_DIR / "translation_memory"
