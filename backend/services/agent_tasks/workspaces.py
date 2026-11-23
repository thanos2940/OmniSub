"""Claiming a queued episode for an agent and building its workspace (plan, work file, brief); workspaces for one-off targets.

Part of the agent task service (services/agent_task_service.py): ``AgentTaskService`` inherits this mixin.
Moved here unchanged from that module; ``self`` is the service.
"""

import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils import storage
from utils.translation_queue import TranslationQueue, KIND_TRANSLATION, PRIORITY_MANUAL
from utils.language_codes import to_code
from utils.episode_summaries import EpisodeSummaryManager
from services import agent_workspace as aw
from services.agent_tasks.common import logger, REPO_ROOT, _now, _options, _ignored_item, cli_command


class WorkspacesMixin:
    def _claim_from_queue(self, max_priority: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Claim the next pending queue row, cancelling and skipping any ignored projects."""
        q = TranslationQueue()
        now_str = _now()
        with q._conn() as conn:
            prio_clause = "AND priority <= ?" if max_priority is not None else ""
            params = [now_str, KIND_TRANSLATION] + ([max_priority] if max_priority is not None else [])
            rows = conn.execute(
                f"""
                SELECT * FROM translation_queue
                WHERE status IN ('queued', 'pending')
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                  AND kind = ?
                  {prio_clause}
                ORDER BY CASE WHEN priority = 10 THEN -2 ELSE priority END ASC, created_at ASC
                LIMIT 50
                """,
                params
            ).fetchall()
        for row in rows:
            if _ignored_item(row, _options(row)):
                q.update_status(row["id"], status="cancelled", error="Project set to Ignored", completed_at=_now())
                continue
            claimed = self._claim_specific(row["id"])
            if claimed:
                return claimed
        return None

    def prepare_task_workspace(self, queue_item_id: Optional[str] = None) -> Dict[str, Any]:
        """Claim a queue item (a specific one, or the next by priority) and build its workspace.

        Without an id, episodes that turn out to need nothing (already fully
        translated) are finalized on the spot and the next item is claimed, so
        ``next`` always hands the agent real work or reports an empty queue.
        """
        self.housekeeping()
        skipped: List[Dict[str, Any]] = []
        for _ in range(50):
            item = self._claim_specific(queue_item_id) if queue_item_id else self._claim_from_queue()
            if not item:
                res = {"success": False, "status": "empty",
                       "message": "No pending translation items available in the queue."}
                if skipped:
                    res["finalized_without_work"] = skipped
                return res
            res = self.build_claimed_workspace(item)
            if res.get("status") == "ignored":
                continue
            if res.get("status") in ("nothing_to_translate", "missing_source") and not queue_item_id:
                skipped.append({k: res.get(k) for k in ("project_name", "episode_name", "status", "message")})
                continue
            if skipped:
                res["finalized_without_work"] = skipped
            return res
        return {"success": False, "status": "empty", "message": "Too many items without work; try again.",
                "finalized_without_work": skipped}

    def claim(self, queue_item_id: Optional[str] = None) -> Dict[str, Any]:
        return self.prepare_task_workspace(queue_item_id)

    def claim_item(self, queue_item_id: str) -> Optional[Dict[str, Any]]:
        """Claim one waiting queue item (pending -> running) without building its workspace; None when it is no
        longer waiting. ``build_claimed_workspace`` does the rest."""
        return self._claim_specific(queue_item_id)

    def build_claimed_workspace(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Build the workspace of an item already claimed (its row is ``running``). Apart from the claim so that a
        caller claiming under its own lock (the agent runner) builds outside it: a build may first pull the source
        subtitle out of the video, which takes minutes. An item of an Ignored project is cancelled (status
        ``ignored``); a build that fails gives the item back to the queue and raises."""
        if _ignored_item(item, _options(item)):
            TranslationQueue().update_status(item["id"], status="cancelled", error="Project set to Ignored",
                                             completed_at=_now())
            return {"success": False, "status": "ignored", "queue_id": item["id"],
                    "project_name": item["project_name"], "episode_name": item["episode_name"]}
        try:
            res = self._build_workspace(item)
        except Exception as exc:
            # The row is already 'running' (claimed): give it back, or it would look like live work
            # with nobody on it until the claim TTL.
            logger.exception(f"Could not build a workspace for {item['project_name']} {item['episode_name']}")
            self._release(item["id"], f"workspace build failed: {exc}")
            self._broadcast("queue_updated", {"reason": "agent_claim_failed", "queue_id": item["id"]})
            raise
        if res.get("status") == "claimed":
            row = TranslationQueue().get_item(item["id"])
            if not row or row.get("status") != "running":
                # Cancelled (or removed) while it was being prepared, e.g. during a long extraction.
                folder = Path(res["workspace_dir"])
                manifest = self._read_manifest(folder)
                manifest.update(status="cancelled", cancelled_at=_now())
                self._write_manifest(folder, manifest)
                if row:
                    self._set_item_options(item["id"], agent_task_id=None)
                return {"success": False, "status": "cancelled", "queue_id": item["id"],
                        "project_name": item["project_name"], "episode_name": item["episode_name"]}
        return res

    def _claim_specific(self, queue_item_id: str) -> Optional[Dict[str, Any]]:
        q = TranslationQueue()
        with q._conn() as conn:
            # 'queued' is a pending item someone pressed Continue / Run Now on.
            row = conn.execute("SELECT * FROM translation_queue WHERE id = ? AND status IN ('pending', 'queued')",
                               (queue_item_id,)).fetchone()
            if not row:
                return None
            now = _now()
            cur = conn.execute(
                "UPDATE translation_queue SET status = 'running', started_at = ?, attempts = attempts + 1 "
                "WHERE id = ? AND status IN ('pending', 'queued')", (now, queue_item_id))
            conn.commit()
            if cur.rowcount != 1:
                return None
            item = dict(row)
            item.update(status="running", started_at=now)
            return item

    def _extract_missing_source(self, project_name: str, episode_name: str):
        """An episode without a source subtitle: pull the embedded track out of its video (as the model
        worker does) and return ``(cues, error)``. Runs on its own thread and event loop, so it works
        from the runner's threads and from API requests alike."""
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        from services.embedded_extractor import extract_embedded_for_episode

        def _run():
            return asyncio.run(extract_embedded_for_episode(project_name, episode_name, force=False, migrate_srt=True))

        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                res = ex.submit(_run).result()
        except Exception as e:
            logger.warning(f"No source subtitle for {project_name}/{episode_name} and extraction failed: {e}")
            return [], e
        logger.info(f"Extracted the source subtitle of {project_name}/{episode_name} from its video "
                    f"(stream {res.get('stream_index')}, {res.get('format')}, {res.get('line_count')} lines).")
        return storage.load_episode_data(project_name, episode_name) or [], None

    def _release(self, item_id: str, reason: str) -> None:
        """Hand a claimed item back to the queue."""
        q = TranslationQueue()
        with q._conn() as conn:
            conn.execute("UPDATE translation_queue SET status = 'pending', started_at = NULL WHERE id = ? AND status = 'running'",
                         (item_id,))
            conn.commit()
        self._set_item_options(item_id, agent_task_id=None)
        logger.info(f"Released agent claim on queue item {item_id}: {reason}")

    def _build_workspace(self, item: Dict[str, Any]) -> Dict[str, Any]:
        project_name, episode_name = item["project_name"], item["episode_name"]
        opts = item["_opts"] = _options(item)
        q = TranslationQueue()
        proj_meta = storage.load_resolved_project_metadata(project_name) or {}
        target_name, target_code, is_primary = self._target(item, proj_meta)
        mode = "full" if (opts.get("mode") == "full" or opts.get("force") or opts.get("translation_type") == "original") else "gaps"

        cues = storage.load_episode_data(project_name, episode_name) or []
        if not cues:
            raw_text, ext = storage.load_original_subtitle(project_name, episode_name)
            if raw_text:
                from utils.subtitle_io import parse_subtitle
                cues = parse_subtitle(raw_text, filename=f"sub.{(ext or 'srt').lstrip('.')}")
                if cues:
                    storage.save_episode(project_name, episode_name, cues)
        extract_error = None
        if not cues:
            cues, extract_error = self._extract_missing_source(project_name, episode_name)
        if not cues:
            msg = f"No source subtitle cues for {project_name}/{episode_name}"
            msg += f": extracting one from the video failed ({extract_error})." if extract_error else "."
            ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}
            # A share that dropped out is worth retrying; a video without a usable track is not.
            offline = (extract_error is not None and ep_meta.get("arr_media_path")
                       and "reachable" in str(extract_error).lower() and int(item.get("attempts") or 0) < 5)
            if offline:
                retry_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
                q.update_status(item["id"], status="pending", error=msg, started_at=None, next_retry_at=retry_at)
            else:
                q.update_status(item["id"], status="failed", error=msg, completed_at=_now())
            self._set_item_options(item["id"], agent_task_id=None)
            return {"success": False, "status": "missing_source", "project_name": project_name,
                    "episode_name": episode_name, "message": msg}

        before = sum(1 for c in cues if isinstance(c.get("_ass"), dict) and c["_ass"].get("translatable") is False)
        units, stats = aw.build_units(cues, target_code, is_primary, full=(mode == "full"))
        after = sum(1 for c in cues if isinstance(c.get("_ass"), dict) and c["_ass"].get("translatable") is False)
        if after != before:
            # Newer karaoke rules reclassified cues — persist so the editor and export agree.
            storage.save_episode(project_name, episode_name, cues)

        if not units:
            return self._finalize_without_work(item, project_name, episode_name, cues, target_code, is_primary, stats)

        task_id = f"task_{re.sub(r'[^A-Za-z0-9_-]', '_', episode_name)[:25]}_{int(time.time())}_{uuid.uuid4().hex[:4]}"
        folder = self.tasks_dir / task_id
        folder.mkdir(parents=True, exist_ok=True)

        fingerprint = aw.source_fingerprint(cues, units)
        plan = aw.units_to_plan(units, fingerprint, stats, mode)
        (folder / aw.PLAN_FILE).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

        title = f"{project_name} · {episode_name} · English → {target_name}"
        (folder / aw.WORK_FILE).write_text(aw.render_work_file(units, title, target_name), encoding="utf-8")

        glossary = proj_meta.get("glossary") or {"terms": []}
        (folder / "glossary.json").write_text(json.dumps(glossary, indent=2, ensure_ascii=False), encoding="utf-8")
        (folder / aw.TERMS_FILE).write_text("[]\n", encoding="utf-8")
        (folder / aw.SUMMARY_FILE).write_text("", encoding="utf-8")
        guide = (proj_meta.get("context_guide") or "").strip()
        (folder / aw.GUIDE_FILE).write_text(
            f"# Context Guide: {project_name}\n\n{guide}\n" if guide else
            f"# Context Guide: {project_name}\n\n<!-- Empty: replace this comment with 2-3 paragraphs about the show "
            f"(premise, setting, factions, tone, how characters address each other). Saved to the project on submit. -->\n",
            encoding="utf-8")

        brief = self._render_brief(
            task_id=task_id, project_name=project_name, episode_name=episode_name,
            target_name=target_name, mode=mode, stats=stats, units=units,
            proj_meta=proj_meta, glossary=glossary, guide=guide)
        (folder / aw.BRIEF_FILE).write_text(brief, encoding="utf-8")

        manifest = {
            "task_id": task_id,
            "plan_version": aw.PLAN_VERSION,
            "queue_id": item["id"],
            "project_name": project_name,
            "episode_name": episode_name,
            "target_language": target_name,
            "target_code": target_code,
            "is_primary": is_primary,
            "mode": mode,
            "units": len(units),
            "total_cues": len(cues),
            "created_at": _now(),
            "status": "prepared",
        }
        self._write_manifest(folder, manifest)
        self._set_item_options(item["id"], engine="agent", agent_task_id=task_id, mode=mode)

        result = {
            "success": True,
            "status": "claimed",
            "task_id": task_id,
            "queue_id": item["id"],
            "project_name": project_name,
            "episode_name": episode_name,
            "target_language": target_name,
            "mode": mode,
            "units": len(units),
            "dialogue_units": stats["dialogue_units"],
            "sign_units": stats["sign_units"],
            "total_cues": len(cues),
            "workspace_dir": str(folder),
            "brief_path": str(folder / aw.BRIEF_FILE),
            "work_file": str(folder / aw.WORK_FILE),
            "check_command": cli_command("check", task_id),
            "submit_command": cli_command("submit", task_id),
        }
        self._broadcast("agent_task_claimed", {k: result[k] for k in (
            "task_id", "queue_id", "project_name", "episode_name", "units", "mode")})
        return result

    def _finalize_without_work(self, item, project_name, episode_name, cues, target_code, is_primary, stats):
        """Every line already has a real translation: re-export (so fixes such as
        signs no longer being blanked reach the media folder) and close the item."""
        exported = None
        has_translation = any(aw.cue_translation(c, target_code, is_primary) for c in cues)
        if has_translation and is_primary:
            exported = self._export(project_name, episode_name, cues)
        TranslationQueue().update_status(item["id"], status="completed", completed_at=_now(), logs=[
            "Agent claim: every translatable line already has a translation — nothing to do.",
            f"Re-exported to {exported}." if exported else "No export performed.",
        ])
        self._set_item_options(item["id"], agent_task_id=None)
        self._broadcast("queue_updated", {"reason": "agent_nothing_to_translate", "queue_id": item["id"],
                                          "project_name": project_name, "episode_name": episode_name})
        return {"success": True, "status": "nothing_to_translate", "queue_id": item["id"],
                "project_name": project_name, "episode_name": episode_name, "exported_path": exported,
                "message": f"{project_name} {episode_name} is fully translated ({stats['distinct_units']} lines); re-exported."}

    def _render_brief(self, *, task_id, project_name, episode_name, target_name, mode, stats, units,
                      proj_meta, glossary, guide) -> str:
        from utils.translation_rules import build_style_guide
        from utils.work_origin import is_japanese_work
        texts = [u.text for u in units]

        story = ""
        try:
            prev = EpisodeSummaryManager(project_name).get_previous_summaries(episode_name, count=3)
            story = "\n".join(f"- **{p['episode']}**: {p['summary']}" for p in prev)
        except Exception:
            pass

        characters = ""
        try:
            from utils.character_profiles import CharacterProfileManager
            cpm = CharacterProfileManager(project_name)
            profiles = cpm.get_profiles_for_chunk(texts, glossary=glossary, fallback_to_main_cast=True)
            characters = cpm.build_profile_context(profiles)
        except Exception:
            pass

        from utils.glossary_judgment import GLOSSARY_INSTRUCTION, Matcher, brief_lines, project_vocabulary
        terms = brief_lines(Matcher(glossary.get("terms", []), project_vocabulary(project_name)).find_all(texts))

        scope = ("Retranslate every line." if mode == "full" else
                 "Only lines that are missing a real translation are listed; "
                 f"{stats['already_translated_units']} already-translated lines were left out.")
        signs_note = ""
        if stats["sign_units"]:
            signs_note = (
                "\n**On-screen text** (the last section of the work file) is drawn over the video: signs, "
                "titles, next-episode previews and the mid-episode *Currently Disclosed Information* cards. "
                "Translate the meaning concisely, keep the English line breaks of multi-line cards, and keep "
                "numbering such as ① or 3-4 as is. Several consecutive entries can be one sentence split across "
                "lines of a card — translate them so they still read in order.\n")

        return f"""# Translate {project_name} — {episode_name} → {target_name}

Task `{task_id}` · {len(units)} lines ({stats['dialogue_units']} dialogue, {stats['sign_units']} on-screen text) · {scope}

## How to do this task

You are the translator. Omnisub has already done the preparation and will do all of
the post-processing (cleanup, line wrapping, glossary check, translation memory,
export next to the video, queue status). Your only job is language work:

1. Read this brief once: the show context, story so far, characters, glossary and style rules below.
2. Edit **`{aw.WORK_FILE}`** in this folder: replace the English after each `N|` with {target_name}.
   Keep the numbers; one entry per number. You can work through it in several passes —
   lines still in English are simply not done yet.
3. While translating, add every **new** recurring name, place, organisation or term that is not in the
   glossary to **`{aw.TERMS_FILE}`** so later episodes stay consistent:
   `[{{"term": "Wall Rose", "translation": "Τείχος Ρόουζ", "type": "location"}}]`
   (types: character, location, organization, object, technique, other; add `"gender"` for people).
4. Write 2–3 plain sentences about what happens in this episode to **`{aw.SUMMARY_FILE}`**.
5. If the show context below is missing or wrong, improve **`{aw.GUIDE_FILE}`**.
6. From the Omnisub folder (`{REPO_ROOT}`), check and submit:
   - `{cli_command("check", task_id)}` — reports what is missing, still English, or off-glossary (changes nothing)
   - `{cli_command("submit", task_id)}` — validates, then ingests and exports. If it reports lines, fix them and submit again.
7. Continue with `{cli_command("next")}` for the next episode.

Do **not** write scripts, read the media files, or edit Omnisub's database or project files —
everything you need is in this folder, and everything you produce goes into the files above.

## Show context
{guide or "_No context guide yet — please write one in `" + aw.GUIDE_FILE + "` (premise, setting, factions, tone, how characters address each other)._"}

## Story so far
{story or "_No earlier episode summaries yet._"}

## Characters in this episode
{characters or "_No character profiles yet. Default to singular familiar address unless the scene is formal or addresses a group._"}

## Glossary terms in this episode
{GLOSSARY_INSTRUCTION} The full glossary is in `glossary.json`.
{chr(10).join(terms) if terms else "_None of the glossary terms occur in these lines._"}
{signs_note}
## Style rules
These are the same rules the API translator follows.

{build_style_guide(target_name, japanese_work=is_japanese_work(proj_meta))}
"""

    def prepare_target_workspace(
        self,
        project_name: str,
        episode_name: Optional[str] = None,
        lang: Optional[str] = None,
        full: bool = False,
    ) -> Dict[str, Any]:
        """Claim a workspace for a specific episode (enqueueing it when needed).

        An episode already claimed by an agent is returned as-is instead of being
        yanked away from that session.
        """
        self.housekeeping()
        matched_proj = self.find_matching_project(project_name)
        if not matched_proj:
            return {"success": False, "status": "not_found", "message": f"Project '{project_name}' not found."}
        episodes = storage.list_episodes(matched_proj)
        if not episodes:
            return {"success": False, "status": "not_found", "message": f"No episodes found in project '{matched_proj}'."}

        if episode_name:
            matched_ep = self.find_matching_episode(matched_proj, episode_name)
            if not matched_ep:
                return {"success": False, "status": "not_found",
                        "message": f"Episode '{episode_name}' not found in project '{matched_proj}'. Available: {episodes[:5]}"}
        else:
            proj_meta = storage.load_project_metadata(matched_proj) or {}
            if proj_meta.get("type") == "movie" or proj_meta.get("arr_media_type") == "movie" or len(episodes) == 1:
                matched_ep = episodes[0]
            else:
                matched_ep = next((ep for ep in episodes
                                   if not (storage.load_episode_metadata(matched_proj, ep) or {}).get("translated")),
                                  episodes[0])

        lang_code = to_code(lang) if lang else ""
        q = TranslationQueue()
        existing = q.find_item(matched_proj, matched_ep, lang=lang_code)
        if existing and existing.get("status") == "running":
            task_id = _options(existing).get("agent_task_id")
            folder = self._find_task_folder(task_id) if task_id else None
            if folder:
                manifest = self._read_manifest(folder)
                return {"success": True, "status": "already_claimed", "task_id": task_id, "queue_id": existing["id"],
                        "project_name": matched_proj, "episode_name": matched_ep,
                        "target_language": manifest.get("target_language"), "units": manifest.get("units"),
                        "workspace_dir": str(folder), "brief_path": str(folder / aw.BRIEF_FILE),
                        "work_file": str(folder / aw.WORK_FILE),
                        "check_command": cli_command("check", task_id), "submit_command": cli_command("submit", task_id),
                        "message": f"{matched_proj} {matched_ep} is already claimed by an agent ({task_id})."}
            return {"success": False, "status": "busy",
                    "message": f"{matched_proj} {matched_ep} is being translated right now; try again when it finishes."}

        from services.queue_service import enqueue_translation
        queue_id = enqueue_translation(matched_proj, matched_ep, PRIORITY_MANUAL,
                                       {"engine": "agent", "mode": "full" if full else "gaps"}, lang=lang_code)
        return self.prepare_task_workspace(queue_item_id=queue_id)

    def enqueue_target_project(
        self,
        project_name: str,
        episode_names: Optional[List[str]] = None,
        priority: int = PRIORITY_MANUAL,
        full: bool = False,
        include_translated: bool = False,
    ) -> Dict[str, Any]:
        """Queue episodes of a project for agent translation.

        Without ``episode_names`` this queues the untranslated episodes, or every
        episode with ``include_translated`` (gap mode then finalizes the complete
        ones instantly when claimed). Use ``audit_project`` to find translated
        episodes that still contain untranslated lines.
        """
        matched_proj = self.find_matching_project(project_name)
        if not matched_proj:
            return {"success": False, "message": f"Project '{project_name}' not found."}

        all_eps = storage.list_episodes(matched_proj)
        if episode_names:
            if isinstance(episode_names, str):
                episode_names = [e.strip() for e in episode_names.split(",") if e.strip()]
            target_eps = []
            for ep_q in episode_names:
                m = self.find_matching_episode(matched_proj, ep_q)
                if m and m not in target_eps:
                    target_eps.append(m)
        else:
            target_eps = [ep for ep in all_eps
                          if include_translated or not (storage.load_episode_metadata(matched_proj, ep) or {}).get("translated")]

        if not target_eps:
            return {"success": True, "project_name": matched_proj, "enqueued_count": 0, "episodes": [], "queue_ids": [],
                    "message": f"No eligible untranslated episodes to enqueue for '{matched_proj}'."}

        from services.queue_service import enqueue_translation
        opts = {"engine": "agent", "mode": "full" if full else "gaps"}
        enqueued = [qid for qid in (enqueue_translation(matched_proj, ep, priority, dict(opts)) for ep in target_eps) if qid]
        self._broadcast("queue_updated", {"reason": "agent_enqueue", "project_name": matched_proj, "count": len(enqueued)})
        return {
            "success": True,
            "is_batch": True,
            "project_name": matched_proj,
            "enqueued_count": len(enqueued),
            "episodes": target_eps,
            "queue_ids": enqueued,
            "message": f"Enqueued {len(enqueued)} episode(s) of '{matched_proj}' for AI Agent translation.",
        }
