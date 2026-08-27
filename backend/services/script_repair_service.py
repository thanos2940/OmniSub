"""Project-wide wrong-alphabet repair (see utils/script_guard.py).

Backfill for episodes translated before the script guard existed, and the manual
"fix this show" action in the UI. Two stages, cheapest first:

1. **Deterministic scrub** — visual-twin characters are rewritten for zero tokens.
2. **One batched repair call per chunk** — everything the twin table can't resolve
   is gathered across the *whole project* and sent together, so a show with 193 bad
   lines spread over 55 episodes costs ~3 requests, not 55 and certainly not 193.

Lines the model doesn't return, or returns still contaminated, stay flagged
``needs_review`` rather than being silently accepted.
"""

import asyncio
import logging
from typing import Dict, List, Optional

from utils import storage
from utils.jobs_manager import update_job
from utils.language_codes import to_code
from utils.rate_limiter import DailyLimitExhausted, active_model_var, translation_rate_limiter
from utils.script_guard import foreign_issues, scrub_rows

logger = logging.getLogger(__name__)

# Lines per model request. Big enough that a whole show is a handful of calls,
# small enough that the numbered response stays reliably aligned.
DEFAULT_CHUNK_SIZE = 60

_REVIEW_NOTE = "Wrong-script characters in translation"


def collect_project_issues(project_name: str, target_code: str) -> Dict:
    """Scrub every episode deterministically and report what is left.

    Returns ``{"episodes": {ep: rows}, "chars_fixed": int, "tickets": [...]}``
    where each ticket carries the episode/line it came from so a batched response
    can be routed back. Nothing is written to disk here.
    """
    episodes: Dict[str, List[Dict]] = {}
    tickets: List[Dict] = []
    chars_fixed = 0

    for ep_name in storage.list_episodes(project_name):
        ep_data = storage.load_episode(project_name, ep_name)
        if not ep_data or not ep_data.get("data"):
            continue
        rows = ep_data["data"]
        n_fixed = scrub_rows(rows, target_code, target_code)

        ep_tickets = []
        for i, row in enumerate(rows):
            current = ((row.get("translations", {}) or {}).get(target_code)
                       or row.get("translated") or "")
            if not current:
                continue
            issues = foreign_issues(row.get("original") or "", current, target_code)
            if issues:
                ep_tickets.append({
                    "episode": ep_name, "index": i,
                    "source": row.get("original") or "", "current": current,
                    "issues": issues,
                })

        if n_fixed or ep_tickets:
            episodes[ep_name] = rows
            chars_fixed += n_fixed
            tickets.extend(ep_tickets)

    return {"episodes": episodes, "chars_fixed": chars_fixed, "tickets": tickets}


async def _repair_chunk(
    chunk: List[Dict],
    target_lang_name: str,
    model_name: str,
) -> Dict[int, str]:
    """One model call for a whole chunk. Returns ``{item_number: corrected_text}``."""
    from adk_agents.llm_factory import generate
    from utils.api_call_wrapper import rate_limited_call
    from utils.llm_utils import parse_translations_from_text, strip_reasoning_blocks
    from utils.qc_funnel import build_repair_prompt_items

    prompt = build_repair_prompt_items(chunk, target_lang_name)

    async def _call():
        return await generate(
            model_name=model_name,
            prompt=prompt,
            system_instruction=f"You are a meticulous {target_lang_name} subtitle corrector.",
            temperature=0.2,
            role="repair",
        )

    response = await rate_limited_call(_call, rate_limiter=translation_rate_limiter)
    parsed = parse_translations_from_text(strip_reasoning_blocks(response or ""))
    return {p["index"]: (p["text"] or "").strip() for p in parsed}


async def repair_project_scripts(
    job_id: str,
    project_name: str,
    model: Optional[str] = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    export: bool = True,
    use_llm: bool = True,
) -> None:
    """Job body: scrub, batch-repair, save, re-export.

    With ``use_llm=False`` only the deterministic stage runs and the remainder is
    flagged for review — zero requests, zero tokens.
    """
    update_job(job_id, status="running", progress=0.0, message="Scanning translations...")
    try:
        proj_meta = storage.load_project_metadata(project_name)
        if not proj_meta:
            update_job(job_id, status="failed", message="Project not found")
            return

        target_lang = proj_meta.get("target_language", "Greek")
        target_code = to_code(target_lang)

        found = await asyncio.to_thread(collect_project_issues, project_name, target_code)
        episodes, tickets = found["episodes"], found["tickets"]
        chars_fixed = found["chars_fixed"]

        if not episodes:
            update_job(job_id, status="completed", progress=100.0,
                       message="No wrong-alphabet characters found",
                       result={"chars_fixed": 0, "lines_repaired": 0, "lines_flagged": 0,
                               "requests": 0, "episodes_changed": 0})
            return

        update_job(job_id, progress=15.0,
                   message=f"Fixed {chars_fixed} look-alike character(s); {len(tickets)} line(s) need the model",
                   log=f"{project_name}: deterministic pass fixed {chars_fixed} character(s) in "
                       f"{len(episodes)} episode(s); {len(tickets)} line(s) ticketed")

        repaired = 0
        requests_made = 0
        if tickets and use_llm:
            if not model:
                from utils.model_resolver import resolve_model
                model = resolve_model("reconciliation", proj_meta)
            active_model_var.set(model)

            chunks = [tickets[i:i + chunk_size] for i in range(0, len(tickets), chunk_size)]
            update_job(job_id, log=f"{project_name}: repairing {len(tickets)} line(s) in "
                                   f"{len(chunks)} batched request(s) (model: {model})")

            for c_i, chunk in enumerate(chunks):
                try:
                    by_item = await _repair_chunk(chunk, target_lang, model)
                    requests_made += 1
                except DailyLimitExhausted:
                    update_job(job_id, log="Daily model quota exhausted — remaining lines stay flagged for review")
                    break
                except Exception as e:
                    update_job(job_id, log=f"Repair batch {c_i + 1} failed (lines stay flagged): {e}")
                    continue

                for n, ticket in enumerate(chunk, start=1):
                    new_text = by_item.get(n) or ""
                    if not new_text:
                        continue
                    row = episodes[ticket["episode"]][ticket["index"]]
                    # Only accept a correction that actually resolved the contamination.
                    if foreign_issues(ticket["source"], new_text, target_code):
                        continue
                    row.setdefault("translations", {})[target_code] = new_text
                    row["translated"] = new_text
                    row.pop("needs_review", None)
                    row.pop("review_issues", None)
                    ticket["repaired"] = True
                    repaired += 1

                update_job(job_id, progress=15.0 + 65.0 * (c_i + 1) / len(chunks),
                           message=f"Repaired {repaired}/{len(tickets)} line(s) in {requests_made} request(s)")

        # Whatever is still contaminated goes to the review queue instead of shipping.
        flagged = 0
        for ticket in tickets:
            if ticket.get("repaired"):
                continue
            row = episodes[ticket["episode"]][ticket["index"]]
            row["needs_review"] = True
            existing = row.get("review_issues") or ""
            if _REVIEW_NOTE not in existing:
                row["review_issues"] = f"{existing}; {_REVIEW_NOTE}" if existing else _REVIEW_NOTE
            flagged += 1

        update_job(job_id, progress=85.0, message="Saving episodes...")

        def _persist() -> int:
            from services.translation_service import auto_export_translated_subtitle
            changed = 0
            for ep_name, rows in episodes.items():
                ep_meta = storage.load_episode_metadata(project_name, ep_name) or {}
                storage.save_episode(project_name, ep_name, rows, ep_meta)
                changed += 1
                if export:
                    auto_export_translated_subtitle(project_name, ep_name, rows, ep_meta)
            return changed

        episodes_changed = await asyncio.to_thread(_persist)

        # needs_review counts moved — drop the cached global queue so the badge refreshes.
        try:
            from routers.review import _global_review_cache
            _global_review_cache["ts"] = 0.0
        except Exception:
            pass

        update_job(
            job_id, status="completed", progress=100.0,
            message=(f"Fixed {chars_fixed} character(s), repaired {repaired} line(s) "
                     f"in {requests_made} request(s), flagged {flagged} for review"),
            result={
                "chars_fixed": chars_fixed,
                "lines_repaired": repaired,
                "lines_flagged": flagged,
                "requests": requests_made,
                "episodes_changed": episodes_changed,
            },
        )
    except Exception as e:
        logger.error(f"Script repair failed for {project_name}: {e}", exc_info=True)
        update_job(job_id, status="failed", message=f"Script repair failed: {e}")
