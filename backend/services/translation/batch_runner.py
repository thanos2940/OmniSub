"""
Batch Translation, Targeted Retranslation, and Comparison Workflows.
"""

import asyncio
import json
import logging
import re
from typing import List, Dict, Optional
from uuid import uuid4

from utils import storage
from utils.language_codes import to_code
from utils.srt_parser import build_scene_ast, get_scene_context_hint
from utils.llm_utils import strip_reasoning_blocks
from utils.structured_output import parse_structured
from utils.rate_limiter import translation_rate_limiter, active_model_var, DailyLimitExhausted
from utils.api_call_wrapper import rate_limited_call
from utils.translation_memory import TranslationMemory, build_priority_glossary_block, filter_glossary_for_chunk
from utils.character_profiles import CharacterProfileManager
from utils.jobs_manager import update_job, jobs

from adk_agents import (
    create_translation_pipeline,
    generate_glossary_adk,
    research_project_adk,
    enhance_context_guide_adk,
)
from adk_agents.translator_agent import build_translation_prompt
from adk_config import get_ephemeral_session_service
from google.adk.runners import types as adk_types, Runner as _Runner

from services.translation.atomic_runner import _translate_episode_atomic
from services.translation.helpers import _select_golden_ratio_files, _merge_glossaries, _pipeline_pause

logger = logging.getLogger(__name__)


async def _process_batch_translation(
    job_id: str, project_name: str, episode_names: List[str], 
    model: str, enhance_glossary_flag: bool, is_simple_pipeline: bool = False,
    context_model: Optional[str] = None,
    glossary_model: Optional[str] = None,
    force: bool = False
) -> None:
    """Process batch translation across multiple episodes with optional auto-pipeline steps."""
    update_job(job_id, status="running", progress=0.0, message="Starting translation...", log="Job started")
    try:
        metadata = storage.load_project_metadata(project_name)
        if not metadata:
            update_job(job_id, status="failed", message="Project not found")
            return

        from utils.model_resolver import resolve_model
        model = resolve_model("translation", metadata, model_override=model)
        active_model_var.set(model)

        glossary = metadata.get("glossary", {"terms": []})
        target_lang = metadata.get("target_language", "English")
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

        if force:
            force_code = to_code(target_lang)
            for ep_name in episode_names:
                try:
                    ep_data = storage.load_episode(project_name, ep_name)
                    if ep_data and ep_data.get("data"):
                        for line in ep_data["data"]:
                            line.pop("translated", None)
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
                    checkpoint_path = storage.episode_dir(project_name, ep_name) / "checkpoint.json"
                    if checkpoint_path.exists():
                        try:
                            checkpoint_path.unlink()
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning(f"Failed to reset episode {ep_name} for force-translation: {e}")

        # Auto-seed glossary if empty
        if not glossary.get("terms") or len(glossary.get("terms", [])) == 0:
            update_job(job_id, log="Glossary is empty. Selecting representative episodes to seed initial glossary...")
            golden_episodes = _select_golden_ratio_files(episode_names)
            seed_text_chunks = []
            for gep in golden_episodes:
                ep_data = storage.load_episode(project_name, gep)
                if ep_data and ep_data.get("data"):
                    lines = [line.get("original", "") for line in ep_data["data"][:150]]
                    seed_text_chunks.append("\n".join(lines))
            
            combined_seed_text = "\n\n--- Next Episode Sample ---\n\n".join(seed_text_chunks)
            if combined_seed_text.strip():
                try:
                    effective_glossary_model = resolve_model("glossary", metadata, model_override=glossary_model)
                    update_job(job_id, log=f"Auto-generating initial glossary using {len(golden_episodes)} sample episode(s) and model {effective_glossary_model}...")
                    new_glossary, _ = await generate_glossary_adk(
                        combined_seed_text, project_name, target_lang,
                        model_name=effective_glossary_model
                    )
                    glossary = new_glossary
                    metadata["glossary"] = glossary
                    storage.save_project_metadata(project_name, metadata)
                    update_job(job_id, log=f"Auto-seeded glossary with {len(glossary.get('terms', []))} terms.")
                    
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
                except Exception as e:
                    update_job(job_id, log=f"Failed to auto-seed glossary (proceeding without it): {e}")

        # Auto-context pipeline step
        if is_simple_pipeline:
            effective_context_model = resolve_model("context", metadata, model_override=context_model)
            update_job(job_id, message="Step 1/2: Researching and creating context guide...", progress=5.0)
            try:
                update_job(job_id, log=f"Researching web for '{project_name}'...")
                research_notes = await research_project_adk(project_name)
                update_job(job_id, log=f"Generating context guide using {effective_context_model}...")
                context_guide = await enhance_context_guide_adk(
                    project_name, research_notes, glossary, 
                    current_guide=context_guide, target_language=target_lang,
                    model_name=effective_context_model
                )
                metadata["context_guide"] = context_guide
                storage.save_project_metadata(project_name, metadata)
                update_job(job_id, log="Context guide generated and saved successfully.")
            except Exception as e:
                update_job(job_id, log=f"Context generation warning: {e}. Proceeding with existing context.")

            await _pipeline_pause(job_id, "context", "Context guide generated. Review before translation.")
            if jobs[job_id].cancelled:
                update_job(job_id, status="cancelled", message="Cancelled by user")
                return

            metadata = storage.load_project_metadata(project_name)
            context_guide = metadata.get("context_guide", "")
            glossary = metadata.get("glossary", {"terms": []})
            
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
            update_job(job_id, message="Step 2/2: Translating episodes...", progress=10.0)

        for i, ep_name in enumerate(episode_names):
            if jobs[job_id].cancelled:
                update_job(job_id, status="cancelled", message="Cancelled by user")
                return

            progress_start = (i / total) * 100.0
            update_job(job_id, progress=progress_start, message=f"Translating episode {i+1}/{total}: {ep_name}")

            success = await _translate_episode_atomic(
                job_id=job_id,
                project_name=project_name,
                episode_name=ep_name,
                target_lang=target_lang,
                shared_agent=shared_agent,
                rate_limiter=translation_rate_limiter,
                semaphore=semaphore,
                global_config=global_config,
                total_episodes=total,
                episode_idx=i,
                start_progress=progress_start,
                model_name=model,
            )

            if not success:
                update_job(job_id, status="failed", message=f"Failed translating {ep_name}")
                return

        update_job(
            job_id,
            status="completed",
            progress=100.0,
            message=f"Batch translation complete! Translated {total} episode(s).",
            result_data={
                "project_name": project_name,
                "translated_episodes": total,
                "episodes": episode_names
            }
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error: {str(e)}", log=str(e))


async def _process_targeted_retranslation(job_id: str, project_name: str, affected_terms: List[str], model: str) -> None:
    """Targeted retranslation of scenes containing edited glossary terms."""
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
            if not any(line.get("translated") for line in parsed_srt):
                continue
            
            has_affected_scene = False
            max_lines = global_config.get("max_lines_per_scene", 200)
            scenes = build_scene_ast(parsed_srt, gap_threshold_ms=3000, max_lines_per_scene=max_lines)
            
            for scene in scenes:
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
            
        update_job(job_id, log=f"Found {total} episode(s) with scenes affected by term changes: {', '.join(episodes_to_retranslate)}")

        _eph_svc = get_ephemeral_session_service()
        profile_mgr = CharacterProfileManager(project_name)
        profiles_enabled = global_config.get("character_profiles_enabled", True)

        total_scenes_retranslated = 0
        total_lines_updated = 0

        for ep_idx, ep_name in enumerate(episodes_to_retranslate):
            if jobs[job_id].cancelled:
                update_job(job_id, status="cancelled", message="Cancelled by user")
                return

            ep_data = storage.load_episode(project_name, ep_name)
            parsed_srt = ep_data.get("data", [])
            max_lines = global_config.get("max_lines_per_scene", 200)
            scenes = build_scene_ast(parsed_srt, gap_threshold_ms=3000, max_lines_per_scene=max_lines)

            affected_scenes = []
            for scene in scenes:
                if not any(line.get("translated") for line in scene.get("lines", [])):
                    continue
                scene_text = " ".join(line.get("original", "") for line in scene.get("lines", []))
                if pattern.search(scene_text):
                    affected_scenes.append(scene)

            update_job(job_id, log=f"Episode {ep_name}: Retranslating {len(affected_scenes)} affected scene(s)...")

            async def _retranslate_scene_task(scene: Dict):
                nonlocal total_lines_updated
                async with semaphore:
                    if jobs[job_id].cancelled:
                        return

                    lines = scene.get("lines", [])
                    source_texts = [line.get("original", "") for line in lines]
                    if not source_texts:
                        return

                    filtered_glossary = filter_glossary_for_chunk(glossary, source_texts)
                    priority_block = build_priority_glossary_block(filtered_glossary)

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
                        context_hint="",
                        few_shot_context="",
                        priority_glossary=priority_block,
                        character_context=character_block,
                        episode_context="",
                        conformance_hint="",
                        negative_examples="",
                        lookahead_line="",
                    )

                    session_id = f"targeted_{uuid4().hex[:8]}"
                    runner = _Runner(agent=pipeline, app_name=f"Omnisub_{session_id}", session_service=_eph_svc)
                    await _eph_svc.create_session(
                        session_id=session_id, user_id="default_user", app_name=f"Omnisub_{session_id}"
                    )

                    async def _perform_scene_translation():
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

                    resp_text = await rate_limited_call(_perform_scene_translation, rate_limiter=translation_rate_limiter)
                    clean_resp = strip_reasoning_blocks(resp_text)
                    parsed_lines = parse_structured(clean_resp, expected_count=len(source_texts))

                    for item in parsed_lines:
                        llm_idx = item["index"] - 1
                        if 0 <= llm_idx < len(lines):
                            global_idx = scene["start_index"] + llm_idx
                            new_text = item["text"]
                            if parsed_srt[global_idx].get("translated") != new_text:
                                parsed_srt[global_idx]["translated"] = new_text
                                parsed_srt[global_idx].setdefault("translations", {})[target_lang_code] = new_text
                                total_lines_updated += 1

            tasks = [_retranslate_scene_task(sc) for sc in affected_scenes]
            await asyncio.gather(*tasks)
            total_scenes_retranslated += len(affected_scenes)

            metadata_ep = storage.load_episode_metadata(project_name, ep_name) or {}
            storage.save_episode(project_name, ep_name, parsed_srt, metadata_ep)
            from services.translation.export_formatter import auto_export_translated_subtitle
            auto_export_translated_subtitle(project_name, ep_name, parsed_srt, metadata_ep)

            progress = ((ep_idx + 1) / total) * 100.0
            update_job(job_id, progress=progress, message=f"Processed {ep_idx+1}/{total} episodes")

        update_job(
            job_id,
            status="completed",
            progress=100.0,
            message=f"Targeted retranslation complete! Updated {total_lines_updated} line(s) across {total_scenes_retranslated} scene(s).",
            result_data={
                "project_name": project_name,
                "episodes_affected": episodes_to_retranslate,
                "scenes_updated": total_scenes_retranslated,
                "lines_updated": total_lines_updated
            }
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error in targeted retranslation: {str(e)}", log=str(e))


async def _process_retranslation_for_comparison(
    job_id: str,
    project_name: str,
    episode_name: str,
    target_lang: str,
    model_name: str,
    comparison_key: str
) -> None:
    """Retranslates an episode to a comparison key for side-by-side evaluation."""
    update_job(job_id, status="running", progress=0.0, message="Starting comparison translation...", log="Comparison translation started")
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
        total_scenes = len(scenes)

        _eph_svc = get_ephemeral_session_service()
        profile_mgr = CharacterProfileManager(project_name)
        profiles_enabled = global_config.get("character_profiles_enabled", True)

        translated_map = {}

        async def _translate_scene_task(scene: Dict, prev_lines: List[Dict] = None):
            async with semaphore:
                if jobs[job_id].cancelled:
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
                        if jobs[job_id].cancelled:
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

        await asyncio.gather(*tasks)

        for idx, text in translated_map.items():
            if "comparisons" not in parsed_srt[idx]:
                parsed_srt[idx]["comparisons"] = {}
            parsed_srt[idx]["comparisons"][comparison_key] = text

        ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}
        storage.save_episode(project_name, episode_name, parsed_srt, ep_meta)

        update_job(
            job_id,
            status="completed",
            progress=100.0,
            message=f"Comparison translation complete for {episode_name} ({comparison_key})",
            result_data={
                "project_name": project_name,
                "episode_name": episode_name,
                "comparison_key": comparison_key,
                "model_name": model_name
            }
        )
    except Exception as e:
        update_job(job_id, status="failed", message=f"Error in comparison translation: {str(e)}", log=str(e))
