"""
Project context bootstrap — the context guide and glossary a first translation needs.

Those two artefacts carry most of what separates Omnisub from line-by-line MT:
the guide fixes tone, register and who-is-who; the glossary pins every name and
term. A project minted by a sync or an on-demand request has neither, so without
this the first episode a viewer actually asked for would be translated blind and
the glossary only grown afterwards by term harvesting.

Runs inside the translation worker, which executes several episodes of one
project in parallel. The per-project lock makes a season request build the
context once rather than once per episode, and everything is re-checked after
acquiring it because a sibling episode has usually just finished the work.
"""

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from utils import storage

logger = logging.getLogger(__name__)

_locks: Dict[str, asyncio.Lock] = {}


def _project_lock(project_name: str) -> asyncio.Lock:
    # Keyed case-insensitively to match the queue's NOCASE identity for projects.
    key = project_name.lower()
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    return lock


async def ensure_project_context(
    project_name: str,
    episode_name: str,
    target_language: str,
    *,
    want_context: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> Tuple[Dict, str]:
    """Return ``(glossary, context_guide)``, generating whichever is missing.

    The glossary is always seeded when empty (the worker has always done this).
    The context guide is only generated when ``want_context`` is set — it costs a
    research call, so it stays opt-in for the bulk sync/backlog lanes and is
    requested by the paths where a person is waiting on the result.

    A failure here never fails the translation: a missing guide degrades quality,
    a crashed episode delivers nothing.
    """
    say = log or (lambda _msg: None)

    async with _project_lock(project_name):
        meta = storage.load_project_metadata(project_name) or {}
        glossary = meta.get("glossary") or {"terms": []}
        context_guide = meta.get("context_guide") or ""

        need_glossary = not glossary.get("terms")
        need_context = want_context and not context_guide.strip()
        if not need_glossary and not need_context:
            return glossary, context_guide

        from utils.model_resolver import resolve_model
        from services.translation.helpers import _gather_project_text

        show_name = meta.get("show_name") or project_name
        text_lines = await _gather_project_text(project_name, [episode_name])

        if need_context:
            say(f"Building the context guide for '{show_name}' (first translation in this project)...")
            try:
                from adk_agents.operations import research_project_adk, enhance_context_guide_adk
                context_model = resolve_model("context", meta)
                research, _ = await research_project_adk(
                    show_name, text_lines, target_language, model_name=context_model
                )
                guide, _ = await enhance_context_guide_adk(
                    research.get("findings", ""), show_name,
                    target_language=target_language, model_name=context_model,
                )
                if guide and guide.strip():
                    context_guide = guide
            except Exception as e:
                logger.warning(f"Context guide generation for {project_name} failed (non-fatal): {e}")
                say(f"Context guide generation failed, continuing without it: {e}")

        if need_glossary:
            say(f"Glossary empty for '{show_name}'. Seeding it from {episode_name}...")
            from adk_agents.operations import generate_glossary_adk
            generated, _ = await generate_glossary_adk(
                text_lines, show_name, target_language,
                model_name=resolve_model("glossary", meta),
                # With no text to mine, the only source of names is research.
                enable_research=not text_lines,
            )
            if generated.get("terms"):
                glossary = generated

        # Re-read before writing: term harvesting from an episode that finished while
        # the model calls above were in flight may have saved the project since.
        fresh = storage.load_project_metadata(project_name) or meta
        changed = False
        if need_context and context_guide.strip() and not (fresh.get("context_guide") or "").strip():
            fresh["context_guide"] = context_guide
            changed = True
        if need_glossary and glossary.get("terms") and not (fresh.get("glossary") or {}).get("terms"):
            fresh["glossary"] = glossary
            changed = True

        if changed:
            storage.save_project_metadata(project_name, fresh)
            try:
                from utils.cache_manager import invalidate_cache
                invalidate_cache(fresh)
            except Exception:
                pass
            try:
                from adk_config import adk_session_manager
                if need_context:
                    await adk_session_manager.update_context(project_name, fresh.get("context_guide", ""))
                if need_glossary:
                    await adk_session_manager.update_glossary(project_name, fresh.get("glossary", {}))
            except Exception:
                pass
            logger.info(
                f"Bootstrapped project context for {project_name}: "
                f"guide={'yes' if (fresh.get('context_guide') or '').strip() else 'no'}, "
                f"glossary_terms={len((fresh.get('glossary') or {}).get('terms', []))}"
            )

        return (
            fresh.get("glossary") or glossary,
            fresh.get("context_guide") or context_guide,
        )


# ---------------------------------------------------------------------------
# Agent CLI path: one prompt builds the context guide and the glossary
# ---------------------------------------------------------------------------
#
# With the translation engine on "agent" the runner (and the guide / glossary buttons) ask the agent CLI
# instead of the ADK agents: a single prompt with the show's facts and a spread of its dialogue answers with
# `G|` guide lines and `+ term | …` glossary lines, which go through the same merge an episode's submit uses.

SAMPLE_LINES = 400               # source lines shown to the model
SAMPLE_EPISODES = 3              # ... taken from this many episodes, the earliest first
SAMPLE_BLOCK = 8                 # consecutive lines kept together, so the sample still reads as conversation
RETRY_AFTER_FAILURE = timedelta(hours=24)
TRANSIENT = ("stopped", "unavailable", "busy")   # AgyError kinds that say nothing about the show

_thread_locks: Dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def thread_lock(project_name: str) -> threading.Lock:
    """One lock per project (case-insensitive, like the queue), for the threads of the runner's lanes."""
    with _thread_locks_guard:
        return _thread_locks.setdefault(project_name.lower(), threading.Lock())


def _when(value) -> Optional[datetime]:
    try:
        stamp = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def needs_bootstrap(meta: Dict, force: bool = False) -> bool:
    """True for a project (resolved metadata) without a context guide or without glossary terms. After a
    failed attempt it waits ``RETRY_AFTER_FAILURE`` before asking again, unless ``force``."""
    if (meta.get("context_guide") or "").strip() and (meta.get("glossary") or {}).get("terms"):
        return False
    failed = _when(meta.get("bootstrap_error_at"))
    return force or not failed or datetime.now(timezone.utc) - failed >= RETRY_AFTER_FAILURE


def project_brief(meta: Dict) -> Dict:
    """What the metadata knows of the work besides its name (Sonarr/Radarr fields, when sync stored them)."""
    def first(*keys):
        return next((meta[k] for k in keys if meta.get(k)), None)
    genres = first("genres", "arr_genres") or []
    return {"year": first("year", "arr_year"), "overview": first("overview", "arr_overview"),
            "genres": genres if isinstance(genres, list) else [genres],
            "movie": meta.get("arr_media_type") == "movie" or str(meta.get("type", "")).lower() == "movie"}


def spread(lines: List[str], count: int) -> List[str]:
    """``count`` lines spread evenly over ``lines``, in blocks of consecutive lines."""
    if len(lines) <= count:
        return lines
    blocks = max(1, count // SAMPLE_BLOCK)
    last = len(lines) - SAMPLE_BLOCK
    starts = [0] if blocks == 1 else [i * last // (blocks - 1) for i in range(blocks)]
    return [line for s in starts for line in lines[s:s + SAMPLE_BLOCK]][:count]


def sample_dialogue(project_name: str, episodes: Optional[List[str]] = None, limit: int = SAMPLE_LINES,
                    max_episodes: int = SAMPLE_EPISODES) -> Tuple[List[str], int]:
    """About ``limit`` source dialogue lines spread over up to ``max_episodes`` episodes (the earliest that have
    subtitles, or the named ``episodes``). Returns ``(lines, episodes used)``."""
    picked: List[List[str]] = []
    for name in episodes or storage.list_episodes(project_name):
        if len(picked) >= max_episodes:
            break
        try:
            cues = storage.load_episode_data(project_name, name) or []
        except Exception:
            continue
        lines = []
        for cue in cues:
            if not isinstance(cue, dict):
                continue
            meta = cue.get("_ass") if isinstance(cue.get("_ass"), dict) else {}
            text = " ".join((cue.get("original") or "").split())
            if len(text) > 1 and meta.get("kind") != "sign" and meta.get("translatable") is not False:
                lines.append(text)
        if lines:
            picked.append(lines)
    if not picked:
        return [], 0
    return [line for lines in picked for line in spread(lines, limit // len(picked))], len(picked)


def clean_terms(raw_terms: List[Dict]) -> List[Dict]:
    """Parsed ``+ term`` lines with the gender in the spelling the glossary and the profiles use (or dropped)."""
    from utils.character_profiles import normalize_gender
    out = []
    for term in raw_terms or []:
        term = dict(term)
        gender = normalize_gender(term.pop("gender", ""))
        if gender:
            term["gender"] = gender
        out.append(term)
    return out


def ask_for_context(project_name: str, ask: Callable[[str], str], *, want_guide: bool, want_terms: bool,
                    improve_guide: bool = False, episodes: Optional[List[str]] = None) -> Dict:
    """Build the brief prompt for a project, send it with ``ask`` and parse the answer:
    ``{"guide", "terms", "prompt", "text"}``. Raises whatever ``ask`` raises."""
    from services import agent_prompts as ap
    resolved = storage.load_resolved_project_metadata(project_name) or {}
    lines, used = sample_dialogue(project_name, episodes)
    known = [t["term"] for t in (resolved.get("glossary") or {}).get("terms") or [] if t.get("term")]
    prompt = ap.context_prompt(
        show=resolved.get("show_name") or project_name, target_name=resolved.get("target_language") or "Greek",
        brief=project_brief(resolved), lines=lines, want_guide=want_guide, want_terms=want_terms,
        guide=(resolved.get("context_guide") or "") if improve_guide else "", known_terms=known, episodes=used)
    text = ask(prompt)
    parsed = ap.parse_reply(text, allowed=set())
    return {"guide": parsed["guide"].strip(), "terms": clean_terms(parsed["terms"]), "prompt": prompt, "text": text}


def _record(project_name: str, **changes) -> None:
    meta = storage.load_project_metadata(project_name) or {}
    meta.update(changes)
    storage.save_project_metadata(project_name, meta)


def bootstrap_project(project_name: str, ask: Callable[[str], str], *, service, force: bool = False,
                      write_lock=None) -> Dict:
    """Give a project without a context guide or glossary both, before its first translation: one prompt (see
    ``ask_for_context``), saved through the same merge an episode's submit uses, then ``bootstrapped_at`` (or
    ``bootstrap_error`` and ``bootstrap_error_at``) in the project metadata. One thread bootstraps a project
    at a time; the others wait and find it done. A failure is reported, never raised.

    Returns ``{"status": "done" | "partial" | "failed" | "skipped", "message", "terms", "guide"}``."""
    from contextlib import nullcontext
    from utils.language_codes import to_code
    result = {"status": "skipped", "message": "", "terms": 0, "guide": False}
    with thread_lock(project_name):
        resolved = storage.load_resolved_project_metadata(project_name) or {}
        want_guide = not (resolved.get("context_guide") or "").strip()
        want_terms = not (resolved.get("glossary") or {}).get("terms")
        if not (want_guide or want_terms) or not needs_bootstrap(resolved, force):
            return result
        code = to_code(resolved.get("target_language") or "Greek")
        failed_at = datetime.now(timezone.utc).isoformat()
        try:
            found = ask_for_context(project_name, ask, want_guide=want_guide, want_terms=want_terms)
        except Exception as e:
            result.update(status="failed", message=str(e))
            if getattr(e, "kind", "failed") not in TRANSIENT:
                with write_lock or nullcontext():
                    _record(project_name, bootstrap_error=str(e)[:300], bootstrap_error_at=failed_at)
            return result
        with write_lock or nullcontext():
            guide_saved = bool(want_guide and found["guide"] and service.save_context_guide(project_name, found["guide"]))
            added = service._merge_terms(project_name, found["terms"], code)["added"] if want_terms else 0
            lacking = [what for what, wanted, got in (("context guide", want_guide, guide_saved),
                                                      ("glossary terms", want_terms, added)) if wanted and not got]
            if lacking:
                result["message"] = "the answer gave no " + " and no ".join(lacking)
                _record(project_name, bootstrap_error=result["message"], bootstrap_error_at=failed_at)
            else:
                _record(project_name, bootstrapped_at=failed_at, bootstrap_error=None, bootstrap_error_at=None)
        result.update(status="failed" if len(lacking) == want_guide + want_terms else ("partial" if lacking else "done"),
                      terms=added, guide=guide_saved)
        return result
