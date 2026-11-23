"""Constants and helpers the agent task service and its mixins (services/agent_tasks/*) share.

``services.agent_task_service`` re-exports them (``from services.agent_task_service import _options`` works).
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from utils import storage
from utils.paths import DATA_DIR

logger = logging.getLogger("omnisub.agent_task")

AGENT_TASKS_DIR = DATA_DIR / "agent_tasks"
DEFAULT_CLAIM_TTL_HOURS = 12.0
CLOSED_WORKSPACE_DAYS = 7.0      # a finished workspace is deleted after this long (prune_closed_workspaces)
GUIDE_MAX_CHARS = 6000           # the context guide stops growing here (every prompt carries it)
GUIDE_ADDITIONS_HEADING = "## Story so far"

REPO_ROOT = Path(__file__).resolve().parents[3]   # backend/services/agent_tasks/common.py -> repo
# Legacy output names accepted in place of the work file (older skills wrote these).
_LEGACY_OUTPUTS = (
    "output_translated.srt", "output_translated.txt", "output_translated.json",
    "translated.srt", "translated.txt", "output.srt", "output.txt",
)
_ACTIVE = ("pending", "running", "paused_daily_limit", "paused")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _options(row: Any) -> Dict[str, Any]:
    """A queue row's options as a dict (accepts sqlite rows and plain dicts)."""
    raw = row["options"] if row is not None and "options" in row.keys() else None
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except Exception:
        return {}


def _ignored_item(row: Any, opts: Dict[str, Any]) -> bool:
    """A queue row its project's Ignored tier rules out (``queue_service.skips_ignored``)."""
    from services.queue_service import skips_ignored
    return skips_ignored(storage.load_project_metadata(row["project_name"]) or {}, row["priority"], opts)


def cli_command(*args: str) -> str:
    """The agent CLI invocation to show agents, runnable from the repo root."""
    venv_py = REPO_ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    py = ".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python"
    if not venv_py.exists():
        py = "python"
    return " ".join([py, "scripts/agent_task.py", *args])


