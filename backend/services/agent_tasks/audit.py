"""The translation audit of a project or the library: gaps, lines on the wrong cue, broken text, and their repair.

Part of the agent task service (services/agent_task_service.py): ``AgentTaskService`` inherits this mixin.
Moved here unchanged from that module; ``self`` is the service.
"""

import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from utils import storage, text_repair
from utils.language_codes import to_code
from services import agent_workspace as aw
from services.agent_tasks.common import logger, _now


class AuditMixin:
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
