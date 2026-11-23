"""Reading an agent's work file, checking it and ingesting it: validation, Script Guard, Translation Memory, summary, export, queue completion.

Part of the agent task service (services/agent_task_service.py): ``AgentTaskService`` inherits this mixin.
Moved here unchanged from that module; ``self`` is the service.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils import storage
from utils.translation_queue import TranslationQueue, KIND_TRANSLATION
from utils.language_codes import to_code
from utils.episode_summaries import EpisodeSummaryManager
from services import agent_workspace as aw
from services.agent_tasks.common import logger, _LEGACY_OUTPUTS, _ACTIVE, _now, _options, cli_command


class SubmissionMixin:
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
        content = self._resolve_content(file_path_or_content, allow_file_path=True, allowed_dir=folder)
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
    def _resolve_content(
        file_path_or_content: Optional[str],
        *,
        allow_file_path: bool = False,
        allowed_dir: Optional[Any] = None,
    ) -> Optional[str]:
        if not file_path_or_content or not isinstance(file_path_or_content, str):
            return None
        if not allow_file_path:
            return file_path_or_content if file_path_or_content.strip() else None
        try:
            if len(file_path_or_content) < 1024 and "\n" not in file_path_or_content:
                p = Path(file_path_or_content)
                if p.is_file():
                    real_p = p.resolve()
                    name_lower = real_p.name.lower()
                    if name_lower in ("config.json", ".env") or name_lower.endswith((".db", ".env", ".sqlite", ".key", ".pem")):
                        return file_path_or_content if file_path_or_content.strip() else None

                    import tempfile
                    allowed = False
                    if allowed_dir:
                        try:
                            if real_p.is_relative_to(Path(allowed_dir).resolve()):
                                allowed = True
                        except Exception:
                            pass
                    else:
                        try:
                            temp_dir = Path(tempfile.gettempdir()).resolve()
                            if real_p.is_relative_to(temp_dir):
                                allowed = True
                        except Exception:
                            pass
                        if not allowed:
                            try:
                                from utils import storage
                                proj_dir = Path(storage.PROJECTS_DIR).resolve()
                                if real_p.is_relative_to(proj_dir) and "agent_workspace" in real_p.parts:
                                    allowed = True
                            except Exception:
                                pass

                    if allowed:
                        return real_p.read_text(encoding="utf-8-sig")
        except OSError:
            pass
        return file_path_or_content if file_path_or_content.strip() else None

    @staticmethod
    def _export(project_name: str, episode_name: str, cues: List[Dict[str, Any]], overwrite: bool = False) -> Optional[str]:
        """Write the subtitle next to the video. ``overwrite``: the person asked to replace the file there even if
        Omnisub did not write it (otherwise a foreign subtitle is protected)."""
        try:
            from services.translation.export_formatter import auto_export_translated_subtitle
            ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}
            exported = auto_export_translated_subtitle(project_name, episode_name, cues, ep_meta, force=overwrite)
            if exported:
                ep_meta = storage.load_episode_metadata(project_name, episode_name) or ep_meta
                ep_meta["arr_translated_from_fingerprint"] = ep_meta.get("arr_sub_fingerprint")
                storage.save_episode_metadata(project_name, episode_name, ep_meta, update_stats=False)
            return exported
        except Exception as e:
            logger.warning(f"Export failed for {project_name}/{episode_name}: {e}")
            return None

    @staticmethod
    def _why_not_exported(project_name: str, episode_name: str, is_primary: bool) -> str:
        """Why an ingested translation was not written next to the video, for the log and the result."""
        if not is_primary:
            return "not exported: only the main language is written next to the video"
        meta = storage.load_episode_metadata(project_name, episode_name) or {}
        warning = str(meta.get("translation_warning") or "")
        if warning.startswith("Not exported:"):
            return ("not exported: a subtitle Omnisub did not write is already next to the video "
                    "(Translate with “Replace the subtitle file next to the video” to replace it)")
        if warning.startswith("Export scheduled for retry"):
            return "not exported yet: the media folder could not be written; it is tried again"
        if not meta.get("arr_media_path"):
            return "not exported: no video file is linked to this episode"
        return "not exported (media folder unreachable)"

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
        overwrite = False
        if queue_id:
            row = TranslationQueue().get_item(queue_id)
            overwrite = bool(row and _options(row).get("overwrite"))
            if row is None or row.get("status") == "cancelled":
                # Cancelled (or removed with "Clear the queue") while it was being translated: nothing is saved,
                # and the row is not brought back as completed.
                manifest.update(status="cancelled", cancelled_at=_now())
                self._write_manifest(folder, manifest)
                return {"success": False, "status": "cancelled", "task_id": task_id,
                        "project_name": project_name, "episode_name": episode_name,
                        "message": f"{project_name} {episode_name} was cancelled while it was being translated; "
                                   f"nothing was saved."}

        units = aw.units_from_plan(plan)
        translations = self._read_translations(folder, units, self._resolve_content(file_path_or_content, allow_file_path=True, allowed_dir=folder))
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
        if not aw.plan_matches(cues, plan):
            return {"success": False, "status": "episode_changed", "task_id": task_id,
                    "message": (f"The source subtitle of {project_name} {episode_name} changed since this task was "
                                f"claimed. Release it (`{cli_command('release', task_id)}`) and claim it again.")}

        for i, cue in enumerate(cues):
            # Hand-made/imported cue lists may lack the fields the exporters index by.
            cue.setdefault("id", str(cue.get("index", i + 1)))
            if "timecode" not in cue:
                cue["timecode"] = f"{cue.get('start', '00:00:00,000')} --> {cue.get('end', '00:00:00,000')}"
        written = aw.apply_translations(cues, units, translations, target_code, is_primary)
        reviewed = aw.apply_review(cues, units, folder)
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

        # Song lyrics memory: store translated lyrics for reuse across episodes
        try:
            from utils.ass_parser import record_song_lyrics_memory
            pmeta = storage.load_project_metadata(project_name) or {}
            if record_song_lyrics_memory(cues, pmeta, target_code):
                storage.save_project_metadata(project_name, pmeta)
        except Exception as e:
            logger.warning(f"Failed to record song lyrics memory: {e}")

        # Context guide
        context_guide_updated = False
        guide = context_guide or self._extract_context_guide(
            (folder / aw.GUIDE_FILE).read_text(encoding="utf-8") if (folder / aw.GUIDE_FILE).is_file() else "", project_name)
        if guide:
            context_guide_updated = self.save_context_guide(project_name, guide)

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
        exported_path = None
        if is_primary:
            exported_path = (self._export(project_name, episode_name, cues, overwrite=True) if overwrite
                             else self._export(project_name, episode_name, cues))
        export_note = None if exported_path else self._why_not_exported(project_name, episode_name, is_primary)

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
        if reviewed["fixes"] or reviewed["flags"]:
            log.append(f"Review: {reviewed['fixes']} line(s) corrected, {reviewed['flags']} flagged for a person.")
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
            "export_note": export_note,
            "harvested_terms_count": terms["added"],
            "review_fixes": reviewed["fixes"],
            "review_flags": reviewed["flags"],
            "glossary_updated_terms": terms["updated"],
            "glossary_conflicts": terms["conflicts"],
            "glossary_warnings": report["glossary_warnings"],
            "context_guide_updated": context_guide_updated,
            "episode_summary_saved": summary_saved,
            "tm_pairs_saved": tm_pairs,
            "next_command": cli_command("next"),
            "message": f"Ingested {report['total'] - len(report['missing'])}/{report['total']} lines ({written} cues)"
                       + (f"; exported to {exported_path}." if exported_path else f"; {export_note}."),
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
