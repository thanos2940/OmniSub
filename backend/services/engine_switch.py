"""Switching who translates the queue: ``translation_engine`` "agent" (the agy runner, the Gemini API only
for lines the CLI refuses) or "model" (the Gemini API through the background worker).

The two never translate the queue together. To the model API: the runner takes no new episode, finishes
the ones it has, and stops (and does not start with Omnisub again); episodes queued for it go to the worker.
To the agent runner: the worker takes no new episode (it reads the setting on every claim) and the runner
starts watching the queue, when "Start with OmbiSub" is on.
"""

import logging
from typing import Dict

logger = logging.getLogger("omnisub.engine_switch")

ENGINES = ("agent", "model")


def apply(old: str, new: str) -> Dict:
    """Bring the runner and the queue in line with a changed engine; returns what was done."""
    if new == old or new not in ENGINES:
        return {}
    from services import agent_runner
    from utils.translation_queue import TranslationQueue

    if new == "model":
        runner = agent_runner.get_runner()
        stopping = runner.running()
        if stopping:
            runner.stop(reason="Translation moved to the Gemini API: finishing the episodes in hand, then stopping.")
        moved = TranslationQueue().release_agent_items()
        logger.info(f"Translation engine -> Gemini API; runner {'stopping' if stopping else 'not running'}, "
                    f"{moved} queued episode(s) handed to the worker.")
        return {"engine": "model", "runner_stopping": stopping, "handed_to_worker": moved}

    started = agent_runner.autostart()
    logger.info(f"Translation engine -> agent runner; runner {'started' if started else 'not started'}.")
    return {"engine": "agent", "runner_started": started}
