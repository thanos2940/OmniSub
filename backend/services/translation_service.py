import os
import re
import json
import time
import asyncio
import logging
from datetime import datetime
from uuid import uuid4
from typing import List, Dict, Optional, Any
from pathlib import Path

from utils import storage
from utils.language_codes import to_code, output_filename, output_filename_from_original
from utils.srt_parser import parse_srt, extract_text_only, reconstruct_srt, build_scene_ast, get_scene_context_hint, get_scene_lookahead_hint
from utils.subtitle_fixer import _run_se_cli, SubtitleEditUnavailable
from utils.llm_utils import strip_reasoning_blocks, parse_translations_from_text
from utils.structured_output import parse_structured
from utils.rate_limiter import RateLimiter, DailyLimitExhausted, translation_rate_limiter, active_model_var
from utils.api_call_wrapper import rate_limited_call
from utils.translation_memory import TranslationMemory, filter_glossary_for_chunk, build_priority_glossary_block
from utils.character_profiles import CharacterProfileManager
from utils.cache_manager import invalidate_cache as _invalidate_translation_cache
from utils.jobs_manager import create_job, update_job, jobs

from adk_agents import (
    create_translation_pipeline,
    generate_glossary_adk,
    research_project_adk,
    enhance_context_guide_adk
)
from adk_agents.translator_agent import build_translation_prompt
from adk_config import get_ephemeral_session_service
from google.adk.runners import types as adk_types

logger = logging.getLogger(__name__)

# Events dictionary for pausing/resuming simple pipeline and standard pipeline
_pipeline_resume_events: Dict[str, asyncio.Event] = {}


def _apply_output_conformance(rows: List[Dict], target_lang_code: str, global_config: Dict) -> None:
    """Re-wrap over-long lines for OUTPUT only — mutates the given (copied) rows.

    Conformance wrapping is a formatting fix, so like SubtitleEdit fixes it belongs at
    export, never in the editor's 1:1 data. Condensing (an LLM rewrite) and violation
    flagging are intentionally NOT done here: the editor already shows live conformance
    status per cue, and the editor must hold only raw translations.
    """
    try:
        from utils.subtitle_conformance import default_limits, apply_wrapping
        limits = default_limits(global_config)
        for line in rows:
            line["translated"] = (line.get("translations", {}) or {}).get(target_lang_code, "") or line.get("translated", "")
        apply_wrapping(rows, limits)
        for line in rows:
            line.setdefault("translations", {})[target_lang_code] = line.get("translated", "")
    except Exception as e:
        logger.warning(f"Output conformance wrapping failed: {e}")


def auto_export_translated_subtitle(project_name: str, episode_name: str, parsed_srt: List[Dict], metadata: Dict) -> Optional[str]:
    """Export the translated subtitle next to its media file (and to the optional
    auto-export dir). Returns the media-adjacent target path if written, else None.

    All output-level fixes (conformance wrapping, SubtitleEdit FixCommonErrors +
    SplitLongLines) are applied HERE, on a copy / on the written file — never to the
    editor's ``parsed_srt`` / data.json, which must stay 1:1 with the source.
    """
    exported_path: Optional[str] = None
    try:
        # 1. Export next to media file if media path exists
        media_path = metadata.get("arr_media_path") or metadata.get("bazarr_media_path")
        
        # Map target language name to code (single source of truth)
        proj_meta = storage.load_project_metadata(project_name) or {}
        target_lang_name = proj_meta.get("target_language", "Greek")
        tgt_lang = to_code(target_lang_name)

        gc = storage.load_global_config()

        _, original_ext = storage.load_original_subtitle(project_name, episode_name)
        ext = original_ext.lstrip('.') if original_ext else "srt"

        # Output-only formatting on a COPY — the editor's parsed_srt is never mutated.
        # Conformance wrapping inserts line breaks; SubtitleEdit can split one long cue
        # into several cues. Both belong in the written file, not in the 1:1 editor data.
        export_rows = parsed_srt
        # Conformance line-wrapping is SRT-only; on ASS it would inject breaks into styled
        # event text. Skip it for ass/ssa (SE fixes are already gated below).
        if gc.get("conformance_enabled", False) and ext not in ("ass", "ssa"):
            import copy as _copy
            export_rows = _copy.deepcopy(parsed_srt)
            _apply_output_conformance(export_rows, tgt_lang, gc)

        from utils.source_clean import reconstruct_cleaned_srt
        translated_srt_content = reconstruct_cleaned_srt(project_name, episode_name, export_rows, tgt_lang)

        if gc.get("apply_subtitle_edit_fixes") and ext not in ["ass", "ssa"]:
            try:
                translated_srt_content = _run_se_cli(translated_srt_content)
            except SubtitleEditUnavailable:
                pass
            except Exception as e:
                logger.warning(f"SubtitleEdit fixes failed at export for {project_name}/{episode_name}: {e}")

        if media_path:
            media_file = Path(media_path)
            target_filename = output_filename_from_original(f"{media_file.stem}.{ext}", tgt_lang)
            target_srt_path = media_file.parent / target_filename
            target_srt_path.write_text(translated_srt_content, encoding="utf-8-sig")
            logger.info(f"Auto-exported translated subtitle next to media: {target_srt_path}")

            metadata["arr_has_target"] = True
            metadata["bazarr_has_target"] = True
            metadata["arr_target_path"] = str(target_srt_path)
            storage.save_episode_metadata(project_name, episode_name, metadata)
            exported_path = str(target_srt_path)
            
        # 2. Export to settings-defined auto_export_dir if enabled
        project_settings = proj_meta.get("settings", {})
        if project_settings.get("auto_export_enabled") and project_settings.get("auto_export_dir"):
            export_dir = project_settings.get("auto_export_dir")
            os.makedirs(export_dir, exist_ok=True)
            
            orig_filename = metadata.get("original_filename")
            if orig_filename:
                filename = output_filename_from_original(orig_filename, tgt_lang)
            else:
                filename = output_filename_from_original(f"{episode_name}.{ext}", tgt_lang)
                
            export_path = os.path.join(export_dir, filename)
            with open(export_path, 'w', encoding='utf-8') as f:
                f.write(translated_srt_content)
            logger.info(f"Auto-exported translated subtitle to dir: {export_path}")
            
    except Exception as e:
        logger.error(f"Error auto-exporting translated subtitle for {project_name}/{episode_name}: {e}", exc_info=True)
    return exported_path


def _parse_json_response(response: str) -> Dict:
    """Parse JSON from model response, handling markdown code blocks."""
    clean = response.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    if clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    
    match = re.search(r'\{.*\}', clean, re.DOTALL)
    if match:
        clean = match.group(0)
    
    return json.loads(clean)


def _record_user_edits(
    project_name: str,
    episode_name: str,
    old_data: List[Dict],
    new_data: List[Dict],
    lang: Optional[str] = None,
):
    """Detect user edits and store them in the Translation Memory as gold examples."""
    proj_meta = storage.load_project_metadata(project_name)
    primary_lang = proj_meta.get("target_language", "Greek") if proj_meta else "Greek"
    
    from utils.language_codes import to_code
    if not lang:
        lang = primary_lang
    lang_code = to_code(lang)

    edits_source = []
    edits_target = []

    for old_line, new_line in zip(old_data, new_data):
        old_trans = old_line.get("translations", {}).get(lang_code) or ""
        if not old_trans and lang_code == to_code(primary_lang):
            old_trans = old_line.get("translated") or ""
            
        new_trans = new_line.get("translations", {}).get(lang_code) or ""
        if not new_trans and lang_code == to_code(primary_lang):
            new_trans = new_line.get("translated") or ""
            
        old_trans = old_trans.strip()
        new_trans = new_trans.strip()
        original = (new_line.get("original") or "").strip()

        if old_trans and new_trans and original and old_trans != new_trans:
            from utils.review_feedback import record_feedback
            record_feedback(
                project=project_name,
                episode=episode_name,
                line_index=new_line.get("index", 0),
                source=original,
                bad_target=old_trans,
                corrected_target=new_trans,
                reason="User edit in editor",
                lang_code=lang_code
            )
            new_line.pop("needs_review", None)
            new_line.pop("review_issues", None)
            new_line.pop("review_scores", None)


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

    ONE batched Gemini call for the whole episode — never one call per line (same
    principle the QC repair pass follows). Each ticketed line carries its EN source,
    current translation, and the required term rendering(s); the model returns a
    numbered list we map back by item number.
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

    items = sorted(line_to_constraints.items())  # [(line_idx, [(term, canonical), ...])]
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

    async def _call():
        return await generate(
            model_name=model_name,
            prompt=prompt,
            system_instruction=f"You are a {target_lang_name} subtitle term-consistency enforcer.",
            temperature=0.1,
            role="reconciliation",
        )

    try:
        response_text = await rate_limited_call(_call, rate_limiter=rate_limiter)
    except DailyLimitExhausted:
        raise
    except Exception as e:
        update_job(job_id, log=f"Reconciliation batch call failed (non-critical): {e}")
        return 0

    parsed = parse_translations_from_text(strip_reasoning_blocks(response_text or ""))
    by_item = {p["index"]: p["text"] for p in parsed}

    retranslated_count = 0
    for n, (idx, _constraints) in enumerate(items, start=1):
        new_trans = (by_item.get(n) or "").strip()
        if not new_trans:
            continue
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
    # Gather 2 earlier lines
    context_lines = []
    
    # helper to get translation for an index
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
            
    # The line to translate
    context_lines.append(
        f"Line to Translate:\n"
        f"Source (English): {parsed_srt[line_idx].get('original', '')}\n"
        f"Translation ({target_lang_name}): [TO BE TRANSLATED]"
    )
    
    # Gather 2 later lines
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
    
    async def _call():
        from adk_agents.llm_factory import generate
        return await generate(
            model_name=model_name,
            prompt=prompt,
            temperature=0.3,
            role="translation",
        )
        
    try:
        from utils.api_call_wrapper import rate_limited_call
        res = await rate_limited_call(_call, rate_limiter=rate_limiter)
        if res:
            res_stripped = res.strip()
            # strip any reasoning tags or quotes if the model wrapped it
            from utils.llm_utils import strip_reasoning_blocks
            res_stripped = strip_reasoning_blocks(res_stripped)
            res_stripped = re.sub(r'^["\'\s]+|["\'\s]+$', '', res_stripped)
            return res_stripped
    except Exception as e:
        logger.warning(f"Failed to translate missing line {line_idx} with context: {e}")
    return None



async def _translate_episode_atomic(
    job_id: str,
    project_name: str,
    episode_name: str,
    target_lang: str,
    shared_agent: Any,
    rate_limiter: RateLimiter,
    semaphore: asyncio.Semaphore,
    global_config: Dict,
    total_episodes: int,
    episode_idx: int,
    start_progress: float,
    model_name: Optional[str] = None,
    lang_suffix: str = "",
    disable_tm: bool = False,
) -> bool:
    """Translates an entire episode. Resumable via checkpointing.

    ``model_name`` selects the rate-limit bucket. When provided it overrides the
    active-model context; when None, the caller's already-set context is trusted
    (falling back to the global default only if nothing was set).
    """
    from utils.model_resolver import resolve_model
    project_meta = storage.load_project_metadata(project_name)
    if model_name:
        active_model_var.set(model_name)
    elif active_model_var.get() in (None, "default"):
        active_model_var.set(resolve_model("translation", project_meta))
    # Telemetry context (v2 D8): every LLM call in this episode records under it.
    try:
        from utils.telemetry import usage_ctx
        if not usage_ctx.get():
            usage_ctx.set({"project": project_name, "episode": episode_name, "lane": "live"})
    except Exception:
        pass
    episode_data = storage.load_episode(project_name, episode_name)
    if not episode_data:
        update_job(job_id, log=f"Episode {episode_name} not found, skipping.")
        return False

    from utils.language_codes import to_code
    target_lang_code = to_code(target_lang)
    
    project_meta = storage.load_project_metadata(project_name)
    primary_lang = project_meta.get("target_language", "Greek") if project_meta else "Greek"
    primary_code = to_code(primary_lang)

    parsed_srt = episode_data["data"]
    translated_map = {}

    for i, entry in enumerate(parsed_srt):
        if entry.get("_ass", {}).get("translatable") is False:
            translated_map[i] = entry.get("original", "")
        elif not entry.get("original", "").strip():
            translated_map[i] = ""
        else:
            trans_dict = entry.get("translations", {})
            if target_lang_code in trans_dict and trans_dict[target_lang_code]:
                translated_map[i] = trans_dict[target_lang_code]
            elif target_lang_code == primary_code and entry.get("translated"):
                translated_map[i] = entry.get("translated")

    _cp_name = f"checkpoint.{lang_suffix}.json" if lang_suffix else "checkpoint.json"
    checkpoint_path = storage.PROJECTS_DIR / project_name / "episodes" / episode_name / _cp_name
    # Throttle the per-scene full data.json rewrite: the checkpoint already captures
    # incremental progress for recovery, so data.json only needs occasional refreshes for
    # the live view (and a final write at the end). Avoids rewriting the whole file on
    # every scene — major write reduction during translation.
    _last_incremental_save = {"t": 0.0}
    _incr_throttle = float(global_config.get("incremental_save_throttle_seconds", 8.0) or 0)
    if checkpoint_path.exists():
        try:
            checkpoint_data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint_map = {int(k): v for k, v in checkpoint_data.items()}
            translated_map.update(checkpoint_map)
            update_job(job_id, log=f"{episode_name}: Resumed {len(checkpoint_map)} lines from checkpoint file.")
        except Exception as e:
            update_job(job_id, log=f"{episode_name}: Failed to load checkpoint (will start fresh): {e}")

    # Scene size: cap the configured maximum by what the active model can reliably
    # keep aligned (v2 D12). Small local models drift/drop lines on long numbered
    # lists; get_recommended_chunk_size() returns a safe per-model budget (~80 for a
    # 4B model, 300 for Gemini). Model-agnostic by construction.
    from adk_agents.llm_factory import get_recommended_chunk_size
    from utils.model_resolver import resolve_model as _resolve_model_for_chunks
    effective_model = model_name or active_model_var.get()
    if not effective_model or effective_model == "default":
        effective_model = _resolve_model_for_chunks("translation", project_meta)
    configured_max = global_config.get("max_lines_per_scene", 200)
    model_max = get_recommended_chunk_size(effective_model)
    max_lines = max(10, min(configured_max, model_max))
    if model_max < configured_max:
        update_job(job_id, log=f"{episode_name}: capping scene size at {max_lines} lines for model '{effective_model}' (configured {configured_max}).")
    scenes = build_scene_ast(parsed_srt, gap_threshold_ms=3000, max_lines_per_scene=max_lines)
    total_scenes = len(scenes)

    update_job(job_id, log=f"Translating {episode_name} ({total_scenes} scenes)")

    _eph_svc = get_ephemeral_session_service()
    from google.adk.runners import Runner as _Runner

    profile_mgr = CharacterProfileManager(project_name)
    profiles_enabled = global_config.get("character_profiles_enabled", True)
    project_characters = list(profile_mgr.load_all_resolved().keys()) if profiles_enabled else []

    def infer_speaker(text: str, chars: List[str] = None) -> str:
        # Check for prefix: e.g. "Name: ..."
        m = re.match(r"^([A-Z][a-zA-Z\s]{1,15}):", text)
        if m:
            return m.group(1).strip()
        m2 = re.match(r"^\[([A-Z][a-zA-Z\s]{1,15})\]", text)
        if m2:
            return m2.group(1).strip()
        if chars:
            for char in chars:
                if re.search(r"\b" + re.escape(char) + r"\b", text, re.IGNORECASE):
                    return char
        return ""

    tm_enabled = global_config.get("tm_enabled", True) and not disable_tm
    tm = None
    tm_exact_count = 0

    if tm_enabled:
        try:
            tm = TranslationMemory(project_name, lang_code=target_lang_code)
            all_source_lines = [line.get("original", "") for line in parsed_srt]
            exact_threshold = global_config.get("tm_exact_match_threshold", 0.95)
            
            # Context-aware TM reuse (Plan 21)
            max_words = global_config.get("tm_exact_reuse_max_words", 6)
            require_same_speaker = global_config.get("tm_exact_reuse_require_same_speaker", False)
            speakers = [infer_speaker(line, project_characters) for line in all_source_lines]
            
            # Off-load the embedding encode + vector search (CPU-bound) so it doesn't
            # block the event loop while other episodes/scenes run concurrently.
            exact_matches = await asyncio.to_thread(
                tm.get_exact_matches,
                all_source_lines,
                exact_threshold,
                max_words,
                require_same_speaker,
                speakers,
            )

            for idx, match_info in exact_matches.items():
                translated_map[idx] = match_info["target"]
                parsed_srt[idx]["from_tm"] = True
                parsed_srt[idx]["tm_user_edited"] = match_info["is_user_edited"]
                parsed_srt[idx]["tm_origin_episode"] = match_info.get("episode", "")

            tm_exact_count = len(exact_matches)
            if tm_exact_count > 0:
                update_job(job_id, log=f"{episode_name}: {tm_exact_count}/{len(parsed_srt)} lines reused from Translation Memory (skipped LLM)")
        except Exception as e:
            update_job(job_id, log=f"{episode_name}: TM lookup failed (non-critical): {e}")
            tm = None

    project_meta = storage.load_project_metadata(project_name)
    glossary = project_meta.get("glossary", {"terms": []}) if project_meta else {"terms": []}

    episode_context = ""
    if global_config.get("episode_summaries_enabled", True):
        try:
            from utils.episode_summaries import EpisodeSummaryManager
            summary_mgr = EpisodeSummaryManager(project_name)
            summary_window = global_config.get("episode_summary_window", 3)
            episode_context = summary_mgr.get_recent_summaries(episode_name, window=summary_window)
            if episode_context:
                update_job(job_id, log=f"{episode_name}: Injecting context from {min(summary_window, len(summary_mgr.load_all()))} past episode(s).")
        except Exception as e:
            update_job(job_id, log=f"{episode_name}: Episode context load failed (non-critical): {e}")

    # Subtitle conformance prompt hint (Plan 13) — preventive guidance to the model.
    conformance_hint = ""
    if global_config.get("conformance_enabled", False):
        from utils.subtitle_conformance import default_limits, conformance_prompt_hint
        conformance_hint = conformance_prompt_hint(default_limits(global_config))

    # Context caching (Plan 02) — reuse cache_manager's fingerprint-based cache of the
    # stable translator system instruction (glossary + context guide). Invalidation is
    # already wired to glossary/context edits via invalidate_cache().
    use_context_cache = False
    context_cache_name = None
    cached_system_instruction = ""
    from utils.model_resolver import resolve_model
    cache_model = model_name or resolve_model("translation", project_meta)
    if global_config.get("context_cache_enabled", False) and project_meta and not cache_model.startswith("local/"):
        try:
            from adk_agents.translator_agent import build_translator_instruction
            from utils.cache_manager import get_or_create_cache
            from utils.structured_output import use_structured_output
            structured = use_structured_output(cache_model)
            cached_system_instruction = build_translator_instruction(
                target_lang, glossary, project_meta.get("context_guide", ""), structured=structured
            )
            context_cache_name, project_meta = get_or_create_cache(project_meta, cache_model, cached_system_instruction)
            storage.save_project_metadata(project_name, project_meta)
            use_context_cache = bool(context_cache_name) or len(cached_system_instruction) >= 4000
        except Exception as e:
            update_job(job_id, log=f"{episode_name}: context cache setup failed (non-critical): {e}")
            use_context_cache = False

    # ------------------------------------------------------------------
    # Whole-episode mode (v2 plan, D1) — the Gemini-lane centerpiece.
    #
    # One request carries every untranslated line: the model sees the full
    # narrative (intra-episode consistency holds by construction), the stable
    # context is paid once instead of once per scene, and request count drops
    # ~10× (the binding free-tier constraint is RPD). Whatever lines fail to
    # map fall through to the scene pass below, which only processes lines
    # missing from translated_map — i.e. the scene machinery doubles as the
    # span-repair fallback.
    # ------------------------------------------------------------------
    episode_mode = global_config.get("episode_request_mode", "auto")
    whole_cap = global_config.get("whole_episode_max_cues", 1200)
    _untranslated = [i for i in range(len(parsed_srt))
                     if i not in translated_map and parsed_srt[i].get("original", "").strip()]
    use_whole_episode = (
        episode_mode != "scenes"
        and not cache_model.startswith("local/")
        and len(_untranslated) > 0
        and (episode_mode == "whole" or len(_untranslated) <= whole_cap)
    )

    if use_whole_episode:
        from services.prompt_assembler import compile_bible, assemble_episode_request
        from utils.structured_output import parse_new_terms
        bible = compile_bible(project_name, project_meta or {}, cache_model, target_language=target_lang)
        # When an explicit context cache carries the system instruction we can't
        # append the new-terms rule to it, so skip collection in that case.
        collect_terms = (
            global_config.get("new_terms_suggest_enabled", True)
            and bible.structured and not context_cache_name
        )

        whole_char_block = ""
        if profiles_enabled:
            try:
                _profs = profile_mgr.get_profiles_for_chunk(
                    [parsed_srt[i].get("original", "") for i in _untranslated], glossary)
                if _profs:
                    whole_char_block = profile_mgr.build_profile_context(_profs)
            except Exception:
                pass

        for _attempt in range(2):
            remaining = [i for i in _untranslated if i not in translated_map]
            if not remaining:
                break
            req = assemble_episode_request(
                bible,
                [parsed_srt[i] for i in remaining],
                remaining,
                episode_context=episode_context,
                conformance_hint=conformance_hint,
                character_context=whole_char_block,
                collect_new_terms=collect_terms and _attempt == 0,
            )
            update_job(job_id, log=f"{episode_name}: whole-episode request — {len(remaining)} lines (attempt {_attempt + 1}/2).")

            async def _whole_call():
                from adk_agents.llm_factory import generate
                return await generate(
                    model_name=cache_model,
                    prompt=req.prompt,
                    cache_name=context_cache_name,
                    system_instruction=req.system_instruction if not context_cache_name else "",
                    temperature=global_config.get("temperature", 0.3) or 0.3,
                    response_schema=req.response_schema,
                    role="translation",
                )

            try:
                _whole_text = await rate_limited_call(_whole_call, rate_limiter=rate_limiter)
            except DailyLimitExhausted:
                raise
            except Exception as we:
                update_job(job_id, log=f"{episode_name}: whole-episode attempt failed ({we}); falling back to scene mode.")
                break

            _clean = strip_reasoning_blocks(_whole_text)
            _parsed_lines = parse_structured(_clean, expected_count=req.expected_count)
            _src_local = [parsed_srt[g].get("original", "") for g in req.index_map]

            # Observability: when the model returns a different entry count than we
            # sent (the split-shift symptom — a long cue split into two entries, a
            # line dropped/duplicated) — or always, if debug_save_llm_responses is on
            # — dump the raw response + per-entry breakdown to the episode's debug/
            # dir so the exact model output can be inspected.
            try:
                from utils import llm_debug
                from utils.alignment_audit import audit_alignment, _verbatim_terms
                _diag = llm_debug.detect_split(_parsed_lines, req.expected_count)
                _anomalous = bool(_diag["overflow"] or _diag["duplicate_indices"] or _diag["missing_indices"])

                # Content-level shift check: catches the case the structural diagnosis
                # can't — a well-formed entry count (no overflow/dup/missing) where the
                # text assigned to a local index is actually a neighbour's translation
                # (the model keeps counting sequentially while splitting one cue's
                # content across two slots). Runs on just this response's local span.
                _local_rows = [{"original": s, "translations": {}} for s in _src_local]
                for _it in _parsed_lines:
                    _li = _it.get("index", 0) - 1
                    if 0 <= _li < len(_local_rows):
                        _local_rows[_li]["translations"][target_lang_code] = _it.get("text", "")
                _shift_findings = audit_alignment(_local_rows, target_lang_code, _verbatim_terms(glossary))
                if _shift_findings:
                    _anomalous = True
                    _diag["content_shift_findings"] = _shift_findings

                if _anomalous or global_config.get("debug_save_llm_responses", False):
                    _dbg_path = llm_debug.save_response(
                        project_name, episode_name, "whole_episode",
                        prompt=req.prompt, raw_response=_whole_text, parsed=_parsed_lines,
                        expected_count=req.expected_count, source_texts=_src_local,
                        extra={"diagnosis": _diag, "attempt": _attempt + 1,
                               "model": cache_model, "structured": req.structured,
                               "verify_echo": getattr(req, "verify_echo", False)},
                    )
                    if _anomalous:
                        update_job(job_id, log=(
                            f"{episode_name}: ⚠ model returned {_diag['parsed_count']} entries for "
                            f"{req.expected_count} lines (overflow {_diag['overflow']}, "
                            f"{len(_diag['duplicate_indices'])} dup, {len(_diag['missing_indices'])} missing, "
                            f"{len(_shift_findings)} content-shift span(s)). "
                            f"Raw response saved → {_dbg_path}"))
            except Exception:
                pass

            # Source-echo guard: when the model echoed each source line's leading
            # words, drop any line whose echo clearly belongs to a neighbour (a
            # shift) so it stays a safe gap the scene/repair pass re-translates from
            # its own source, instead of being mis-assigned.
            if getattr(req, "verify_echo", False):
                from utils.alignment_audit import filter_by_source_echo
                _parsed_lines, _echo_dropped = filter_by_source_echo(_parsed_lines, _src_local)
                if _echo_dropped:
                    update_job(job_id, log=f"{episode_name}: source-echo guard dropped {_echo_dropped} mis-aligned line(s); left for span repair.")

            _mapped = 0
            _seen_local = set()
            for _item in _parsed_lines:
                _li = _item["index"] - 1
                if not (0 <= _li < len(req.index_map)):
                    continue
                # Guard against a duplicate echoed index (the split-cue symptom): keep
                # the first translation for a slot and leave later duplicates as gaps
                # for the span-repair pass, instead of silently overwriting.
                if _li in _seen_local:
                    continue
                _seen_local.add(_li)
                _gi = req.index_map[_li]
                translated_map[_gi] = _item["text"]
                parsed_srt[_gi].setdefault("translations", {})[target_lang_code] = _item["text"]
                if target_lang_code == primary_code:
                    parsed_srt[_gi]["translated"] = _item["text"]
                _mapped += 1
            update_job(job_id, log=f"{episode_name}: whole-episode mapped {_mapped}/{len(remaining)} lines.")

            # D7: harvest new-term suggestions from the same response (zero extra calls).
            if collect_terms and _attempt == 0:
                try:
                    _terms = parse_new_terms(_whole_text)
                    if _terms:
                        from utils.consistency import save_suggestions
                        save_suggestions(project_name, _terms)
                        update_job(job_id, log=f"{episode_name}: captured {len(_terms)} new glossary suggestion(s) from the translation response.")
                except Exception:
                    pass

            try:
                checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                checkpoint_path.write_text(json.dumps({str(k): v for k, v in translated_map.items()}, ensure_ascii=False), encoding="utf-8")
            except Exception as ce:
                logger.error(f"Failed to write checkpoint for {episode_name}: {ce}")

            _covered = sum(1 for i in _untranslated if i in translated_map)
            if _covered / max(1, len(_untranslated)) >= 0.98:
                break

        _leftover = sum(1 for i in _untranslated if i not in translated_map)
        if _leftover:
            update_job(job_id, log=f"{episode_name}: {_leftover} line(s) left for the scene-pass span repair.")
        update_job(job_id, progress=start_progress + (60.0 / total_episodes))

    # Cancellation that survives a process restart: the in-memory jobs[job_id].cancelled
    # flag is lost on restart, but a user cancel also sets the queue row to 'cancelled'.
    # Re-check that row (throttled) so a long local episode can still be stopped mid-run.
    _cancel_state = {"t": 0.0, "cancelled": False}

    def _is_cancelled() -> bool:
        if jobs[job_id].cancelled:
            return True
        if _cancel_state["cancelled"] or not job_id.startswith("bg_"):
            return _cancel_state["cancelled"]
        now = time.time()
        if now - _cancel_state["t"] < 5.0:
            return False
        _cancel_state["t"] = now
        try:
            import sqlite3
            from utils.translation_queue import DB_FILE
            conn = sqlite3.connect(DB_FILE, timeout=5.0)
            try:
                row = conn.execute(
                    "SELECT status FROM translation_queue WHERE id = ?", (job_id[3:],)
                ).fetchone()
            finally:
                conn.close()
            if row and row[0] == "cancelled":
                _cancel_state["cancelled"] = True
                jobs[job_id].cancelled = True  # short-circuit the rest of the run
                return True
        except Exception:
            pass
        return False

    async def _translate_scene_task(
        scene: Dict,
        prev_scene_lines: List[Dict] = None,
        next_scene_lines: List[Dict] = None
    ):
        scene_label = f"Scene {scene['scene_id']}"
        update_job(job_id, scene_status={scene_label: "pending"})

        async with semaphore:
            if _is_cancelled():
                return

            update_job(job_id, scene_status={scene_label: "running"})

            lines = scene.get("lines", [])
            if not lines:
                update_job(job_id, scene_status={scene_label: "completed"})
                return

            lines_to_translate = []
            original_scene_indices = []
            for j, line in enumerate(lines):
                global_idx = scene["start_index"] + j
                if global_idx not in translated_map:
                    lines_to_translate.append(line)
                    original_scene_indices.append(j)

            if not lines_to_translate:
                update_job(job_id, scene_status={scene_label: "completed"})
                completed_count = len([s for s in jobs[job_id].scene_status.items() if s[1] == "completed" and s[0].startswith("Scene")])
                progress = start_progress + ((completed_count / total_scenes) * (100.0 / total_episodes))
                update_job(job_id, progress=progress)
                return

            source_texts = [line.get("original", "") for line in lines_to_translate]

            few_shot_block = ""
            if tm is not None:
                try:
                    tm_threshold = global_config.get("tm_similarity_threshold", 0.80)
                    # Off-load the embedding search so concurrent scenes don't block the loop.
                    few_shot_block = await asyncio.to_thread(
                        tm.build_few_shot_block,
                        source_texts,
                        target_lang,
                        5,
                        tm_threshold,
                        global_config.get("tm_exact_match_threshold", 0.95),
                        set(),
                    )
                except Exception:
                    pass

            priority_block = ""
            filtered = filter_glossary_for_chunk(glossary, source_texts)
            if filtered.get("terms"):
                if use_context_cache and context_cache_name:
                    from utils.translation_memory import build_priority_glossary_reminder_block
                    priority_block = build_priority_glossary_reminder_block(filtered)
                else:
                    priority_block = build_priority_glossary_block(filtered)

            character_block = ""
            if profiles_enabled:
                try:
                    scene_profiles = profile_mgr.get_profiles_for_chunk(source_texts, glossary)
                    if scene_profiles:
                        character_block = profile_mgr.build_profile_context(scene_profiles)
                except Exception:
                    pass

            # Blacklist (Plan 14): avoid previously-rejected renderings for these lines.
            scene_local_to_global = {
                k + 1: scene["start_index"] + original_scene_indices[k]
                for k in range(len(original_scene_indices))
            }
            negative_block = ""
            blacklist_map = {}
            try:
                from utils import blacklist as _bl
                blacklist_map = _bl.for_lines(project_name, episode_name, list(scene_local_to_global.values()))
                if blacklist_map:
                    negative_block = _bl.build_negative_block(blacklist_map, scene_local_to_global)
            except Exception:
                pass

            lookahead_k = global_config.get("scene_lookahead_lines", 2)
            lookahead_hint = get_scene_lookahead_hint(next_scene_lines, lookahead_k) if next_scene_lines else ""

            async def _perform_translation():
                context_hint = get_scene_context_hint(prev_scene_lines) if prev_scene_lines else ""
                prompt = build_translation_prompt(
                    source_texts,
                    target_lang,
                    context_hint,
                    few_shot_context=few_shot_block,
                    priority_glossary=priority_block,
                    character_context=character_block,
                    episode_context=episode_context,
                    conformance_hint=conformance_hint,
                    negative_examples=negative_block,
                    lookahead_line=lookahead_hint,
                )

                # Context-caching path (Plan 02): direct genai call with the cached
                # stable system instruction. Falls back to ADK on any error.
                if use_context_cache or cache_model.startswith("local/"):
                    try:
                        from adk_agents.llm_factory import generate
                        from utils.structured_output import SceneTranslationResponse, use_structured_output
                        structured = use_structured_output(cache_model)
                        return await generate(
                            model_name=cache_model,
                            prompt=prompt,
                            cache_name=context_cache_name,
                            system_instruction=cached_system_instruction if not context_cache_name else "",
                            temperature=global_config.get("temperature", 0.3) or 0.3,
                            response_schema=SceneTranslationResponse if structured else None,
                            role="translation",
                        )
                    except Exception as ce:
                        logger.warning(f"Unified generate failed, falling back to ADK: {ce}")

                session_id = f"scene_{uuid4().hex[:8]}"
                runner = _Runner(agent=shared_agent, app_name=f"Omnisub_{session_id}", session_service=_eph_svc)
                await _eph_svc.create_session(
                    session_id=session_id, user_id="default_user", app_name=f"Omnisub_{session_id}"
                )

                response_text = ""
                async for event in runner.run_async(
                    user_id="default_user",
                    session_id=session_id,
                    new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=prompt)])
                ):
                    if jobs[job_id].cancelled:
                        raise asyncio.CancelledError()

                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text:
                                response_text += part.text
                return response_text

            response_text = ""
            try:
                response_text = await rate_limited_call(
                    _perform_translation,
                    rate_limiter=rate_limiter
                )

                reasoning_match = re.search(r'<internal_reasoning>(.*?)</internal_reasoning>', response_text, re.DOTALL)
                if reasoning_match:
                    reasoning_text = reasoning_match.group(1).strip()
                    update_job(job_id, log=f"{episode_name} S{scene['scene_id']} Reasoning: {reasoning_text[:200]}...")

                response_text_clean = strip_reasoning_blocks(response_text)
                parsed_lines = parse_structured(response_text_clean, expected_count=len(source_texts))

                scene_mapped = []
                for item in parsed_lines:
                    llm_idx = item["index"] - 1
                    if 0 <= llm_idx < len(original_scene_indices):
                        scene_local_idx = original_scene_indices[llm_idx]
                        global_idx = scene["start_index"] + scene_local_idx
                        translated_text = item["text"]
                        translated_map[global_idx] = translated_text
                        if "translations" not in parsed_srt[global_idx]:
                            parsed_srt[global_idx]["translations"] = {}
                        parsed_srt[global_idx]["translations"][target_lang_code] = translated_text
                        if target_lang_code == primary_code:
                            parsed_srt[global_idx]["translated"] = translated_text
                        scene_mapped.append(global_idx)
                        # Blacklist post-check: if the model reproduced a rejected rendering, flag it.
                        if blacklist_map.get(global_idx) and translated_text.strip() in {b.strip() for b in blacklist_map[global_idx]}:
                            parsed_srt[global_idx]["needs_review"] = True
                            parsed_srt[global_idx]["review_issues"] = "Reproduced a blacklisted translation"

                missing_in_scene = len(original_scene_indices) - len(scene_mapped)
                if missing_in_scene > 0:
                    update_job(
                        job_id,
                        log=f"Scene {scene['scene_id']} translation incomplete. Expected {len(original_scene_indices)} lines, parsed {len(scene_mapped)}. Missing {missing_in_scene} lines. Model response:\n{response_text}"
                    )

                try:
                    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                    checkpoint_path.write_text(json.dumps({str(k): v for k, v in translated_map.items()}, indent=2, ensure_ascii=False), encoding="utf-8")
                except Exception as e:
                    logger.error(f"Failed to write checkpoint for {episode_name}: {e}")

                _now = time.time()
                if (_now - _last_incremental_save["t"]) >= _incr_throttle:
                    _last_incremental_save["t"] = _now
                    try:
                        metadata = storage.load_episode_metadata(project_name, episode_name) or {}
                        storage.save_episode(project_name, episode_name, parsed_srt, metadata, update_stats=False)
                    except Exception as e:
                        logger.error(f"Failed to perform incremental save for {episode_name}: {e}")

                update_job(job_id, scene_status={scene_label: "completed"})

                completed_count = len([s for s in jobs[job_id].scene_status.items() if s[1] == "completed" and s[0].startswith("Scene")])
                progress = start_progress + ((completed_count / total_scenes) * (100.0 / total_episodes))
                update_job(job_id, progress=progress)

            except DailyLimitExhausted:
                update_job(job_id, scene_status={scene_label: "failed"})
                raise
            except Exception as e:
                log_msg = f"Scene {scene['scene_id']} failed: {str(e)}"
                if response_text:
                    log_msg += f"\nModel response:\n{response_text}"
                update_job(job_id, scene_status={scene_label: "failed"}, log=log_msg)
                raise

    update_job(job_id, scene_status={f"Scene {s['scene_id']}": "pending" for s in scenes})

    # Sequential mode (quality, D12): run scenes one at a time so each scene's [prev]
    # hint sees the *translated* tail of the previous scene (scene["lines"] hold the
    # same dict objects as parsed_srt). Parallel mode (default) gathers all at once.
    sequential = global_config.get("sequential_scenes", False)

    if sequential:
        results = []
        for i, scene in enumerate(scenes):
            if jobs[job_id].cancelled:
                break
            prev_scene_lines = scenes[i - 1]["lines"] if i > 0 else None
            next_scene_lines = scenes[i + 1]["lines"] if i < len(scenes) - 1 else None
            try:
                await _translate_scene_task(scene, prev_scene_lines, next_scene_lines)
                results.append(None)
            except DailyLimitExhausted:
                raise
            except Exception as e:  # tolerated; gaps are flagged below
                results.append(e)
    else:
        tasks = []
        for i, scene in enumerate(scenes):
            prev_scene_lines = scenes[i - 1]["lines"] if i > 0 else None
            next_scene_lines = scenes[i + 1]["lines"] if i < len(scenes) - 1 else None
            tasks.append(_translate_scene_task(scene, prev_scene_lines, next_scene_lines))

        # Wait for ALL scene tasks before proceeding. Without return_exceptions, the
        # first scene failure would propagate while sibling tasks kept running detached
        # — still mutating parsed_srt/translated_map and writing checkpoints after the
        # episode was marked failed, which could then overlap a retry run.
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # A daily-limit hit must pause the whole item (handled by the caller), not be
    # swallowed as a per-scene gap.
    for r in results:
        if isinstance(r, DailyLimitExhausted):
            raise r

    # Other per-scene errors are tolerated: the completeness check below either fails
    # the episode atomically (>20% missing) or fills + flags the gaps for review.
    for e in [r for r in results
              if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError)]:
        update_job(job_id, log=f"{episode_name}: scene task error (tolerated — affected lines flagged for review): {e}")

    if jobs[job_id].cancelled:
        return False

    if len(translated_map) < len(parsed_srt):
        missing = len(parsed_srt) - len(translated_map)
        missing_indices_1based = [i + 1 for i in range(len(parsed_srt)) if i not in translated_map]
        # Graceful filling + review flagging applies to all pipeline and queue jobs.
        missing_pct = missing / len(parsed_srt)
        if missing_pct > 0.2:
            update_job(job_id, log=f"Episode {episode_name} has {missing} untranslated lines ({missing_pct:.1%}) at line numbers: {missing_indices_1based[:50]}.... Too many failures, failing atomically.")
            return False
        update_job(job_id, log=f"Episode {episode_name} has {missing} untranslated lines ({missing_pct:.1%}) at line numbers: {missing_indices_1based[:50]}.... Gracefully filling and flagging for review.")
        # Cap the per-line context-aware fallback: it issues one sequential model call per
        # missing line, so a partial (sub-20%) systemic failure could otherwise balloon into
        # hundreds of blocking calls inside the atomic path. Beyond the cap, flag for review.
        fallback_cap = int(global_config.get("missing_fallback_max_lines", 50) or 0)
        fallback_used = 0
        for i in range(len(parsed_srt)):
            if i not in translated_map:
                fallback_trans = None
                if fallback_used < fallback_cap:
                    fallback_used += 1
                    fallback_trans = await _translate_missing_line_with_context(
                        project_name=project_name,
                        episode_name=episode_name,
                        line_idx=i,
                        parsed_srt=parsed_srt,
                        translated_map=translated_map,
                        target_lang_name=target_lang,
                        target_lang_code=target_lang_code,
                        model_name=cache_model,
                        rate_limiter=rate_limiter,
                    )
                if fallback_trans:
                    translated_map[i] = fallback_trans
                    parsed_srt[i]["needs_review"] = True
                    parsed_srt[i]["review_issues"] = "Translation missing — API failure (Fixed via context-aware fallback)"
                    update_job(job_id, log=f"Fixed missing translation at line {i + 1}: '{fallback_trans}'")
                else:
                    translated_map[i] = ""
                    parsed_srt[i]["needs_review"] = True
                    parsed_srt[i]["review_issues"] = "Translation missing — API failure"

    for idx, text in translated_map.items():
        if "translations" not in parsed_srt[idx]:
            parsed_srt[idx]["translations"] = {}
        parsed_srt[idx]["translations"][target_lang_code] = text
        if target_lang_code == primary_code:
            parsed_srt[idx]["translated"] = text

    # ------------------------------------------------------------------
    # QC funnel (v2 plan, D6): cheapest checks first.
    # Stage outcomes are recorded so soft failures are observable instead of
    # vanishing into "non-critical" logs.
    # ------------------------------------------------------------------
    qc_stages: Dict[str, str] = {"translate": "ok"}

    # Stage 1a — deterministic glossary enforcement (zero tokens): fix glossary
    # proper nouns that leaked into the target untranslated.
    if global_config.get("glossary_enforce_enabled", True) and glossary.get("terms"):
        try:
            from utils.glossary_enforcer import enforce_glossary
            _tgt_texts = [parsed_srt[i].get("translations", {}).get(target_lang_code, "") for i in range(len(parsed_srt))]
            _src_texts = [parsed_srt[i].get("original", "") for i in range(len(parsed_srt))]
            _corrected, _n_fixed = enforce_glossary(_tgt_texts, glossary, _src_texts, target_lang)
            if _n_fixed > 0:
                for i, _txt in enumerate(_corrected):
                    parsed_srt[i].setdefault("translations", {})[target_lang_code] = _txt
                    if target_lang_code == primary_code:
                        parsed_srt[i]["translated"] = _txt
                update_job(job_id, log=f"{episode_name}: glossary enforcement corrected {_n_fixed} leaked term(s).")
            qc_stages["glossary_enforce"] = f"ok ({_n_fixed} fixed)"
        except Exception as e:
            qc_stages["glossary_enforce"] = f"failed: {e}"
            update_job(job_id, log=f"{episode_name}: glossary enforcement failed (non-critical): {e}")
    else:
        qc_stages["glossary_enforce"] = "skipped"

    # Stage 1b+2 — validators (tags, untranslated-Latin) → one batched repair call.
    if global_config.get("repair_pass_enabled", True):
        try:
            from utils.qc_funnel import validate_lines, run_repair_pass
            _tickets = validate_lines(parsed_srt, target_lang_code, glossary=glossary)
            if _tickets:
                update_job(job_id, log=f"{episode_name}: validators ticketed {len(_tickets)} line(s); running one batched repair call...")
                from utils.model_resolver import resolve_model as _rm
                _repair_model = _rm("reconciliation", project_meta) or cache_model
                _fixed = await run_repair_pass(
                    parsed_srt, _tickets, target_lang_code, target_lang,
                    primary_code, _repair_model, rate_limiter,
                )
                update_job(job_id, log=f"{episode_name}: repair pass fixed {_fixed}/{len(_tickets)} line(s).")
                qc_stages["repair"] = f"ok ({_fixed}/{len(_tickets)})"
            else:
                qc_stages["repair"] = "ok (0 tickets)"
        except DailyLimitExhausted:
            raise
        except Exception as e:
            qc_stages["repair"] = f"failed: {e}"
            update_job(job_id, log=f"{episode_name}: repair pass failed (non-critical): {e}")
    else:
        qc_stages["repair"] = "skipped"

    # Intra-episode consistency reconciliation (Plan 20)
    if global_config.get("intra_episode_reconcile_enabled", True):
        try:
            from utils.consistency import build_intra_episode_ledger
            update_job(job_id, log=f"Running intra-episode consistency reconciliation for {episode_name}...")
            drift_findings = await build_intra_episode_ledger(
                project_name=project_name,
                parsed_srt=parsed_srt,
                target_lang_code=target_lang_code,
                target_lang_name=target_lang,
                reconcile_min_freq=global_config.get("reconcile_min_freq", 2)
            )
            if drift_findings:
                total_minority = sum(len(d["minority_lines"]) for d in drift_findings)
                max_reconcile = global_config.get("reconcile_max_lines", 30)
                if total_minority <= max_reconcile:
                    update_job(job_id, log=f"Found {len(drift_findings)} drifting terms affecting {total_minority} line(s). Re-translating...")
                    retranslated_count = await _retranslate_minority_lines(
                        project_name=project_name,
                        parsed_srt=parsed_srt,
                        drift_findings=drift_findings,
                        target_lang_code=target_lang_code,
                        target_lang_name=target_lang,
                        job_id=job_id,
                        rate_limiter=rate_limiter
                    )
                    if retranslated_count > 0:
                        update_job(job_id, log=f"Reconciled {retranslated_count} lines successfully.")
                else:
                    update_job(job_id, log=f"Drift affects {total_minority} lines (exceeds cap of {max_reconcile}), skipping auto-reconciliation.")
            else:
                update_job(job_id, log="No intra-episode consistency drift detected.")
        except Exception as e:
            update_job(job_id, log=f"Intra-episode reconciliation failed (non-critical): {e}")

    # NOTE: SubtitleEdit fixes are intentionally NOT applied here. They re-segment
    # cues (SplitLongLines can turn one long cue into several), which would break the
    # editor's required 1:1 mapping with the source. SE runs at export only, on the
    # written file (see auto_export_translated_subtitle).

    # NOTE: Subtitle conformance (wrapping / condensing / violation-flagging) is NOT
    # applied here. Wrapping inserts line breaks (formatting) and would mutate the
    # editor's 1:1 translations; it now runs at export only (auto_export_translated_
    # subtitle → _apply_output_conformance), on a copy. The editor shows live
    # conformance status per cue without needing the data rewritten.

    # Sampled judge (v2 D6): the reviewer runs on a deterministic hash-based sample of
    # episodes (review_episode_sample_pct) — a quality *trend* signal, not a per-line
    # gate on every episode. 1.0 = review every episode (legacy behavior).
    _review_sample_pct = float(global_config.get("review_episode_sample_pct", 1.0) or 0.0)
    import hashlib as _qc_hashlib
    _ep_bucket = int(_qc_hashlib.sha256(f"{project_name}/{episode_name}".encode("utf-8")).hexdigest(), 16) % 10000
    _reviewer_sampled_in = _ep_bucket < _review_sample_pct * 10000

    if global_config.get("enable_reviewer", False) and not _reviewer_sampled_in:
        qc_stages["review"] = "skipped (not in sample)"
    if global_config.get("enable_reviewer", False) and _reviewer_sampled_in:
        try:
            from utils.review_pipeline import select_lines_for_review, run_review, apply_review_results

            review_max_pct = global_config.get("review_max_pct", 0.25)
            review_indices = select_lines_for_review(
                parsed_srt, glossary, max_review_pct=review_max_pct,
            )

            if review_indices:
                update_job(job_id, log=f"{episode_name}: Reviewing {len(review_indices)} flagged lines...")

                from utils.model_resolver import resolve_model
                review_model = resolve_model("review", project_meta)
                review_threshold = global_config.get("review_threshold", 0.70)

                review_results = await run_review(
                    parsed_srt,
                    review_indices,
                    glossary,
                    target_lang,
                    model_name=review_model,
                    retranslation_threshold=review_threshold,
                    rate_limiter=rate_limiter,
                )

                if review_results:
                    stats = apply_review_results(
                        parsed_srt,
                        review_results,
                        project=project_name,
                        episode=episode_name,
                        lang_code=target_lang_code,
                    )
                    update_job(
                        job_id,
                        log=f"{episode_name}: Review complete. "
                            f"{stats['retranslated']} corrected, "
                            f"{stats['flagged']} flagged for user review "
                            f"(out of {stats['total_issues']} issues found)",
                    )
                else:
                    update_job(job_id, log=f"{episode_name}: Review passed — no issues found.")
                qc_stages["review"] = "ok"
            else:
                update_job(job_id, log=f"{episode_name}: No lines selected for review (heuristic filter).")
                qc_stages["review"] = "ok (no candidates)"
        except Exception as e:
            qc_stages["review"] = f"failed: {e}"
            update_job(job_id, log=f"{episode_name}: Review failed (non-critical): {e}")

    metadata = storage.load_episode_metadata(project_name, episode_name) or {}
    metadata["translated"] = True
    metadata["translation_status"] = "completed"
    metadata["last_translation_attempt"] = datetime.now().isoformat()
    metadata.pop("translation_failed", None)
    metadata.pop("translation_error", None)
    # v2 observability/versioning: per-stage outcomes + which bible this run used (D4/D6).
    metadata["qc_stages"] = qc_stages
    try:
        from utils.cache_manager import compute_fingerprint as _bible_fp
        metadata["bible_fp"] = _bible_fp(glossary, (project_meta or {}).get("context_guide", ""))
    except Exception:
        pass

    storage.save_episode(project_name, episode_name, parsed_srt, metadata)
    auto_export_translated_subtitle(project_name, episode_name, parsed_srt, metadata)

    if tm is not None:
        try:
            source_lines = [line.get("original", "") for line in parsed_srt]
            target_lines = [line.get("translations", {}).get(target_lang_code, "") for line in parsed_srt]
            
            # Fetch line frequencies across all episodes in the project
            frequencies = storage.get_project_line_frequencies(project_name)
            duplicate_lines = {line for line, count in frequencies.items() if count >= 2}
            
            # Filter lines based on duplicate detection or word-count heuristic (<= 3 words)
            filtered_sources = []
            filtered_targets = []
            for src, tgt in zip(source_lines, target_lines):
                src_clean = src.strip()
                tgt_clean = tgt.strip()
                if not src_clean or not tgt_clean:
                    continue
                
                normalized_src = src_clean.lower()
                word_count = len(src_clean.split())
                
                if normalized_src in duplicate_lines or word_count <= 3:
                    filtered_sources.append(src)
                    filtered_targets.append(tgt)
            
            if filtered_sources:
                # Embedding + write — off-load so it doesn't stall the loop at episode end.
                added = await asyncio.to_thread(tm.add_translations, filtered_sources, filtered_targets, episode_name)
                update_job(job_id, log=f"{episode_name}: Stored {added} common/duplicate lines in Translation Memory (filtered from {len(source_lines)} total lines)")
            else:
                update_job(job_id, log=f"{episode_name}: No common/duplicate lines to store in Translation Memory")
        except Exception as e:
            update_job(job_id, log=f"{episode_name}: TM storage failed (non-critical): {e}")


    if global_config.get("episode_summaries_enabled", True):
        try:
            from utils.episode_summaries import EpisodeSummaryManager
            from adk_agents.operations import generate_episode_summary_adk

            proj_meta = storage.load_project_metadata(project_name)
            show_name = proj_meta.get("show_name", project_name) if proj_meta else project_name
            from utils.model_resolver import resolve_model
            summary_model = resolve_model("summary", proj_meta)

            summary = await generate_episode_summary_adk(
                parsed_srt,
                episode_name,
                show_name=show_name,
                model_name=summary_model,
            )

            if summary:
                ep_summary_mgr = EpisodeSummaryManager(project_name)
                ep_summary_mgr.save_summary(episode_name, summary)
                update_job(job_id, log=f"{episode_name}: Summary stored for future context.")
        except Exception as e:
            update_job(job_id, log=f"{episode_name}: Summary generation failed (non-critical): {e}")

    if checkpoint_path.exists():
        try:
            checkpoint_path.unlink()
        except Exception:
            pass

    return True


async def _process_batch_translation(
    job_id: str, project_name: str, episode_names: List[str], 
    model: str, enhance_glossary_flag: bool, is_simple_pipeline: bool = False,
    context_model: Optional[str] = None,
    glossary_model: Optional[str] = None,
    force: bool = False
):
    update_job(job_id, status="running", progress=0.0, message="Starting translation...", log="Job started")
    global_config = storage.load_global_config()
    if not model:
        model = global_config.get("default_translation_model", "gemini-flash-lite-latest")
    active_model_var.set(model)
    try:
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return

        glossary = metadata.get("glossary", {"terms": []})
        target_lang = metadata.get("target_language", "English")
        context_guide = metadata.get("context_guide", "")

        concurrent_limit = global_config.get("concurrent_scenes", 3)
        semaphore = asyncio.Semaphore(concurrent_limit)

        shared_agent = create_translation_pipeline(
            project_name=project_name,
            target_language=target_lang,
            glossary=glossary,
            context_guide=context_guide,
            cartographer_model=model,
            translator_model=model,
            skip_glossary_step=not enhance_glossary_flag,
            temperature=storage.get_project_setting(metadata, "temperature"),
            top_k=storage.get_project_setting(metadata, "top_k"),
            top_p=storage.get_project_setting(metadata, "top_p"),
        )

        valid_episode_names = []
        skipped_existing = 0
        for ep_name in episode_names:
            ep_meta = storage.load_episode_metadata(project_name, ep_name)
            if not force and storage.episode_has_target(ep_meta):
                skipped_existing += 1
                continue
            valid_episode_names.append(ep_name)

        if skipped_existing > 0:
            update_job(job_id, log=f"Skipped {skipped_existing} episode(s) that already have a {target_lang} subtitle.")
        
        episode_names = valid_episode_names
        total = len(episode_names)
        if total == 0:
            update_job(job_id, status="completed", message="All selected episodes already have translations.", progress=100.0)
            return

        # If force=True, clear existing translations and checkpoints before translating
        if force:
            force_code = to_code(target_lang)
            for ep_name in episode_names:
                try:
                    ep_data = storage.load_episode(project_name, ep_name)
                    if ep_data and ep_data.get("data"):
                        for line in ep_data["data"]:
                            line.pop("translated", None)
                            # Critical: also clear the per-language entry. The atomic
                            # translator rebuilds translated_map from translations[code]
                            # FIRST, so leaving this stale would make force=True silently
                            # reuse the old translation and skip the LLM entirely.
                            trans = line.get("translations")
                            if isinstance(trans, dict):
                                trans.pop(force_code, None)
                            line.pop("from_tm", None)
                            line.pop("tm_user_edited", None)
                            line.pop("needs_review", None)
                            line.pop("review_issues", None)
                            line.pop("review_scores", None)
                        ep_meta = storage.load_episode_metadata(project_name, ep_name) or {}
                        ep_meta["translated"] = False
                        ep_meta["translation_status"] = "pending"
                        ep_meta.pop("translation_failed", None)
                        ep_meta.pop("translation_error", None)
                        storage.save_episode(project_name, ep_name, ep_data["data"], ep_meta)
                    # Delete checkpoint so it starts fresh
                    checkpoint_path = storage.PROJECTS_DIR / project_name / "episodes" / ep_name / "checkpoint.json"
                    if checkpoint_path.exists():
                        checkpoint_path.unlink()
                    update_job(job_id, log=f"{ep_name}: Cleared existing translation for force retranslation.")
                except Exception as e:
                    update_job(job_id, log=f"{ep_name}: Failed to clear translation (non-critical): {e}")

        completed_episodes = []
        failed_episodes = []
        
        estimate = translation_rate_limiter.estimate_requests(episode_names)
        if estimate["exceeds_daily"]:
            update_job(job_id, log=f"Warning: Batch needs ~{estimate['total_requests']} requests, daily limit is {translation_rate_limiter.daily_limit}. Will process as many as possible.")

        for idx, episode_name in enumerate(episode_names):
            if jobs[job_id].cancelled:
                update_job(job_id, status="cancelled", message="Cancelled by user")
                return

            update_job(job_id, message=f"Translating {episode_name} ({idx+1}/{total})...")
            
            try:
                success = await _translate_episode_atomic(
                    job_id=job_id,
                    project_name=project_name,
                    episode_name=episode_name,
                    target_lang=target_lang,
                    shared_agent=shared_agent,
                    rate_limiter=translation_rate_limiter,
                    semaphore=semaphore,
                    global_config=global_config,
                    total_episodes=total,
                    episode_idx=idx,
                    start_progress=(idx / total) * 100.0,
                    model_name=model,
                )

                if success:
                    completed_episodes.append(episode_name)
                    update_job(job_id, completed_episodes=completed_episodes)
                    
                    if enhance_glossary_flag:
                        try:
                            update_job(job_id, log=f"{episode_name}: Extracting new glossary terms...")
                            ep_data = storage.load_episode(project_name, episode_name)
                            if ep_data:
                                text_lines = extract_text_only(ep_data["data"])
                                new_glossary, _ = await generate_glossary_adk(
                                    text_lines,
                                    metadata.get("show_name", project_name),
                                    target_lang,
                                    existing_glossary=metadata.get("glossary"),
                                    model_name=glossary_model or model,
                                    enable_research=False
                                )
                                if new_glossary and new_glossary.get("terms"):
                                    old_glossary = metadata.get("glossary", {"terms": []})
                                    merged = _merge_glossaries(old_glossary, new_glossary)
                                    metadata["glossary"] = merged
                                    storage.save_project_metadata(project_name, metadata)
                                    added_count = len(merged["terms"]) - len(old_glossary["terms"])
                                    update_job(job_id, log=f"{episode_name}: Added {added_count} new terms to glossary.")
                                    
                                    shared_agent = create_translation_pipeline(
                                        project_name=project_name,
                                        target_language=target_lang,
                                        glossary=merged,
                                        context_guide=context_guide,
                                        cartographer_model=glossary_model or model,
                                        translator_model=model,
                                        skip_glossary_step=not enhance_glossary_flag,
                                        temperature=storage.get_project_setting(metadata, "temperature"),
                                        top_k=storage.get_project_setting(metadata, "top_k"),
                                        top_p=storage.get_project_setting(metadata, "top_p"),
                                    )
                        except Exception as e:
                            update_job(job_id, log=f"{episode_name}: Glossary enrichment failed (non-critical): {e}")
                else:
                    failed_episodes.append(episode_name)
                    update_job(job_id, failed_episodes=failed_episodes)
                    
                    ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}
                    ep_meta["translation_failed"] = True
                    ep_meta["translation_error"] = "Incomplete translation or model failure"
                    ep_meta["last_translation_attempt"] = datetime.now().isoformat()
                    storage.save_episode_metadata(project_name, episode_name, ep_meta)
                    
            except DailyLimitExhausted:
                update_job(job_id, daily_limit_reached=True, log="Daily API limit reached. Stopping batch.")
                remaining = episode_names[idx:]
                for rem_ep in remaining:
                    ep_meta = storage.load_episode_metadata(project_name, rem_ep) or {}
                    ep_meta["translation_failed"] = True
                    ep_meta["translation_error"] = "Daily Gemini API limit reached"
                    ep_meta["last_translation_attempt"] = datetime.now().isoformat()
                    storage.save_episode_metadata(project_name, rem_ep, ep_meta)
                failed_episodes.extend(remaining)
                update_job(job_id, failed_episodes=failed_episodes)
                break
            except Exception as e:
                update_job(job_id, log=f"Episode {episode_name} failed: {str(e)}")
                failed_episodes.append(episode_name)
                update_job(job_id, failed_episodes=failed_episodes)
                
                ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}
                ep_meta["translation_failed"] = True
                ep_meta["translation_error"] = str(e)
                ep_meta["last_translation_attempt"] = datetime.now().isoformat()
                storage.save_episode_metadata(project_name, episode_name, ep_meta)

        final_status = "completed" if not failed_episodes else "partial"
        message = f"Translated {len(completed_episodes)}/{total} episodes."
        if failed_episodes:
            message += f" {len(failed_episodes)} failed or skipped."
            
        update_job(
            job_id, status=final_status, progress=100.0,
            message=message,
            result={
                "completed": completed_episodes,
                "failed": failed_episodes
            }
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {str(e)}", log=str(e))


async def _process_targeted_retranslation(job_id: str, project_name: str, affected_terms: List[str], model: str):
    update_job(job_id, status="running", progress=0.0, message="Scanning for affected scenes...", log="Targeted retranslation started")
    global_config = storage.load_global_config()
    if not model:
        model = global_config.get("default_translation_model", "gemini-flash-lite-latest")
    active_model_var.set(model)
    try:
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return

        glossary = metadata.get("glossary", {"terms": []})
        target_lang = metadata.get("target_language", "English")
        target_lang_code = to_code(target_lang)
        context_guide = metadata.get("context_guide", "")
        
        episode_names = storage.list_episodes(project_name)
        if not episode_names:
            update_job(job_id, status="completed", progress=100.0, message="No episodes to process")
            return

        pipeline = create_translation_pipeline(
            project_name=project_name,
            target_language=target_lang,
            glossary=glossary,
            context_guide=context_guide,
            cartographer_model=model,
            translator_model=model,
            skip_glossary_step=True,
            temperature=storage.get_project_setting(metadata, "temperature"),
            top_k=storage.get_project_setting(metadata, "top_k"),
            top_p=storage.get_project_setting(metadata, "top_p"),
        )
        
        escaped_terms = [re.escape(term) for term in affected_terms if term.strip()]
        if not escaped_terms:
            update_job(job_id, status="completed", progress=100.0, message="No valid terms provided")
            return
            
        pattern = re.compile(r'\b(' + '|'.join(escaped_terms) + r')\b', re.IGNORECASE)

        concurrent_limit = global_config.get("concurrent_scenes", 3)
        semaphore = asyncio.Semaphore(concurrent_limit)

        episodes_to_retranslate = []
        for ep_name in episode_names:
            ep_data = storage.load_episode(project_name, ep_name)
            if not ep_data:
                continue
            
            parsed_srt = ep_data.get("data", [])
            # Only check if the episode has translated subtitles
            if not any(line.get("translated") for line in parsed_srt):
                continue
            
            has_affected_scene = False
            max_lines = global_config.get("max_lines_per_scene", 200)
            scenes = build_scene_ast(parsed_srt, gap_threshold_ms=3000, max_lines_per_scene=max_lines)
            
            for scene in scenes:
                # Only check scenes that have translations
                if not any(line.get("translated") for line in scene.get("lines", [])):
                    continue
                scene_text = " ".join(line.get("original", "") for line in scene.get("lines", []))
                if pattern.search(scene_text):
                    has_affected_scene = True
                    break
                    
            if has_affected_scene:
                episodes_to_retranslate.append(ep_name)
                
        total = len(episodes_to_retranslate)
        if total == 0:
            update_job(job_id, status="completed", progress=100.0, message="No episodes contain the affected terms")
            return
            
        update_job(job_id, message=f"Found {total} episodes to update...")
        completed_episodes = []
        failed_episodes = []
        
        for idx, ep_name in enumerate(episodes_to_retranslate):
            if jobs[job_id].cancelled:
                update_job(job_id, status="cancelled", message="Cancelled by user")
                return
                
            update_job(job_id, message=f"Updating {ep_name} ({idx+1}/{total})...")
            
            try:
                ep_data = storage.load_episode(project_name, ep_name)
                parsed_srt = ep_data["data"]
                max_lines = global_config.get("max_lines_per_scene", 200)
                scenes = build_scene_ast(parsed_srt, gap_threshold_ms=3000, max_lines_per_scene=max_lines)
                
                checkpoint_path = storage.PROJECTS_DIR / project_name / "episodes" / ep_name / "checkpoint.json"
                checkpoint_map = {}
                if checkpoint_path.exists():
                    try:
                        checkpoint_data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                        checkpoint_map = {int(k): v for k, v in checkpoint_data.items()}
                    except Exception:
                        pass
                
                translated_map = {}
                for i, line in enumerate(parsed_srt):
                    if line.get("_ass", {}).get("translatable") is False:
                        translated_map[i] = line.get("original", "")
                    elif line.get("translated"):
                        translated_map[i] = line.get("translated")
                translated_map.update(checkpoint_map)
                
                affected_scenes = []
                for scene in scenes:
                    # Only check scenes that have translations
                    if not any(line.get("translated") for line in scene.get("lines", [])):
                        continue
                    scene_text = " ".join(line.get("original", "") for line in scene.get("lines", []))
                    if pattern.search(scene_text):
                        affected_scenes.append(scene)
                        for j in range(len(scene["lines"])):
                            g_idx = scene["start_index"] + j
                            translated_map.pop(g_idx, None)
                
                if not affected_scenes:
                    completed_episodes.append(ep_name)
                    continue
                    
                profile_mgr = CharacterProfileManager(project_name)
                profiles_enabled = global_config.get("character_profiles_enabled", True)
                
                episode_context = ""
                if global_config.get("episode_summaries_enabled", True):
                    try:
                        summary_mgr = EpisodeSummaryManager(project_name)
                        summary_window = global_config.get("episode_summary_window", 3)
                        episode_context = summary_mgr.get_recent_summaries(ep_name, window=summary_window)
                    except Exception:
                        pass
                
                async def _retranslate_scene_task(
                    scene: Dict,
                    prev_scene_lines: List[Dict] = None,
                    next_scene_lines: List[Dict] = None
                ):
                    scene_label = f"{ep_name} Scene {scene['scene_id']}"
                    update_job(job_id, scene_status={scene_label: "pending"})
                    
                    async with semaphore:
                        if jobs[job_id].cancelled:
                            return
                        update_job(job_id, scene_status={scene_label: "running"})
                        
                        lines = scene.get("lines", [])
                        lines_to_translate = []
                        original_scene_indices = []
                        for j, line in enumerate(lines):
                            global_idx = scene["start_index"] + j
                            if global_idx not in translated_map:
                                lines_to_translate.append(line)
                                original_scene_indices.append(j)
                                
                        if not lines_to_translate:
                            update_job(job_id, scene_status={scene_label: "completed"})
                            return
                            
                        source_texts = [line.get("original", "") for line in lines_to_translate]
                        
                        few_shot_block = ""
                        tm = None
                        if global_config.get("tm_enabled", True):
                            try:
                                tm = TranslationMemory(project_name, lang_code=target_lang_code)
                                tm_threshold = global_config.get("tm_similarity_threshold", 0.80)
                                few_shot_block = tm.build_few_shot_block(
                                    source_texts,
                                    target_language=target_lang,
                                    max_examples=5,
                                    similarity_threshold=tm_threshold,
                                    exact_threshold=global_config.get("tm_exact_match_threshold", 0.95),
                                    reused_indices=set(),
                                )
                            except Exception:
                                pass
                                
                        priority_block = ""
                        filtered = filter_glossary_for_chunk(glossary, source_texts)
                        if filtered.get("terms"):
                            priority_block = build_priority_glossary_block(filtered)
                            
                        character_block = ""
                        if profiles_enabled:
                            try:
                                scene_profiles = profile_mgr.get_profiles_for_chunk(source_texts, glossary)
                                if scene_profiles:
                                    character_block = profile_mgr.build_profile_context(scene_profiles)
                            except Exception:
                                pass
                                
                        lookahead_k = global_config.get("scene_lookahead_lines", 2)
                        lookahead_hint = get_scene_lookahead_hint(next_scene_lines, lookahead_k) if next_scene_lines else ""

                        async def _perform_scene_translation():
                            session_id = f"retrans_{uuid4().hex[:8]}"
                            _eph = get_ephemeral_session_service()
                            from google.adk.runners import Runner as _Runner
                            runner = _Runner(agent=pipeline, app_name=f"Omnisub_{session_id}", session_service=_eph)
                            await _eph.create_session(session_id=session_id, user_id="default_user", app_name=f"Omnisub_{session_id}")
                            
                            context_hint = get_scene_context_hint(prev_scene_lines) if prev_scene_lines else ""
                            prompt = build_translation_prompt(
                                source_texts,
                                target_lang,
                                context_hint,
                                few_shot_context=few_shot_block,
                                priority_glossary=priority_block,
                                character_context=character_block,
                                episode_context=episode_context,
                                lookahead_line=lookahead_hint,
                            )
                            
                            response_text = ""
                            async for event in runner.run_async(
                                user_id="default_user",
                                session_id=session_id,
                                new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=prompt)])
                            ):
                                if jobs[job_id].cancelled:
                                    raise asyncio.CancelledError()
                                if event.content and event.content.parts:
                                    for part in event.content.parts:
                                        if part.text:
                                            response_text += part.text
                            return response_text
                            
                        response_text = ""
                        try:
                            response_text = await rate_limited_call(
                                _perform_scene_translation,
                                rate_limiter=translation_rate_limiter
                            )
                            response_text_clean = strip_reasoning_blocks(response_text)
                            parsed_lines = parse_structured(response_text_clean, expected_count=len(source_texts))

                            scene_mapped = []
                            for item in parsed_lines:
                                llm_idx = item["index"] - 1
                                if 0 <= llm_idx < len(original_scene_indices):
                                    scene_local_idx = original_scene_indices[llm_idx]
                                    global_idx = scene["start_index"] + scene_local_idx
                                    translated_text = item["text"]
                                    translated_map[global_idx] = translated_text
                                    parsed_srt[global_idx]["translated"] = translated_text
                                    # Keep the per-language store in sync — the editor,
                                    # QC/audit, and export all prefer translations[code].
                                    parsed_srt[global_idx].setdefault("translations", {})[target_lang_code] = translated_text
                                    scene_mapped.append(global_idx)

                            missing_in_scene = len(original_scene_indices) - len(scene_mapped)
                            if missing_in_scene > 0:
                                update_job(
                                    job_id,
                                    log=f"Scene {scene['scene_id']} retranslation incomplete. Expected {len(original_scene_indices)} lines, parsed {len(scene_mapped)}. Missing {missing_in_scene} lines. Model response:\n{response_text}"
                                )

                            try:
                                checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                                checkpoint_path.write_text(json.dumps({str(k): v for k, v in translated_map.items()}, indent=2, ensure_ascii=False), encoding="utf-8")
                            except Exception as e:
                                logger.error(f"Failed to write checkpoint: {e}")
                                
                            try:
                                metadata = storage.load_episode_metadata(project_name, ep_name) or {}
                                storage.save_episode(project_name, ep_name, parsed_srt, metadata, update_stats=False)
                            except Exception as e:
                                logger.error(f"Failed to perform incremental save for {ep_name}: {e}")

                            update_job(job_id, scene_status={scene_label: "completed"})
                        except Exception as e:
                            log_msg = f"Scene failed: {e}"
                            if response_text:
                                log_msg += f"\nModel response:\n{response_text}"
                            update_job(job_id, scene_status={scene_label: "failed"}, log=log_msg)
                            raise
                            
                tasks = []
                for scene in affected_scenes:
                    prev_scene_lines = None
                    next_scene_lines = None
                    for i, s in enumerate(scenes):
                        if s["scene_id"] == scene["scene_id"]:
                            prev_scene_lines = scenes[i - 1]["lines"] if i > 0 else None
                            next_scene_lines = scenes[i + 1]["lines"] if i < len(scenes) - 1 else None
                            break
                    tasks.append(_retranslate_scene_task(scene, prev_scene_lines, next_scene_lines))
                    
                await asyncio.gather(*tasks)
                
                if len(translated_map) < len(parsed_srt):
                    failed_episodes.append(ep_name)
                    continue

                for idx, text in translated_map.items():
                    parsed_srt[idx]["translated"] = text
                    parsed_srt[idx].setdefault("translations", {})[target_lang_code] = text

                # SE fixes are export-only (they re-segment cues); the saved episode
                # stays 1:1 with the source. auto_export applies them to the file.
                metadata = storage.load_episode_metadata(project_name, ep_name) or {}
                storage.save_episode(project_name, ep_name, parsed_srt)
                auto_export_translated_subtitle(project_name, ep_name, parsed_srt, metadata)
                completed_episodes.append(ep_name)
                update_job(job_id, completed_episodes=completed_episodes, log=f"Updated {ep_name}")
                
                if checkpoint_path.exists():
                    checkpoint_path.unlink()
            except Exception as ep_err:
                update_job(job_id, log=f"Failed to update {ep_name}: {ep_err}")
                failed_episodes.append(ep_name)
                
        final_status = "completed" if not failed_episodes else "partial"
        message = f"Updated {len(completed_episodes)}/{total} episodes."
        update_job(job_id, status=final_status, progress=100.0, message=message, result={"completed": completed_episodes, "failed": failed_episodes})
    except Exception as e:
        update_job(job_id, status="failed", message=f"Retranslation error: {str(e)}", log=str(e))


async def _process_retranslation_for_comparison(
    job_id: str, project_name: str, episode_name: str, model: str
):
    active_model_var.set(model)
    update_job(job_id, status="running", progress=0.0, message=f"Retranslating {episode_name}...", log="Job started")
    try:
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return

        glossary = metadata.get("glossary", {"terms": []})
        target_lang = metadata.get("target_language", "English")
        target_lang_code = to_code(target_lang)
        context_guide = metadata.get("context_guide", "")

        global_config = storage.load_global_config()
        concurrent_limit = global_config.get("concurrent_scenes", 3)
        semaphore = asyncio.Semaphore(concurrent_limit)

        shared_agent = create_translation_pipeline(
            project_name=project_name,
            target_language=target_lang,
            glossary=glossary,
            context_guide=context_guide,
            cartographer_model=model,
            translator_model=model,
            skip_glossary_step=True,
            temperature=storage.get_project_setting(metadata, "temperature"),
            top_k=storage.get_project_setting(metadata, "top_k"),
            top_p=storage.get_project_setting(metadata, "top_p"),
        )

        episode_data = storage.load_episode(project_name, episode_name)
        if not episode_data:
            update_job(job_id, status="failed", message="Episode not found")
            return

        parsed_srt = episode_data["data"]
        translated_map = {}

        for i, entry in enumerate(parsed_srt):
            if entry.get("_ass", {}).get("translatable") is False:
                translated_map[i] = entry.get("original", "")
            elif not entry.get("original", "").strip():
                translated_map[i] = ""

        max_lines = global_config.get("max_lines_per_scene", 200)
        scenes = build_scene_ast(parsed_srt, gap_threshold_ms=3000, max_lines_per_scene=max_lines)
        total_scenes = len(scenes)

        _eph_svc = get_ephemeral_session_service()
        from google.adk.runners import Runner as _Runner

        profile_mgr = CharacterProfileManager(project_name)
        profiles_enabled = global_config.get("character_profiles_enabled", True)

        episode_context = ""
        if global_config.get("episode_summaries_enabled", True):
            try:
                from utils.episode_summaries import EpisodeSummaryManager
                summary_mgr = EpisodeSummaryManager(project_name)
                summary_window = global_config.get("episode_summary_window", 3)
                episode_context = summary_mgr.get_recent_summaries(episode_name, window=summary_window)
            except Exception:
                pass

        async def _translate_scene_task(
            scene: Dict,
            prev_scene_lines: List[Dict] = None,
            next_scene_lines: List[Dict] = None
        ):
            scene_label = f"Scene {scene['scene_id']}"
            update_job(job_id, scene_status={scene_label: "pending"})

            async with semaphore:
                if jobs[job_id].cancelled:
                    return

                update_job(job_id, scene_status={scene_label: "running"})
                lines = scene.get("lines", [])
                if not lines:
                    update_job(job_id, scene_status={scene_label: "completed"})
                    return

                source_texts = [line.get("original", "") for line in lines]
                
                few_shot_block = ""
                if global_config.get("tm_enabled", True):
                    try:
                        tm = TranslationMemory(project_name, lang_code=target_lang_code)
                        tm_threshold = global_config.get("tm_similarity_threshold", 0.80)
                        few_shot_block = tm.build_few_shot_block(
                            source_texts,
                            target_language=target_lang,
                            max_examples=5,
                            similarity_threshold=tm_threshold,
                            exact_threshold=global_config.get("tm_exact_match_threshold", 0.95),
                            reused_indices=set(),
                        )
                    except Exception:
                        pass

                priority_block = ""
                filtered = filter_glossary_for_chunk(glossary, source_texts)
                if filtered.get("terms"):
                    priority_block = build_priority_glossary_block(filtered)

                character_block = ""
                if profiles_enabled:
                    try:
                        scene_profiles = profile_mgr.get_profiles_for_chunk(source_texts, glossary)
                        if scene_profiles:
                            character_block = profile_mgr.build_profile_context(scene_profiles)
                    except Exception:
                        pass

                lookahead_k = global_config.get("scene_lookahead_lines", 2)
                lookahead_hint = get_scene_lookahead_hint(next_scene_lines, lookahead_k) if next_scene_lines else ""

                async def _perform_translation():
                    session_id = f"comparison_{uuid4().hex[:8]}"
                    runner = _Runner(agent=shared_agent, app_name=f"Omnisub_{session_id}", session_service=_eph_svc)
                    await _eph_svc.create_session(session_id=session_id, user_id="default_user", app_name=f"Omnisub_{session_id}")

                    context_hint = get_scene_context_hint(prev_scene_lines) if prev_scene_lines else ""
                    prompt = build_translation_prompt(
                        source_texts,
                        target_lang,
                        context_hint,
                        few_shot_context=few_shot_block,
                        priority_glossary=priority_block,
                        character_context=character_block,
                        episode_context=episode_context,
                        lookahead_line=lookahead_hint,
                    )

                    response_text = ""
                    async for event in runner.run_async(
                        user_id="default_user",
                        session_id=session_id,
                        new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=prompt)])
                    ):
                        if jobs[job_id].cancelled:
                            raise asyncio.CancelledError()
                        if event.content and event.content.parts:
                            for part in event.content.parts:
                                if part.text:
                                    response_text += part.text
                    return response_text

                response_text = ""
                try:
                    response_text = await rate_limited_call(_perform_translation, rate_limiter=translation_rate_limiter)
                    response_text_clean = strip_reasoning_blocks(response_text)
                    parsed_lines = parse_structured(response_text_clean, expected_count=len(source_texts))

                    scene_mapped = []
                    for item in parsed_lines:
                        llm_idx = item["index"] - 1
                        if 0 <= llm_idx < len(lines):
                            global_idx = scene["start_index"] + llm_idx
                            translated_map[global_idx] = item["text"]
                            scene_mapped.append(global_idx)

                    missing_in_scene = len(lines) - len(scene_mapped)
                    if missing_in_scene > 0:
                        update_job(
                            job_id,
                            log=f"Scene {scene['scene_id']} comparison retranslation incomplete. Expected {len(lines)} lines, parsed {len(scene_mapped)}. Missing {missing_in_scene} lines. Model response:\n{response_text}"
                        )

                    update_job(job_id, scene_status={scene_label: "completed"})
                    completed_count = len([s for s in jobs[job_id].scene_status.items() if s[1] == "completed" and s[0].startswith("Scene")])
                    progress = (completed_count / total_scenes) * 100.0
                    update_job(job_id, progress=progress)
                except Exception as e:
                    log_msg = f"Scene {scene['scene_id']} failed: {str(e)}"
                    if response_text:
                        log_msg += f"\nModel response:\n{response_text}"
                    update_job(job_id, scene_status={scene_label: "failed"}, log=log_msg)
                    raise

        update_job(job_id, scene_status={f"Scene {s['scene_id']}": "pending" for s in scenes})

        tasks = []
        for i, scene in enumerate(scenes):
            prev_scene_lines = scenes[i - 1]["lines"] if i > 0 else None
            next_scene_lines = scenes[i + 1]["lines"] if i < len(scenes) - 1 else None
            tasks.append(_translate_scene_task(scene, prev_scene_lines, next_scene_lines))

        await asyncio.gather(*tasks)

        if jobs[job_id].cancelled:
            update_job(job_id, status="cancelled", message="Cancelled by user")
            return

        comparison_data = []
        for idx, entry in enumerate(parsed_srt):
            comparison_data.append({
                "index": idx + 1,
                "original": entry.get("original", ""),
                "current": entry.get("translated", ""),
                "new": translated_map.get(idx, "")
            })

        update_job(
            job_id, status="completed", progress=100.0,
            message="Retranslation complete.",
            result={"comparison": comparison_data}
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Retranslation error: {str(e)}", log=str(e))


def _select_golden_ratio_files(episodes: List[str]) -> List[str]:
    """Select a few representative episodes from a list using golden ratio offsets."""
    if not episodes:
        return []
    if len(episodes) <= 5:
        return episodes
        
    n = len(episodes)
    phi = 0.61803398875
    indices = {0, n - 1}
    
    indices.add(round((n - 1) * (1 - phi)))
    indices.add(round((n - 1) * phi))
    
    if n > 12:
        indices.add(round((n - 1) * (1 - phi**2)))
        indices.add(round((n - 1) * (1 - (1-phi)**2)))

    sorted_indices = sorted(list(indices))
    return [episodes[i] for i in sorted_indices if 0 <= i < n]


def _merge_glossaries(existing: Dict, new: Dict) -> Dict:
    """Merge two glossaries, avoiding duplicates by term (case-insensitive)."""
    if not existing:
        return new or {"terms": []}
    if not new:
        return existing
        
    merged_terms = existing.get("terms", [])[:]
    existing_terms_lower = {t.get("term", "").lower() for t in merged_terms}
    
    for term in new.get("terms", []):
        if term.get("term", "").lower() not in existing_terms_lower:
            merged_terms.append(term)
            existing_terms_lower.add(term.get("term", "").lower())
            
    return {"terms": merged_terms}


async def _pipeline_pause(job_id: str, stage: str, message: str):
    """Pause pipeline and wait for user to call /pipeline/continue."""
    event = asyncio.Event()
    _pipeline_resume_events[job_id] = event
    jobs[job_id].status = "awaiting_review"
    jobs[job_id].message = message
    jobs[job_id].logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] Paused for review ({stage})")
    while not event.is_set():
        if jobs[job_id].cancelled:
            return
        await asyncio.sleep(0.5)


async def _resume_pipeline(job_id: str, project_name: str):
    """Signal a paused pipeline to continue."""
    event = _pipeline_resume_events.get(job_id)
    if event:
        event.set()


async def _gather_project_text(project_name: str, episode_names: List[str] = None) -> List[str]:
    """Gather all subtitle text from specified or all episodes."""
    episodes = episode_names or storage.list_episodes(project_name)
    all_text = []

    for ep_name in episodes:
        ep_data = storage.load_episode(project_name, ep_name)
        if ep_data and isinstance(ep_data, dict) and "data" in ep_data:
            data = ep_data["data"]
            if isinstance(data, list):
                all_text.extend(extract_text_only(data))

    return all_text


async def translate_project_missing_lines(job_id: str, project_name: str):
    """Background task to scan all episodes in the project, find missing lines, and translate them with context."""
    from utils.jobs_manager import update_job, jobs
    from utils.language_codes import to_code
    from utils.model_resolver import resolve_model
    from utils.rate_limiter import translation_rate_limiter, active_model_var
    import logging
    logger = logging.getLogger(__name__)

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

        # Build list of missing lines first
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
                        # Find matching target language name for this code
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

        # Group tasks by episode so each episode is loaded, saved, and re-exported exactly
        # once instead of once per missing line (was O(N) full-file I/O per episode). A
        # plain dict preserves insertion order (Python 3.7+), keeping episode order stable.
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

                # Build translated map for the context helper. Rebuilt per line so later
                # gaps in the same episode see fixes already applied in this pass.
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

            # Persist the episode once, after all its gaps have been processed.
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

