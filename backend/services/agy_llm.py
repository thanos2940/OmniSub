"""
One-off requests to the agent CLI — "agy first" outside the runner's lanes
===========================================================================
Everything that is not an episode in the queue but still needs a model (retranslate one
line, build a context guide or glossary, summarise an episode, harvest terms, repair a
wrong alphabet) asks Antigravity (or whichever CLI the runner is set to) before anything
else: ``complete(prompt, purpose=...)`` uses the runner's engine, model and fallback chain
and honours the pauses the runner already knows about.

The Gemini API is the exception, never the rule. Only a prompt the CLI model's safety
filter *refuses* goes there (``api_rescue``, ``rescue_model`` and Omnisub's
``gemini_safety_filter`` apply, exactly as for the runner's refused lines). With no CLI, or
every model failing for another reason, the call raises ``AgyError`` — a one-off request
never spends API money behind the user's back.

``complete`` blocks; async code calls ``acomplete``. At most ``MAX_CONCURRENT`` one-off
requests run at once, so a burst of them cannot starve the runner's lanes.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from utils import paths, storage
from services import agent_cli, agent_prompts as ap, agent_workspace as aw

logger = logging.getLogger("omnisub.agy_llm")

DEFAULT_TIMEOUT = 300.0          # seconds one request may take, the whole fallback chain included
MAX_CONCURRENT = 2
CONTEXT_PAIRS = 4                # neighbouring lines shown on each side of a retranslated line
GUIDE_EXCERPT = 1800             # characters of the context guide a one-line prompt carries

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)


class AgyError(RuntimeError):
    """A one-off request could not be answered. ``kind``: unavailable (no CLI, every model paused),
    busy, blocked (the safety filter refused and nothing else may answer), timeout, failed."""

    def __init__(self, message: str, kind: str = "failed"):
        super().__init__(message)
        self.kind = kind


def agent_mode(cfg: Optional[Dict] = None) -> bool:
    """True when translation is handed to the agent CLI (the default); False for the Gemini model engine."""
    cfg = cfg if cfg is not None else (storage.load_global_config() or {})
    return cfg.get("translation_engine", "agent") == "agent"


def _scratch() -> Path:
    folder = Path(paths.DATA_DIR) / "agy_calls"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _call(runner, model: str, prompt: str, purpose: str, project: Optional[str], stop: threading.Event,
          on_proc: Optional[Callable], deadline: float) -> agent_cli.Reply:
    """One CLI request, cut off when the deadline passes."""
    engine, exe = runner.config["engine"], runner._exe()
    left = deadline - time.time()
    if left <= 0:
        raise AgyError("The request timed out.", "timeout")
    state: Dict = {"timer": None, "late": False}

    def started(proc) -> None:
        def cut() -> None:
            state["late"] = True
            agent_cli.kill_process(proc)
        state["timer"] = threading.Timer(left, cut)
        state["timer"].start()
        if on_proc:
            on_proc(proc)

    try:
        reply = agent_cli.call_model(engine, exe, model, prompt, _scratch(), stop, on_proc=started)
    finally:
        if state["timer"]:
            state["timer"].cancel()
    runner.note_call(model, reply, project or "", purpose)
    if state["late"]:
        raise AgyError("The request timed out.", "timeout")
    return reply


def _refused(runner, model: str, prompt: str, purpose: str, project: Optional[str], stop: threading.Event,
             on_proc: Optional[Callable], deadline: float) -> str:
    """The CLI model's safety filter refused the prompt: the Gemini API answers it when a rescue is set up,
    else a model of another family does."""
    api = runner._rescue_model(project or "")
    if api:
        logger.info(f"[agy] {purpose}: refused by {model or 'the default model'}; sending it to the Gemini API ({api})")
        reply = runner._api_call(api, prompt, project or "", purpose)
        if not reply.failure:
            return reply.text.strip()
        raise AgyError(f"The safety filter refused this request and the Gemini API did not answer it either "
                       f"({reply.message[:200]}).", "blocked")
    other = runner._other_family(model, exclude=(model,))
    if other:
        reply = _call(runner, other, prompt, purpose, project, stop, on_proc, deadline)
        if not reply.failure and reply.text.strip():
            return reply.text.strip()
    raise AgyError("The safety filter refused this request. Turn on the Gemini API rescue (it needs a Gemini API key) "
                   "to have such requests answered there.", "blocked")


def ask(prompt: str, *, purpose: str, project: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT,
        stop: Optional[threading.Event] = None, on_proc: Optional[Callable] = None, runner=None) -> str:
    """The request itself (no concurrency limit): used by ``complete`` and, with the runner's own stop
    event, by the runner's bootstrap. Raises ``AgyError``."""
    from services import agent_runner
    runner = runner or agent_runner.get_runner()
    engine = runner.config["engine"]
    if not runner._exe():
        raise AgyError(f"{agent_cli.ENGINES.get(engine, engine)} is not installed (or not on PATH), so this "
                       f"cannot be done with it. Install it, or switch the translation engine to the Gemini model "
                       f"in the settings.", "unavailable")
    stop = stop or threading.Event()
    deadline = time.time() + timeout
    problems: List[str] = []
    waiting: List[float] = []
    limited = True               # every failure so far was a quota or rate limit
    for model in runner._models():
        if not runner._available(model):
            waiting.append(runner.pauses.get(model or "default", 0))
            continue
        reply = _call(runner, model, prompt, purpose, project, stop, on_proc, deadline)
        if reply.failure == "stopped":
            raise AgyError("The request was stopped.", "stopped")
        if reply.failure == "blocked":
            return _refused(runner, model, prompt, purpose, project, stop, on_proc, deadline)
        if reply.failure in ("quota", "rate"):
            runner._failure(reply, model, -1, purpose)   # pauses the model (group); the next one takes over
            problems.append(f"{model or 'default model'}: {reply.message[:160]}")
            continue
        limited = False
        if reply.failure == "auth":
            raise AgyError(f"{agent_cli.ENGINES.get(engine, engine)} is not signed in or not allowed: "
                           f"{reply.message[:200]} — sign in (run the CLI once in a terminal).", "unavailable")
        if reply.failure or not reply.text.strip():
            problems.append(f"{model or 'default model'}: {reply.message[:160] or 'the answer was empty'}")
            continue
        return reply.text.strip()
    if not problems and waiting:
        until = datetime.fromtimestamp(min(waiting))
        raise AgyError(f"{agent_cli.ENGINES.get(engine, engine)} is paused until {until:%H:%M} (quota or rate limit).",
                       "unavailable")
    raise AgyError("No model answered: " + "; ".join(problems), "unavailable" if limited else "failed")


def complete(prompt: str, *, purpose: str, project: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT) -> str:
    """The answer to ``prompt`` from the agent CLI (``purpose``: a short label for the usage table and the log).
    Raises ``AgyError`` — see the module docstring for when the Gemini API steps in."""
    if not _slots.acquire(timeout=timeout):
        raise AgyError("Too many requests are waiting for the agent CLI; try again in a moment.", "busy")
    try:
        return ask(prompt, purpose=purpose, project=project, timeout=timeout)
    finally:
        _slots.release()


async def acomplete(prompt: str, *, purpose: str, project: Optional[str] = None,
                    timeout: float = DEFAULT_TIMEOUT) -> str:
    return await asyncio.to_thread(complete, prompt, purpose=purpose, project=project, timeout=timeout)


# ------------------------------------------------------------------ one line, with its surroundings

def _language_name(code: str) -> str:
    from utils.language_codes import LANG_CODES
    return next((name for name, c in LANG_CODES.items() if c == code), code)


def line_context(project: str, cues: List[Dict], index: int, lang_code: str, is_primary: bool) -> Dict:
    """What the prompt for one line needs: the lines around it with the translations they have, the
    glossary terms the line uses and the characters who speak (the runner's matcher and profiles)."""
    from services import agent_runner
    from utils.glossary_judgment import brief_lines

    def pair(i: int) -> Optional[tuple]:
        cue = cues[i]
        if not isinstance(cue, dict):
            return None
        meta = cue.get("_ass") if isinstance(cue.get("_ass"), dict) else {}
        src = (cue.get("original") or "").strip()
        if not src or meta.get("kind") == "sign" or meta.get("translatable") is False:
            return None
        return src, aw.cue_translation(cue, lang_code, is_primary)

    before = [p for p in (pair(i) for i in range(max(0, index - CONTEXT_PAIRS * 2), index)) if p][-CONTEXT_PAIRS:]
    after = [p for p in (pair(i) for i in range(index + 1, min(len(cues), index + 1 + CONTEXT_PAIRS * 2))) if p][:CONTEXT_PAIRS]
    meta = storage.load_resolved_project_metadata(project) or {}
    target_name = (meta.get("target_language") or "Greek") if is_primary else _language_name(lang_code)
    runner = agent_runner.get_runner()
    shared = runner._project_context(project, target_name)
    text = cues[index].get("original", "")
    guide = (meta.get("context_guide") or "").strip()
    if len(guide) > GUIDE_EXCERPT:
        guide = guide[:GUIDE_EXCERPT].rsplit("\n", 1)[0] + "\n…"
    return {"target_name": target_name, "before": before, "after": after, "guide": guide,
            "glossary_lines": brief_lines(shared["matcher"].find_all([text])),
            "characters": runner._characters(project, shared["glossary"], [text] + [s for s, _ in before + after])}


def retranslate_line(project: str, episode: str, cues: List[Dict], index: int, lang_code: str,
                     is_primary: bool = True) -> str:
    """A fresh translation of one cue, asked of the agent CLI with its surroundings. Raises ``AgyError``."""
    from utils.glossary_judgment import GLOSSARY_INSTRUCTION
    from utils.text_normalize import sanitize_translation_text
    from utils.translation_rules import build_style_guide
    cue = cues[index]
    source = cue.get("original", "")
    ctx = line_context(project, cues, index, lang_code, is_primary)
    static = ap.static_part(ctx["target_name"], ctx["guide"], build_style_guide(ctx["target_name"]))
    prompt = ap.line_prompt(static, title=f"{project} — {episode}", text=source, before=ctx["before"],
                            after=ctx["after"], characters=ctx["characters"], glossary_lines=ctx["glossary_lines"],
                            glossary_note=GLOSSARY_INSTRUCTION, current=aw.cue_translation(cue, lang_code, is_primary))
    reply = complete(prompt, purpose="retranslate_line", project=project)
    answer = ap.parse_reply(reply, allowed={1}, two_line={1} if "\n" in source.strip() else None)["answers"].get(1)
    if not answer:
        raise AgyError(f"The reply had no usable line: {reply[:200]!r}", "failed")
    return sanitize_translation_text(answer, source_text=source, target_lang_code=lang_code)


async def aretranslate_line(project: str, episode: str, cues: List[Dict], index: int, lang_code: str,
                            is_primary: bool = True) -> str:
    return await asyncio.to_thread(retranslate_line, project, episode, cues, index, lang_code, is_primary)
