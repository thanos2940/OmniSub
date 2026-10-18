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
from typing import Callable, Dict, Optional, Tuple

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
