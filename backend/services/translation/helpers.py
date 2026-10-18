"""
Translation Helper Utilities, Response Parsers, and Pipeline Synchronization.
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import List, Dict, Optional

from utils import storage
from utils.srt_parser import extract_text_only
from utils.jobs_manager import jobs

logger = logging.getLogger(__name__)

# Events dictionary for pausing/resuming simple pipeline and standard pipeline
_pipeline_resume_events: Dict[str, asyncio.Event] = {}


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
) -> None:
    """Detect user edits and store them in the Translation Memory as gold examples."""
    proj_meta = storage.load_project_metadata(project_name)
    primary_lang = proj_meta.get("target_language", "Greek") if proj_meta else "Greek"
    
    from utils.language_codes import to_code
    if not lang:
        lang = primary_lang
    lang_code = to_code(lang)

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


def _select_golden_ratio_files(episodes: List[str]) -> List[str]:
    """Select a few representative episodes from a list using golden ratio offsets."""
    if not episodes:
        return []
    valid_episodes = [str(e).strip() for e in episodes if e and str(e).strip()]
    if not valid_episodes:
        return []
    if len(valid_episodes) <= 5:
        return valid_episodes
        
    n = len(valid_episodes)
    phi = 0.61803398875
    indices = {0, n - 1}
    
    indices.add(round((n - 1) * (1 - phi)))
    indices.add(round((n - 1) * phi))
    
    if n > 12:
        indices.add(round((n - 1) * (1 - phi**2)))
        indices.add(round((n - 1) * (1 - (1-phi)**2)))

    sorted_indices = sorted(list(indices))
    return [valid_episodes[i] for i in sorted_indices if 0 <= i < n]


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


async def _pipeline_pause(job_id: str, stage: str, message: str) -> None:
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


async def _resume_pipeline(job_id: str, project_name: str) -> None:
    """Signal a paused pipeline to continue."""
    event = _pipeline_resume_events.get(job_id)
    if event:
        event.set()


async def _gather_project_text(project_name: str, episode_names: List[str] = None) -> List[str]:
    """Gather all subtitle text from specified or all episodes."""
    if not project_name or not str(project_name).strip():
        return []
    if episode_names:
        episodes = [str(ep).strip() for ep in episode_names if ep and str(ep).strip()]
    else:
        episodes = [str(ep).strip() for ep in storage.list_episodes(project_name) if ep and str(ep).strip()]
    all_text = []

    for ep_name in episodes:
        if not ep_name:
            continue
        try:
            ep_data = storage.load_episode(project_name, ep_name)
            if ep_data and isinstance(ep_data, dict) and "data" in ep_data:
                data = ep_data["data"]
                if isinstance(data, list):
                    all_text.extend(extract_text_only(data))
            elif storage.original_subtitle_exists(project_name, ep_name):
                content, ext = storage.load_original_subtitle(project_name, ep_name)
                if content:
                    from utils.source_clean import import_and_clean_srt
                    clean_res = import_and_clean_srt(project_name, ep_name, content, filename=f"{ep_name}.{ext}")
                    if clean_res and "data" in clean_res and isinstance(clean_res["data"], list):
                        all_text.extend(extract_text_only(clean_res["data"]))
        except Exception as e:
            logger.warning(f"Failed to load episode '{ep_name}' in _gather_project_text: {e}")

    return all_text


