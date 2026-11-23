"""Translating one claimed episode: the show's bootstrap, the parts with their fix rounds, the extras a reply carries, and the submit.

Part of the agent runner (services/agent_runner.py): ``AgentRunner`` inherits this mixin. Moved here unchanged
from that module; ``self`` is the runner.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from utils import storage
from utils.translation_queue import TranslationQueue
from services import agent_prompts as ap, agent_workspace as aw
from services.runner.common import (
    MAX_AROUND,
    GUIDE_GROWTH_EVERY,
    GUIDE_ADDITIONS_FILE,
    BOOTSTRAP_TIMEOUT,
    BOOTSTRAP_STATE,
    logger,
    _human,
    _now_iso,
)


class EpisodeMixin:
    def _episode(self, lane: int, task: Dict, model: str) -> None:
        folder = Path(task["workspace_dir"])
        manifest = self.service._read_manifest(folder)
        project = manifest.get("project_name", task["project_name"])
        episode = manifest.get("episode_name", task["episode_name"])
        title = f"{project} — {episode}"
        if not self._still_mine(folder, lane, title):
            return
        if not self.service.workspace_current(folder):
            # The episode was re-imported, compacted or edited since the claim: the plan's cue positions
            # are stale. Give it back; the next claim plans it again from the episode as it is now
            # (gap mode keeps whatever is already translated).
            self.service.release_agent_task(task["task_id"])
            self.log(f"Lane {lane + 1}: {title} changed since it was claimed; put back in the queue to be "
                     f"planned again.", "warn")
            return
        plan = json.loads((folder / aw.PLAN_FILE).read_text(encoding="utf-8"))
        units = aw.units_from_plan(plan)
        by_id = {u.id: u for u in units}
        target_name = manifest.get("target_language", "Greek")
        code = manifest.get("target_code") or "el"
        work = folder / aw.WORK_FILE
        whole = not (plan.get("stats") or {}).get("already_translated_units")
        self._bootstrap(lane, task, model, project, episode)
        if self.stop_event.is_set():
            return
        ctx = self._context(project, episode, target_name, units, whole, code, manifest.get("is_primary", True))
        if ctx["grow"]:
            (folder / GUIDE_ADDITIONS_FILE).touch()
        self._lane_set(lane, state="working", task=task["task_id"], project=project, episode=episode, model=model,
                       progress=None, units_done=0, units_total=0, since=_now_iso())

        def current() -> Dict[int, str]:
            return aw.parse_work_file(work.read_text(encoding="utf-8"))[0]

        def needs(u: aw.Unit, cur: Dict[int, str]) -> bool:
            return aw.looks_untranslated(u.text, cur.get(u.id, ""), code)

        def progress(cur: Dict[int, str]) -> None:
            done = sum(1 for u in units if not needs(u, cur))
            self._lane_set(lane, progress=f"{done}/{len(units)}", units_done=done, units_total=len(units))

        cur = current()
        todo = [u for u in units if needs(u, cur)]
        progress(cur)
        chunks = self._chunks(todo, int(self.config["chunk_lines"] or 250))
        resumed = len(todo) < len(units)
        self.log(f"Lane {lane + 1}: {title} — {len(todo)} line(s){f' of {len(units)} (resumed)' if resumed else ''}"
                 f"{f' in {len(chunks)} parts' if len(chunks) > 1 else ''}, {model or 'default model'}"
                 f"{'' if whole else ' (filling in an already translated episode)'}.")
        started = time.time()
        tokens = [0, 0]
        # Lines the filter refuses are set aside here and sent on their own at the end, when there is
        # somewhere to send them (else a refusal is narrowed down on the spot, as before).
        refused: Optional[set] = set() if self._rescue_target(project, model) else None
        for k, chunk in enumerate(chunks):
            if self.stop_event.is_set():
                return
            part = f"part {k + 1} of {len(chunks)}" if len(chunks) > 1 else ""
            ok = self._chunk(lane, model, folder, title, ctx, units, by_id, chunk, code, needs, current,
                             part=part, first_part=k == 0, last=k == len(chunks) - 1, tokens=tokens,
                             project=project, episode=episode, refused=refused)
            if not ok:
                return  # stopped, cancelled, or the model is paused: the episode stays claimed and resumes later
            progress(current())
        if self.stop_event.is_set():
            return
        if refused:
            if not self._rescue(lane, model, folder, title, ctx, units, by_id, refused, code, needs, current,
                                tokens=tokens, project=project, episode=episode):
                return
            progress(current())
            if self.stop_event.is_set():
                return
        if not self._review(lane, model, folder, title, ctx, units, code, needs, current, tokens=tokens,
                            project=project, episode=episode):
            return  # stopped or cancelled during the review: the episode stays claimed and resumes later
        self._submit(lane, task, folder, units, current, needs, started, tokens, whole=whole)

    def _context_requested(self, task: Dict) -> bool:
        """The queue item asks for the show's context to be built first (``options.ensure_context``)."""
        try:
            from services.agent_task_service import _options
            item = TranslationQueue().get_item(task.get("queue_id") or "")
            return bool(item) and bool(_options(item).get("ensure_context"))
        except Exception:
            return False

    def _bootstrap(self, lane: int, task: Dict, model: str, project: str, episode: str) -> None:
        """A show without a context guide or without glossary terms gets both, from one request, before its first
        episode is translated (``bootstrap`` setting; an item with ``ensure_context`` forces the check, even
        right after a failure). It never stops the translation: when it fails the episode goes on without."""
        from services import agy_llm, project_bootstrap as pb
        forced = self._context_requested(task)
        if not (self.config["bootstrap"] or forced):
            return
        try:
            if not pb.needs_bootstrap(storage.load_resolved_project_metadata(project) or {}, forced):
                return
        except Exception:
            return
        self._lane_set(lane, state=BOOTSTRAP_STATE, task=task["task_id"], project=project,
                       episode=episode, model=model, progress=None, units_done=0, units_total=0, since=_now_iso())

        def ask(prompt: str) -> str:
            return agy_llm.ask(prompt, purpose="bootstrap", project=project, timeout=BOOTSTRAP_TIMEOUT,
                               stop=self.stop_event, on_proc=lambda p: self.procs.__setitem__(lane, p), runner=self)

        started = time.time()
        try:
            res = pb.bootstrap_project(project, ask, service=self.service, force=forced, write_lock=self.submit_lock)
        except Exception as exc:
            res = {"status": "failed", "message": f"{type(exc).__name__}: {exc}", "terms": 0, "guide": False}
        finally:
            self.procs.pop(lane, None)
        if res["status"] == "skipped":
            return
        built = ", ".join(x for x in ("a context guide" if res["guide"] else "", f"{res['terms']} glossary term(s)"
                                      if res["terms"] else "") if x)
        took = f"in {_human(time.time() - started)}"
        if res["status"] == "done":
            self.log(f"Lane {lane + 1}: built the context for {project}: {built} {took}.", "ok")
        else:
            self.log(f"Lane {lane + 1}: context for {project} {res['status']} — {res['message'][:300] or 'no reason given'}"
                     f"{f'; saved {built}' if built else ''}; translating {'with what there is' if built else 'without it'}.",
                     "warn")

    def _grow_guide(self, folder: Path, project: str, whole: bool) -> None:
        """After a submitted episode: count it, and when this one asked for additions to the context guide
        (its workspace has the file) add them and start counting again."""
        if not whole:
            return  # gaps filled into a translated episode bring no new story
        path = folder / GUIDE_ADDITIONS_FILE
        asked = path.is_file()
        try:
            additions = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if asked else []
            with self.submit_lock:
                added = self.service.append_guide_additions(project, additions) if additions else 0
                meta = storage.load_project_metadata(project) or {}
                meta["guide_growth_episodes"] = 0 if asked else int(meta.get("guide_growth_episodes") or 0) + 1
                storage.save_project_metadata(project, meta)
            if asked:
                path.unlink()
                self.log(f"{project}: {added} addition(s) to the context guide after {GUIDE_GROWTH_EVERY} episodes.")
        except Exception as exc:  # housekeeping; the episode is already submitted
            logger.warning(f"Could not update the context guide counter of {project}: {exc}")

    def _chunk(self, lane: int, model: str, folder: Path, title: str, ctx: Dict, units: List[aw.Unit],
               by_id: Dict[int, aw.Unit], chunk: List[aw.Unit], code: str, needs, current, *, part: str,
               first_part: bool, last: bool, tokens: List[int], project: str, episode: str,
               refused: Optional[set] = None, start: Optional[Dict] = None, extras: bool = True,
               around_done: bool = False) -> bool:
        """Translate one chunk, then fix rounds for what came back missing or wrong. ``refused``: the
        episode's set-aside lines (left out here); ``start``: flags for the first request (see ``_ask``);
        ``extras``: ask for the summary/guide; ``around_done``: show the translated lines around each."""
        from utils.glossary_judgment import GLOSSARY_INSTRUCTION, brief_lines
        from utils.line_alignment import repair_reply
        work = folder / aw.WORK_FILE
        fix_rounds = int(self.config["fix_rounds"] or 0)
        problems: Dict[int, str] = {}
        flagged_before: Dict[int, str] = {}
        aside = refused if refused is not None else set()
        for round_no in range(1 + fix_rounds):
            if not self._still_mine(folder, lane, title):
                return False
            cur = current()
            wanted = {u.id for u in chunk if needs(u, cur)} if round_no == 0 else set(problems)
            todo = [u for u in chunk if u.id in wanted and u.id not in aside]
            if not todo:
                return True
            glossary_lines = brief_lines(ctx["matcher"].find_all([u.text for u in todo]))
            around = {u.id: ctx["around"][u.id] for u in todo[:MAX_AROUND] if u.id in ctx["around"]}
            if around_done:
                around = {**self._around_done(units, todo, cur, needs), **around}
            if round_no == 0:
                earlier = self._earlier(units, chunk[0], cur, needs)
                so_far = "" if first_part else self._summary(folder)
                characters = self._characters(project, ctx["glossary"], [u.text for u in todo])
                want_summary = ctx["whole"] and extras
                want_guide = ctx["whole"] and last and not ctx["has_guide"] and extras
                want_additions = ctx["grow"] and last and extras

                def build(sub: List[aw.Unit], first: bool = True) -> str:
                    ids = {u.id for u in sub}
                    return ap.translate_prompt(
                        ctx["static"], title=title, story=ctx["story"], characters=characters,
                        glossary_lines=glossary_lines, glossary_note=GLOSSARY_INSTRUCTION, earlier=earlier, units=sub,
                        want_summary=want_summary and first, want_guide=want_guide and first, part=part,
                        around={i: v for i, v in around.items() if i in ids}, episode_so_far=so_far,
                        want_additions=want_additions and first)
            else:
                snapshot, asked = dict(cur), dict(problems)

                def build(sub: List[aw.Unit], first: bool = True) -> str:
                    ids = {u.id for u in sub}
                    return ap.fix_prompt(
                        ctx["static"], title=title, glossary_lines=glossary_lines, glossary_note=GLOSSARY_INSTRUCTION,
                        units=sub, current=snapshot, problems={i: p for i, p in asked.items() if i in ids},
                        around={i: v for i, v in around.items() if i in ids})
            doing = "translating" if round_no == 0 else "fixing"
            self._lane_set(lane, state=f"{doing} {len(todo)} line(s){f' ({part})' if part else ''}")
            result = self._ask(lane, model, build, todo, folder, title, project, episode, tokens,
                               refused=refused, start=start)
            if result is None:
                return False
            todo = [u for u in todo if u.id not in aside]  # the request was refused: its lines wait for the end
            if not todo:
                return True
            answers, slip = repair_reply([u.id for u in todo], result["answers"], {u.id: u.text for u in todo},
                                         result["extra"], code)
            if slip:
                self._log_slip(lane, slip)
            if answers:
                work.write_text(aw.write_answers(work.read_text(encoding="utf-8"), answers), encoding="utf-8")
            self._keep_extras(folder, result, ctx)
            label = "translated" if round_no == 0 else "fix round"
            self.log(f"Lane {lane + 1}: {label}{f' ({part})' if part else ''} — {len(answers)}/{len(todo)} line(s) back "
                     f"in {_human(result['seconds'])} ({result['tokens_in']:,} tokens in / {result['tokens_out']:,} out).")
            if not answers and result.get("text"):
                self.log(f"Lane {lane + 1}: the reply had no usable lines: {result['text'][:200]!r}", "warn")
            cur = current()
            problems = {}
            for u in chunk:
                if u.id in aside:
                    continue
                if not (cur.get(u.id) or "").strip():
                    problems[u.id] = "not translated yet"
                elif needs(u, cur):
                    problems[u.id] = "still in English (or not a translation of this line)"
            for m in aw.misplaced_lines(chunk, cur, code):
                if m["id"] in problems or m["id"] in aside or flagged_before.get(m["id"]) == cur.get(m["id"]):
                    continue  # the same answer twice: accept it
                other = by_id.get(m["belongs_to"])
                problems[m["id"]] = ("this looks like the translation of another line (“"
                                     f"{aw._norm(other.text)[:70] if other else '?'}”), not of this one")
            flagged_before = {i: cur.get(i, "") for i in problems}
            if not problems:
                return True
        if problems:
            self.log(f"Lane {lane + 1}: {len(problems)} line(s) still need work after {fix_rounds + 1} round(s) — "
                     f"they stay untranslated for now (the audit finds them later).", "warn")
        return True

    def _log_slip(self, lane: int, slip: Dict) -> None:
        gap = slip["gap"]
        what = f"skipped {gap}" if gap > 0 else f"split or repeated {-gap}"
        if slip["moved"]:
            asked_again = f"; {slip['dropped']} unclear one(s) are asked again" if slip["dropped"] else ""
            self.log(f"Lane {lane + 1}: the reply lost its place at line {slip['from']} (it {what} line(s) but kept "
                     f"counting) — moved {slip['moved']} answer(s) back to their own lines{asked_again}.", "warn")
        else:
            self.log(f"Lane {lane + 1}: the reply seems to have lost its place at line {slip['from']} — "
                     f"{slip['dropped']} answer(s) are asked again.", "warn")

    def _keep_extras(self, folder: Path, result: Dict, ctx: Dict) -> None:
        """The reply's episode summary, context guide and new terms go into the workspace files
        (so they survive an interruption and the normal submit picks them up)."""
        if result.get("summary") and ctx["whole"]:
            (folder / aw.SUMMARY_FILE).write_text(result["summary"].strip() + "\n", encoding="utf-8")
        if result.get("guide") and not ctx["has_guide"]:
            project = self.service._read_manifest(folder).get("project_name", "")
            (folder / aw.GUIDE_FILE).write_text(f"# Context Guide: {project}\n\n{result['guide'].strip()}\n",
                                                encoding="utf-8")
        elif result.get("guide") and ctx["grow"]:   # additions to the guide that exists; applied once the episode is submitted
            lines = [g for g in result["guide"].splitlines() if g.strip()][:ap.GUIDE_MAX_ADDITIONS]
            (folder / GUIDE_ADDITIONS_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
        if result.get("terms"):
            path = folder / aw.TERMS_FILE
            try:
                existing = json.loads(path.read_text(encoding="utf-8") or "[]")
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
            seen = {str(t.get("term", "")).lower() for t in existing if isinstance(t, dict)}
            for t in result["terms"]:
                if t["term"].lower() not in seen:
                    seen.add(t["term"].lower())
                    existing.append(t)
            path.write_text(json.dumps(existing, ensure_ascii=False, indent=1), encoding="utf-8")

    def _submit(self, lane: int, task: Dict, folder: Path, units: List[aw.Unit], current, needs, started: float,
                tokens: List[int], whole: bool = True) -> None:
        """Submit through the normal agent path. Lines that never came back stay untranslated (they
        export as authored and the audit re-queues them); an episode that is mostly untranslated goes
        back to the queue instead."""
        task_id = task["task_id"]
        title = f"{task['project_name']} {task['episode_name']}"
        cur = current()
        left = [u for u in units if needs(u, cur)]
        if len(left) > len(units) * 0.5:
            outcome = self._put_back(task, f"{len(left)} of {len(units)} lines never came back")
            self.log(f"Lane {lane + 1}: {title} — {len(left)} of {len(units)} lines never came back; {outcome}.", "error")
            return
        if left:  # clear what is still English, so it stays a gap instead of being saved as a translation
            work = folder / aw.WORK_FILE
            work.write_text(aw.write_answers(work.read_text(encoding="utf-8"), {u.id: "" for u in left}, clear=True),
                            encoding="utf-8")
        with self.submit_lock:
            res = self.service.submit_task_result(task_id, force=bool(left))
        if not res.get("success"):
            self.log(f"Lane {lane + 1}: {title} could not be submitted: {res.get('message') or res.get('status')}",
                     "error")
            if res.get("status") in ("episode_changed", "legacy_workspace"):
                self.service.release_agent_task(task_id)
            return
        done = len(units) - len(left)
        with self.lock:
            self.session["episodes"] += 1
            self.crashes = 0
            self._crash_run_failed = []   # another episode went through: those were the episodes' own faults
            self.session["lines"] += done
        self._grow_guide(folder, task["project_name"], whole)
        notes = []
        if left:
            notes.append(f"{len(left)} left for later")
        if res.get("harvested_terms_count"):
            notes.append(f"+{res['harvested_terms_count']} glossary term(s)")
        notes.append("exported" if res.get("exported_path") else (res.get("export_note") or "not exported"))
        self.log(f"Lane {lane + 1}: submitted {title} — {done}/{len(units)} lines in {_human(time.time() - started)} "
                 f"({tokens[0]:,} tokens in / {tokens[1]:,} out); {', '.join(notes)}.", "ok")
        self._event("agent_runner_submitted", {"task_id": task_id, "project_name": task["project_name"],
                                               "episode_name": task["episode_name"]})  # not a status change
