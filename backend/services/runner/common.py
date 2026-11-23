"""Constants and small helpers the agent runner and its mixins (services/runner/*) share.

``services.agent_runner`` re-exports every name, so ``agent_runner.MAX_FAILURES`` and the like keep working.
Two constants tests change at run time stay in services/agent_runner.py: ``WATCH_SECONDS`` and
``PAUSE_CACHE_SECONDS`` (only the lifecycle code there reads them).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

logger = logging.getLogger("omnisub.agent_runner")

API_PREFIX = "api:"              # a model name with this prefix is called through the Gemini API, not the CLI
QUOTA_MARGIN = 30
QUOTA_CACHE_SECONDS = 120
SMALL_BLOCK = 20                 # refused requests this small skip the retry and may go to another model family
EARLIER_LINES = 8                # translated lines of the previous chunk shown for continuity
AROUND_BEFORE, AROUND_AFTER = 2, 1   # translated lines shown around a line filled into a translated episode
MAX_AROUND = 80                  # ... for at most this many lines per request
MAX_FAILURES = 2                 # an episode that fails this often in one session is left for a person
MAX_CRASHES = 5                  # unexpected errors in a row (nothing submitted between them) that stop the runner
KEEPALIVE_SECONDS = 1800         # a waiting runner touches its claimed workspaces so they are not released as idle
ENGINE_CACHE_SECONDS = 60
HOUSEKEEPING_SECONDS = 60        # abandoned claims are released at most this often while the runner looks for work
PENDING_CACHE_SECONDS = 5        # the queue length shown in the panel is read at most this often
PAUSED_MESSAGE = "Translations are paused (Pause translations is on); episodes in hand are finished first."
GUIDE_GROWTH_EVERY = 6           # translated episodes between two rounds of additions to the context guide
GUIDE_ADDITIONS_FILE = "guide_additions.txt"   # in an episode's workspace: the additions its reply asked for (empty: asked, none came)
BOOTSTRAP_TIMEOUT = 600          # seconds the bootstrap request may take
BOOTSTRAP_STATE = "building the show's context"   # a lane's state during the bootstrap
PREPARING_STATE = "preparing the episode"          # ... while its workspace is built (maybe extracting the subtitle)
CLAIM_PREFIX = "claim:"                            # held[] key of a claim whose workspace is being built
CONTEXT_CACHE_SECONDS = 300      # a show's glossary matcher and prompt prefix are reused this long
PID_STALE_SECONDS = 45 * 60      # a lock file not touched for this long belongs to a runner that died
TERMINAL = ("completed", "cancelled", "expired", "released")
NO_WORK = {"task": None, "project": None, "episode": None, "progress": None,
           "units_done": 0, "units_total": 0}   # a lane's fields while it has no episode in hand
SENSITIVE = re.compile(r"\b(child|children|kids?|bab(?:y|ies)|infants?|toddlers?|boys?|girls?|sons?|daughters?|"
                       r"grand(?:daughter|son|child)s?|orphans?|little ones?|lads?|lass(?:es)?|brats?|offspring|"
                       r"pregnan\w*|womb|unborn)\b", re.I)



def _human(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


