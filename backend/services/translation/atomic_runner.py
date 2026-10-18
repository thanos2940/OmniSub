"""
Atomic Episode Translation Runner.
Handles single-episode translation orchestration, scene AST chunking,
translation memory lookups, structured output parsing, and QC funnels.
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any
from uuid import uuid4

from utils import storage
from utils.language_codes import to_code
from utils.srt_parser import build_scene_ast, get_scene_context_hint, get_scene_lookahead_hint
from utils.llm_utils import strip_reasoning_blocks
from utils.structured_output import parse_structured
from utils.text_normalize import sanitize_translation_text
from utils.rate_limiter import RateLimiter, DailyLimitExhausted, active_model_var, per_model_rate_limiter
from utils.api_call_wrapper import rate_limited_call
from utils.translation_memory import TranslationMemory, filter_glossary_for_chunk, build_priority_glossary_block
from utils.character_profiles import CharacterProfileManager
from utils.jobs_manager import update_job, jobs

from adk_agents.translator_agent import build_translation_prompt
from adk_config import get_ephemeral_session_service
from google.adk.runners import types as adk_types, Runner as _Runner

from services.translation.export_formatter import auto_export_translated_subtitle
from services.translation.line_recovery import (
    _retranslate_minority_lines,
    _translate_missing_line_with_context,
)

logger = logging.getLogger(__name__)


def build_macro_chunks(
    parsed_data: List[Dict],
    max_lines_per_chunk: int = 450,
    gap_threshold_ms: int = 3000,
) -> List[Dict]:
    """Group subtitle cues into macro-chunks optimized for API call efficiency and context.

    Sizing:
    - Standard TV episodes (300-800 cues) -> <= 2 macro-chunks (<= 2 API calls)
    - Full-length movies (800-1800 cues) -> <= 5 macro-chunks (~350-450 cues per chunk)
    - Small episodes (<= 450 cues) -> 1 macro-chunk (1 API call)

    Splits are chosen at the largest natural scene/time gaps near the target boundaries.
    """
    total = len(parsed_data)
    if total == 0:
        return []

    if total <= max_lines_per_chunk:
        chunk_count = 1
    elif total <= 850:
        chunk_count = 2
    elif total <= 1300:
        chunk_count = 3
    elif total <= 1750:
        chunk_count = 4
    elif total <= 2200:
        chunk_count = 5
    else:
        import math
        chunk_count = max(5, math.ceil(total / max_lines_per_chunk))

    if chunk_count <= 1:
        return [{
            "scene_id": 1,
            "start_index": 0,
            "end_index": total,
            "lines": parsed_data,
        }]

    from utils.srt_parser import timecode_to_ms
    cues_meta = []
    for entry in parsed_data:
        tc = entry.get("timecode", "")
        tc_parts = tc.split("-->")
        start_ms = end_ms = 0
        if len(tc_parts) == 2:
            start_ms = timecode_to_ms(tc_parts[0].strip())
            end_ms = timecode_to_ms(tc_parts[1].strip())
        cues_meta.append((start_ms, end_ms))

    split_indices = [0]
    for k in range(1, chunk_count):
        target_idx = round(k * total / chunk_count)
        window_radius = min(75, max(10, total // (2 * chunk_count)))
        w_start = max(split_indices[-1] + 10, target_idx - window_radius)
        w_end = min(total - (chunk_count - k) * 10, target_idx + window_radius)

        best_split = target_idx
        best_gap = -1

        for j in range(w_start, w_end):
            prev_end = cues_meta[j - 1][1]
            curr_start = cues_meta[j][0]
            gap = curr_start - prev_end
            if gap > best_gap:
                best_gap = gap
                best_split = j

        split_indices.append(best_split)

    split_indices.append(total)

    chunks = []
    for scene_id in range(1, len(split_indices)):
        s_idx = split_indices[scene_id - 1]
        e_idx = split_indices[scene_id]
        chunks.append({
            "scene_id": scene_id,
            "start_index": s_idx,
            "end_index": e_idx,
            "lines": parsed_data[s_idx:e_idx],
        })

    return chunks


def infer_speaker(text: str, chars: List[str] = None) -> str:
    """Infer the speaker name from subtitle prefix or character list."""
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


async def _translate_episode_atomic(
    job_id: str,
    project_name: str,
    episode_name: str,
    target_lang: str,
    shared_agent: Any = None,
    rate_limiter: Optional[RateLimiter] = None,
    semaphore: Optional[asyncio.Semaphore] = None,
    global_config: Optional[Dict] = None,
    total_episodes: int = 1,
    episode_idx: int = 0,
    start_progress: float = 0.0,
    model_name: Optional[str] = None,
    lang_suffix: str = "",
    disable_tm: bool = False,
) -> bool:
    """Translates an entire episode. Resumable via checkpointing."""
    if global_config is None:
        global_config = storage.load_global_config() or {}
    # The first error the model API returned. When the episode ends up failing,
    # this — "API key not valid", "quota exceeded" — is the reason worth showing,
    # not the "N lines untranslated" consequence of it.
    model_errors: List[str] = []
    if semaphore is None:
        semaphore = asyncio.Semaphore(1)
    if rate_limiter is None:
        from utils.rate_limiter import get_rate_limiter
        rate_limiter = get_rate_limiter(model_name or "default")

    from utils.model_resolver import resolve_model
    project_meta = storage.load_project_metadata(project_name)
    if model_name:
        active_model_var.set(model_name)
    elif active_model_var.get() in (None, "default"):
        active_model_var.set(resolve_model("translation", project_meta))

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

    target_lang_code = to_code(target_lang)
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
    checkpoint_path = storage.episode_dir(project_name, episode_name) / _cp_name
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

    from adk_agents.llm_factory import get_recommended_chunk_size
    from utils.model_resolver import resolve_model as _resolve_model_for_chunks
    effective_model = model_name or active_model_var.get()
    if not effective_model or effective_model == "default":
        effective_model = _resolve_model_for_chunks("translation", project_meta)
    configured_max = global_config.get("max_lines_per_scene", 450)
    model_max = get_recommended_chunk_size(effective_model)
    max_lines = max(10, min(configured_max, model_max))
    if model_max < configured_max:
        update_job(job_id, log=f"{episode_name}: capping scene size at {max_lines} lines for model '{effective_model}' (configured {configured_max}).")
    
    use_macro = global_config.get("macro_chunking", True)
    if use_macro and max_lines >= 100:
        scenes = build_macro_chunks(parsed_srt, max_lines_per_chunk=max_lines, gap_threshold_ms=3000)
    else:
        scenes = build_scene_ast(parsed_srt, gap_threshold_ms=3000, max_lines_per_scene=max_lines)
    total_scenes = len(scenes)

    update_job(job_id, log=f"Translating {episode_name} ({total_scenes} scenes/macro-chunks)")

    _eph_svc = get_ephemeral_session_service()

    profile_mgr = CharacterProfileManager(project_name)
    profiles_enabled = global_config.get("character_profiles_enabled", True)

    tm_enabled = global_config.get("tm_enabled", True) and not disable_tm
    tm = None
    tm_exact_count = 0

    if tm_enabled:
        try:
            tm = TranslationMemory(project_name, lang_code=target_lang_code)
            tm_threshold = float(global_config.get("tm_exact_match_threshold", 0.95))
            max_words = global_config.get("tm_exact_reuse_max_words", None)
            require_same_speaker = bool(global_config.get("tm_exact_reuse_require_same_speaker", False))
            speakers = [line.get("character", "") for line in parsed_srt] if require_same_speaker else None

            # Offload CPU/LanceDB search to worker thread and pass proper parameters
            exact_matches = await asyncio.to_thread(
                tm.get_exact_matches,
                [line.get("original", "") for line in parsed_srt],
                threshold=tm_threshold,
                max_words=max_words,
                require_same_speaker=require_same_speaker,
                speakers=speakers,
            )
            for idx, match_info in exact_matches.items():
                trans_text = match_info.get("target") if isinstance(match_info, dict) else match_info
                if idx not in translated_map and trans_text:
                    translated_map[idx] = trans_text
                    if "translations" not in parsed_srt[idx]:
                        parsed_srt[idx]["translations"] = {}
                    parsed_srt[idx]["translations"][target_lang_code] = trans_text
                    if target_lang_code == primary_code:
                        parsed_srt[idx]["translated"] = trans_text
                    parsed_srt[idx]["from_tm"] = True
                    tm_exact_count += 1
            if tm_exact_count > 0:
                update_job(job_id, log=f"{episode_name}: Pre-filled {tm_exact_count} exact matches from Translation Memory.")
        except Exception as e:
            update_job(job_id, log=f"{episode_name}: TM exact match check failed: {e}")

    glossary = project_meta.get("glossary", {"terms": []})
    context_guide = project_meta.get("context_guide", "")

    episode_context = ""
    if global_config.get("episode_summaries_enabled", True):
        try:
            from utils.episode_summaries import EpisodeSummaryManager
            ep_summary_mgr = EpisodeSummaryManager(project_name)
            prev_summaries = ep_summary_mgr.get_previous_summaries(episode_name, count=2)
            if prev_summaries:
                episode_context = ep_summary_mgr.format_summaries_for_prompt(prev_summaries)
        except Exception as e:
            logger.warning(f"Could not load episode summaries for {episode_name}: {e}")

    from utils.model_resolver import get_daily_limit_policy, resolve_model
    daily_limit_policy = get_daily_limit_policy(project_meta)

    failover_state = {
        "model_name": effective_model,
        "rate_limiter": rate_limiter or per_model_rate_limiter.get_limiter(effective_model),
        "shared_agent": shared_agent,
        "cache_model": effective_model,
        "context_cache_name": None,
        "cached_system_instruction": "",
        "lock": asyncio.Lock(),
        "failover_count": 0,
    }

    async def _handle_daily_limit_failover(
        exc: Optional[Exception] = None,
        failed_model: Optional[str] = None,
    ) -> bool:
        """Dynamic failover to configured fallback model if daily_limit_policy == 'auto_fallback'.
        Returns True if successfully failed over to a new model, False otherwise."""
        if daily_limit_policy != "auto_fallback":
            return False

        async with failover_state["lock"]:
            current_model = failover_state["model_name"]
            # Concurrency guard: If a concurrent task already failed over from the model
            # that threw this exception, reuse the active fallback without latching it!
            if failed_model and failed_model != current_model:
                logger.info(
                    f"{episode_name}: Concurrency race avoided — task failed on '{failed_model}', "
                    f"but active model already failed over to '{current_model}'. Reusing fallback model."
                )
                return True

            limiter = per_model_rate_limiter.get_limiter(current_model)
            limiter.trigger_daily_limit()

            fallback_model = resolve_model(
                "translation",
                project_meta,
                check_exhaustion=True,
                daily_limit_policy="auto_fallback"
            )

            fb_limiter = per_model_rate_limiter.get_limiter(fallback_model)
            fb_is_exhausted = (
                (hasattr(fb_limiter, "is_daily_limit_exhausted") and fb_limiter.is_daily_limit_exhausted()) or
                fb_limiter.maybe_reset_daily()
            )

            if not fallback_model or fallback_model == current_model or fb_is_exhausted:
                logger.warning(
                    f"{episode_name}: Mid-run failover impossible — primary model '{current_model}' exhausted and fallback '{fallback_model}' unavailable."
                )
                return False

            failover_msg = (
                f"Primary model '{current_model}' encountered daily quota exhaustion (429). "
                f"Mid-run dynamic failover to '{fallback_model}' (policy: {daily_limit_policy})."
            )
            logger.info(f"{episode_name}: {failover_msg}")
            update_job(job_id, log=f"{episode_name}: {failover_msg}")

            failover_state["model_name"] = fallback_model
            failover_state["rate_limiter"] = fb_limiter
            failover_state["cache_model"] = fallback_model
            failover_state["context_cache_name"] = None
            failover_state["failover_count"] += 1
            active_model_var.set(fallback_model)

            try:
                from adk_agents.translation_pipeline import create_translation_pipeline
                failover_state["shared_agent"] = create_translation_pipeline(
                    project_name=project_name,
                    target_language=target_lang,
                    glossary=glossary,
                    context_guide=context_guide,
                    cartographer_model=fallback_model,
                    translator_model=fallback_model,
                    skip_glossary_step=True,
                    temperature=global_config.get("temperature", 0.3),
                    top_k=global_config.get("top_k"),
                    top_p=global_config.get("top_p"),
                )
            except Exception as pipe_err:
                logger.warning(f"Could not re-create shared_agent for fallback model {fallback_model}: {pipe_err}")
                failover_state["shared_agent"] = None

            return True

    use_context_cache = global_config.get("context_cache_enabled", False)
    context_cache_name = None
    cache_model = effective_model
    cached_system_instruction = ""
    conformance_hint = ""

    if use_context_cache:
        try:
            from utils.cache_manager import get_or_create_context_cache
            cached_system_instruction = getattr(shared_agent, "instruction", "")
            failover_state["cached_system_instruction"] = cached_system_instruction
            cache_info = await get_or_create_context_cache(
                project_name=project_name,
                model_name=cache_model,
                glossary=glossary,
                context_guide=context_guide,
                character_profiles=profile_mgr.load_all_resolved() if profiles_enabled else None,
                system_instruction=cached_system_instruction,
                target_language=target_lang,
            )
            if cache_info and cache_info.get("name"):
                context_cache_name = cache_info["name"]
                failover_state["context_cache_name"] = context_cache_name
                update_job(job_id, log=f"{episode_name}: using Gemini context cache '{context_cache_name}' (model: {cache_model})")
        except Exception as ce:
            update_job(job_id, log=f"{episode_name}: Context cache setup skipped ({ce}); translating directly.")

    # Whole-episode single-call mode
    episode_mode = global_config.get("episode_request_mode", "auto")
    whole_cap = global_config.get("whole_episode_max_cues", 450)
    _untranslated = [i for i in range(len(parsed_srt))
                     if i not in translated_map and parsed_srt[i].get("original", "").strip()]
    use_whole_episode = (
        episode_mode != "scenes"
        and len(_untranslated) > 0
        and (episode_mode == "whole" or len(_untranslated) <= whole_cap)
    )

    def _is_cancelled() -> bool:
        if job_id and job_id in jobs and jobs[job_id].cancelled:
            return True
        if job_id:
            try:
                from utils.translation_queue import TranslationQueue
                q = TranslationQueue()
                raw_id = job_id[3:] if job_id.startswith("bg_") else job_id
                item = q.get_item(raw_id)
                if item and item.get("status") == "cancelled":
                    if job_id in jobs:
                        jobs[job_id].cancelled = True
                    return True
            except Exception:
                pass
        return False

    bible = None
    if use_whole_episode:
        from services.prompt_assembler import compile_bible, assemble_episode_request
        from utils.structured_output import parse_new_terms
        from utils.consistency import save_suggestions
        bible = compile_bible(project_name, project_meta or {}, cache_model, target_language=target_lang)
        collect_terms = (
            bool(global_config.get("new_terms_suggest_enabled", True))
            and not context_cache_name
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
            if _is_cancelled():
                return False
            remaining = [i for i in _untranslated if i not in translated_map]
            if not remaining:
                break
            req = assemble_episode_request(
                bible,
                [parsed_srt[i] for i in remaining],
                remaining,
                episode_context=episode_context,
                conformance_hint=conformance_hint,
                collect_new_terms=collect_terms and _attempt == 0,
            )
            try:
                async def _whole_ep_call():
                    from adk_agents.llm_factory import generate
                    return await generate(
                        model_name=failover_state["cache_model"],
                        prompt=req.prompt,
                        system_instruction=req.system_instruction,
                        temperature=global_config.get("temperature", 0.3) or 0.3,
                        response_schema=req.response_schema,
                        cache_name=failover_state["context_cache_name"],
                        role="translation",
                    )

                resp_text = await rate_limited_call(_whole_ep_call, rate_limiter=failover_state["rate_limiter"])
                clean_resp = strip_reasoning_blocks(resp_text or "")
                if collect_terms and _attempt == 0:
                    new_terms = parse_new_terms(clean_resp)
                    if new_terms:
                        save_suggestions(project_name, new_terms)

                parsed_whole = parse_structured(clean_resp, expected_count=len(remaining))
                for item in parsed_whole:
                    idx = item["index"] - 1
                    if 0 <= idx < len(remaining):
                        global_idx = remaining[idx]
                        translated_map[global_idx] = item["text"]
                        parsed_srt[global_idx].setdefault("translations", {})[target_lang_code] = item["text"]
                        if target_lang_code == primary_code:
                            parsed_srt[global_idx]["translated"] = item["text"]
            except DailyLimitExhausted as dle:
                if daily_limit_policy == "auto_fallback" and await _handle_daily_limit_failover(dle):
                    continue
                raise
            except Exception as e:
                logger.warning(f"Whole episode attempt {_attempt+1} failed: {e}")
                model_errors.append(str(e))
                update_job(job_id, log=f"{episode_name}: whole-episode request failed ({e}); falling back to scenes.")
                break

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

            # The prompt is built inside the retried call below; the job's debug view
            # reads it after the call returns, from out here.
            sent_prompt = {"text": ""}

            async def _perform_translation():
                context_hint = get_scene_context_hint(prev_scene_lines) if prev_scene_lines else ""
                prompt = sent_prompt["text"] = build_translation_prompt(
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

                if not failover_state["shared_agent"] or use_context_cache:
                    from adk_agents.llm_factory import generate
                    from utils.structured_output import SceneTranslationResponse, use_structured_output
                    structured = use_structured_output(failover_state["cache_model"])
                    return await generate(
                        model_name=failover_state["cache_model"],
                        prompt=prompt,
                        cache_name=failover_state["context_cache_name"],
                        system_instruction=failover_state["cached_system_instruction"] if not failover_state["context_cache_name"] else "",
                        temperature=global_config.get("temperature", 0.3) or 0.3,
                        response_schema=SceneTranslationResponse if structured else None,
                        role="translation",
                    )

                session_id = f"scene_{uuid4().hex[:8]}"
                runner = _Runner(agent=failover_state["shared_agent"], app_name=f"Omnisub_{session_id}", session_service=_eph_svc)
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
            for attempt_idx in range(2):
                if _is_cancelled():
                    return
                attempt_model = failover_state["model_name"]
                try:
                    response_text = await rate_limited_call(
                        _perform_translation,
                        rate_limiter=failover_state["rate_limiter"]
                    )
                    break
                except DailyLimitExhausted as dle:
                    if attempt_idx == 0 and daily_limit_policy == "auto_fallback":
                        did_failover = await _handle_daily_limit_failover(dle, failed_model=attempt_model)
                        if did_failover:
                            continue
                    update_job(job_id, scene_status={scene_label: "failed"})
                    raise
                except Exception as e:
                    model_errors.append(str(e))
                    log_msg = f"Scene {scene['scene_id']} failed: {str(e)}"
                    if response_text:
                        log_msg += f"\nModel response:\n{response_text}"
                    update_job(job_id, scene_status={scene_label: "failed"}, log=log_msg)
                    raise

            if _is_cancelled():
                return

            try:
                reasoning_match = re.search(r'<(?:internal_reasoning|think|thinking)>(.*?)</(?:internal_reasoning|think|thinking)>', response_text, re.DOTALL | re.IGNORECASE)
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
                        raw_translated = item["text"]
                        source_text_for_line = source_texts[llm_idx] if 0 <= llm_idx < len(source_texts) else ""
                        translated_text = sanitize_translation_text(
                            raw_translated,
                            source_text=source_text_for_line,
                            target_lang=target_lang,
                            target_lang_code=target_lang_code
                        )
                        translated_map[global_idx] = translated_text
                        if "translations" not in parsed_srt[global_idx]:
                            parsed_srt[global_idx]["translations"] = {}
                        parsed_srt[global_idx]["translations"][target_lang_code] = translated_text
                        if target_lang_code == primary_code:
                            parsed_srt[global_idx]["translated"] = translated_text
                        scene_mapped.append(global_idx)
                        if blacklist_map.get(global_idx) and translated_text.strip() in {b.strip() for b in blacklist_map[global_idx]}:
                            parsed_srt[global_idx]["needs_review"] = True
                            parsed_srt[global_idx]["review_issues"] = "Reproduced a blacklisted translation"

                missing_in_scene = len(original_scene_indices) - len(scene_mapped)
                if missing_in_scene > 0:
                    missing_pct_scene = missing_in_scene / len(original_scene_indices)
                    update_job(
                        job_id,
                        log=f"Scene {scene['scene_id']} translation incomplete. Expected {len(original_scene_indices)} lines, parsed {len(scene_mapped)}. Missing {missing_in_scene} lines ({missing_pct_scene:.1%})."
                    )

                    # Automated localized sub-chunk repair pass (up to 25 missing cues in scene)
                    if 0 < missing_in_scene <= 25:
                        missing_scene_locals = [orig_idx for orig_idx in original_scene_indices if (scene["start_index"] + orig_idx) not in set(scene_mapped)]
                        sub_missing_global = [scene["start_index"] + orig_idx for orig_idx in missing_scene_locals]
                        sub_source_texts = [parsed_srt[g_idx].get("original", "") for g_idx in sub_missing_global]

                        update_job(
                            job_id,
                            log=f"Scene {scene['scene_id']}: Running automated localized sub-chunk repair for {len(sub_source_texts)} dropped cue(s)."
                        )

                        async def _perform_sub_repair():
                            sub_prompt = build_translation_prompt(
                                sub_source_texts,
                                target_lang,
                                context_hint=f"CRITICAL REPAIR: Translate ONLY these {len(sub_source_texts)} missing cue(s) from scene {scene['scene_id']}.",
                                few_shot_context=few_shot_block,
                                priority_glossary=priority_block,
                                character_context=character_block,
                                episode_context=episode_context,
                                conformance_hint=conformance_hint,
                                negative_examples=negative_block,
                            )
                            if not failover_state["shared_agent"] or use_context_cache:
                                from adk_agents.llm_factory import generate
                                from utils.structured_output import SceneTranslationResponse, use_structured_output
                                structured = use_structured_output(failover_state["cache_model"])
                                return await generate(
                                    model_name=failover_state["cache_model"],
                                    prompt=sub_prompt,
                                    cache_name=failover_state["context_cache_name"],
                                    system_instruction=failover_state["cached_system_instruction"] if not failover_state["context_cache_name"] else "",
                                    temperature=0.2,
                                    response_schema=SceneTranslationResponse if structured else None,
                                    role="repair",
                                )

                            sub_session_id = f"repair_{uuid4().hex[:8]}"
                            sub_runner = _Runner(agent=failover_state["shared_agent"], app_name=f"Omnisub_{sub_session_id}", session_service=_eph_svc)
                            await _eph_svc.create_session(
                                session_id=sub_session_id, user_id="default_user", app_name=f"Omnisub_{sub_session_id}"
                            )
                            sub_resp = ""
                            async for event in sub_runner.run_async(
                                user_id="default_user",
                                session_id=sub_session_id,
                                new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=sub_prompt)])
                            ):
                                if jobs[job_id].cancelled:
                                    raise asyncio.CancelledError()
                                if event.content and event.content.parts:
                                    for part in event.content.parts:
                                        if part.text:
                                            sub_resp += part.text
                            return sub_resp

                        try:
                            sub_resp_text = await rate_limited_call(
                                _perform_sub_repair,
                                rate_limiter=failover_state["rate_limiter"]
                            )
                            sub_parsed = parse_structured(strip_reasoning_blocks(sub_resp_text or ""), expected_count=len(sub_source_texts))
                            repaired_count = 0
                            for item in sub_parsed:
                                s_idx = item["index"] - 1
                                if 0 <= s_idx < len(sub_missing_global):
                                    g_idx = sub_missing_global[s_idx]
                                    sub_src = sub_source_texts[s_idx] if 0 <= s_idx < len(sub_source_texts) else ""
                                    repaired_txt = sanitize_translation_text(
                                        item["text"].strip(),
                                        source_text=sub_src,
                                        target_lang=target_lang,
                                        target_lang_code=target_lang_code
                                    )
                                    if repaired_txt:
                                        translated_map[g_idx] = repaired_txt
                                        if "translations" not in parsed_srt[g_idx]:
                                            parsed_srt[g_idx]["translations"] = {}
                                        parsed_srt[g_idx]["translations"][target_lang_code] = repaired_txt
                                        if target_lang_code == primary_code:
                                            parsed_srt[g_idx]["translated"] = repaired_txt
                                        scene_mapped.append(g_idx)
                                        repaired_count += 1

                            update_job(
                                job_id,
                                log=f"Scene {scene['scene_id']}: Localized sub-chunk repair recovered {repaired_count}/{missing_in_scene} cues."
                            )
                        except DailyLimitExhausted:
                            raise
                        except Exception as sub_err:
                            logger.warning(f"Scene {scene['scene_id']} localized sub-chunk repair error: {sub_err}")

                    # If after localized repair some residual cues remain missing, flag only those residual lines for review
                    # CRITICAL: Do NOT set translated_map[g_idx] = "" here! Falsely populating translated_map
                    # neutralizes the >20% atomic failure guard and bypasses the downstream batched repair pass.
                    for orig_idx in original_scene_indices:
                        g_idx = scene["start_index"] + orig_idx
                        if g_idx not in set(scene_mapped):
                            parsed_srt[g_idx]["needs_review"] = True
                            parsed_srt[g_idx]["review_issues"] = "Translation missing — dropped line unrepaired"

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
                update_job(
                    job_id,
                    progress=progress,
                    scenes_done=completed_count,
                    scenes_total=total_scenes,
                    cues_translated=len([k for k, v in translated_map.items() if str(v).strip()]),
                    cues_total=len(parsed_srt),
                    ai_response=response_text_clean[:10000] if response_text_clean else None,
                    prompt=sent_prompt["text"][:5000] or None,
                )

            except DailyLimitExhausted:
                update_job(job_id, scene_status={scene_label: "failed"})
                raise
            except Exception as e:
                log_msg = f"Scene {scene['scene_id']} post-processing failed: {str(e)}"
                update_job(job_id, scene_status={scene_label: "failed"}, log=log_msg)
                raise

    update_job(job_id, scene_status={f"Scene {s['scene_id']}": "pending" for s in scenes})

    sequential = global_config.get("sequential_scenes", False)

    for outer_attempt in range(2):
        results = []
        if sequential:
            hit_daily = None
            for i, scene in enumerate(scenes):
                if _is_cancelled():
                    break
                prev_scene_lines = scenes[i - 1]["lines"] if i > 0 else None
                next_scene_lines = scenes[i + 1]["lines"] if i < len(scenes) - 1 else None
                try:
                    await _translate_scene_task(scene, prev_scene_lines, next_scene_lines)
                    results.append(None)
                except DailyLimitExhausted as dle:
                    hit_daily = dle
                    break
                except Exception as e:
                    results.append(e)

            if hit_daily is not None:
                if outer_attempt == 0 and daily_limit_policy == "auto_fallback":
                    if await _handle_daily_limit_failover(hit_daily):
                        continue
                raise hit_daily
            break
        else:
            tasks = []
            for i, scene in enumerate(scenes):
                prev_scene_lines = scenes[i - 1]["lines"] if i > 0 else None
                next_scene_lines = scenes[i + 1]["lines"] if i < len(scenes) - 1 else None
                tasks.append(_translate_scene_task(scene, prev_scene_lines, next_scene_lines))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            if _is_cancelled():
                return False

            daily_err = next((r for r in results if isinstance(r, DailyLimitExhausted)), None)
            if daily_err is not None:
                if outer_attempt == 0 and daily_limit_policy == "auto_fallback":
                    if await _handle_daily_limit_failover(daily_err):
                        continue
                raise daily_err
            break

    for r in results:
        if isinstance(r, DailyLimitExhausted):
            raise r

    for e in [r for r in results
              if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError)]:
        update_job(job_id, log=f"{episode_name}: scene task error (tolerated — affected lines flagged for review): {e}")

    if _is_cancelled():
        return False

    # Identify all translatable cues that lack a valid translation
    missing_indices = [
        i for i in range(len(parsed_srt))
        if i not in translated_map or (parsed_srt[i].get("original", "").strip() and not str(translated_map.get(i, "")).strip())
    ]
    if missing_indices:
        missing = len(missing_indices)
        missing_indices_1based = [i + 1 for i in missing_indices]
        missing_pct = missing / len(parsed_srt) if parsed_srt else 0.0
        update_job(job_id, log=f"Episode {episode_name} has {missing} untranslated lines ({missing_pct:.1%}) at line numbers: {missing_indices_1based[:50]}.... Attempting batched repair and context fallback.")

        missing_tickets = [
            {"index": i, "issues": ["translation missing — line was dropped or untranslated"]}
            for i in missing_indices
            if parsed_srt[i].get("original", "").strip()
        ]

        repair_pass_enabled = global_config.get("repair_pass_enabled", True)
        if missing_tickets and repair_pass_enabled:
            try:
                from utils.qc_funnel import run_repair_pass
                from utils.model_resolver import resolve_model as _rm
                _repair_model = _rm("reconciliation", project_meta) or failover_state["cache_model"]
                await run_repair_pass(
                    parsed_srt=parsed_srt,
                    tickets=missing_tickets,
                    target_lang_code=target_lang_code,
                    target_lang_name=target_lang,
                    primary_code=primary_code,
                    model_name=_repair_model,
                    rate_limiter=failover_state["rate_limiter"],
                )
            except DailyLimitExhausted as dle:
                if daily_limit_policy == "auto_fallback" and await _handle_daily_limit_failover(dle):
                    try:
                        await run_repair_pass(
                            parsed_srt=parsed_srt,
                            tickets=missing_tickets,
                            target_lang_code=target_lang_code,
                            target_lang_name=target_lang,
                            primary_code=primary_code,
                            model_name=failover_state["model_name"],
                            rate_limiter=failover_state["rate_limiter"],
                        )
                    except Exception as re_err:
                        logger.warning(f"Batched missing line repair retry failed: {re_err}")
                else:
                    raise
            except Exception as e:
                logger.warning(f"Batched missing line repair pass failed: {e}")

        # Update translated_map from repaired entries
        for i in missing_indices:
            repaired_text = parsed_srt[i].get("translations", {}).get(target_lang_code) or parsed_srt[i].get("translated") or ""
            if repaired_text:
                translated_map[i] = repaired_text
                parsed_srt[i]["needs_review"] = True
                parsed_srt[i]["review_issues"] = "Translation missing — API failure (Fixed via batched repair)"
                update_job(job_id, log=f"Fixed missing translation at line {i + 1}: '{repaired_text}'")

        # Second pass: for lines still missing that have original text, run context-aware single line fallback
        still_missing = [
            i for i in missing_indices
            if (i not in translated_map or not str(translated_map.get(i, "")).strip())
            and parsed_srt[i].get("original", "").strip()
        ]
        if still_missing and repair_pass_enabled:
            update_job(job_id, log=f"Episode {episode_name}: {len(still_missing)} lines still missing after batched repair; attempting context fallback (up to 25 lines)...")
            from utils.model_resolver import resolve_model as _rm
            _ctx_model = _rm("reconciliation", project_meta) or failover_state["cache_model"]
            for i in still_missing[:25]:
                try:
                    fallback_trans = await _translate_missing_line_with_context(
                        project_name=project_name,
                        episode_name=episode_name,
                        line_idx=i,
                        parsed_srt=parsed_srt,
                        translated_map=translated_map,
                        target_lang_name=target_lang,
                        target_lang_code=target_lang_code,
                        model_name=_ctx_model,
                        rate_limiter=failover_state["rate_limiter"],
                    )
                    if fallback_trans:
                        translated_map[i] = fallback_trans
                        parsed_srt[i].setdefault("translations", {})[target_lang_code] = fallback_trans
                        if target_lang_code == primary_code:
                            parsed_srt[i]["translated"] = fallback_trans
                        parsed_srt[i]["needs_review"] = True
                        parsed_srt[i]["review_issues"] = "Translation missing — API failure (Fixed via context fallback)"
                        update_job(job_id, log=f"Fixed missing translation at line {i + 1} via context fallback: '{fallback_trans}'")
                except Exception as fb_err:
                    logger.warning(f"Context fallback failed for line {i + 1}: {fb_err}")

        # Final check on missing lines
        final_missing = [
            i for i in range(len(parsed_srt))
            if parsed_srt[i].get("original", "").strip() and (i not in translated_map or not str(translated_map.get(i, "")).strip())
        ]
        final_pct = len(final_missing) / len(parsed_srt) if parsed_srt else 0.0
        atomic_thresh = float(global_config.get("atomic_failure_threshold", 0.20) or 0.20)
        if final_pct > atomic_thresh:
            update_job(job_id, log=f"Episode {episode_name} still has {len(final_missing)} untranslated lines ({final_pct:.1%}) after all recovery attempts; failing atomically.")
            reason = f"{len(final_missing)} of {len(parsed_srt)} lines could not be translated"
            if model_errors:
                reason += f"; the model API said: {model_errors[0][:300]}"
            update_job(job_id, result={"failure_reason": reason})
            return False

        for i in final_missing:
            translated_map[i] = ""
            parsed_srt[i]["needs_review"] = True
            parsed_srt[i]["review_issues"] = "Translation missing — API failure"

    for idx, text in translated_map.items():
        if isinstance(idx, int) and 0 <= idx < len(parsed_srt):
            if "translations" not in parsed_srt[idx]:
                parsed_srt[idx]["translations"] = {}
            parsed_srt[idx]["translations"][target_lang_code] = text
            if target_lang_code == primary_code:
                parsed_srt[idx]["translated"] = text

    qc_stages: Dict[str, str] = {"translate": "ok"}

    # Stage 1a — deterministic glossary enforcement
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

    # Stage 1a0 — Dual-text / arrow artifact sanitization guard
    try:
        _n_sanitized = 0
        for i in range(len(parsed_srt)):
            t_val = parsed_srt[i].get("translations", {}).get(target_lang_code) or (parsed_srt[i].get("translated") if target_lang_code == primary_code else "")
            if t_val:
                orig_txt = parsed_srt[i].get("original", "")
                clean_val = sanitize_translation_text(
                    t_val,
                    source_text=orig_txt,
                    target_lang=target_lang,
                    target_lang_code=target_lang_code
                )
                if clean_val != t_val:
                    parsed_srt[i].setdefault("translations", {})[target_lang_code] = clean_val
                    if target_lang_code == primary_code:
                        parsed_srt[i]["translated"] = clean_val
                    translated_map[i] = clean_val
                    _n_sanitized += 1
        if _n_sanitized > 0:
            update_job(job_id, log=f"{episode_name}: dual-text sanitization guard cleaned {_n_sanitized} cue(s) with arrow/paired artifacts.")
        qc_stages["sanitization_guard"] = f"ok ({_n_sanitized} cleaned)"
    except Exception as e:
        qc_stages["sanitization_guard"] = f"failed: {e}"
        update_job(job_id, log=f"{episode_name}: dual-text sanitization guard error (non-critical): {e}")

    # Stage 1a2 — script guard
    if global_config.get("script_guard_enabled", True):
        try:
            from utils.script_guard import scrub_rows
            _n_script = scrub_rows(parsed_srt, target_lang_code, primary_code)
            if _n_script:
                update_job(job_id, log=f"{episode_name}: script guard fixed {_n_script} wrong-alphabet character(s).")
            qc_stages["script_guard"] = f"ok ({_n_script} fixed)"
        except Exception as e:
            qc_stages["script_guard"] = f"failed: {e}"
            update_job(job_id, log=f"{episode_name}: script guard failed (non-critical): {e}")
    else:
        qc_stages["script_guard"] = "skipped"

    # Stage 1a3 — Japanese names Greek tonos enforcement
    is_greek = (target_lang or "").strip().lower() in ("greek", "el", "ελληνικά") or target_lang_code in ("el", "ell", "gre", "grk")
    if is_greek:
        try:
            from utils.text_normalize import fix_japanese_name_greek_tonos
            _n_tonos = 0
            for i in range(len(parsed_srt)):
                t_val = parsed_srt[i].get("translations", {}).get(target_lang_code) or (parsed_srt[i].get("translated") if target_lang_code == primary_code else "")
                if t_val:
                    fixed_val = fix_japanese_name_greek_tonos(t_val)
                    if fixed_val != t_val:
                        parsed_srt[i].setdefault("translations", {})[target_lang_code] = fixed_val
                        if target_lang_code == primary_code:
                            parsed_srt[i]["translated"] = fixed_val
                        translated_map[i] = fixed_val
                        _n_tonos += 1
            if _n_tonos > 0:
                update_job(job_id, log=f"{episode_name}: Japanese name tonos normalizer adjusted {_n_tonos} cue(s).")
            qc_stages["tonos_guard"] = f"ok ({_n_tonos} adjusted)"
        except Exception as e:
            qc_stages["tonos_guard"] = f"failed: {e}"
            update_job(job_id, log=f"{episode_name}: Japanese name tonos normalizer failed (non-critical): {e}")
    else:
        qc_stages["tonos_guard"] = "skipped"

    # Stage 1b+2 — validators
    if global_config.get("repair_pass_enabled", True):
        try:
            from utils.qc_funnel import validate_lines, run_repair_pass
            _tickets = validate_lines(parsed_srt, target_lang_code, glossary=glossary)
            if _tickets:
                update_job(job_id, log=f"{episode_name}: validators ticketed {len(_tickets)} line(s); running one batched repair call...")
                from utils.model_resolver import resolve_model as _rm
                _repair_model = _rm("reconciliation", project_meta) or failover_state["cache_model"]
                _fixed = await run_repair_pass(
                    parsed_srt, _tickets, target_lang_code, target_lang,
                    primary_code, _repair_model, failover_state["rate_limiter"],
                )
                update_job(job_id, log=f"{episode_name}: repair pass fixed {_fixed}/{len(_tickets)} line(s).")
                qc_stages["repair"] = f"ok ({_fixed}/{len(_tickets)})"
            else:
                qc_stages["repair"] = "ok (0 tickets)"
        except DailyLimitExhausted as dle:
            if daily_limit_policy == "auto_fallback" and await _handle_daily_limit_failover(dle):
                try:
                    _fixed = await run_repair_pass(
                        parsed_srt, _tickets, target_lang_code, target_lang,
                        primary_code, failover_state["model_name"], failover_state["rate_limiter"],
                    )
                    update_job(job_id, log=f"{episode_name}: repair pass fixed {_fixed}/{len(_tickets)} line(s).")
                    qc_stages["repair"] = f"ok ({_fixed}/{len(_tickets)})"
                except Exception as e:
                    qc_stages["repair"] = f"failed: {e}"
                    update_job(job_id, log=f"{episode_name}: repair pass failed (non-critical): {e}")
            else:
                raise
        except Exception as e:
            qc_stages["repair"] = f"failed: {e}"
            update_job(job_id, log=f"{episode_name}: repair pass failed (non-critical): {e}")
    else:
        qc_stages["repair"] = "skipped"

    if _is_cancelled():
        return False

    # Intra-episode consistency reconciliation
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

    # Sampled judge reviewer
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
                    stats = await asyncio.to_thread(
                        apply_review_results,
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

    if _is_cancelled():
        return False

    metadata = storage.load_episode_metadata(project_name, episode_name) or {}
    metadata["translated"] = True
    metadata["translation_status"] = "completed"
    metadata["last_translation_attempt"] = datetime.now().isoformat()
    metadata.pop("translation_failed", None)
    metadata.pop("translation_error", None)
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
            
            frequencies = storage.get_project_line_frequencies(project_name)
            duplicate_lines = {line for line, count in frequencies.items() if count >= 2}
            
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
