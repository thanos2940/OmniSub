"""
OmbiSub Agent Task Service
==========================
Lets an external AI agent (Claude Code, Antigravity, any CLI assistant) take the
translation model's place. Omnisub prepares each episode as a small workspace — a
brief (context, story so far, characters, glossary, style rules) and a work file of
English lines — the agent replaces the English with the translation, and submission
runs the same post-processing a model translation gets: validation, Script Guard,
glossary merge, Translation Memory, episode summary, export next to the video.

Queue contract
  * Items meant for agents carry ``options.engine = "agent"``; the background worker
    never claims those, and claims no translations at all while the global
    ``translation_engine`` is ``"agent"``.
  * A claim marks the item ``running`` and records ``options.agent_task_id``. Claims
    survive a server restart and are released after ``agent_claim_ttl_hours``
    without workspace activity, so an abandoned session never blocks an episode.
  * ``options.mode`` is ``"gaps"`` (translate only lines lacking a translation — the
    default) or ``"full"`` (retranslate everything). An item with a mode is an
    explicit request, so storage/disk reconciliation never completes it behind the
    agent's back.
"""

import json
import logging
import re
import shutil
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from utils import storage, text_repair
from utils.paths import DATA_DIR
from utils.translation_queue import TranslationQueue, KIND_TRANSLATION, PRIORITY_MANUAL
from utils.text_normalize import sanitize_translation_text, fix_japanese_name_greek_tonos
from utils.language_codes import to_code
from utils.episode_summaries import EpisodeSummaryManager
from services import agent_workspace as aw

logger = logging.getLogger("omnisub.agent_task")

AGENT_TASKS_DIR = DATA_DIR / "agent_tasks"
DEFAULT_CLAIM_TTL_HOURS = 12.0

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# Legacy output names accepted in place of the work file (older skills wrote these).
_LEGACY_OUTPUTS = (
    "output_translated.srt", "output_translated.txt", "output_translated.json",
    "translated.srt", "translated.txt", "output.srt", "output.txt",
)
_ACTIVE = ("pending", "running", "paused_daily_limit", "paused")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _options(row: Any) -> Dict[str, Any]:
    """A queue row's options as a dict (accepts sqlite rows and plain dicts)."""
    raw = row["options"] if row is not None and "options" in row.keys() else None
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except Exception:
        return {}


def cli_command(*args: str) -> str:
    """The agent CLI invocation to show agents, runnable from the repo root."""
    venv_py = REPO_ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    py = ".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python"
    if not venv_py.exists():
        py = "python"
    return " ".join([py, "scripts/agent_task.py", *args])


class AgentTaskService:
    """Claims queue items for agents, builds their workspaces and ingests results."""

    def __init__(self, tasks_dir: Optional[Path] = None):
        self.tasks_dir = Path(tasks_dir) if tasks_dir else AGENT_TASKS_DIR
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ helpers

    def _candidate_dirs(self) -> List[Path]:
        """Where workspaces may live (older builds wrote next to backend/ or data/)."""
        backend_dir = Path(__file__).resolve().parent.parent
        seen, dirs = set(), []
        for d in (self.tasks_dir, DATA_DIR / "agent_tasks", backend_dir / "agent_tasks",
                  backend_dir.parent / "data" / "agent_tasks"):
            try:
                r = d.resolve()
            except OSError:
                continue
            if r not in seen and r.exists():
                seen.add(r)
                dirs.append(r)
        return dirs

    def _find_task_folder(self, task_id: str) -> Optional[Path]:
        if not task_id:
            return None
        direct = Path(task_id)
        if direct.is_dir() and (direct / "manifest.json").is_file():
            return direct
        for d in self._candidate_dirs():
            cand = d / task_id
            if (cand / "manifest.json").is_file():
                return cand
        return None

    @staticmethod
    def _read_manifest(folder: Path) -> Dict[str, Any]:
        try:
            return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _write_manifest(folder: Path, manifest: Dict[str, Any]) -> None:
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    def _set_item_options(self, item_id: str, **changes: Any) -> None:
        """Merge ``changes`` into a queue item's options (``None`` removes a key)."""
        q = TranslationQueue()
        with q._conn() as conn:
            row = conn.execute("SELECT options FROM translation_queue WHERE id = ?", (item_id,)).fetchone()
            opts = _options(row) if row else {}
            for k, v in changes.items():
                if v is None:
                    opts.pop(k, None)
                else:
                    opts[k] = v
            conn.execute("UPDATE translation_queue SET options = ? WHERE id = ?",
                         (json.dumps(opts, ensure_ascii=False) if opts else None, item_id))
            conn.commit()

    @staticmethod
    def _broadcast(event_type: str, data: Dict[str, Any]) -> None:
        try:
            from utils.event_broadcaster import broadcast_event
            broadcast_event(event_type, data)
        except Exception as e:
            logger.debug(f"Event broadcast failed: {e}")

    @staticmethod
    def _claim_ttl_hours() -> float:
        try:
            return float(storage.load_global_config().get("agent_claim_ttl_hours") or DEFAULT_CLAIM_TTL_HOURS)
        except Exception:
            return DEFAULT_CLAIM_TTL_HOURS

    @staticmethod
    def _target(item: Dict[str, Any], proj_meta: Dict[str, Any]):
        """(target language name, code, is_primary) for a queue item."""
        primary_name = proj_meta.get("target_language") or "Greek"
        primary_code = to_code(primary_name)
        opts = item.get("_opts") or {}
        lang = (item.get("lang") or "").strip()
        code = to_code(lang) if lang else primary_code
        name = opts.get("target_language_name") or (primary_name if code == primary_code else lang)
        return name, code, code == primary_code

    # ------------------------------------------------------------------ fuzzy lookup

    @staticmethod
    def find_matching_project(query: str) -> Optional[str]:
        """Find a project by exact, case-insensitive, prefix or substring match."""
        if not query:
            return None
        all_projects = storage.list_projects()
        q_clean = query.strip()
        if q_clean in all_projects:
            return q_clean
        q_lower = q_clean.lower()
        for test in (lambda p: p.lower() == q_lower, lambda p: p.lower().startswith(q_lower),
                     lambda p: q_lower in p.lower()):
            for p in all_projects:
                if test(p):
                    return p
        return None

    @staticmethod
    def find_matching_episode(project_name: str, query: str) -> Optional[str]:
        """Find an episode by exact name, SxxExx / 1x02 tag, or bare episode number."""
        if not query:
            return None
        episodes = storage.list_episodes(project_name)
        q_clean = query.strip()
        if q_clean in episodes:
            return q_clean
        q_lower = q_clean.lower()
        for ep in episodes:
            if ep.lower() == q_lower:
                return ep
        m = re.search(r's?(\d+)[ex](\d+)', q_lower)
        if m:
            pattern = f"s{int(m.group(1)):02d}e{int(m.group(2)):02d}"
            for ep in episodes:
                if pattern in ep.lower():
                    return ep
        for ep in episodes:
            if q_lower in ep.lower():
                return ep
        m_ep = re.search(r'\b(?:ep?|episode)?\s*(\d+)\b', q_lower)
        if m_ep:
            pattern = f"e{int(m_ep.group(1)):02d}"
            for ep in episodes:
                if pattern in ep.lower():
                    return ep
        return None

    # ------------------------------------------------------------------ listing

    def list_pending_tasks(self) -> List[Dict[str, Any]]:
        """Pending translation items, in the order ``next`` would claim them."""
        self.housekeeping()
        q = TranslationQueue()
        with q._conn() as conn:
            rows = conn.execute("""
                SELECT id, project_name, episode_name, priority, status, created_at, lang, options
                FROM translation_queue
                WHERE status = 'pending' AND kind = ?
                ORDER BY priority ASC, created_at ASC
            """, (KIND_TRANSLATION,)).fetchall()
        items = []
        for r in rows:
            opts = _options(r)
            item = {k: r[k] for k in ("id", "project_name", "episode_name", "priority", "status", "created_at", "lang")}
            item["engine"] = opts.get("engine") or "model"
            item["mode"] = opts.get("mode") or "gaps"
            meta = storage.load_episode_metadata(r["project_name"], r["episode_name"]) or {}
            item["total_cues"] = meta.get("line_count") or 0
            items.append(item)
        return items

    def list_active_tasks(self) -> List[Dict[str, Any]]:
        """Items currently claimed by agents, with workspace progress."""
        q = TranslationQueue()
        with q._conn() as conn:
            rows = conn.execute("""
                SELECT id, project_name, episode_name, started_at, options
                FROM translation_queue
                WHERE status = 'running' AND kind = ?
                  AND COALESCE(json_extract(CASE WHEN json_valid(options) THEN options END, '$.agent_task_id'), '') != ''
                ORDER BY started_at ASC
            """, (KIND_TRANSLATION,)).fetchall()
        active = []
        for r in rows:
            task_id = _options(r).get("agent_task_id")
            folder = self._find_task_folder(task_id)
            entry = {
                "task_id": task_id,
                "queue_id": r["id"],
                "project_name": r["project_name"],
                "episode_name": r["episode_name"],
                "claimed_at": r["started_at"],
                "workspace_dir": str(folder) if folder else None,
            }
            if folder:
                entry.update(self._progress(folder))
            active.append(entry)
        return active

    def _progress(self, folder: Path) -> Dict[str, Any]:
        """How far the agent got: units whose work-file text is no longer the source."""
        try:
            manifest = self._read_manifest(folder)
            plan = json.loads((folder / aw.PLAN_FILE).read_text(encoding="utf-8"))
            units = aw.units_from_plan(plan)
            work = folder / aw.WORK_FILE
            parsed, _ = aw.parse_work_file(work.read_text(encoding="utf-8"))
            code = manifest.get("target_code") or to_code(manifest.get("target_language", "Greek"))
            done = sum(1 for u in units if not aw.looks_untranslated(u.text, parsed.get(u.id, ""), code))
            return {
                "units": len(units),
                "units_done": done,
                "last_activity": datetime.fromtimestamp(work.stat().st_mtime, timezone.utc).isoformat(),
            }
        except Exception:
            return {}

    # ------------------------------------------------------------------ claiming

    def prepare_task_workspace(self, queue_item_id: Optional[str] = None) -> Dict[str, Any]:
        """Claim a queue item (a specific one, or the next by priority) and build its workspace.

        Without an id, episodes that turn out to need nothing (already fully
        translated) are finalized on the spot and the next item is claimed, so
        ``next`` always hands the agent real work or reports an empty queue.
        """
        self.housekeeping()
        q = TranslationQueue()
        skipped: List[Dict[str, Any]] = []
        for _ in range(50):
            item = self._claim_specific(queue_item_id) if queue_item_id else q.claim_next_for_agent()
            if not item:
                res = {"success": False, "status": "empty",
                       "message": "No pending translation items available in the queue."}
                if skipped:
                    res["finalized_without_work"] = skipped
                return res
            res = self._build_workspace(item)
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

    def _claim_specific(self, queue_item_id: str) -> Optional[Dict[str, Any]]:
        q = TranslationQueue()
        with q._conn() as conn:
            row = conn.execute("SELECT * FROM translation_queue WHERE id = ? AND status = 'pending'",
                               (queue_item_id,)).fetchone()
            if not row:
                return None
            now = _now()
            cur = conn.execute(
                "UPDATE translation_queue SET status = 'running', started_at = ?, attempts = attempts + 1 "
                "WHERE id = ? AND status = 'pending'", (now, queue_item_id))
            conn.commit()
            if cur.rowcount != 1:
                return None
            item = dict(row)
            item.update(status="running", started_at=now)
            return item

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
        if not cues:
            msg = f"No source subtitle cues for {project_name}/{episode_name} (extract or import a subtitle first)."
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

{build_style_guide(target_name)}
"""

    # ------------------------------------------------------------------ targeting

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

    def audit_project(self, project_name: str, requeue: bool = False, full: bool = False,
                      misplaced: bool = True, repair: bool = False) -> Dict[str, Any]:
        """Find episodes whose stored translation still has untranslated lines — or, with
        ``misplaced``, lines that carry the translation of another line (a reply that lost its
        place, a subtitle file made for a different cut) — and lines whose text is broken
        (``utils.text_repair``): Greek mojibake, leaked prompt text, wrong-alphabet words.

        Results are saved on each episode (``translation_gaps``) for the UI. With ``repair`` the
        broken lines are fixed in the episode — mojibake decoded, leaks salvaged or cleared, the old
        text kept in ``text_repair_backup.json`` next to it; wrong-alphabet lines are left as they
        are — and the copy on disk follows: an episode Omnisub translated is exported again, an
        imported subtitle file is repaired in place (the original kept in ``disk_backup/``).
        With ``requeue`` (which implies ``repair``) the wrong-alphabet lines are cleared too and the
        incomplete episodes are queued for agent gap-filling; in episodes Omnisub translated itself
        the misplaced lines are cleared first (backed up in ``misplaced_backup.json``) so they are
        translated again. Imported subtitles' misplaced lines are only reported.
        """
        matched = self.find_matching_project(project_name)
        if not matched:
            return {"success": False, "message": f"Project '{project_name}' not found."}
        repair = repair or requeue
        proj_meta = storage.load_project_metadata(matched) or {}
        code = to_code(proj_meta.get("target_language") or "Greek")
        rows, incomplete, wrong, imported = [], [], {}, []
        damaged: List[str] = []
        cleared_eps: List[str] = []
        fixes: Dict[str, Any] = {"repaired_lines": 0, "broken_cleared": 0, "disk_repaired": [], "disk_failed": [],
                                 "reexported": [], "reexport_failed": []}
        for ep in storage.list_episodes(matched):
            cues = storage.load_episode_data(matched, ep) or []
            if not cues:
                continue
            broken = self._broken_cues(cues, code, known_mojibake=self._had_mojibake_repair(matched, ep))
            applied = {"applied": 0, "cleared": 0}
            if repair:
                # Before the gaps are counted: lines cleared here are gaps for the requeue below.
                applied = self._repair_episode(matched, ep, cues, broken, code, requeue, fixes)
                if applied["cleared"]:
                    cleared_eps.append(ep)
            units, stats = aw.build_units(cues, code, True, full=False)
            has_any = stats["already_translated_units"] > 0
            found = self._misplaced_cues(cues, code) if misplaced and has_any else []
            gaps = {"dialogue": stats["dialogue_units"], "signs": stats["sign_units"],
                    "total_lines": stats["distinct_units"], "misplaced": len(found),
                    "broken": len(broken) - applied["applied"], "checked_at": _now()}
            meta = storage.load_episode_metadata(matched, ep) or {}
            if meta.get("translation_gaps") != gaps:
                meta["translation_gaps"] = gaps
                storage.save_episode_metadata(matched, ep, meta, update_stats=False)
            row = {"episode_name": ep, "translated_lines": stats["already_translated_units"],
                   **{k: v for k, v in gaps.items() if k != "broken"}}
            rows.append(row)
            if units and (has_any or meta.get("translated")):
                incomplete.append(ep)
            if broken or found:
                row["imported"] = not self._translated_here(meta)
            if broken:
                counts = Counter(b["kind"] for b in broken)
                row["broken"] = {k: counts[k] for k in text_repair.KINDS if counts[k]}
                row["broken_lines"] = [{"line": b["cue"] + 1, "kind": b["kind"]} for b in broken[:40]]
                damaged.append(ep)
            if found:
                row["misplaced_lines"] = [{"line": m["cue"] + 1, "fits_line": m["belongs_to"] + 1} for m in found[:40]]
                if row["imported"]:
                    imported.append(ep)
                else:
                    wrong[ep] = found
        result = {"success": True, "project_name": matched, "episodes": rows,
                  "incomplete": incomplete, "incomplete_count": len(incomplete),
                  "misplaced": sorted(wrong), "misplaced_count": len(wrong), "misplaced_imported": imported,
                  "broken": damaged, "broken_count": len(damaged)}
        if repair:
            result.update({k: v for k, v in fixes.items() if v or k in ("repaired_lines", "broken_cleared",
                                                                      "disk_repaired", "reexported")})
        if requeue:
            result["cleared_lines"] = sum(self._clear_misplaced(matched, ep, found, code) for ep, found in wrong.items())
            wanted = set(incomplete) | set(wrong) | set(cleared_eps)
            targets = [ep for ep in storage.list_episodes(matched) if ep in wanted]
            if targets:
                result["enqueue"] = self.enqueue_target_project(matched, targets, full=full)
        return result

    def audit_library(self, requeue: bool = False, repair: bool = False, misplaced: bool = True,
                      progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
        """``audit_project`` for every project of the library, keeping only what needs attention.

        ``{"projects": [...], "totals": {...}}``: each project entry lists the episodes with
        untranslated lines, misplaced lines or broken text (with the project's repair, clearing,
        disk and enqueue results when those were asked for); projects with nothing to report are
        left out, a project that fails is recorded as ``{"project_name", "error"}`` and the run
        goes on. ``progress(done, total, project_name)`` is called after each project.
        ``totals.projects`` counts every project checked; ``incomplete``, ``misplaced``,
        ``misplaced_imported`` and ``broken`` count episodes; ``repaired_lines`` the lines fixed and
        ``cleared_lines`` every line cleared for retranslation (misplaced and broken alike,
        ``broken_cleared`` being the broken part of it).
        """
        names = storage.list_projects()
        totals = {"projects": len(names), "episodes_checked": 0, "incomplete": 0, "misplaced": 0,
                  "misplaced_imported": 0, "broken": 0, "broken_lines": 0, "repaired_lines": 0,
                  "cleared_lines": 0, "broken_cleared": 0, "enqueued": 0}
        projects: List[Dict[str, Any]] = []
        for done, name in enumerate(names, start=1):
            try:
                res = self.audit_project(name, requeue=requeue, misplaced=misplaced, repair=repair)
                if not res.get("success"):
                    raise RuntimeError(res.get("message") or "audit failed")
            except Exception as e:
                logger.warning(f"Library audit of '{name}' failed: {e}", exc_info=True)
                projects.append({"project_name": name, "error": str(e)})
            else:
                entry = self._library_entry(res, totals)
                if entry:
                    projects.append(entry)
            if progress:
                try:
                    progress(done, len(names), name)
                except Exception as e:
                    logger.debug(f"Library audit progress callback failed: {e}")
        return {"success": True, "projects": projects, "totals": totals}

    @staticmethod
    def _library_entry(res: Dict[str, Any], totals: Dict[str, int]) -> Optional[Dict[str, Any]]:
        """One project's share of a library audit (added to ``totals``); ``None`` when it has
        nothing to report."""
        attention = set(res["incomplete"]) | set(res["misplaced"]) | set(res["misplaced_imported"]) | set(res["broken"])
        episodes = [r for r in res["episodes"] if r["episode_name"] in attention]
        cleared = (res.get("cleared_lines") or 0) + (res.get("broken_cleared") or 0)
        totals["episodes_checked"] += len(res["episodes"])
        totals["incomplete"] += res["incomplete_count"]
        totals["misplaced"] += res["misplaced_count"]
        totals["misplaced_imported"] += len(res["misplaced_imported"])
        totals["broken"] += res["broken_count"]
        totals["broken_lines"] += sum(sum(r["broken"].values()) for r in episodes if r.get("broken"))
        totals["repaired_lines"] += res.get("repaired_lines") or 0
        totals["cleared_lines"] += cleared
        totals["broken_cleared"] += res.get("broken_cleared") or 0
        totals["enqueued"] += (res.get("enqueue") or {}).get("enqueued_count") or 0
        if not episodes:
            return None
        entry = {"project_name": res["project_name"], "episodes": episodes, "incomplete": res["incomplete"],
                 "misplaced": res["misplaced"], "misplaced_imported": res["misplaced_imported"],
                 "broken": res["broken"]}
        for key in ("enqueue", "repaired_lines", "broken_cleared", "cleared_lines", "disk_repaired", "disk_failed",
                    "reexported", "reexport_failed"):
            if key in res:
                entry[key] = res[key]
        return entry

    @staticmethod
    def _translated_here(meta: Dict[str, Any]) -> bool:
        """Whether Omnisub (an agent, the runner, the model or the editor) wrote the episode's
        translation, as opposed to a subtitle file imported from disk."""
        return bool(meta.get("translation_source") or meta.get("last_translated_at")
                    or meta.get("last_translation_attempt"))

    @staticmethod
    def _misplaced_cues(cues: List[Dict[str, Any]], code: str) -> List[Dict[str, Any]]:
        """Dialogue cues whose translation fits another cue of the episode better:
        [{"cue", "belongs_to", "evidence"}] (cue indexes)."""
        rows = []
        for i, cue in enumerate(cues):
            if not isinstance(cue, dict):
                continue
            meta = cue.get("_ass") if isinstance(cue.get("_ass"), dict) else {}
            src = (cue.get("original") or "").strip()
            if meta.get("kind") == "sign" or meta.get("translatable") is False or not any(ch.isalpha() for ch in src):
                continue
            text = aw.cue_translation(cue, code)
            rows.append((i, src, None if aw.looks_untranslated(src, text, code) else text))
        if sum(1 for r in rows if r[2]) < 4:
            return []
        try:
            from utils import line_alignment
            found = line_alignment.misplaced([r[1] for r in rows], [r[2] for r in rows], code)
        except Exception as e:
            logger.debug(f"misplaced-line check skipped: {e}")
            return []
        return [{"cue": rows[p][0], "belongs_to": rows[v["belongs_to"]][0], "evidence": v["evidence"]}
                for p, v in sorted(found.items())]

    @staticmethod
    def _clear_misplaced(project_name: str, episode_name: str, found: List[Dict[str, Any]], code: str) -> int:
        """Clear lines that carry another line's translation so they are translated again
        (the cleared text is kept in ``misplaced_backup.json`` next to the episode)."""
        cues = storage.load_episode_data(project_name, episode_name) or []
        backup = []
        for m in found:
            i = m["cue"]
            if i >= len(cues) or not isinstance(cues[i], dict):
                continue
            cue = cues[i]
            text = aw.cue_translation(cue, code)
            if not text:
                continue
            backup.append({"line": i + 1, "original": cue.get("original", ""), "translation": text,
                           "fits_line": m["belongs_to"] + 1})
            (cue.get("translations") or {}).pop(code, None)
            if cue.get("translated"):
                cue["translated"] = ""
        if not backup:
            return 0
        path = storage.episode_dir(project_name, episode_name) / "misplaced_backup.json"
        try:
            history = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        except Exception:
            history = []
        history.append({"cleared_at": _now(), "lang": code, "lines": backup})
        path.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
        storage.save_episode(project_name, episode_name, cues)
        logger.info(f"Cleared {len(backup)} misplaced line(s) in {project_name}/{episode_name} for retranslation.")
        return len(backup)

    @staticmethod
    def _broken_cues(cues: List[Dict[str, Any]], code: str, known_mojibake: bool = False) -> List[Dict[str, Any]]:
        """Cues whose target text is broken (``text_repair.repair_translation``):
        [{"cue", "kind", "before", "after"}] (cue indexes), ``after`` being the repaired text, or ""
        for a line that has to be translated again. ``known_mojibake``: an earlier repair found
        mojibake here, so the lines already repaired count as evidence for the leftovers."""
        found: List[Dict[str, Any]] = []
        texts: Dict[int, str] = {}
        hits = 0
        for i, cue in enumerate(cues):
            if not isinstance(cue, dict):
                continue
            text = aw.cue_translation(cue, code)
            if not text:
                continue
            texts[i] = text
            fix = text_repair.repair_translation(text, cue.get("original") or "", code)
            if fix:
                found.append({"cue": i, "kind": fix["kind"], "before": text, "after": fix["text"]})
                hits += fix["kind"] == "mojibake"
        evidence = hits + (sum(1 for t in texts.values() if text_repair.has_greek(t)) if known_mojibake else 0)
        if (evidence and text_repair.is_greek_code(code)
                and text_repair.mojibake_prevails(evidence, sum(text_repair.has_text(t) for t in texts.values()))):
            # Most of the episode is mojibake: its short leftovers ("Τι;" is "Ôé;") are too.
            seen = {f["cue"] for f in found}
            for i, text in texts.items():
                fixed = None if i in seen else text_repair.repair_mojibake(text, relaxed=True)
                if fixed is not None:
                    found.append({"cue": i, "kind": "mojibake", "before": text, "after": fixed})
            found.sort(key=lambda f: f["cue"])
        return found

    @staticmethod
    def _write_translation(cue: Dict[str, Any], code: str, text: str) -> None:
        """Set a cue's translation in ``code`` (the primary language), or remove it for ""; the
        legacy single-translation fields follow."""
        if text:
            translations = cue.get("translations")
            if not isinstance(translations, dict):
                translations = cue["translations"] = {}
            translations[code] = text
            cue["translated"] = text
            if "translation" in cue:
                cue["translation"] = text
        else:
            (cue.get("translations") or {}).pop(code, None)
            if cue.get("translated"):
                cue["translated"] = ""
            cue.pop("translation", None)

    def _repair_episode(self, project_name: str, episode_name: str, cues: List[Dict[str, Any]],
                        broken: List[Dict[str, Any]], code: str, requeue: bool,
                        fixes: Dict[str, Any]) -> Dict[str, int]:
        """Write the repairs for an episode's broken lines into it, then carry them to its copy on
        disk. Wrong-alphabet lines (``RETRANSLATE_KINDS``) are only cleared with ``requeue``. The old
        text goes to ``text_repair_backup.json`` next to the episode first. Returns how many lines
        changed and how many of those were cleared; ``fixes`` collects the project totals."""
        todo = [b for b in broken if requeue or b["kind"] not in text_repair.RETRANSLATE_KINDS]
        meta = storage.load_episode_metadata(project_name, episode_name) or {}
        imported = not self._translated_here(meta)
        cleared = sum(1 for b in todo if not b["after"])
        if todo:
            backup = []
            for b in todo:
                cue = cues[b["cue"]]
                backup.append({"line": b["cue"] + 1, "original": cue.get("original", ""), "before": b["before"],
                               "after": b["after"], "kind": b["kind"]})
                self._write_translation(cue, code, b["after"])
            path = storage.episode_dir(project_name, episode_name) / "text_repair_backup.json"
            try:
                history = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
            except Exception:
                history = []
            history.append({"repaired_at": _now(), "lang": code, "lines": backup})
            path.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
            storage.save_episode(project_name, episode_name, cues, update_stats=False)
            fixes["repaired_lines"] += len(todo) - cleared
            fixes["broken_cleared"] += cleared
            logger.info(f"Repaired {len(todo) - cleared} and cleared {cleared} broken line(s) in "
                        f"{project_name}/{episode_name}.")
            if not imported:
                exported = self._export(project_name, episode_name, cues)
                fixes["reexported" if exported else "reexport_failed"].append(episode_name)
        if imported and (any(b["kind"] == "mojibake" for b in todo)
                         or self._had_mojibake_repair(project_name, episode_name)):
            # The subtitle file is where an imported episode's text comes from. An earlier repair
            # that could not reach it (share offline, file locked) is retried here.
            self._repair_disk_copy(project_name, episode_name, meta, fixes)
        return {"applied": len(todo), "cleared": cleared}

    @staticmethod
    def _had_mojibake_repair(project_name: str, episode_name: str) -> bool:
        path = storage.episode_dir(project_name, episode_name) / "text_repair_backup.json"
        try:
            history = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        except Exception:
            return False
        return any(line.get("kind") == "mojibake" for h in history if isinstance(h, dict)
                   for line in h.get("lines") or [] if isinstance(line, dict))

    @staticmethod
    def _repair_disk_copy(project_name: str, episode_name: str, meta: Dict[str, Any], fixes: Dict[str, Any]) -> None:
        """Repair the mojibake in an imported episode's own target subtitle file, in place (the
        episode is not exported again: its cues may be cut differently from the file's). Only a
        UTF-8 file is touched, and only when ``repair_subtitle_text`` changes something; the original
        is copied to ``disk_backup/`` in the episode folder first (an earlier copy is never
        overwritten) and the file keeps its BOM and line endings."""
        path = meta.get("arr_target_path")
        if not path:
            return
        target = Path(path)
        try:
            if not target.is_file():
                return
            raw = target.read_bytes()
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                return  # legacy-encoded Greek is not mojibake on disk; the readers decode it correctly now
            fixed, lines = text_repair.repair_subtitle_text(text, known=True)
            if not lines:
                return
            backup = storage.episode_dir(project_name, episode_name) / "disk_backup" / target.name
            if not backup.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
            target.write_bytes(bom + fixed.encode("utf-8"))
        except OSError as e:
            logger.warning(f"Could not repair {path} for {project_name}/{episode_name}: {e}")
            fixes["disk_failed"].append({"episode": episode_name, "path": str(path), "error": str(e)})
            return
        logger.info(f"Repaired {lines} mojibake line(s) in {target}.")
        fixes["disk_repaired"].append({"episode": episode_name, "path": str(target), "lines": lines})

    # ------------------------------------------------------------------ parsing / checking

    def parse_agent_translation(self, content: str, expected_count: int) -> List[str]:
        """Flexibly parse translations from SRT, ASS, numbered lines or JSON (legacy outputs)."""
        stripped = (content or "").strip()
        if not stripped:
            return []
        stripped = re.sub(r'^\s*```[a-zA-Z0-9_\-]*\s*\r?\n', '', stripped)
        stripped = re.sub(r'\r?\n\s*```\s*$', '', stripped).strip()
        if not stripped:
            return []

        def fit(res: List[str]) -> List[str]:
            if len(res) == expected_count:
                return res
            if abs(len(res) - expected_count) <= 5:
                return (res + [""] * (expected_count - len(res)))[:expected_count]
            return []

        if "[Events]" in stripped or "[Script Info]" in stripped:
            try:
                from utils.ass_parser import parse_ass
                parsed = parse_ass(stripped)
                if parsed:
                    return fit([c.get("translated", "") or c.get("original", "").strip() for c in parsed])
            except Exception:
                pass
            return []

        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                data = json.loads(stripped)
                lines_data = (data.get("lines") or data.get("translations") or []) if isinstance(data, dict) else data
                res = []
                for it in lines_data if isinstance(lines_data, list) else []:
                    if isinstance(it, str):
                        res.append(it)
                    elif isinstance(it, dict):
                        res.append(str(it.get("t") or it.get("text") or it.get("translation") or ""))
                return fit(res) if res else []
            except Exception:
                return []

        parsed, _ = aw.parse_work_file(stripped)
        non_empty = [l for l in stripped.splitlines() if l.strip()]
        numbered = sum(1 for l in non_empty if aw._NUMBERED.match(l))
        if parsed and (numbered == len(non_empty) or (numbered >= 2 and numbered / max(1, len(non_empty)) >= 0.3)
                       or (numbered >= 1 and len(non_empty) <= 3)):
            max_idx = max(max(parsed), expected_count)
            res = [""] * max_idx
            for idx, text in parsed.items():
                if 1 <= idx <= max_idx:
                    res[idx - 1] = text
            return res[:expected_count] if expected_count > 0 else res

        if "-->" in stripped:
            try:
                from utils.srt_parser import parse_srt
                parsed_cues = parse_srt(stripped)
                if parsed_cues:
                    return fit([c.get("original", "").strip() for c in parsed_cues])
            except Exception:
                pass
            return []

        plain = [l.strip() for l in stripped.splitlines() if l.strip()]
        return (plain + [""] * max(0, expected_count - len(plain)))[:expected_count]

    def _load_task(self, task_id: str):
        folder = self._find_task_folder(task_id)
        if not folder:
            return None, None, None, {"success": False, "status": "not_found", "message": f"Task '{task_id}' not found."}
        manifest = self._read_manifest(folder)
        if manifest.get("plan_version") != aw.PLAN_VERSION or not (folder / aw.PLAN_FILE).is_file():
            return folder, manifest, None, {
                "success": False, "status": "legacy_workspace",
                "message": (f"Task '{task_id}' uses the old workspace format. Release it with "
                            f"`{cli_command('release', task_id)}` and claim it again with `{cli_command('next')}`.")}
        plan = json.loads((folder / aw.PLAN_FILE).read_text(encoding="utf-8"))
        return folder, manifest, plan, None

    def _read_translations(self, folder: Path, units: List[aw.Unit], content: Optional[str]) -> Dict[int, str]:
        """Parse the agent's text: the work file, or a supplied file/content, falling
        back to a legacy output file (SRT/JSON/numbered, one entry per unit)."""
        work_text = content if content is not None else (folder / aw.WORK_FILE).read_text(encoding="utf-8")
        translations, _ = aw.parse_work_file(work_text)
        code_ok = sum(1 for u in units if (translations.get(u.id) or "").strip() and translations[u.id].strip() != u.text.strip())
        if code_ok >= max(1, len(units) // 2):
            return translations
        candidates = [work_text] if content is not None else []
        candidates += [(folder / n).read_text(encoding="utf-8") for n in _LEGACY_OUTPUTS if (folder / n).is_file()]
        for text in candidates:
            lines = self.parse_agent_translation(text, len(units))
            if lines and sum(1 for l in lines if l.strip()) > code_ok:
                return {u.id: lines[i] for i, u in enumerate(units) if i < len(lines)}
        return translations

    def check_task(self, task_id: str, file_path_or_content: Optional[str] = None) -> Dict[str, Any]:
        """Validate a workspace without changing anything."""
        folder, manifest, plan, err = self._load_task(task_id)
        if err:
            return err
        units = aw.units_from_plan(plan)
        content = self._resolve_content(file_path_or_content)
        translations = self._read_translations(folder, units, content)
        proj_meta = storage.load_project_metadata(manifest["project_name"]) or {}
        report = aw.validate(units, translations, manifest.get("target_code") or "el",
                             proj_meta.get("glossary"), manifest.get("target_language", "Greek"),
                             vocab=self._vocab(manifest["project_name"]))
        problems = aw.describe_problems(report, units, translations)
        advisories = aw.describe_advisories(report, units)
        message = problems or f"All {report['total']} lines translated — ready to submit."
        return {
            "success": True,
            "status": "ready" if report["ok"] else "incomplete",
            "task_id": task_id,
            "project_name": manifest["project_name"],
            "episode_name": manifest["episode_name"],
            "report": report,
            "message": message + (f"\n{advisories}" if advisories else ""),
            "new_terms_ready": self._terms_file_count(folder),
            "summary_ready": bool((folder / aw.SUMMARY_FILE).is_file() and (folder / aw.SUMMARY_FILE).read_text(encoding="utf-8").strip()),
        }

    @staticmethod
    def _vocab(project_name: str):
        """Everyday English words of the show (glossary terms that are also everyday words are judged by context)."""
        try:
            from utils.glossary_judgment import project_vocabulary
            return project_vocabulary(project_name)
        except Exception:
            return None

    @staticmethod
    def _terms_file_count(folder: Path) -> int:
        try:
            data = json.loads((folder / aw.TERMS_FILE).read_text(encoding="utf-8") or "[]")
            return len(data) if isinstance(data, (list, dict)) else 0
        except Exception:
            return -1

    @staticmethod
    def _resolve_content(file_path_or_content: Optional[str]) -> Optional[str]:
        if not file_path_or_content:
            return None
        try:
            p = Path(file_path_or_content)
            if len(file_path_or_content) < 1024 and "\n" not in file_path_or_content and p.is_file():
                return p.read_text(encoding="utf-8-sig")
        except OSError:
            pass
        return file_path_or_content if file_path_or_content.strip() else None

    # ------------------------------------------------------------------ ingestion helpers

    def _extract_context_guide(self, raw_text: str, project_name: str) -> Optional[str]:
        """Substantive context-guide text, without the template header/comments/placeholders."""
        if not raw_text or not raw_text.strip():
            return None
        cleaned = re.sub(r'<!--.*?-->', '', raw_text, flags=re.DOTALL).strip()
        cleaned = re.sub(rf"^#\s*Context\s+Guide:\s*{re.escape(project_name)}\s*", '', cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r'\*\*Show\*\*:[^\n]*\n?', '', cleaned)
        cleaned = re.sub(r'\*\*Target Language\*\*:[^\n]*\n?', '', cleaned)
        cleaned = re.sub(r'\*\*Series Type\*\*:[^\n]*\n?', '', cleaned)
        cleaned = cleaned.strip()
        return cleaned if len(cleaned) > 15 else None

    def _normalize_harvested_terms(self, terms_raw: Any, target_code: str = "el") -> List[Dict[str, Any]]:
        """Normalize harvested terms from lists, dicts or wrappers; Greek tonos fixes for names."""
        if isinstance(terms_raw, str):
            try:
                terms_raw = json.loads(terms_raw)
            except Exception:
                return []
        if isinstance(terms_raw, dict):
            if isinstance(terms_raw.get("discovered_terms"), list):
                terms_list = terms_raw["discovered_terms"]
            elif isinstance(terms_raw.get("terms"), list):
                terms_list = terms_raw["terms"]
            else:
                terms_list = [{"term": k, "translation": v} for k, v in terms_raw.items() if isinstance(v, str)]
        elif isinstance(terms_raw, list):
            terms_list = terms_raw
        else:
            return []

        normalized: List[Dict[str, Any]] = []
        for item in terms_list:
            if not isinstance(item, dict):
                continue
            term = (item.get("term") or item.get("source") or item.get("en") or "").strip()
            translation = (item.get("translation") or item.get("target") or item.get("el") or "").strip()
            if not term or not translation:
                continue
            term_type = item.get("type", "general")
            if target_code == "el":
                translation = sanitize_translation_text(translation, source_text=term, target_lang_code="el")
                if term_type in ("character", "person", "name") or len(term.split()) <= 2:
                    fixed = fix_japanese_name_greek_tonos(translation)
                    if fixed:
                        translation = fixed
            entry: Dict[str, Any] = {"term": term, "translation": translation, "type": term_type}
            for key in ("gender", "notes", "sense"):
                if item.get(key):
                    entry[key] = item[key]
            if not entry.get("sense") and item.get("used_as"):
                entry["sense"] = item["used_as"]
            if item.get("update") or item.get("replace"):
                entry["_update"] = True
            normalized.append(entry)
        return normalized

    def _merge_terms(self, project_name: str, terms_raw: Any, target_code: str) -> Dict[str, Any]:
        """Add new glossary terms; existing terms change only when flagged ``update``."""
        normalized = self._normalize_harvested_terms(terms_raw, target_code=target_code)
        if not normalized:
            return {"added": 0, "updated": 0, "conflicts": [], "skipped": 0}
        from utils.glossary_judgment import accept_new_term
        vocab = self._vocab(project_name)
        proj_meta = storage.load_project_metadata(project_name) or {}
        terms = proj_meta.setdefault("glossary", {}).setdefault("terms", [])
        by_key = {(t.get("term") or t.get("source") or "").strip().lower(): t for t in terms if isinstance(t, dict)}
        added, updated, conflicts, skipped = 0, 0, [], 0
        for t in normalized:
            key = t["term"].lower()
            wants_update = t.pop("_update", False)
            existing = by_key.get(key)
            if existing is None and not accept_new_term(t, vocab):
                skipped += 1  # an everyday word with a one-scene rendering ("petal", "Hold") is not a term
                continue
            if existing is None:
                terms.append(t)
                by_key[key] = t
                added += 1
            elif (existing.get("translation") or "").strip() != t["translation"]:
                if wants_update:
                    existing.update(t)
                    updated += 1
                else:
                    conflicts.append({"term": t["term"], "kept": existing.get("translation"), "proposed": t["translation"]})
        if added or updated:
            try:
                from utils.cache_manager import invalidate_cache
                invalidate_cache(proj_meta)
            except Exception:
                pass
            storage.save_project_metadata(project_name, proj_meta)
        return {"added": added, "updated": updated, "conflicts": conflicts, "skipped": skipped}

    @staticmethod
    def _export(project_name: str, episode_name: str, cues: List[Dict[str, Any]]) -> Optional[str]:
        try:
            from services.translation.export_formatter import auto_export_translated_subtitle
            ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}
            exported = auto_export_translated_subtitle(project_name, episode_name, cues, ep_meta)
            if exported:
                ep_meta = storage.load_episode_metadata(project_name, episode_name) or ep_meta
                ep_meta["arr_translated_from_fingerprint"] = ep_meta.get("arr_sub_fingerprint")
                storage.save_episode_metadata(project_name, episode_name, ep_meta, update_stats=False)
            return exported
        except Exception as e:
            logger.warning(f"Export failed for {project_name}/{episode_name}: {e}")
            return None

    # ------------------------------------------------------------------ submit

    def submit_task_result(
        self,
        task_id: str,
        file_path_or_content: Optional[str] = None,
        context_guide: Optional[str] = None,
        new_terms: Optional[Any] = None,
        episode_summary: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Validate and ingest an agent's translation, then post-process and export.

        Nothing is written unless the work file passes validation (every line
        translated, no more than 1% still in the source language) — ``force``
        accepts it anyway. Resubmitting a completed task is a no-op.
        """
        folder, manifest, plan, err = self._load_task(task_id)
        if err:
            return err
        project_name, episode_name = manifest["project_name"], manifest["episode_name"]
        queue_id = manifest.get("queue_id")
        target_name = manifest.get("target_language", "Greek")
        target_code = manifest.get("target_code") or to_code(target_name)
        is_primary = manifest.get("is_primary", True)
        if manifest.get("status") == "completed":
            return {"success": True, "status": "already_completed", "task_id": task_id,
                    "project_name": project_name, "episode_name": episode_name,
                    "message": f"Task '{task_id}' was already submitted."}
        if manifest.get("status") in ("cancelled", "expired"):
            return {"success": False, "status": manifest["status"], "task_id": task_id,
                    "message": f"Task '{task_id}' was {manifest['status']}; claim the episode again with `{cli_command('next')}`."}

        units = aw.units_from_plan(plan)
        translations = self._read_translations(folder, units, self._resolve_content(file_path_or_content))
        proj_meta = storage.load_project_metadata(project_name) or {}
        report = aw.validate(units, translations, target_code, proj_meta.get("glossary"), target_name,
                             vocab=self._vocab(project_name))
        if not report["ok"] and not force:
            return {"success": False, "status": "validation_failed", "task_id": task_id,
                    "project_name": project_name, "episode_name": episode_name, "report": report,
                    "message": aw.describe_problems(report, units, translations)
                    + f"\nFix these lines in {aw.WORK_FILE} and submit again"
                    + " (`--force` accepts them as they are)."}

        cues = storage.load_episode_data(project_name, episode_name) or []
        from utils.ass_parser import refresh_passthrough
        refresh_passthrough(cues)
        if not cues or aw.source_fingerprint(cues, units) != plan.get("source_fingerprint"):
            return {"success": False, "status": "episode_changed", "task_id": task_id,
                    "message": (f"The source subtitle of {project_name} {episode_name} changed since this task was "
                                f"claimed. Release it (`{cli_command('release', task_id)}`) and claim it again.")}

        for i, cue in enumerate(cues):
            # Hand-made/imported cue lists may lack the fields the exporters index by.
            cue.setdefault("id", str(cue.get("index", i + 1)))
            if "timecode" not in cue:
                cue["timecode"] = f"{cue.get('start', '00:00:00,000')} --> {cue.get('end', '00:00:00,000')}"
        written = aw.apply_translations(cues, units, translations, target_code, is_primary)
        storage.save_episode(project_name, episode_name, cues)

        # Translation Memory: one pair per unit that really is translated.
        tm_pairs = 0
        try:
            if storage.load_global_config().get("tm_enabled", True):
                from utils.translation_memory import TranslationMemory
                good = [u for u in units if not aw.looks_untranslated(u.text, translations.get(u.id, ""), target_code)]
                if good:
                    TranslationMemory(project_name, lang_code=target_code).add_translations(
                        source_lines=[u.text for u in good],
                        target_lines=[translations[u.id].strip() for u in good],
                        episode_name=episode_name,
                    )
                    tm_pairs = len(good)
        except Exception as e:
            logger.warning(f"Failed to harvest Translation Memory: {e}")

        # Context guide
        context_guide_updated = False
        guide = context_guide or self._extract_context_guide(
            (folder / aw.GUIDE_FILE).read_text(encoding="utf-8") if (folder / aw.GUIDE_FILE).is_file() else "", project_name)
        proj_meta = storage.load_project_metadata(project_name) or {}
        if guide and guide.strip() != (proj_meta.get("context_guide") or "").strip():
            proj_meta["context_guide"] = guide.strip()
            try:
                from utils.cache_manager import invalidate_cache
                invalidate_cache(proj_meta)
            except Exception:
                pass
            storage.save_project_metadata(project_name, proj_meta)
            context_guide_updated = True

        # Glossary
        terms_raw = new_terms
        if not terms_raw and (folder / aw.TERMS_FILE).is_file():
            try:
                terms_raw = json.loads((folder / aw.TERMS_FILE).read_text(encoding="utf-8") or "[]")
            except Exception as e:
                logger.warning(f"Could not parse {aw.TERMS_FILE} in {folder}: {e}")
        terms = self._merge_terms(project_name, terms_raw, target_code) if terms_raw else {"added": 0, "updated": 0, "conflicts": []}

        # Episode summary
        summary_saved = False
        summary = (episode_summary or "").strip()
        if not summary and (folder / aw.SUMMARY_FILE).is_file():
            summary = (folder / aw.SUMMARY_FILE).read_text(encoding="utf-8").strip()
        if summary:
            try:
                EpisodeSummaryManager(project_name).save_summary(episode_name, summary)
                summary_saved = True
            except Exception as e:
                logger.warning(f"Failed to save episode summary: {e}")

        # Export (primary language: next to the video, named after the current video file)
        exported_path = self._export(project_name, episode_name, cues) if is_primary else None

        ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}
        ep_meta.update(translated=True, translation_status="completed", last_translated_at=_now(),
                       translation_source="agent", agent_task_id=task_id)
        remaining = aw.build_units(cues, target_code, is_primary, full=False)[1]
        ep_meta["translation_gaps"] = {"dialogue": remaining["dialogue_units"], "signs": remaining["sign_units"],
                                       "total_lines": remaining["distinct_units"], "checked_at": _now()}
        storage.save_episode_metadata(project_name, episode_name, ep_meta)

        log = [f"Agent {task_id}: {report['total'] - len(report['missing'])}/{report['total']} lines translated ({written} cues).",
               f"Translation Memory: {tm_pairs} pairs."]
        if terms["added"] or terms["updated"]:
            log.append(f"Glossary: +{terms['added']} new, {terms['updated']} updated.")
        if context_guide_updated:
            log.append("Context guide updated.")
        if summary_saved:
            log.append("Episode summary saved.")
        log.append(f"Exported to {exported_path}." if exported_path else "Not exported (no reachable media folder).")
        if queue_id:
            TranslationQueue().update_status(queue_id, status="completed", completed_at=_now(), logs=log)
            self._set_item_options(queue_id, agent_task_id=None)
        self._complete_jobs(project_name, episode_name, queue_id)

        manifest.update(status="completed", completed_at=_now(), translated_units=report["total"] - len(report["missing"]),
                        translated_cues=written, exported_media_path=exported_path,
                        harvested_terms_count=terms["added"], context_guide_updated=context_guide_updated,
                        episode_summary_saved=summary_saved, tm_pairs_saved=tm_pairs)
        self._write_manifest(folder, manifest)

        result = {
            "success": True,
            "status": "completed",
            "task_id": task_id,
            "queue_id": queue_id,
            "project_name": project_name,
            "episode_name": episode_name,
            "target_language": target_name,
            "units_translated": report["total"] - len(report["missing"]),
            "units": report["total"],
            "cues_translated": written,
            "total_cues": len(cues),
            "exported_path": exported_path,
            "harvested_terms_count": terms["added"],
            "glossary_updated_terms": terms["updated"],
            "glossary_conflicts": terms["conflicts"],
            "glossary_warnings": report["glossary_warnings"],
            "context_guide_updated": context_guide_updated,
            "episode_summary_saved": summary_saved,
            "tm_pairs_saved": tm_pairs,
            "next_command": cli_command("next"),
            "message": f"Ingested {report['total'] - len(report['missing'])}/{report['total']} lines ({written} cues)"
                       + (f"; exported to {exported_path}." if exported_path else "; not exported (media folder unreachable)."),
        }
        self._broadcast("agent_submission", {k: result[k] for k in (
            "task_id", "project_name", "episode_name", "queue_id", "status", "cues_translated", "total_cues", "exported_path")})
        self._broadcast("queue_updated", {"reason": "agent_submission", "queue_id": queue_id,
                                          "project_name": project_name, "episode_name": episode_name, "status": "completed"})
        logger.info(f"Agent task '{task_id}' ingested for {project_name}/{episode_name}.")
        return result

    @staticmethod
    def _complete_jobs(project_name: str, episode_name: str, queue_id: Optional[str]) -> None:
        try:
            from utils.jobs_manager import jobs, update_job
            for j in list(jobs.values()):
                same = (queue_id and j.id == f"bg_{queue_id}") or (j.project_name == project_name and j.episode_name == episode_name)
                if same and j.status in ("running", "pending"):
                    update_job(j.id, status="completed", progress=100.0, message="Translation completed.")
        except Exception:
            pass

    # ------------------------------------------------------------------ completion from elsewhere

    def mark_task_completed(
        self,
        project_name: str,
        episode_name: str,
        queue_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> int:
        """Complete queue items for an episode that got its translation some other way
        (editor save, import, a subtitle found on disk).

        With ``queue_id`` only that item is completed. Otherwise explicit requests
        (items with a ``mode``) and items an agent is working on are left alone —
        a Greek file on disk does not mean the agent's gap-fill is done. Returns the
        number of items completed.
        """
        q = TranslationQueue()
        now = _now()
        completed = 0
        with q._conn() as conn:
            if queue_id:
                cur = conn.execute(
                    f"UPDATE translation_queue SET status = 'completed', completed_at = COALESCE(completed_at, ?) "
                    f"WHERE id = ? AND status IN ({','.join('?' * len(_ACTIVE))})", (now, queue_id, *_ACTIVE))
                completed = cur.rowcount
            else:
                rows = conn.execute(
                    f"SELECT id, status, options FROM translation_queue WHERE project_name = ? AND episode_name = ? "
                    f"AND kind = ? AND status IN ({','.join('?' * len(_ACTIVE))})",
                    (project_name, episode_name, KIND_TRANSLATION, *_ACTIVE)).fetchall()
                for r in rows:
                    opts = _options(r)
                    if opts.get("mode") or opts.get("agent_task_id") or opts.get("force"):
                        continue
                    conn.execute("UPDATE translation_queue SET status = 'completed', completed_at = COALESCE(completed_at, ?) WHERE id = ?",
                                 (now, r["id"]))
                    completed += 1
            conn.commit()
        if completed:
            self._complete_jobs(project_name, episode_name, queue_id)
        if task_id:
            folder = self._find_task_folder(task_id)
            if folder:
                manifest = self._read_manifest(folder)
                if manifest and manifest.get("status") != "completed":
                    manifest.update(status="completed", completed_at=manifest.get("completed_at") or now)
                    self._write_manifest(folder, manifest)
        return completed

    def reconcile_queue_with_storage(self) -> int:
        """Complete pending items (not explicit agent/user requests) whose episode already
        has its target subtitle."""
        q = TranslationQueue()
        with q._conn() as conn:
            rows = conn.execute("""
                SELECT id, project_name, episode_name, lang, options FROM translation_queue
                WHERE status IN ('pending', 'paused_daily_limit', 'paused') AND kind = ?
            """, (KIND_TRANSLATION,)).fetchall()
        done = 0
        for r in rows:
            opts = _options(r)
            if opts.get("mode") or opts.get("force") or opts.get("translation_type") == "original":
                continue
            lang_code = to_code(r["lang"]) if r["lang"] else None
            ep_meta = storage.load_episode_metadata(r["project_name"], r["episode_name"]) or {}
            if storage.episode_has_target(ep_meta, lang_code=lang_code) and not storage.episode_translation_is_stale(ep_meta, lang_code):
                done += self.mark_task_completed(r["project_name"], r["episode_name"], queue_id=r["id"])
        if done:
            self._broadcast("queue_updated", {"reason": "storage_reconciliation", "reconciled_count": done})
        return done

    # ------------------------------------------------------------------ housekeeping

    def release_stale_claims(self) -> List[Dict[str, Any]]:
        """Return abandoned agent claims to the queue (workspace gone or idle past the TTL)."""
        ttl_s = self._claim_ttl_hours() * 3600
        q = TranslationQueue()
        with q._conn() as conn:
            rows = conn.execute("""
                SELECT id, project_name, episode_name, started_at, options FROM translation_queue
                WHERE status = 'running' AND kind = ?
                  AND COALESCE(json_extract(CASE WHEN json_valid(options) THEN options END, '$.agent_task_id'), '') != ''
            """, (KIND_TRANSLATION,)).fetchall()
        released = []
        now = time.time()
        for r in rows:
            task_id = _options(r).get("agent_task_id")
            folder = self._find_task_folder(task_id)
            if folder:
                manifest = self._read_manifest(folder)
                if manifest.get("status") == "completed":
                    self.mark_task_completed(r["project_name"], r["episode_name"], queue_id=r["id"])
                    self._set_item_options(r["id"], agent_task_id=None)
                    continue
                activity = max((p.stat().st_mtime for p in folder.iterdir() if p.is_file()), default=0)
                if now - activity < ttl_s:
                    continue
                manifest.update(status="expired", expired_at=_now())
                self._write_manifest(folder, manifest)
            self._release(r["id"], "workspace missing" if not folder else "idle past claim TTL")
            released.append({"task_id": task_id, "project_name": r["project_name"], "episode_name": r["episode_name"]})
        if released:
            self._broadcast("queue_updated", {"reason": "agent_claims_released", "count": len(released)})
        return released

    def housekeeping(self) -> List[Dict[str, Any]]:
        """Cheap periodic upkeep: release abandoned claims. Never ingests anything —
        a translation only enters the project through an explicit submit."""
        try:
            return self.release_stale_claims()
        except Exception as e:
            logger.debug(f"Agent housekeeping failed: {e}")
            return []

    def detect_and_finish_completed_tasks(self) -> List[Dict[str, Any]]:
        """Back-compat name for :meth:`housekeeping` (auto-ingest was removed: it could
        swallow a work file the agent was still writing)."""
        return self.housekeeping()

    # ------------------------------------------------------------------ cancel / release / clear

    def release_agent_task(self, task_id: str) -> bool:
        """Give a claimed episode back to the queue (e.g. the session is ending)."""
        folder = self._find_task_folder(task_id)
        manifest = self._read_manifest(folder) if folder else {}
        queue_id = manifest.get("queue_id")
        if not folder or not queue_id:
            return False
        if manifest.get("status") != "completed":
            manifest.update(status="released", released_at=_now())
            self._write_manifest(folder, manifest)
            self._release(queue_id, "released by agent")
            self._broadcast("queue_updated", {"reason": "agent_released", "queue_id": queue_id})
        return True

    def cancel_agent_task(self, task_id: str) -> bool:
        """Cancel a task: its workspace is closed and the queue item cancelled."""
        folder = self._find_task_folder(task_id)
        if not folder:
            return False
        manifest = self._read_manifest(folder)
        if manifest.get("status") != "completed":
            manifest["status"] = "cancelled"
            self._write_manifest(folder, manifest)
            if manifest.get("queue_id"):
                TranslationQueue().cancel(manifest["queue_id"])
                self._set_item_options(manifest["queue_id"], agent_task_id=None)
        self._broadcast("queue_updated", {"reason": "agent_cancelled", "task_id": task_id})
        return True

    def cancel_all_agent_tasks(self, status: Optional[str] = None) -> int:
        """Cancel every open (prepared) agent task."""
        cancelled = 0
        for d in self._candidate_dirs():
            for folder in list(d.iterdir()):
                if folder.is_dir() and folder.name.startswith("task_"):
                    if self._read_manifest(folder).get("status", "prepared") == "prepared" and self.cancel_agent_task(folder.name):
                        cancelled += 1
        return cancelled

    def clear_agent_tasks(self, status: Optional[str] = None) -> int:
        """Delete workspace folders. ``status``: all | completed | cancelled | expired |
        released | finished (every closed task). Open claims are only removed by ``all``."""
        status = status or "finished"
        closed = {"completed", "cancelled", "expired", "released"}
        cleared = 0
        for d in self._candidate_dirs():
            for folder in list(d.iterdir()):
                if not (folder.is_dir() and folder.name.startswith("task_")):
                    continue
                st = self._read_manifest(folder).get("status", "prepared")
                if status == "all" or (status == "finished" and st in closed) or st == status:
                    try:
                        shutil.rmtree(folder)
                        cleared += 1
                    except Exception as e:
                        logger.debug(f"Could not remove {folder}: {e}")
        return cleared


# Singleton instance
agent_task_service = AgentTaskService()
