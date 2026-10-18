"""
Batch operations for translation and comparison workflows.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from utils import storage
from utils.language_codes import to_code
from utils.srt_parser import build_scene_ast, get_scene_context_hint
from utils.llm_utils import strip_reasoning_blocks
from utils.structured_output import parse_structured
from utils.rate_limiter import translation_rate_limiter, active_model_var
from utils.api_call_wrapper import rate_limited_call
from utils.translation_memory import build_priority_glossary_block, filter_glossary_for_chunk
from utils.character_profiles import CharacterProfileManager
from utils.jobs_manager import update_job, jobs
from adk_agents import create_translation_pipeline
from adk_agents.translator_agent import build_translation_prompt
from adk_config import get_ephemeral_session_service
from google.adk.runners import types as adk_types, Runner as _Runner
from services.translation_service import auto_export_translated_subtitle

logger = logging.getLogger(__name__)


async def _process_retranslation_for_comparison(
    job_id: str,
    project_name: str,
    episode_name: str,
    target_lang: str,
    model_name: str,
    comparison_key: str
) -> None:
    """Retranslates an episode in-place with 1-click revert capability, and exports."""
    update_job(job_id, status="running", progress=0.0, message="Starting retranslation...", log="Retranslation started")
    active_model_var.set(model_name)
    try:
        project_meta = storage.load_project_metadata(project_name)
        if not project_meta:
            update_job(job_id, status="failed", message="Project not found")
            return

        episode_data = storage.load_episode(project_name, episode_name)
        if not episode_data:
            update_job(job_id, status="failed", message=f"Episode {episode_name} not found")
            return

        parsed_srt = episode_data["data"]
        glossary = project_meta.get("glossary", {"terms": []})
        context_guide = project_meta.get("context_guide", "")

        global_config = storage.load_global_config()
        concurrent_limit = global_config.get("concurrent_scenes", 3)
        semaphore = asyncio.Semaphore(concurrent_limit)

        shared_agent = create_translation_pipeline(
            project_name=project_name,
            target_language=target_lang,
            glossary=glossary,
            context_guide=context_guide,
            cartographer_model=model_name,
            translator_model=model_name,
            skip_glossary_step=True,
            temperature=storage.get_project_setting(project_meta, "temperature"),
            top_k=storage.get_project_setting(project_meta, "top_k"),
            top_p=storage.get_project_setting(project_meta, "top_p"),
        )

        from adk_agents.llm_factory import get_recommended_chunk_size
        configured_max = global_config.get("max_lines_per_scene", 200)
        max_lines = max(10, min(configured_max, get_recommended_chunk_size(model_name)))
        scenes = build_scene_ast(parsed_srt, gap_threshold_ms=3000, max_lines_per_scene=max_lines)

        _eph_svc = get_ephemeral_session_service()
        profile_mgr = CharacterProfileManager(project_name)
        profiles_enabled = global_config.get("character_profiles_enabled", True)

        translated_map = {}

        async def _translate_scene_task(scene: Dict, prev_lines: List[Dict] = None):
            async with semaphore:
                if job_id in jobs and jobs[job_id].cancelled:
                    return

                lines = scene.get("lines", [])
                source_texts = [line.get("original", "") for line in lines]
                if not source_texts:
                    return

                context_hint = get_scene_context_hint(prev_lines) if prev_lines else ""
                priority_block = build_priority_glossary_block(filter_glossary_for_chunk(glossary, source_texts))

                character_block = ""
                if profiles_enabled:
                    try:
                        scene_profiles = profile_mgr.get_profiles_for_chunk(source_texts, glossary)
                        if scene_profiles:
                            character_block = profile_mgr.build_profile_context(scene_profiles)
                    except Exception:
                        pass

                prompt = build_translation_prompt(
                    source_texts,
                    target_lang,
                    context_hint,
                    few_shot_context="",
                    priority_glossary=priority_block,
                    character_context=character_block,
                    episode_context="",
                    conformance_hint="",
                    negative_examples="",
                    lookahead_line="",
                )

                session_id = f"comp_{uuid4().hex[:8]}"
                runner = _Runner(agent=shared_agent, app_name=f"Omnisub_{session_id}", session_service=_eph_svc)
                await _eph_svc.create_session(
                    session_id=session_id, user_id="default_user", app_name=f"Omnisub_{session_id}"
                )

                async def _perform_translation():
                    response_text = ""
                    async for event in runner.run_async(
                        user_id="default_user",
                        session_id=session_id,
                        new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=prompt)])
                    ):
                        if job_id in jobs and jobs[job_id].cancelled:
                            raise asyncio.CancelledError()
                        if event.content and event.content.parts:
                            for part in event.content.parts:
                                if part.text:
                                    response_text += part.text
                    return response_text

                resp_text = await rate_limited_call(_perform_translation, rate_limiter=translation_rate_limiter)
                clean_resp = strip_reasoning_blocks(resp_text)
                parsed_lines = parse_structured(clean_resp, expected_count=len(source_texts))

                for item in parsed_lines:
                    llm_idx = item["index"] - 1
                    if 0 <= llm_idx < len(lines):
                        global_idx = scene["start_index"] + llm_idx
                        translated_map[global_idx] = item["text"]

        tasks = []
        for i, sc in enumerate(scenes):
            prev = scenes[i - 1]["lines"] if i > 0 else None
            tasks.append(_translate_scene_task(sc, prev))

        if tasks:
            await asyncio.gather(*tasks)

        target_lang_code = to_code(target_lang)
        for idx, new_text in translated_map.items():
            if 0 <= idx < len(parsed_srt):
                entry = parsed_srt[idx]
                if "comparisons" not in entry:
                    entry["comparisons"] = {}
                entry["comparisons"][comparison_key] = new_text

                old_translated = entry.get("translated", "")
                if old_translated and old_translated != new_text:
                    entry["previous_translated"] = old_translated

                entry["translated"] = new_text
                entry["retranslated_text"] = new_text

                if "translations" not in entry:
                    entry["translations"] = {}
                entry["translations"][target_lang_code] = new_text

        ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}
        ep_meta["translated"] = True
        ep_meta["translation_status"] = "completed"
        ep_meta["last_translated_at"] = datetime.now(timezone.utc).isoformat()
        storage.save_episode(project_name, episode_name, parsed_srt, ep_meta)

        try:
            auto_export_translated_subtitle(project_name, episode_name, parsed_srt, ep_meta, force=True)
        except Exception as export_err:
            logger.warning(f"Failed to auto-export after retranslation: {export_err}")

        update_job(
            job_id,
            status="completed",
            progress=100.0,
            message=f"Retranslation complete for {episode_name}",
            result_data={
                "project_name": project_name,
                "episode_name": episode_name,
                "comparison_key": comparison_key,
                "model_name": model_name
            }
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error in retranslation: {str(e)}", log=str(e))
