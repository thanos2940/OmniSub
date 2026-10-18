import asyncio
from typing import List, Dict, Optional
from uuid import uuid4
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from utils import storage
from utils.token_helper import estimate_tokens, estimate_glossary_tokens, get_base_instruction_tokens
from utils.cache_manager import invalidate_cache as _invalidate_translation_cache
from utils.jobs_manager import create_job
from services.translation_service import _process_targeted_retranslation
from adk_config import adk_runner_factory, adk_session_manager, get_ephemeral_session_service
from routers.schemas import (
    CreateProjectRequest, ImportRequest, TestTranslationRequest, PromoteTermRequest,
    ArrSyncRequest, BatchPromoteTermsRequest, BatchSuppressTermsRequest,
    BatchRevertTermsRequest, HarvestTermsRequest
)
from routers.settings import validate_api_key

router = APIRouter()

class ProjectPriorityRequest(BaseModel):
    priority: str


class BulkProjectPriorityRequest(BaseModel):
    projects: List[str]
    priority: str


class ProjectArrDisabledRequest(BaseModel):
    arr_disabled: bool


def _get_projects_list():
    names = storage.list_projects()
    result = []
    for name in names:
        meta = storage.load_project_metadata(name)
        if meta:
            meta["name"] = name
            prio = getattr(storage, "resolve_project_priority", lambda m: m.get("translation_priority", "normal"))(meta)
            meta["translation_priority"] = prio
            meta["arr_disabled"] = (prio == "ignored")
            result.append(meta)
    return result


@router.get("/projects")
@router.get("/api/projects")
async def list_projects():
    import asyncio
    return await asyncio.to_thread(_get_projects_list)


@router.post("/projects")
@router.post("/api/projects")
async def create_project(request: CreateProjectRequest):
    is_anime = (request.type or "").lower() == "anime" or "anime" in request.name.lower()
    metadata = {
        "show_name": request.name,
        "target_language": request.target_language,
        "parent_project": request.parent_project,
        "type": request.type,
        "series_type": "anime" if is_anime else "standard",
        "is_anime": is_anime,
        "translation_priority": "normal",
        "arr_disabled": False,
        "glossary": {"terms": []},
        "context_guide": ""
    }
    return storage.create_project(request.name, metadata)


@router.put("/projects/{project_name}/priority")
@router.put("/api/projects/{project_name}/priority")
async def update_project_priority_endpoint(project_name: str, payload: ProjectPriorityRequest):
    priority = (payload.priority or "").lower().strip()
    if priority not in {"high", "normal", "low", "ignored"}:
        raise HTTPException(status_code=400, detail=f"Invalid priority tier: {payload.priority}. Must be one of: high, normal, low, ignored")
    
    proj_meta = storage.load_project_metadata(project_name)
    if proj_meta is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")
    
    proj_meta["translation_priority"] = priority
    proj_meta["arr_disabled"] = (priority == "ignored")
    storage.save_project_metadata(project_name, proj_meta)
    
    from utils.translation_queue import get_translation_queue
    queue_changes = get_translation_queue().update_project_priority(project_name, priority)
    
    return {
        "status": "success",
        "project": project_name,
        "translation_priority": priority,
        "arr_disabled": (priority == "ignored"),
        "queue_changes": queue_changes,
    }


@router.post("/projects/bulk-priority")
@router.post("/api/projects/bulk-priority")
async def bulk_update_project_priority(payload: BulkProjectPriorityRequest):
    priority = (payload.priority or "").lower().strip()
    if priority not in {"high", "normal", "low", "ignored"}:
        raise HTTPException(status_code=400, detail=f"Invalid priority tier: {payload.priority}. Must be one of: high, normal, low, ignored")
    
    from utils.translation_queue import get_translation_queue
    queue = get_translation_queue()
    total_changes = {"updated": 0, "cancelled": 0}
    updated_count = 0
    
    for project_name in payload.projects:
        proj_meta = storage.load_project_metadata(project_name)
        if proj_meta is None:
            continue
        proj_meta["translation_priority"] = priority
        proj_meta["arr_disabled"] = (priority == "ignored")
        storage.save_project_metadata(project_name, proj_meta)
        changes = queue.update_project_priority(project_name, priority)
        total_changes["updated"] += changes.get("updated", 0)
        total_changes["cancelled"] += changes.get("cancelled", 0)
        updated_count += 1
        
    return {
        "status": "success",
        "updated_projects": updated_count,
        "translation_priority": priority,
        "queue_changes": total_changes,
    }


@router.put("/projects/{project_name}/arr-disabled")
@router.put("/api/projects/{project_name}/arr-disabled")
async def set_project_arr_disabled_endpoint(project_name: str, payload: ProjectArrDisabledRequest):
    proj_meta = storage.load_project_metadata(project_name)
    if proj_meta is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")
        
    is_disabled = bool(payload.arr_disabled)
    proj_meta["arr_disabled"] = is_disabled
    if is_disabled:
        proj_meta["ignored_by_user"] = True
    storage.save_project_metadata(project_name, proj_meta)  # re-enabling restores the earlier tier
    priority = proj_meta["translation_priority"]
    
    from utils.translation_queue import get_translation_queue
    queue_changes = get_translation_queue().update_project_priority(project_name, priority)
    
    return {
        "status": "success",
        "project": project_name,
        "arr_disabled": is_disabled,
        "translation_priority": priority,
        "queue_changes": queue_changes,
    }


@router.get("/projects/{project_name}/token-summary")
async def get_project_token_summary(project_name: str):
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    
    glossary = metadata.get("glossary", {"terms": []})
    context = metadata.get("context_guide", "")
    
    glossary_tokens = estimate_glossary_tokens(glossary)
    context_tokens = estimate_tokens(context)
    base_instructions = get_base_instruction_tokens()
    
    return {
        "glossary_tokens": glossary_tokens,
        "context_tokens": context_tokens,
        "base_instruction_tokens": base_instructions,
        "total_static_tokens": glossary_tokens + context_tokens + base_instructions
    }


@router.get("/projects/{project_name}")
@router.get("/api/projects/{project_name}")
async def get_project(project_name: str):
    metadata = storage.load_resolved_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    prio = getattr(storage, "resolve_project_priority", lambda m: m.get("translation_priority", "normal"))(metadata)
    metadata["translation_priority"] = prio
    metadata["arr_disabled"] = (prio == "ignored")
    return metadata


from utils.model_resolver import resolve_model

@router.post("/projects/{project_name}/test-translation")
async def test_project_translation(project_name: str, request: TestTranslationRequest):
    validate_api_key()
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
    
    glossary = metadata.get("glossary", {"terms": []})
    target_lang = metadata.get("target_language", "English")
    context_guide = metadata.get("context_guide", "")
    
    model_name = request.model_name or resolve_model("translation", metadata)
    temperature = request.temperature if request.temperature is not None else storage.get_project_setting(metadata, "temperature", 0.3)
    top_k = request.top_k if request.top_k is not None else storage.get_project_setting(metadata, "top_k", 40)
    top_p = request.top_p if request.top_p is not None else storage.get_project_setting(metadata, "top_p", 1.0)
    
    from adk_agents.translator_agent import create_translator_agent
    from google.adk.runners import Runner, types as adk_types
    import re
    
    agent = create_translator_agent(
        model_name=model_name,
        glossary=glossary,
        target_language=target_lang,
        context_guide=context_guide,
        temperature=temperature,
        top_k=top_k,
        top_p=top_p
    )
    
    session_id = f"test_{uuid4().hex[:8]}"
    eph_svc = get_ephemeral_session_service()
    runner = Runner(
        agent=agent,
        app_name=f"Omnisub_{session_id}",
        session_service=eph_svc,
    )
    
    app_name = f"Omnisub_{session_id}"
    await eph_svc.create_session(
        session_id=session_id, user_id="test_user", app_name=app_name
    )
    
    chunk_input = "\n".join([f"{j+1}: {line}" for j, line in enumerate(request.lines)])
    prompt = f"Translate these subtitles to {target_lang}:\n\n{chunk_input}"
    
    response_text = ""
    async for event in runner.run_async(
        user_id="test_user",
        session_id=session_id,
        new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=prompt)])
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    response_text += part.text

    reasoning = ""
    reasoning_match = re.search(
        r'(?:<internal_reasoning>|<\|channel>thought\s*)(.*?)(?:</internal_reasoning>|<channel\|>)',
        response_text,
        re.DOTALL
    )
    if reasoning_match:
        reasoning = reasoning_match.group(1).strip()
    
    lines_out = []
    line_matches = re.finditer(r'<line\s+index=["\']?(\d+)["\']?>\s*(.*?)\s*(?:</line>|(?=<line)|$)', response_text, re.DOTALL)
    for match in line_matches:
        lines_out.append({
            "index": int(match.group(1)),
            "text": match.group(2).strip()
        })
        
    return {
        "reasoning": reasoning,
        "translations": lines_out,
        "raw": response_text if not lines_out else None
    }


@router.put("/projects/{project_name}")
async def update_project(project_name: str, data: Dict, background_tasks: BackgroundTasks, global_save: bool = False):
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
        
    old_glossary = metadata.get("glossary", {}).get("terms", [])

    glossary_changed = "glossary" in data
    context_changed = "context_guide" in data

    # GET /projects returns the RESOLVED context (child + an appended
    # "--- Universe Context (parent) ---" block). If a client saves that back
    # verbatim, the child's stored context would compound the parent block on every
    # save and double-feed it to the translator. Strip the appended block here so
    # only the child's own context is persisted (this also self-heals any
    # already-compounded data). Inheritance is re-applied at read time by
    # load_resolved_project_metadata.
    if context_changed and metadata.get("parent_project"):
        data["context_guide"] = storage.strip_inherited_context(data.get("context_guide"))

    if glossary_changed:
        new_glossary = data.get("glossary", {})
        new_terms = new_glossary.get("terms", [])
        
        parent_name = metadata.get("parent_project")
        if parent_name:
            parent_meta = storage.load_project_metadata(parent_name)
            if parent_meta:
                parent_glossary = parent_meta.get("glossary", {})
                parent_terms = parent_glossary.get("terms", [])
                parent_term_map = {t.get("term", "").lower(): t for t in parent_terms if t.get("term")}
                
                child_local_terms = []
                parent_modified_terms = []
                
                for term_entry in new_terms:
                    term_name = term_entry.get("term", "")
                    if not term_name:
                        continue
                    term_lower = term_name.lower()
                    is_inherited = term_entry.get("inherited", False)
                    
                    if is_inherited and term_lower in parent_term_map:
                        parent_version = parent_term_map[term_lower]
                        modified = (
                            term_entry.get("translation") != parent_version.get("translation") or
                            term_entry.get("type") != parent_version.get("type") or
                            term_entry.get("gender") != parent_version.get("gender") or
                            term_entry.get("description") != parent_version.get("description") or
                            term_entry.get("keep_original") != parent_version.get("keep_original") or
                            term_entry.get("case_sensitive") != parent_version.get("case_sensitive") or
                            (term_entry.get("sense") or "") != (parent_version.get("sense") or "")
                        )
                        
                        if modified:
                            if global_save:
                                updated_parent_term = dict(term_entry)
                                updated_parent_term.pop("inherited", None)
                                updated_parent_term.pop("inherited_from", None)
                                parent_modified_terms.append(updated_parent_term)
                            else:
                                local_term = dict(term_entry)
                                local_term.pop("inherited", None)
                                local_term.pop("inherited_from", None)
                                child_local_terms.append(local_term)
                        # If not modified, it remains parent-only (not stored in child)
                    else:
                        local_term = dict(term_entry)
                        local_term.pop("inherited", None)
                        local_term.pop("inherited_from", None)
                        child_local_terms.append(local_term)
                        
                if global_save and parent_modified_terms:
                    updated_parent_terms = []
                    modified_map = {t["term"].lower(): t for t in parent_modified_terms}
                    for pt in parent_terms:
                        pt_lower = pt.get("term", "").lower()
                        if pt_lower in modified_map:
                            updated_parent_terms.append(modified_map[pt_lower])
                        else:
                            updated_parent_terms.append(pt)
                    parent_meta["glossary"] = {"terms": updated_parent_terms}
                    storage.save_project_metadata(parent_name, parent_meta)
                    await adk_session_manager.update_glossary(parent_name, parent_meta["glossary"])
                    
                data["glossary"] = {"terms": child_local_terms}
            else:
                for t in new_terms:
                    t.pop("inherited", None)
                    t.pop("inherited_from", None)
        else:
            for t in new_terms:
                t.pop("inherited", None)
                t.pop("inherited_from", None)
                
    if "settings" in data and "settings" in metadata:
        metadata["settings"].update(data["settings"])
        del data["settings"]
        
    metadata.update(data)
    
    affected_terms = set()
    if glossary_changed:
        new_glossary = metadata.get("glossary", {}).get("terms", [])
        old_map = {t.get("term", "").lower(): t for t in old_glossary if t.get("term")}
        
        for nt in new_glossary:
            term = nt.get("term", "")
            if not term:
                continue
            key = term.lower()
            if key not in old_map:
                affected_terms.add(term)
            else:
                ot = old_map[key]
                if (ot.get("translation") != nt.get("translation") or 
                    ot.get("gender") != nt.get("gender") or
                    ot.get("case_sensitive") != nt.get("case_sensitive") or
                    ot.get("keep_original") != nt.get("keep_original")):
                    affected_terms.add(term)
                    
    if glossary_changed or context_changed:
        _invalidate_translation_cache(metadata)
        # Cascading invalidation to all child and grandchild descendant projects
        descendant_names = storage.get_descendant_projects(project_name)
        for desc_name in descendant_names:
            desc_meta = storage.load_project_metadata(desc_name)
            if desc_meta:
                _invalidate_translation_cache(desc_meta)
                resolved_desc = storage.load_resolved_project_metadata(desc_name)
                if resolved_desc:
                    await adk_session_manager.update_glossary(desc_name, resolved_desc.get("glossary", {"terms": []}))
        
    storage.save_project_metadata(project_name, metadata)
    await adk_session_manager.update_glossary(project_name, metadata.get("glossary", {"terms": []}))

    if "translation_priority" in data or "arr_disabled" in data:
        prio = metadata.get("translation_priority", "normal")
        try:
            from utils.translation_queue import get_translation_queue
            get_translation_queue().update_project_priority(project_name, prio)
        except Exception:
            pass
    
    if affected_terms:
        # "Does this project have any translated episodes?" — answer from the index
        # (one query) instead of loading every episode's data.json on the event loop.
        has_translated_subs = False
        try:
            from utils import metadata_index
            cnt = metadata_index.count_translated(project_name)
            if cnt is None:
                # Index not populated for this project — fall back to a cheap metadata scan.
                has_translated_subs = any(
                    (storage.load_episode_metadata(project_name, ep) or {}).get("translated")
                    for ep in storage.list_episodes(project_name)
                )
            else:
                has_translated_subs = cnt > 0
        except Exception:
            has_translated_subs = True  # safe default: allow the retranslation to proceed

        if has_translated_subs:
            model = resolve_model("translation", metadata)
            job_id = create_job("retranslate_invalidated", project_name=project_name)
            background_tasks.add_task(
                _process_targeted_retranslation,
                job_id,
                project_name,
                list(affected_terms),
                model
            )
            metadata["job_id"] = job_id
        
    return metadata


@router.post("/projects/{project_name}/promote-term")
async def promote_term_to_parent(project_name: str, request: PromoteTermRequest):
    """Promote a single term from a child project directly to its parent universe."""
    child_meta = storage.load_project_metadata(project_name)
    if not child_meta:
        raise HTTPException(status_code=404, detail="Project not found")
        
    parent_name = child_meta.get("parent_project")
    if not parent_name:
        raise HTTPException(status_code=400, detail="Project has no parent universe to promote to")
        
    parent_meta = storage.load_project_metadata(parent_name)
    if not parent_meta:
        raise HTTPException(status_code=404, detail="Parent universe project not found")
        
    parent_glossary = parent_meta.get("glossary", {})
    parent_terms = parent_glossary.get("terms", [])
    
    target_term_lower = request.term.lower().strip()
    term_dict = {
        "term": request.term.strip(),
        "translation": request.translation.strip(),
        "type": request.type or "other",
        "gender": request.gender or "unknown",
        "case_sensitive": True if request.case_sensitive is None else bool(request.case_sensitive),
        "keep_original": bool(request.keep_original),
        "description": request.description or "",
    }
    
    # Check if term already exists in parent, update it or append
    found = False
    for i, pt in enumerate(parent_terms):
        if pt.get("term", "").lower().strip() == target_term_lower:
            parent_terms[i] = term_dict
            found = True
            break
            
    if not found:
        parent_terms.append(term_dict)
        
    parent_meta["glossary"] = {"terms": parent_terms}
    _invalidate_translation_cache(parent_meta)
    storage.save_project_metadata(parent_name, parent_meta)
    await adk_session_manager.update_glossary(parent_name, parent_meta["glossary"])
    
    # Also invalidate child and sibling caches
    for desc_name in storage.get_descendant_projects(parent_name):
        desc_meta = storage.load_project_metadata(desc_name)
        if desc_meta:
            _invalidate_translation_cache(desc_meta)
            
    return {"status": "promoted", "parent_project": parent_name, "term": term_dict}


@router.post("/projects/{project_name}/promote-terms-batch")
async def promote_terms_batch(project_name: str, request: BatchPromoteTermsRequest):
    """Promote multiple terms from a child project to its parent universe project."""
    child_meta = storage.load_project_metadata(project_name)
    if not child_meta:
        raise HTTPException(status_code=404, detail="Project not found")
        
    parent_name = child_meta.get("parent_project")
    if not parent_name:
        raise HTTPException(status_code=400, detail="Project has no parent project configured")
        
    parent_meta = storage.load_project_metadata(parent_name)
    if not parent_meta:
        raise HTTPException(status_code=404, detail=f"Parent project '{parent_name}' not found")
        
    parent_terms = list(parent_meta.get("glossary", {}).get("terms", []))
    parent_map = {t.get("term", "").lower().strip(): i for i, t in enumerate(parent_terms)}
    
    promoted_count = 0
    for term_data in request.terms:
        term_name = term_data.get("term", "").strip()
        if not term_name:
            continue
        term_dict = {
            "term": term_name,
            "translation": term_data.get("translation", ""),
            "type": term_data.get("type", "other"),
            "gender": term_data.get("gender", "n/a"),
            "description": term_data.get("description", ""),
            "case_sensitive": term_data.get("case_sensitive", True),
            "keep_original": term_data.get("keep_original", False),
        }
        term_lower = term_name.lower().strip()
        if term_lower in parent_map:
            parent_terms[parent_map[term_lower]] = term_dict
        else:
            parent_terms.append(term_dict)
            parent_map[term_lower] = len(parent_terms) - 1
        promoted_count += 1

    parent_meta["glossary"] = {"terms": parent_terms}
    _invalidate_translation_cache(parent_meta)
    storage.save_project_metadata(parent_name, parent_meta)
    await adk_session_manager.update_glossary(parent_name, parent_meta["glossary"])
    
    for desc_name in storage.get_descendant_projects(parent_name):
        desc_meta = storage.load_project_metadata(desc_name)
        if desc_meta:
            _invalidate_translation_cache(desc_meta)
            
    return {"status": "promoted", "parent_project": parent_name, "count": promoted_count}


@router.post("/projects/{project_name}/suppress-terms-batch")
async def suppress_terms_batch(project_name: str, request: BatchSuppressTermsRequest):
    """Add a batch of terms to a project's suppressed_terms list."""
    meta = storage.load_project_metadata(project_name)
    if not meta:
        raise HTTPException(status_code=404, detail="Project not found")
        
    suppressed = set(meta.get("suppressed_terms", []))
    for t in request.terms:
        t_clean = t.strip()
        if t_clean:
            suppressed.add(t_clean)
            
    meta["suppressed_terms"] = sorted(list(suppressed), key=str.lower)
    _invalidate_translation_cache(meta)
    storage.save_project_metadata(project_name, meta)
    return {"status": "suppressed", "suppressed_terms": meta["suppressed_terms"]}


@router.post("/projects/{project_name}/revert-terms-batch")
async def revert_terms_batch(project_name: str, request: BatchRevertTermsRequest):
    """Revert local overrides and/or unsuppress terms for a project."""
    meta = storage.load_project_metadata(project_name)
    if not meta:
        raise HTTPException(status_code=404, detail="Project not found")
        
    terms_to_revert = {t.lower().strip() for t in request.terms if t}
    
    # 1. Remove from local glossary terms
    local_terms = meta.get("glossary", {}).get("terms", [])
    filtered_terms = [t for t in local_terms if t.get("term", "").lower().strip() not in terms_to_revert]
    meta["glossary"] = {"terms": filtered_terms}
    
    # 2. Remove from suppressed_terms if present
    suppressed = set(meta.get("suppressed_terms", []))
    suppressed = {s for s in suppressed if s.lower().strip() not in terms_to_revert}
    meta["suppressed_terms"] = sorted(list(suppressed), key=str.lower)
    
    _invalidate_translation_cache(meta)
    storage.save_project_metadata(project_name, meta)
    return {"status": "reverted", "reverted_count": len(terms_to_revert)}


@router.post("/projects/{project_name}/episodes/{episode_name}/harvest-terms")
async def harvest_episode_terms_endpoint(
    project_name: str,
    episode_name: str,
    request: Optional[HarvestTermsRequest] = None,
):
    """Harvest new named entities and specialized terms from a translated episode."""
    from services.term_harvester import harvest_terms_for_episode
    model = request.model if request else None
    discovered = await harvest_terms_for_episode(project_name, episode_name, model_name=model)
    return {"episode_name": episode_name, "terms": discovered}


@router.post("/projects/{project_name}/harvest-terms")
async def harvest_project_terms_endpoint(
    project_name: str,
    request: Optional[HarvestTermsRequest] = None,
):
    """Harvest terms across multiple episodes or dialogue text in a project."""
    from services.term_harvester import harvest_terms_for_episode, harvest_terms_from_lines
    meta = storage.load_resolved_project_metadata(project_name)
    if not meta:
        raise HTTPException(status_code=404, detail="Project not found")
        
    model = request.model if request else None
    discovered = []
    
    if request and request.episode_names:
        for ep_name in request.episode_names:
            ep_terms = await harvest_terms_for_episode(project_name, ep_name, model_name=model)
            discovered.extend(ep_terms)
    else:
        # Scan recent/all translated episodes up to 3
        episodes = storage.list_episodes(project_name)
        translated_eps = []
        for ep in episodes:
            ep_data = storage.load_episode_data(project_name, ep)
            if ep_data and any(x.get("translated") or x.get("translations") or x.get("translation") for x in ep_data):
                translated_eps.append(ep)
        for ep_name in translated_eps[:3]:
            ep_terms = await harvest_terms_for_episode(project_name, ep_name, model_name=model)
            discovered.extend(ep_terms)
            
    seen = set()
    unique_terms = []
    for t in discovered:
        k = t.get("term", "").lower().strip()
        if k and k not in seen:
            seen.add(k)
            unique_terms.append(t)
            
    return {"project_name": project_name, "terms": unique_terms}


@router.delete("/projects/{project_name}")
async def delete_project(project_name: str):
    if storage.delete_project(project_name):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Project not found")


@router.post("/projects/{project_name}/import")
async def import_project_data(project_name: str, request: ImportRequest):
    target = storage.load_project_metadata(project_name)
    source = storage.load_project_metadata(request.source_project)
    
    if not target or not source:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if request.import_glossary:
        target["glossary"] = source.get("glossary", {"terms": []})
    if request.import_context:
        target["context_guide"] = source.get("context_guide", "")
    
    _invalidate_translation_cache(target)
    storage.save_project_metadata(project_name, target)
    return target


@router.post("/projects/{project_name}/sync")
async def sync_project(
    project_name: str,
    background_tasks: BackgroundTasks,
    request: Optional[ArrSyncRequest] = None,
):
    """Trigger a media file sync for a specific project as a background job."""
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")
        
    job_id = create_job("project_sync", project_name=project_name)
    background_tasks.add_task(_run_project_sync, job_id, project_name, request)
    return {"job_id": job_id}


async def _run_project_sync(
    job_id: str,
    project_name: str,
    sync_req: Optional[ArrSyncRequest] = None,
):
    import logging
    logger = logging.getLogger(__name__)
    from utils.jobs_manager import update_job
    from integrations.media_sync_engine import MediaSyncEngine
    from routers.integrations import _build_arr_engine
    
    try:
        scan_ass = sync_req.scan_ass if sync_req is not None else True
        extract_embedded = sync_req.extract_embedded_ass if (sync_req is not None and sync_req.extract_embedded_ass is not None) else None

        update_job(
            job_id,
            status="running",
            progress=5.0,
            message=f"Starting project sync (ASS: {'enabled' if scan_ass else 'skipped'}, Embedded probe: {'disabled' if extract_embedded is False else 'enabled'})...",
        )
        config = storage.load_global_config()
        engine = _build_arr_engine(config, scan_ass=scan_ass, extract_embedded_ass=extract_embedded)

        def progress_cb(progress=None, message=None):
            update_job(job_id, progress=progress, message=message, log=message)

        try:
            result = await engine.sync_project(project_name, progress_callback=progress_cb)
        except ValueError as ve:
            # Fall back to direct disk synchronization if Sonarr/Radarr is not configured
            logger.info(f"Arr sync skipped ({ve}), falling back to direct disk sync for '{project_name}'...")
            from services.disk_sync_service import disk_sync_service
            disk_res = await asyncio.to_thread(disk_sync_service.sync_project_disk, project_name)
            update_job(
                job_id,
                status="completed",
                progress=100.0,
                message=(
                    f"Disk sync complete: {disk_res.get('translations_found', 0)} translations found, "
                    f"{disk_res.get('translations_removed', 0)} removed, "
                    f"{disk_res.get('new_episodes_added', 0)} new episodes."
                ),
                result=disk_res,
            )
            return

        # Also run disk sync to catch manual files and deletions
        try:
            from services.disk_sync_service import disk_sync_service
            await asyncio.to_thread(disk_sync_service.sync_project_disk, project_name)
        except Exception:
            pass

        if result.errors:
            for err in result.errors:
                update_job(job_id, log=f"ERROR: {err}")

        update_job(
            job_id,
            status="completed",
            progress=100.0,
            message=(
                f"Project sync complete: {result.new_episodes} new episodes, "
                f"{result.updated_episodes} updated, {result.removed_episodes} removed."
            ),
            result=result.to_dict(),
        )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Project sync failed: {e}\n{tb}")
        update_job(job_id, status="failed", message=f"Sync failed: {str(e)}", log=f"{str(e)}\n{tb}")


class DiskSyncRequest(BaseModel):
    re_queue_removed: bool = True
    import_cues: bool = True
    auto_add_new_episodes: bool = True
    auto_rename_matching_media: bool = True


@router.post("/projects/{project_name}/sync-disk")
async def sync_project_disk_endpoint(project_name: str, payload: Optional[DiskSyncRequest] = None):
    """Scan physical disk for subtitle files and reconcile project & translation queue status."""
    from services.disk_sync_service import disk_sync_service
    req = payload or DiskSyncRequest()
    result = await asyncio.to_thread(
        disk_sync_service.sync_project_disk,
        project_name,
        re_queue_removed=req.re_queue_removed,
        import_cues=req.import_cues,
        auto_add_new_episodes=req.auto_add_new_episodes,
        auto_rename_matching_media=req.auto_rename_matching_media,
    )
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "Disk sync failed"))
    return result


@router.post("/projects/{project_name}/rename-subtitles")
async def rename_project_subtitles_endpoint(project_name: str, apply: bool = True):
    """Find subtitles named after an outdated video file (e.g. after a Sonarr upgrade)
    and, with ``apply``, rename them to the current video name so Plex/Jellyfin/Emby
    attach them. ``apply=false`` only reports the mismatches.
    """
    from services.disk_sync_service import disk_sync_service
    result = await asyncio.to_thread(
        disk_sync_service.rename_subtitles_to_match_media,
        project_name,
        apply,
    )
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "Rename failed"))
    return result


@router.get("/projects/{project_name}/check-missing")
async def check_missing_translations(project_name: str):
    """Scan all episodes in a project for missing translations of non-empty source lines."""
    proj_meta = storage.load_project_metadata(project_name)
    if not proj_meta:
        raise HTTPException(status_code=404, detail="Project not found")

    from utils.language_codes import to_code
    target_names = storage.project_target_languages(proj_meta)
    primary_lang = proj_meta.get("target_language", "Greek")
    primary_code = to_code(primary_lang)

    # We want to check all target languages
    lang_codes = [to_code(name) for name in target_names]
    if not lang_codes:
        lang_codes = [primary_code]

    results = []
    episodes = storage.list_episodes(project_name)

    for ep_name in episodes:
        ep_data = storage.load_episode(project_name, ep_name)
        if not ep_data or "data" not in ep_data:
            continue

        missing_lines = []
        for i, line in enumerate(ep_data["data"]):
            original = line.get("original", "").strip()
            if not original:
                continue

            for code in lang_codes:
                translated = (line.get("translations", {}).get(code)
                              or (line.get("translated") if code == primary_code else None)
                              or "").strip()
                if not translated:
                    missing_lines.append({
                        "line_index": i,
                        "timecode": line.get("timecode", ""),
                        "original": line.get("original", ""),
                        "lang_code": code,
                    })

        if missing_lines:
            results.append({
                "episode_name": ep_name,
                "missing_count": len(missing_lines),
                "lines": missing_lines,
            })

    return {"project": project_name, "results": results, "total_missing": sum(r["missing_count"] for r in results)}


@router.post("/projects/{project_name}/translate-missing")
async def translate_project_missing_endpoint(project_name: str, background_tasks: BackgroundTasks):
    """Trigger a background job to translate missing lines with context."""
    metadata = storage.load_project_metadata(project_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Project not found")

    from services.translation_service import translate_project_missing_lines
    job_id = create_job("translate_missing", project_name=project_name)
    background_tasks.add_task(translate_project_missing_lines, job_id, project_name)
    return {"job_id": job_id}



