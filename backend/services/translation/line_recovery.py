"""
Missing Line Recovery, Minority Term Reconciliation, and Project-Wide Gap Repair.
"""

import logging
import re
from typing import List, Dict, Optional

from utils import storage
from utils.rate_limiter import RateLimiter, DailyLimitExhausted, translation_rate_limiter, active_model_var
from utils.api_call_wrapper import rate_limited_call
from utils.llm_utils import strip_reasoning_blocks, parse_translations_from_text
from utils.jobs_manager import update_job, jobs
from services.translation.export_formatter import auto_export_translated_subtitle

logger = logging.getLogger(__name__)


async def _retranslate_minority_lines(
    project_name: str,
    parsed_srt: List[Dict],
    drift_findings: List[Dict],
    target_lang_code: str,
    target_lang_name: str,
    job_id: str,
    rate_limiter: RateLimiter,
) -> int:
    """Re-translate drifting lines to the canonical term renderings.

    ONE batched Gemini call for the whole episode — never one call per line.
    """
    line_to_constraints: Dict[int, List] = {}
    for drift in drift_findings:
        term = drift["term"]
        canonical = drift["canonical"]
        for ml in drift["minority_lines"]:
            line_to_constraints.setdefault(ml["index"], []).append((term, canonical))

    if not line_to_constraints:
        return 0

    from utils.model_resolver import resolve_model
    from utils.language_codes import to_code
    metadata = storage.load_project_metadata(project_name) or {}
    model_name = resolve_model("reconciliation", metadata)
    primary_code = to_code(metadata.get("target_language", "Greek"))

    items = sorted(line_to_constraints.items())
    blocks = []
    for n, (idx, constraints) in enumerate(items, start=1):
        line = parsed_srt[idx]
        orig = (line.get("original", "") or "").replace("\n", " ")
        curr = (line.get("translations", {}).get(target_lang_code, "")
                or line.get("translated", "") or "").replace("\n", " ")
        required = "; ".join(f"\"{term}\" -> \"{canonical}\"" for term, canonical in constraints)
        blocks.append(f"{n}| EN: {orig}\n   CURRENT: {curr}\n   REQUIRED TERMS: {required}")
    listing = "\n".join(blocks)

    prompt = (
        f"Re-translate each {target_lang_name} subtitle line below so it uses the "
        f"REQUIRED term rendering(s) EXACTLY, while staying faithful to the EN source "
        f"and natural in {target_lang_name}. Keep each translation on a single line.\n\n"
        f"{listing}\n\n"
        f"Output ONLY numbered corrected lines, one per item:\n"
        f"1| corrected line one\n2| corrected line two"
    )

    from adk_agents.llm_factory import generate

    try:
        response_text = await generate(
            model_name=model_name,
            prompt=prompt,
            system_instruction=f"You are a {target_lang_name} subtitle term-consistency enforcer.",
            role="reconciliation",
        )
    except DailyLimitExhausted:
        raise
    except Exception as e:
        update_job(job_id, log=f"Reconciliation batch call failed (non-critical): {e}")
        return 0

    parsed = parse_translations_from_text(strip_reasoning_blocks(response_text or ""))
    by_item = {p["index"]: p["text"] for p in parsed}

    retranslated_count = 0
    from utils.text_normalize import sanitize_translation_text
    for n, (idx, _constraints) in enumerate(items, start=1):
        new_trans = (by_item.get(n) or "").strip()
        if not new_trans:
            continue
        new_trans = sanitize_translation_text(
            new_trans,
            source_text=parsed_srt[idx].get("original", ""),
            target_lang=target_lang_name,
            target_lang_code=target_lang_code
        )
        curr = (parsed_srt[idx].get("translations", {}).get(target_lang_code, "")
                or parsed_srt[idx].get("translated", ""))
        parsed_srt[idx].setdefault("translations", {})[target_lang_code] = new_trans
        if target_lang_code == primary_code:
            parsed_srt[idx]["translated"] = new_trans
        retranslated_count += 1
        update_job(job_id, log=f"Line {idx + 1} reconciled: '{curr}' -> '{new_trans}'")

    return retranslated_count


async def _translate_missing_line_with_context(
    project_name: str,
    episode_name: str,
    line_idx: int,
    parsed_srt: List[Dict],
    translated_map: Dict[int, str],
    target_lang_name: str,
    target_lang_code: str,
    model_name: str,
    rate_limiter: RateLimiter,
) -> Optional[str]:
    context_lines = []
    
    def get_trans(idx):
        if idx in translated_map:
            return translated_map[idx]
        line = parsed_srt[idx]
        return (line.get("translations", {}).get(target_lang_code)
                or line.get("translated") or "")

    for offset in [-2, -1]:
        idx = line_idx + offset
        if 0 <= idx < len(parsed_srt):
            context_lines.append(
                f"Earlier Line {offset + 3}:\n"
                f"Source (English): {parsed_srt[idx].get('original', '')}\n"
                f"Translation ({target_lang_name}): {get_trans(idx)}"
            )
            
    context_lines.append(
        f"Line to Translate:\n"
        f"Source (English): {parsed_srt[line_idx].get('original', '')}\n"
        f"Translation ({target_lang_name}): [TO BE TRANSLATED]"
    )
    
    for offset in [1, 2]:
        idx = line_idx + offset
        if 0 <= idx < len(parsed_srt):
            context_lines.append(
                f"Later Line {offset}:\n"
                f"Source (English): {parsed_srt[idx].get('original', '')}\n"
                f"Translation ({target_lang_name}): {get_trans(idx)}"
            )
            
    prompt = (
        f"You are translating a subtitle from English to {target_lang_name}.\n"
        f"We have a missing translation for a specific line. Here is the context of surrounding lines (2 earlier and 2 later lines if available):\n\n"
        + "\n\n".join(context_lines) +
        f"\n\nProduce ONLY the translated text in {target_lang_name} for the 'Line to Translate'. Do not include any tags, notes, markdown formatting, or extra text. Just provide the raw translation."
    )
    
    try:
        from adk_agents.llm_factory import generate
        res = await generate(
            model_name=model_name,
            prompt=prompt,
            role="translation",
        )
        if res:
            res_stripped = res.strip()
            res_stripped = strip_reasoning_blocks(res_stripped)
            res_stripped = re.sub(r'^["\'\s]+|["\'\s]+$', '', res_stripped)
            from utils.text_normalize import sanitize_translation_text
            res_stripped = sanitize_translation_text(
                res_stripped,
                source_text=parsed_srt[line_idx].get("original", ""),
                target_lang=target_lang_name,
                target_lang_code=target_lang_code
            )
            if (target_lang_name or "").strip().lower() in ("greek", "el", "ελληνικά") or target_lang_code in ("el", "ell", "gre", "grk"):
                from utils.text_normalize import fix_japanese_name_greek_tonos
                res_stripped = fix_japanese_name_greek_tonos(res_stripped)
            return res_stripped
    except Exception as e:
        logger.warning(f"Failed to translate missing line {line_idx} with context: {e}")
    return None


async def translate_project_missing_lines(job_id: str, project_name: str) -> None:
    """Background task to scan all episodes in the project, find missing lines, and translate them with context."""
    from utils.language_codes import to_code
    from utils.model_resolver import resolve_model

    try:
        update_job(job_id, status="running", progress=5.0, message="Scanning for missing lines...")
        proj_meta = storage.load_project_metadata(project_name)
        if not proj_meta:
            update_job(job_id, status="failed", message="Project not found")
            return

        target_names = storage.project_target_languages(proj_meta)
        primary_lang = proj_meta.get("target_language", "Greek")
        primary_code = to_code(primary_lang)
        lang_codes = [to_code(name) for name in target_names]
        if not lang_codes:
            lang_codes = [primary_code]

        episodes = storage.list_episodes(project_name)
        model_name = resolve_model("translation", proj_meta)
        active_model_var.set(model_name)

        missing_tasks = []
        for ep_name in episodes:
            ep_data = storage.load_episode(project_name, ep_name)
            if not ep_data or "data" not in ep_data:
                continue
            parsed_srt = ep_data["data"]
            for i, line in enumerate(parsed_srt):
                original = line.get("original", "").strip()
                if not original:
                    continue
                for code in lang_codes:
                    translated = (line.get("translations", {}).get(code)
                                  or (line.get("translated") if code == primary_code else None)
                                  or "").strip()
                    if not translated:
                        lang_name = primary_lang
                        for name in target_names:
                            if to_code(name) == code:
                                lang_name = name
                                break
                        missing_tasks.append({
                            "episode_name": ep_name,
                            "line_idx": i,
                            "lang_code": code,
                            "lang_name": lang_name
                        })

        total_missing = len(missing_tasks)
        if total_missing == 0:
            update_job(job_id, status="completed", progress=100.0, message="No missing lines found.")
            return

        update_job(job_id, progress=10.0, message=f"Found {total_missing} missing lines. Starting context-aware translation...")

        tasks_by_episode = {}
        for task in missing_tasks:
            tasks_by_episode.setdefault(task["episode_name"], []).append(task)

        fixed_count = 0
        processed = 0
        for ep_name, ep_tasks in tasks_by_episode.items():
            if jobs[job_id].cancelled:
                update_job(job_id, status="cancelled", message="Cancelled by user")
                return

            ep_data = storage.load_episode(project_name, ep_name)
            if not ep_data or "data" not in ep_data:
                processed += len(ep_tasks)
                continue
            parsed_srt = ep_data["data"]

            ep_dirty = False
            for task in ep_tasks:
                if jobs[job_id].cancelled:
                    update_job(job_id, status="cancelled", message="Cancelled by user")
                    return

                line_idx = task["line_idx"]
                lang_code = task["lang_code"]
                lang_name = task["lang_name"]
                processed += 1

                if line_idx >= len(parsed_srt):
                    continue

                translated_map = {}
                for i, entry in enumerate(parsed_srt):
                    if entry.get("_ass", {}).get("translatable") is False:
                        translated_map[i] = entry.get("original", "")
                    elif not entry.get("original", "").strip():
                        translated_map[i] = ""
                    else:
                        trans_dict = entry.get("translations", {})
                        if lang_code in trans_dict and trans_dict[lang_code]:
                            translated_map[i] = trans_dict[lang_code]
                        elif lang_code == primary_code and entry.get("translated"):
                            translated_map[i] = entry.get("translated")

                fallback_trans = await _translate_missing_line_with_context(
                    project_name=project_name,
                    episode_name=ep_name,
                    line_idx=line_idx,
                    parsed_srt=parsed_srt,
                    translated_map=translated_map,
                    target_lang_name=lang_name,
                    target_lang_code=lang_code,
                    model_name=model_name,
                    rate_limiter=translation_rate_limiter,
                )

                if fallback_trans:
                    if "translations" not in parsed_srt[line_idx]:
                        parsed_srt[line_idx]["translations"] = {}
                    parsed_srt[line_idx]["translations"][lang_code] = fallback_trans
                    if lang_code == primary_code:
                        parsed_srt[line_idx]["translated"] = fallback_trans
                    parsed_srt[line_idx]["needs_review"] = True
                    parsed_srt[line_idx]["review_issues"] = "Translation missing — API failure (Fixed via context-aware fallback)"
                    ep_dirty = True
                    fixed_count += 1
                    update_job(job_id, log=f"{ep_name}: Translated line {line_idx + 1} with context -> '{fallback_trans}'")

                progress = 10.0 + (90.0 * processed / total_missing)
                update_job(job_id, progress=progress, message=f"Translated {processed}/{total_missing} lines...")

            if ep_dirty:
                metadata = storage.load_episode_metadata(project_name, ep_name) or {}
                metadata["translated"] = True
                storage.save_episode(project_name, ep_name, parsed_srt, metadata)
                auto_export_translated_subtitle(project_name, ep_name, parsed_srt, metadata)

        update_job(job_id, status="completed", progress=100.0, message=f"Successfully fixed {fixed_count} missing line(s) out of {total_missing}.")

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Failed to translate missing lines: {e}\n{tb}")
        update_job(job_id, status="failed", message=f"Translation failed: {str(e)}", log=f"{str(e)}\n{tb}")
