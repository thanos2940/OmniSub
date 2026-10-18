"""
Agent translation workspace — the file contract between Omnisub and an external AI
agent (Claude Code, Antigravity, ...) that stands in for the translation model.

Omnisub does everything except the translating:

* prepare — pick the cues that still need a translation, drop karaoke/drawings,
  collapse repeats (a sign redrawn on every frame, stacked typesetting layers) into
  one *unit*, and write a work file the agent can read top to bottom.
* agent   — replaces the English after each ``N|`` with the target language.
* submit  — parse and validate the work file, then fan each unit's translation back
  out to every cue it stands for. Post-processing (Script Guard, TM, glossary,
  export) is the caller's job.

This module is pure logic over cue lists and strings — no queue, no disk layout —
so it is cheap to test and the same rules apply however the work file arrives.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

from utils.ass_parser import refresh_passthrough, is_untranslatable_text, _srt_timecode_to_ms, _SONG_STYLE_RE

PLAN_VERSION = 2
WORK_FILE = "translation.txt"
PLAN_FILE = "plan.json"
BRIEF_FILE = "BRIEF.md"
TERMS_FILE = "new_terms.json"
SUMMARY_FILE = "episode_summary.txt"
GUIDE_FILE = "context_guide.md"

# Gap (ms) between two dialogue units that starts a new scene paragraph in the work file.
SCENE_GAP_MS = 4000

# Script of the target language — lets the validator tell "translated" from "left in
# English". Languages written in Latin script have no entry: only verbatim copies of
# the source can be caught for them.
_TARGET_SCRIPTS = {
    "el": re.compile(r"[Ͱ-Ͽἀ-῿]"),
    "ru": re.compile(r"[Ѐ-ӿ]"),
    "uk": re.compile(r"[Ѐ-ӿ]"),
    "bg": re.compile(r"[Ѐ-ӿ]"),
    "ja": re.compile(r"[぀-ヿ一-鿿]"),
    "zh": re.compile(r"[一-鿿]"),
    "ko": re.compile(r"[가-힯ᄀ-ᇿ]"),
    "ar": re.compile(r"[؀-ۿ]"),
    "he": re.compile(r"[֐-׿]"),
    "th": re.compile(r"[฀-๿]"),
    "hi": re.compile(r"[ऀ-ॿ]"),
}
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
_NUMBERED = re.compile(r"^\s*(\d+)\s*[|｜]\s?(.*)$")
_FENCE = re.compile(r"^\s*```")


@dataclass
class Unit:
    """One distinct thing to translate, standing for one or more cues."""
    id: int
    kind: str                       # "dialogue" | "sign"
    text: str                       # source text (may contain newlines)
    cues: List[int] = field(default_factory=list)
    start_ms: int = 0
    end_ms: int = 0


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _has_letters(text: str) -> bool:
    return any(ch.isalpha() for ch in text or "")


def cue_translation(cue: Dict, target_code: str, is_primary: bool = True) -> str:
    """The cue's current translation into ``target_code`` ('' if none)."""
    text = (cue.get("translations") or {}).get(target_code) or ""
    if not text and is_primary:
        text = cue.get("translated") or ""
    return text.strip()


def looks_misaligned(source: str, translation: str) -> bool:
    """A translation that cannot belong to this source line: several times longer, or a
    multi-line block for a one-line source. Old by-index imports pasted a whole info
    card into one cue's translation; such text overflows its slot and is wrong anyway."""
    s, t = (source or "").strip(), (translation or "").strip()
    if not s or not t:
        return False
    if len(t) > 3.0 * len(s) + 40:
        return True
    breaks = len(re.findall(r"\\[Nn]|\n", t))
    return breaks >= 2 and "\n" not in s and len(t) > 2.2 * len(s) + 20


def looks_untranslated(source: str, translation: str, target_code: str) -> bool:
    """True when ``translation`` is missing or is still the source language.

    For a target written in its own script (Greek, Cyrillic, CJK, ...) a line with
    Latin words and not a single target-script character was not translated. For
    Latin-script targets only a verbatim copy of the source can be detected.
    """
    t = (translation or "").strip()
    if not t:
        return True
    if _norm(t).casefold() == _norm(source).casefold():
        return _has_letters(source)
    if looks_misaligned(source, t):
        return True
    script = _TARGET_SCRIPTS.get((target_code or "").lower())
    if script is None or script.search(t):
        return False
    return bool(_LATIN_WORD.search(t))


def _cue_times(cue: Dict) -> Tuple[int, int]:
    meta = cue.get("_ass") if isinstance(cue.get("_ass"), dict) else None
    if meta and meta.get("start_ms") is not None:
        return int(meta.get("start_ms") or 0), int(meta.get("end_ms") or 0)
    tc = cue.get("timecode") or f"{cue.get('start', '')} --> {cue.get('end', '')}"
    return _srt_timecode_to_ms(tc)


def build_units(
    cues: List[Dict],
    target_code: str,
    is_primary: bool = True,
    full: bool = False,
) -> Tuple[List[Unit], Dict[str, int]]:
    """Group an episode's cues into the units an agent has to translate.

    * Karaoke, vector drawings, song-lyric styles (OP/ED), blank cues and cues
      without a single letter ("♪", "...") are skipped — they export as authored.
    * Signs are merged by text across the whole episode (typesetters redraw one
      sign on every frame); dialogue only merges exact layer duplicates (same
      text, same timing) so "Are you ready?" can still agree with a different
      listener each time it is said.
    * ``full=False`` keeps only units whose cues lack a real translation — so a
      translated episode only asks for its missing lines and signs.

    ``cues`` is refreshed in place with the current karaoke rules (so a later save
    persists the reclassification). Returns the units (ids 1..N, dialogue first)
    and counts for the brief.
    """
    refresh_passthrough(cues)

    groups: Dict[tuple, Unit] = {}
    order: List[tuple] = []
    passthrough = 0
    for i, cue in enumerate(cues):
        meta = cue.get("_ass") if isinstance(cue.get("_ass"), dict) else {}
        text = (cue.get("original") or "").strip()
        # Song lyrics (OP/ED/insert-song styles) are typesetting, like karaoke: left as authored.
        if (meta.get("translatable") is False or not text or not _has_letters(text) or is_untranslatable_text(text)
                or _SONG_STYLE_RE.search(meta.get("style") or "")):
            passthrough += 1
            continue
        kind = "sign" if meta.get("kind") == "sign" else "dialogue"
        start, end = _cue_times(cue)
        if kind == "sign":
            key = ("sign", _norm(text).casefold())
        elif meta:
            key = ("dialogue", start, end, _norm(text))
        else:
            key = ("cue", i)
        unit = groups.get(key)
        if unit is None:
            unit = groups[key] = Unit(id=0, kind=kind, text=text, start_ms=start, end_ms=end)
            order.append(key)
        unit.cues.append(i)

    all_units = [groups[k] for k in order]
    needed = []
    for u in all_units:
        existing = next((t for t in (cue_translation(cues[c], target_code, is_primary) for c in u.cues) if t), "")
        if full or looks_untranslated(u.text, existing, target_code):
            needed.append(u)

    needed.sort(key=lambda u: (u.kind != "dialogue", u.start_ms))
    for n, u in enumerate(needed, start=1):
        u.id = n

    stats = {
        "total_cues": len(cues),
        "passthrough_cues": passthrough,
        "distinct_units": len(all_units),
        "units": len(needed),
        "dialogue_units": sum(1 for u in needed if u.kind == "dialogue"),
        "sign_units": sum(1 for u in needed if u.kind == "sign"),
        "already_translated_units": len(all_units) - len(needed),
    }
    return needed, stats


def source_fingerprint(cues: List[Dict], units: List[Unit]) -> str:
    """Hash of the cue sources the units point at — detects a re-imported episode."""
    h = hashlib.sha1()
    for u in units:
        for c in u.cues:
            h.update(f"{c}\x1f{(cues[c].get('original') or '').strip()}\x1e".encode("utf-8"))
    return h.hexdigest()


def units_to_plan(units: List[Unit], fingerprint: str, stats: Dict[str, int], mode: str) -> Dict:
    return {
        "version": PLAN_VERSION,
        "mode": mode,
        "source_fingerprint": fingerprint,
        "stats": stats,
        "units": [asdict(u) for u in units],
    }


def units_from_plan(plan: Dict) -> List[Unit]:
    return [Unit(**u) for u in plan.get("units", [])]


def render_work_file(units: List[Unit], title: str, target_language: str) -> str:
    """The file the agent edits: one ``N| text`` entry per unit, English to replace."""
    dialogue = [u for u in units if u.kind == "dialogue"]
    signs = [u for u in units if u.kind == "sign"]
    lines = [
        f"# {title}",
        f"# Replace the English after each \"N|\" with {target_language}. Keep every number exactly once.",
        "# A cue may continue on the next line without a number. Lines starting with # are ignored.",
        "",
    ]

    def entry(u: Unit) -> None:
        first, *rest = u.text.splitlines() or [""]
        lines.append(f"{u.id}| {first.strip()}")
        lines.extend(r.strip() for r in rest if r.strip())

    if dialogue:
        lines.append(f"# ===== DIALOGUE ({len(dialogue)}) — blank lines mark scene changes =====")
        prev_end = None
        for u in dialogue:
            if prev_end is not None and u.start_ms - prev_end > SCENE_GAP_MS:
                lines.append("")
            entry(u)
            prev_end = max(u.end_ms, prev_end or 0)
        lines.append("")
    if signs:
        lines.append(f"# ===== ON-SCREEN TEXT ({len(signs)}) — signs, titles, info cards; keep them short =====")
        for u in signs:
            entry(u)
        lines.append("")
    return "\n".join(lines)


def parse_work_file(content: str) -> Tuple[Dict[int, str], Dict[str, List]]:
    """Read ``N| text`` entries (with continuation lines) from a work file.

    Returns ``({id: text}, issues)`` where issues lists duplicate ids and text that
    belongs to no entry.
    """
    entries: Dict[int, List[str]] = {}
    duplicates: List[int] = []
    stray: List[str] = []
    current: Optional[int] = None
    for raw in (content or "").lstrip("﻿").splitlines():
        line = raw.rstrip()
        if not line.strip():
            current = None
            continue
        m = _NUMBERED.match(line)
        if m:
            n = int(m.group(1))
            if n in entries:
                duplicates.append(n)
            text = m.group(2).strip()
            entries[n] = [text] if text else []
            current = n
            continue
        if line.lstrip().startswith("#") or _FENCE.match(line):
            continue
        if current is not None:
            entries[current].append(line.strip())
        else:
            stray.append(line.strip())
    parsed = {n: "\n".join(parts).strip() for n, parts in entries.items()}
    return parsed, {"duplicates": duplicates, "stray": stray[:10]}


def validate(
    units: List[Unit],
    translations: Dict[int, str],
    target_code: str,
    glossary: Optional[Dict] = None,
    target_language: str = "Greek",
    vocab: Optional[set] = None,
) -> Dict:
    """Check a parsed work file before anything is written.

    Blocking: a unit with no text, or more lines still in the source language than
    the 1% allowance (a stray acronym is tolerable, a copied scene is not).
    Advisory: glossary terms whose rendering is not found in the line (terms that are
    also everyday words are left to the translator's judgment), and dialogue lines whose
    translation seems to belong to another line (``misplaced``).
    """
    missing = [u.id for u in units if not (translations.get(u.id) or "").strip()]
    untranslated = [
        u.id for u in units
        if (translations.get(u.id) or "").strip() and looks_untranslated(u.text, translations[u.id], target_code)
    ]
    known = {u.id for u in units}
    unknown = sorted(n for n in translations if n not in known)
    allowance = math.floor(len(units) * 0.01)

    glossary_warnings: List[Dict] = []
    if glossary and glossary.get("terms"):
        try:
            # Agents are told to decline glossary terms, so a declined form ("του
            # Τείχους Μαρία") counts as a match (contains_term with stemming).
            from utils.glossary_judgment import Matcher, missing_terms
            matcher = Matcher(glossary["terms"], vocab)
            for u in units:
                out = translations.get(u.id) or ""
                if not out.strip():
                    continue
                for t in missing_terms(u.text, out, matcher, target_language):
                    glossary_warnings.append({"id": u.id, "term": t["term"], "expected": t["translation"]})
                if len(glossary_warnings) >= 25:
                    break
        except Exception:
            pass

    return {
        "total": len(units),
        "translated": len(units) - len(missing) - len(untranslated),
        "missing": missing,
        "untranslated": untranslated,
        "unknown_ids": unknown[:20],
        "untranslated_allowance": allowance,
        "glossary_warnings": glossary_warnings,
        "misplaced": misplaced_lines(units, translations, target_code),
        "ok": not missing and len(untranslated) <= allowance,
    }


def misplaced_lines(units: List[Unit], translations: Dict[int, str], target_code: str) -> List[Dict]:
    """Dialogue lines whose translation fits another line of the episode better — the mark of a
    reply that lost its place: [{"id", "belongs_to", "evidence"}]."""
    dialogue = [u for u in units if u.kind == "dialogue"]
    if len(dialogue) < 4:
        return []
    try:
        from utils import line_alignment
        found = line_alignment.misplaced([u.text for u in dialogue],
                                         [translations.get(u.id) for u in dialogue], target_code)
    except Exception:
        return []
    return [{"id": dialogue[i].id, "belongs_to": dialogue[v["belongs_to"]].id, "evidence": v["evidence"]}
            for i, v in sorted(found.items())]


def describe_advisories(report: Dict, units: List[Unit], limit: int = 8) -> str:
    """Non-blocking findings worth a look before submitting."""
    by_id = {u.id: u for u in units}
    out: List[str] = []
    mis = report.get("misplaced") or []
    if mis:
        out.append(f"{len(mis)} line(s) look like the translation of another line (check them before submitting):")
        for m in mis[:limit]:
            other = by_id.get(m["belongs_to"])
            out.append(f"  {m['id']}| (fits line {m['belongs_to']}: {_norm(other.text)[:60] if other else '?'})")
        if len(mis) > limit:
            out.append(f"  ... and {len(mis) - limit} more")
    return "\n".join(out)


def write_answers(content: str, answers: Dict[int, str], clear: bool = False) -> str:
    """Put answers into a work file in place: each answered ``N|`` entry gets the new text (a
    multi-line answer continues on the following lines); notes, order and other entries stay.
    Empty answers are ignored, or with ``clear`` empty their entry (``N|``: not translated)."""
    out: List[str] = []
    skipping = False
    for raw in (content or "").splitlines():
        m = _NUMBERED.match(raw)
        if m:
            n = int(m.group(1))
            if n in answers and ((answers[n] or "").strip() or clear):
                first, *rest = (answers[n] or "").strip().splitlines() or [""]
                out.append(f"{n}| {first.strip()}".rstrip())
                out.extend(r.strip() for r in rest if r.strip())
                skipping = True
                continue
            skipping = False
            out.append(raw)
            continue
        if skipping and raw.strip() and not raw.lstrip().startswith("#"):
            continue  # the old continuation line of a replaced entry
        if not raw.strip():
            skipping = False
        out.append(raw)
    return "\n".join(out) + "\n"


def describe_problems(report: Dict, units: List[Unit], translations: Dict[int, str], limit: int = 12) -> str:
    """Human-readable summary of what blocks a submission, with the offending lines."""
    by_id = {u.id: u for u in units}
    out: List[str] = []
    if report["missing"]:
        ids = report["missing"]
        out.append(f"{len(ids)} line(s) have no translation: {_id_list(ids)}")
    if len(report["untranslated"]) > report["untranslated_allowance"]:
        ids = report["untranslated"]
        out.append(f"{len(ids)} line(s) are still in the source language:")
        for n in ids[:limit]:
            out.append(f"  {n}| {_norm(translations.get(n, ''))[:90]}   (source: {_norm(by_id[n].text)[:60]})")
        if len(ids) > limit:
            out.append(f"  ... and {len(ids) - limit} more")
    return "\n".join(out)


def _id_list(ids: List[int], limit: int = 30) -> str:
    shown = ", ".join(str(i) for i in ids[:limit])
    return shown + (f", ... (+{len(ids) - limit})" if len(ids) > limit else "")


def apply_translations(
    cues: List[Dict],
    units: List[Unit],
    translations: Dict[int, str],
    target_code: str,
    is_primary: bool = True,
) -> int:
    """Write each unit's translation onto every cue it stands for (Script Guard applied).

    Returns how many cues received text.
    """
    from utils.text_normalize import sanitize_translation_text

    written = 0
    for u in units:
        text = (translations.get(u.id) or "").strip()
        if not text:
            continue
        for ci in u.cues:
            if ci >= len(cues):
                continue
            cue = cues[ci]
            clean = sanitize_translation_text(text, source_text=cue.get("original", ""), target_lang_code=target_code)
            cue.setdefault("translations", {})[target_code] = clean
            if is_primary:
                cue["translated"] = clean
            written += 1
    return written
