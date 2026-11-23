"""The second pass over a translated episode: a reviewer request per part checks meaning, agreement, names and
placement against the English, and only real errors change.

Part of the agent runner (services/agent_runner.py): ``AgentRunner`` inherits this mixin; ``self`` is the runner.

The ``review`` setting decides what happens to what the reviewer finds:

* ``fix`` (default): corrections it is sure of replace the translation; the ones it is unsure of are left as they
  are and flagged for the Review page, with its suggestion.
* ``flag``: nothing changes; every finding is flagged for the Review page.
* ``off``: no second pass.

Findings go to the workspace's ``review.json``; the submit writes them onto the episode's cues (``review_note``
for a correction, ``needs_review`` + ``review_issues`` for a flag). A review that fails or is refused never holds
the episode up: it is submitted as translated.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List

from services import agent_cli, agent_prompts as ap, agent_workspace as aw
from services.runner.common import API_PREFIX, _human

REVIEW_MODES = ("fix", "flag", "off")
REVIEW_FILE = "review.json"


class ReviewMixin:
    def _review_mode(self) -> str:
        mode = str(self.config.get("review") or "fix").lower()
        return mode if mode in REVIEW_MODES else "fix"

    def _review(self, lane: int, model: str, folder: Path, title: str, ctx: Dict, units: List[aw.Unit], code: str,
                needs, current, *, tokens: List[int], project: str, episode: str) -> bool:
        """Review the translated units part by part. False only when the episode must stop here (stopped,
        cancelled); a failed request just leaves that part unreviewed."""
        from utils.glossary_judgment import GLOSSARY_INSTRUCTION, brief_lines
        mode = self._review_mode()
        if mode == "off":
            return True
        work = folder / aw.WORK_FILE
        cur = current()
        done = [u for u in units if not needs(u, cur)]
        if not done:
            return True
        parts = self._chunks(done, int(self.config["chunk_lines"] or 250))
        found = {"fixes": [], "flags": []}
        started = time.time()
        for k, part_units in enumerate(parts):
            if self.stop_event.is_set() or not self._still_mine(folder, lane, title):
                return False
            part = f"part {k + 1} of {len(parts)}" if len(parts) > 1 else ""
            cur = current()
            self._lane_set(lane, state=f"reviewing {len(part_units)} line(s){f' ({part})' if part else ''}")
            prompt = ap.review_prompt(
                ctx["static"], title=title, part=part,
                glossary_lines=brief_lines(ctx["matcher"].find_all([u.text for u in part_units])),
                glossary_note=GLOSSARY_INSTRUCTION,
                characters=self._characters(project, ctx["glossary"], [u.text for u in part_units]),
                units=part_units, current=cur)
            reply = self._review_request(lane, model, prompt, project, episode)
            tokens[0] += reply.tokens_in
            tokens[1] += reply.tokens_out
            if reply.failure == "stopped":
                return False
            if reply.failure:
                if not self._still_mine(folder, lane, title):
                    return False
                self.log(f"Lane {lane + 1}: the review{f' of {part}' if part else ''} of {title} did not run "
                         f"({reply.message[:160] or reply.failure}); it is submitted as translated.", "warn")
                continue
            by_id = {u.id: u for u in part_units}
            result = ap.parse_review(reply.text, allowed=set(by_id))
            fixes, flags = result["fixes"], dict(result["flags"])
            applied: Dict[int, str] = {}
            for uid, (text, reason) in fixes.items():
                before = cur.get(uid, "")
                if text.strip() == before.strip():
                    continue
                if aw.looks_untranslated(by_id[uid].text, text, code):
                    continue   # a "correction" back into English is no correction
                if mode == "flag":
                    flags[uid] = (text, reason)
                    continue
                applied[uid] = text
                found["fixes"].append({"id": uid, "before": before, "after": text, "reason": reason})
            if applied:
                work.write_text(aw.write_answers(work.read_text(encoding="utf-8"), applied), encoding="utf-8")
            for uid, (text, reason) in flags.items():
                if uid not in applied:
                    found["flags"].append({"id": uid, "suggestion": text, "reason": reason})
            self.log(f"Lane {lane + 1}: review{f' ({part})' if part else ''} — {len(applied)} corrected, "
                     f"{len([f for f in found['flags'] if f['id'] in by_id])} flagged for a person, in "
                     f"{_human(reply.seconds)} ({reply.tokens_in:,} tokens in / {reply.tokens_out:,} out).")
        if found["fixes"] or found["flags"]:
            (folder / REVIEW_FILE).write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
        self.log(f"Lane {lane + 1}: reviewed {title} in {_human(time.time() - started)}: "
                 f"{len(found['fixes'])} line(s) corrected, {len(found['flags'])} flagged.",
                 "ok" if not found["flags"] else "info")
        return True

    def _review_request(self, lane: int, model: str, prompt: str, project: str, episode: str) -> agent_cli.Reply:
        """One review request on the lane's model (a model named ``api:<model>`` through the Gemini API)."""
        self._touch_pid()
        if model.startswith(API_PREFIX):
            reply = self._api_call(model[len(API_PREFIX):], prompt, project, episode)
        else:
            reply = agent_cli.call_model(self.config["engine"], self._exe(), model, prompt, agent_cli.call_dir(),
                                         self.stop_event, on_proc=lambda p: self.procs.__setitem__(lane, p))
            self.procs.pop(lane, None)
            self._usage(model, lane, project, episode, reply)
        with self.lock:
            self.session["calls"] += 1
            self.session["tokens_in"] += reply.tokens_in
            self.session["tokens_out"] += reply.tokens_out
        return reply
