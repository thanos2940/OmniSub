"""What every request for an episode carries besides its lines: the show's shared prompt part, the story so far, characters, the lines around, and how an episode is cut into parts.

Part of the agent runner (services/agent_runner.py): ``AgentRunner`` inherits this mixin. Moved here unchanged
from that module; ``self`` is the runner.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils import storage
from services import agent_prompts as ap, agent_workspace as aw
from services.runner.common import (
    EARLIER_LINES,
    AROUND_BEFORE,
    AROUND_AFTER,
    MAX_AROUND,
    GUIDE_GROWTH_EVERY,
    CONTEXT_CACHE_SECONDS,
)


class ContextMixin:
    def _project_context(self, project: str, target_name: str) -> Dict:
        """What every episode of a show shares: the glossary, the matcher over it and the static part of the
        prompt. Reused for a few minutes; a changed glossary or context guide builds a new one."""
        from utils.translation_rules import build_style_guide
        from utils.glossary_judgment import Matcher, project_vocabulary
        from utils.work_origin import is_japanese_work
        proj_meta = storage.load_resolved_project_metadata(project) or {}
        glossary = proj_meta.get("glossary") or {"terms": []}
        guide = (proj_meta.get("context_guide") or "").strip()
        terms = glossary.get("terms") or []
        stamp = hashlib.sha1(json.dumps([terms, guide], ensure_ascii=False, sort_keys=True, default=str)
                             .encode("utf-8")).hexdigest()
        key, now = (project, target_name), time.time()
        with self.lock:
            hit = self._shared.get(key)
            if hit and hit["stamp"] == stamp and now - hit["at"] < CONTEXT_CACHE_SECONDS:
                return hit["value"]
        value = {"glossary": glossary, "has_guide": bool(guide),
                 "matcher": Matcher(terms, project_vocabulary(project)),
                 "static": ap.static_part(target_name, guide, build_style_guide(
                     target_name, japanese_work=is_japanese_work(proj_meta)))}
        with self.lock:
            self._shared = {k: v for k, v in self._shared.items() if now - v["at"] < CONTEXT_CACHE_SECONDS}
            self._shared[key] = {"at": now, "stamp": stamp, "value": value}
        return value

    def _context(self, project: str, episode: str, target_name: str, units: List[aw.Unit], whole: bool,
                 code: str, is_primary: bool) -> Dict:
        story = ""
        try:
            from utils.episode_summaries import EpisodeSummaryManager
            prev = EpisodeSummaryManager(project).get_previous_summaries(episode, count=3)
            story = "\n".join(f"- {p['episode']}: {p['summary']}" for p in prev if p.get("summary"))
        except Exception:
            pass
        shared = self._project_context(project, target_name)
        try:  # enough episodes since the guide last grew: this one's last part may ask for additions
            grow = whole and shared["has_guide"] and (
                int((storage.load_project_metadata(project) or {}).get("guide_growth_episodes") or 0) >= GUIDE_GROWTH_EVERY)
        except (TypeError, ValueError):
            grow = False
        return {**shared, "story": story, "whole": whole, "grow": bool(grow),
                "around": {} if whole else self._neighbours(project, episode, units, code, is_primary)}

    @staticmethod
    def _neighbours(project: str, episode: str, units: List[aw.Unit], code: str, is_primary: bool) -> ap.Neighbours:
        """For lines filled into an already translated episode: the translated dialogue around each."""
        try:
            cues = storage.load_episode_data(project, episode) or []
        except Exception:
            return {}

        def pair(i: int) -> Optional[Tuple[str, str]]:
            cue = cues[i]
            if not isinstance(cue, dict):
                return None
            meta = cue.get("_ass") if isinstance(cue.get("_ass"), dict) else {}
            if meta.get("kind") == "sign" or meta.get("translatable") is False:
                return None
            src = (cue.get("original") or "").strip()
            tgt = aw.cue_translation(cue, code, is_primary)
            if not src or not tgt or aw.looks_untranslated(src, tgt, code):
                return None
            return src, tgt

        out: ap.Neighbours = {}
        for u in units:
            if u.kind != "dialogue" or not u.cues or max(u.cues) >= len(cues):
                continue
            first, last = min(u.cues), max(u.cues)
            before = [p for p in (pair(i) for i in range(first - 1, max(-1, first - 6), -1)) if p][:AROUND_BEFORE][::-1]
            after = [p for p in (pair(i) for i in range(last + 1, min(len(cues), last + 6))) if p][:AROUND_AFTER]
            if before or after:
                out[u.id] = (before, after)
        return out

    @staticmethod
    def _characters(project: str, glossary: Dict, texts: List[str]) -> str:
        try:
            from utils.character_profiles import CharacterProfileManager
            cpm = CharacterProfileManager(project)
            return cpm.build_profile_context(cpm.get_profiles_for_chunk(texts, glossary=glossary, fallback_to_main_cast=True))
        except Exception:
            return ""

    @staticmethod
    def _chunks(units: List[aw.Unit], size: int) -> List[List[aw.Unit]]:
        """Dialogue in order, cut at scene changes near ``size`` lines; on-screen text rides with the
        last chunk (or gets its own when there is a lot of it)."""
        dialogue = [u for u in units if u.kind == "dialogue"]
        signs = [u for u in units if u.kind != "dialogue"]
        chunks: List[List[aw.Unit]] = []
        cur: List[aw.Unit] = []
        for i, u in enumerate(dialogue):
            cur.append(u)
            nxt = dialogue[i + 1] if i + 1 < len(dialogue) else None
            if nxt is None:
                break
            scene_break = nxt.start_ms - u.end_ms > aw.SCENE_GAP_MS
            rest = len(dialogue) - i - 1
            if len(cur) >= size * 1.25 or (len(cur) >= size * 0.6 and scene_break and rest > size * 0.3):
                chunks.append(cur)
                cur = []
        if cur:
            chunks.append(cur)
        if signs:
            if chunks and len(chunks[-1]) + len(signs) <= size * 1.4:
                chunks[-1] = chunks[-1] + signs
            else:
                chunks.append(signs)
        return chunks

    @staticmethod
    def _earlier(units: List[aw.Unit], first: aw.Unit, cur: Dict[int, str], needs) -> List[Tuple[str, str]]:
        """The last translated dialogue lines before a chunk (continuity between parts)."""
        if first.kind != "dialogue":
            return []
        prev = [u for u in units if u.kind == "dialogue" and u.start_ms < first.start_ms and u.id != first.id
                and not needs(u, cur)]
        return [(u.text, cur[u.id]) for u in prev[-EARLIER_LINES:]]

    @staticmethod
    def _around_done(units: List[aw.Unit], todo: List[aw.Unit], cur: Dict[int, str], needs) -> ap.Neighbours:
        """The translated dialogue right before and after each line of ``todo``, from the work file."""
        dialogue = [u for u in units if u.kind == "dialogue"]
        pos = {u.id: i for i, u in enumerate(dialogue)}
        out: ap.Neighbours = {}
        for u in todo[:MAX_AROUND]:
            i = pos.get(u.id)
            if i is None:
                continue
            before = [(d.text, cur[d.id]) for d in dialogue[max(0, i - 6):i] if not needs(d, cur)][-AROUND_BEFORE:]
            after = [(d.text, cur[d.id]) for d in dialogue[i + 1:i + 6] if not needs(d, cur)][:AROUND_AFTER]
            if before or after:
                out[u.id] = (before, after)
        return out

    @staticmethod
    def _summary(folder: Path) -> str:
        try:
            return (folder / aw.SUMMARY_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
