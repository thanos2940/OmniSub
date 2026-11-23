"""Sending a request to the CLI (or the Gemini API) and dealing with refusals: retry, set aside, another model family, split; the end-of-episode rescue of refused lines.

Part of the agent runner (services/agent_runner.py): ``AgentRunner`` inherits this mixin. Moved here unchanged
from that module; ``self`` is the runner.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from utils import storage
from services import agent_cli, agent_prompts as ap, agent_workspace as aw
from services.runner.common import API_PREFIX, SMALL_BLOCK, SENSITIVE


class RequestsMixin:
    def _ask(self, lane: int, model: str, build: Callable[..., str], units: List[aw.Unit], folder: Path, title: str,
             project: str, episode: str, tokens: List[int], refused: Optional[set] = None,
             start: Optional[Dict] = None) -> Optional[Dict]:
        """One round: send the request. When a safety filter refuses it, its lines go into ``refused``
        (the episode's set-aside lines, sent on their own at the end) or, without that, the refusal is
        narrowed down (retry once, set the lines that usually trip it aside, try another model family,
        split). ``start``: flags for the first request (``after_rescue``, ``tried``); a model named
        ``api:<model>`` is called through the Gemini API.
        Returns the merged reply, or None when the work must stop (quota, failure, stop)."""
        exe = self._exe()
        engine = self.config["engine"]
        models = self._models()
        pieces = [{"units": units, "model": model, "tries": 0, "first": True, **(start or {})}]
        if pieces[0].get("after_rescue"):
            pieces[0]["tries"] = 1  # these lines were refused once already: no plain retry
        merged = {"answers": {}, "summary": "", "guide": "", "terms": [], "extra": 0, "seconds": 0.0,
                  "tokens_in": 0, "tokens_out": 0, "text": ""}
        while pieces:
            if self.stop_event.is_set():
                return None
            piece = pieces.pop(0)
            prompt = build(piece["units"], piece["first"])
            self._touch_pid()
            via_api = piece["model"].startswith(API_PREFIX)
            if via_api:
                reply = self._api_call(piece["model"][len(API_PREFIX):], prompt, project, episode)
            else:
                reply = agent_cli.call_model(engine, exe, piece["model"], prompt, agent_cli.call_dir(), self.stop_event,
                                             on_proc=lambda p: self.procs.__setitem__(lane, p))
                self.procs.pop(lane, None)
            with self.lock:
                self.session["calls"] += 1
                self.session["tokens_in"] += reply.tokens_in
                self.session["tokens_out"] += reply.tokens_out
            tokens[0] += reply.tokens_in
            tokens[1] += reply.tokens_out
            if not via_api:  # API calls record their own usage and cost
                self._usage(piece["model"], lane, project, episode, reply)
            if reply.failure == "stopped":
                return None
            if reply.failure and not self._still_mine(folder, lane, title):
                return None  # cancelled while the request ran (cancel_task cut it off): not the model's failure
            if via_api and reply.failure:
                self.log(f"Lane {lane + 1}: the Gemini API did not translate {len(piece['units'])} line(s) "
                         f"({reply.message[:200]}).", "warn")
                reply.failure = "blocked"  # what the API cannot do goes on like a refusal: another model family
            if reply.failure == "blocked":
                kept = self._before_the_cut(reply.text, piece["units"])
                if kept:  # an output filter stopped the answer midway: keep the lines that came through
                    merged["answers"].update(kept)
                    merged["seconds"] += reply.seconds
                    self.log(f"Lane {lane + 1}: the safety filter cut the reply off after {len(kept) + 1} line(s); "
                             f"keeping the first {len(kept)}.", "warn")
                    piece = {**piece, "units": [u for u in piece["units"] if u.id not in kept]}
                if refused is not None:
                    refused.update(u.id for u in piece["units"])
                    self.log(f"Lane {lane + 1}: the safety filter refused {len(piece['units'])} line(s) of {title}; "
                             f"they are set aside and sent on their own when the rest is done.", "warn")
                    continue
                nxt = self._after_block(piece, models, lane)
                if nxt:
                    pieces[:0] = nxt
                else:
                    self.log(f"Lane {lane + 1}: the safety filter refuses line {piece['units'][0].id} of {title}; "
                             f"it stays untranslated for now.", "warn")
                continue
            if reply.failure:
                self._failure(reply, piece["model"], lane, title)
                return None
            with self.lock:
                self.consecutive_errors = 0
            self._recovered(piece["model"])
            ids = {u.id for u in piece["units"]}
            parsed = ap.parse_reply(reply.text, allowed=ids,
                                    two_line={u.id for u in piece["units"] if "\n" in u.text.strip()})
            merged["answers"].update(parsed["answers"])
            merged["extra"] += parsed["extra"]
            merged["summary"] = merged["summary"] or parsed["summary"]
            merged["guide"] = merged["guide"] or parsed["guide"]
            merged["terms"] += parsed["terms"]
            merged["seconds"] += reply.seconds
            merged["tokens_in"] += reply.tokens_in
            merged["tokens_out"] += reply.tokens_out
            merged["text"] = merged["text"] or reply.text
            if reply.tools_used:
                self.log(f"Lane {lane + 1}: the model used tools ({', '.join(map(str, reply.tools_used[:5]))}); its "
                         f"answer is still read from the reply.", "warn")
        return merged

    @staticmethod
    def _before_the_cut(text: str, units: List[aw.Unit]) -> Dict[int, str]:
        """The answers of a reply an output filter stopped midway (the CLI hands over what was written
        until then), without the last one, which the cut may have hit mid-line."""
        if not (text or "").strip():
            return {}
        answers = ap.parse_reply(text, allowed={u.id for u in units},
                                 two_line={u.id for u in units if "\n" in u.text.strip()})["answers"]
        if answers:
            answers.pop(max(answers))
        return answers

    def _after_block(self, piece: Dict, models: List[str], lane: int) -> List[Dict]:
        """A provider's safety filter refused a whole answer because of one or a few lines."""
        units = piece["units"]
        if piece.get("after_rescue") and not piece.get("isolated"):
            alt = self._other_family(piece["model"], exclude=(*piece.get("tried", ()), piece["model"]))
            if alt:
                self.log(f"Lane {lane + 1}: trying {alt} for {len(units)} refused line(s).", "warn")
                return [{**piece, "model": alt, "tried": (*piece.get("tried", ()), piece["model"])}]
            # nowhere else to send them: narrow the refusal down on the usual models, as below
        if piece["tries"] == 0 and len(units) > SMALL_BLOCK:
            self.log(f"Lane {lane + 1}: the safety filter refused {len(units)} lines; retrying once.", "warn")
            return [{**piece, "tries": 1}]
        if len(units) > 1 and not piece.get("isolated"):
            risky = [u for u in units if SENSITIVE.search(aw._norm(u.text))]
            rest = [u for u in units if u not in risky]
            if risky and rest:
                self.log(f"Lane {lane + 1}: setting aside {len(risky)} line(s) that often trip the filter; "
                         f"sending the other {len(rest)} on their own.", "warn")
                return [{"units": rest, "model": piece["model"], "tries": 1, "isolated": True, "first": piece["first"]},
                        {"units": risky, "model": piece["model"], "tries": 1, "isolated": True, "first": False}]
        if len(units) <= SMALL_BLOCK:
            own = piece["model"]
            fam = agent_cli.family(own[len(API_PREFIX):] if own.startswith(API_PREFIX) else own)
            for alt in models:
                if alt != piece["model"] and agent_cli.family(alt) != fam and self._available(alt) \
                        and alt not in piece.get("tried", ()):
                    self.log(f"Lane {lane + 1}: trying {alt} for {len(units)} refused line(s).", "warn")
                    return [{**piece, "model": alt, "tries": 1, "isolated": True,
                             "tried": (*piece.get("tried", ()), piece["model"])}]
        if len(units) > 1:
            mid = len(units) // 2
            self.log(f"Lane {lane + 1}: splitting {len(units)} refused lines to find the one the filter objects to.",
                     "warn")
            first = models[0] if models else piece["model"]
            tried = tuple(m for m in (*piece.get("tried", ()), piece["model"]) if m != first)
            return [{"units": units[:mid], "model": first, "tries": 1, "isolated": True, "tried": tried,
                     "first": piece["first"]},
                    {"units": units[mid:], "model": first, "tries": 1, "isolated": True, "tried": tried, "first": False}]
        return []

    def _api_call(self, model: str, prompt: str, project: str, episode: str) -> agent_cli.Reply:
        """One request to the Gemini API through Omnisub's model seam (rate limiter, usage and cost
        telemetry, the Gemini safety setting). No text back counts as a failure, with the reason."""
        import asyncio
        from adk_agents.llm_factory import generate
        from utils import telemetry
        api_key = self._get_gemini_api_key()
        if api_key and not os.environ.get("GOOGLE_API_KEY"):
            os.environ["GOOGLE_API_KEY"] = api_key
        blocked: List[str] = []
        started = time.time()
        token = telemetry.usage_ctx.set({"project": project, "episode": episode})
        try:
            text = asyncio.run(generate(model, prompt, role="translation", on_blocked=blocked.append))
        except Exception as exc:
            return agent_cli.Reply(failure="error", message=f"{type(exc).__name__}: {exc}"[:300],
                                   seconds=time.time() - started)
        finally:
            telemetry.usage_ctx.reset(token)
        reply = agent_cli.Reply(text=text or "", seconds=time.time() - started)
        if not reply.text.strip():
            reply.failure = "blocked" if blocked else "error"
            reply.message = blocked[0] if blocked else "the answer was empty"
        return reply

    def _has_gemini_api_key(self) -> bool:
        if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
            return True
        cfg = getattr(storage, "GLOBAL_CONFIG", {}) or {}
        return bool(cfg.get("gemini_api_key"))

    def _get_gemini_api_key(self) -> str:
        key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
        if not key:
            cfg = getattr(storage, "GLOBAL_CONFIG", {}) or {}
            key = cfg.get("gemini_api_key") or ""
        return key

    def _rescue_model(self, project: str) -> Optional[str]:
        """The Gemini API model for lines the CLI model's filter refused; None when the rescue is off,
        there is no Gemini API key, or the project translates with a local model."""
        if not self.config.get("api_rescue", True) or not self._has_gemini_api_key():
            return None
        model = (self.config.get("rescue_model") or "").strip()
        if not model:
            try:
                from utils.model_resolver import resolve_model
                model = resolve_model("translation", (storage.load_resolved_project_metadata(project) if project else None) or {})
            except Exception:
                model = ""
        if not model or model.lower().startswith("local"):
            return None
        return model

    def _other_family(self, model: str, exclude: Tuple[str, ...] = ()) -> Optional[str]:
        """The first usable CLI model of another family than ``model`` (the Gemini API counts as Gemini)."""
        fam = agent_cli.family(model[len(API_PREFIX):] if model.startswith(API_PREFIX) else model)
        for alt in self._models():
            if alt and alt not in exclude and agent_cli.family(alt) != fam and self._available(alt):
                return alt
        return None

    def _rescue_target(self, project: str, model: str) -> Optional[str]:
        api = self._rescue_model(project)
        return API_PREFIX + api if api else self._other_family(model, exclude=(model,))

    def _rescue(self, lane: int, model: str, folder: Path, title: str, ctx: Dict, units: List[aw.Unit],
                by_id: Dict[int, aw.Unit], refused: set, code: str, needs, current, *, tokens: List[int],
                project: str, episode: str) -> bool:
        """The lines set aside after a refusal, sent on their own now that the rest of the episode is
        translated (so each comes with the translated lines around it): to the Gemini API, else to a
        model of another family. Returns False when the work must stop (the episode resumes later)."""
        todo = [u for u in units if u.id in refused and needs(u, current())]
        target = self._rescue_target(project, model)
        if not todo or not target:
            return True
        where = f"the Gemini API ({target[len(API_PREFIX):]})" if target.startswith(API_PREFIX) else target
        self.log(f"Lane {lane + 1}: sending the {len(todo)} refused line(s) of {title} to {where}.")
        need_summary = ctx["whole"] and not self._summary(folder)  # the refused part was the one asked for it
        parts = self._chunks(todo, int(self.config["chunk_lines"] or 250))
        for k, chunk in enumerate(parts):
            part = "refused lines" + (f", part {k + 1} of {len(parts)}" if len(parts) > 1 else "")
            ok = self._chunk(lane, target, folder, title, ctx, units, by_id, chunk, code, needs, current, part=part,
                             first_part=False, last=k == len(parts) - 1, tokens=tokens, project=project,
                             episode=episode, start={"after_rescue": True, "tried": (model,)}, extras=need_summary,
                             around_done=True)
            if not ok:
                return False
        return True
