"""
Alignment audit — post-translation drift detector (zero-token, deterministic).

Detects the *shift* corruption where the translation assigned to source line X is
actually the translation of a neighbouring source line (X±1, X±2). Index-mapping
is meant to prevent this, but drift still leaks in when the model miscounts,
splits/merges a cue, or skips a line in whole-episode mode — and once it starts it
cascades down the file (the symptom the user reports: "line X holds the
translation of line X+1, X+1 holds X+2, ...").

The detector relies on *script-invariant anchors* that survive translation
verbatim, so it works for any target language without an LLM call:

  * digit sequences  — "1985", "3:00", "Chapter 7"          (strongest signal)
  * verbatim glossary proper-nouns (``keep_original`` terms) — "Sephiroth"

For every source line that carries anchors, we find the offset ``delta`` in
``[-max_delta, +max_delta]`` at which the line's anchors appear in the *assigned*
translation stream. ``delta == 0`` means the line is correctly aligned. A run of
lines agreeing on the same non-zero ``delta`` is a localised shift.

Sign convention: a finding's ``delta`` is *where the correct translation for a
source line currently sits*, relative to that line. So ``delta == -1`` means each
affected line's correct translation is one slot earlier (the classic "everything
shifted up" leak); ``delta == +1`` means one slot later.

This module only *detects*. Correction is delegated to the existing QC repair
pass: a flagged line is re-translated from its own English source, which fixes a
shift regardless of whatever wrong text currently occupies it. ``flag_drift`` is
provided for lanes (e.g. the Batch API drainer) that have no repair pass wired —
there it just marks the lines ``needs_review`` so the drift is visible.
"""

import re
from typing import Dict, List, Optional

_DIGIT_RE = re.compile(r"\d+")

# Anchors shorter than this (as a string) are too weak to stand alone as the sole
# evidence for a one-line finding — a coincidental single-digit match is common.
_STRONG_ANCHOR_LEN = 3

# Minimum fraction of a source line's anchors that must appear at an offset for
# that offset to count as a vote.
_VOTE_THRESHOLD = 0.5


def _translation_of(line: Dict, target_lang_code: str) -> str:
    return (line.get("translations", {}).get(target_lang_code)
            or line.get("translated") or "")


def _verbatim_terms(glossary: Optional[Dict]) -> List[str]:
    """Glossary terms kept verbatim in the target (proper nouns) — usable as anchors."""
    if not glossary:
        return []
    out = []
    for t in (glossary.get("terms") or []):
        if t.get("keep_original"):
            term = (t.get("term") or "").strip()
            if len(term) >= _STRONG_ANCHOR_LEN:
                out.append(term)
    return out


def _anchors(text: str, verbatim_terms: List[str]) -> set:
    """Extract the script-invariant anchor set from a piece of text.

    Each anchor is a typed tuple so a digit run and a glossary term can never
    collide: ``("d", "1985")`` / ``("g", "sephiroth")``.
    """
    out = set()
    for d in _DIGIT_RE.findall(text or ""):
        out.add(("d", d))
    if verbatim_terms:
        low = (text or "").lower()
        for term in verbatim_terms:
            if term.lower() in low:
                out.add(("g", term.lower()))
    return out


def audit_alignment(
    parsed_srt: List[Dict],
    target_lang_code: str,
    verbatim_terms: Optional[List[str]] = None,
    max_delta: int = 2,
) -> List[Dict]:
    """Return detected shift spans.

    Each finding: ``{"start": int, "end": int, "delta": int, "lines": [int],
    "strength": int}`` where ``[start, end]`` is the inclusive source-index span
    (including any anchorless lines between two shifted anchored lines), ``lines``
    are the anchored lines that voted, and ``strength`` is the longest matched
    anchor (years/names score high → a single such line is conclusive).
    """
    verbatim_terms = [t for t in (verbatim_terms or []) if len(t) >= _STRONG_ANCHOR_LEN]
    n = len(parsed_srt)
    if n < 2:
        return []

    present = [_anchors(_translation_of(parsed_srt[i], target_lang_code), verbatim_terms)
               for i in range(n)]

    # Per-line vote: 0 == aligned, non-zero == correct translation sits at i+delta.
    votes: Dict[int, int] = {}
    strengths: Dict[int, int] = {}
    deltas = sorted((d for d in range(-max_delta, max_delta + 1) if d != 0),
                    key=lambda d: (abs(d), d))  # prefer the smallest magnitude

    for i in range(n):
        anchors = _anchors(parsed_srt[i].get("original") or "", verbatim_terms)
        if not anchors:
            continue
        self_score = len(anchors & present[i]) / len(anchors)
        if self_score >= _VOTE_THRESHOLD:
            votes[i] = 0  # this line's anchors are in its own translation — aligned
            continue
        best_delta, best_score = None, 0.0
        for delta in deltas:
            j = i + delta
            if 0 <= j < n:
                score = len(anchors & present[j]) / len(anchors)
                if score >= _VOTE_THRESHOLD and score > best_score:
                    best_delta, best_score = delta, score
        if best_delta is not None:
            votes[i] = best_delta
            matched = anchors & present[i + best_delta]
            strengths[i] = max((len(tok) for (_kind, tok) in matched), default=1)

    # Group consecutive voting lines that agree on the same non-zero delta. An
    # aligned (delta==0) vote breaks an open run; an anchorless line does not.
    findings: List[Dict] = []
    run: Optional[Dict] = None
    for i in range(n):
        if i not in votes:
            continue
        d = votes[i]
        if d == 0:
            if run:
                findings.append(run)
                run = None
            continue
        if run and run["delta"] == d:
            run["end"] = i
            run["lines"].append(i)
            run["strength"] = max(run["strength"], strengths.get(i, 1))
        else:
            if run:
                findings.append(run)
            run = {"start": i, "end": i, "delta": d, "lines": [i],
                   "strength": strengths.get(i, 1)}
    if run:
        findings.append(run)

    # Keep only confident findings: a run of >=2 agreeing lines, or a single line
    # whose anchor is strong enough (a 3+ char digit run / proper noun) that a
    # coincidental neighbour match is implausible.
    return [f for f in findings
            if len(f["lines"]) >= 2 or f["strength"] >= _STRONG_ANCHOR_LEN]


_DRIFT_ISSUE = (
    "this line may be mis-aligned with its source — a neighbouring line's "
    "translation appears to have leaked here; translate THIS English source line "
    "from scratch, ignoring the current text"
)


def alignment_tickets(
    parsed_srt: List[Dict],
    target_lang_code: str,
    verbatim_terms: Optional[List[str]] = None,
) -> List[Dict]:
    """Repair tickets (``{"index", "issues"}``) for every line in a detected shift
    span. Fed into the existing QC repair pass, which re-translates each line from
    its own source — fixing the shift no matter what wrong text is currently there.
    """
    tickets: List[Dict] = []
    for f in audit_alignment(parsed_srt, target_lang_code, verbatim_terms):
        for idx in range(f["start"], f["end"] + 1):
            tickets.append({"index": idx, "issues": [_DRIFT_ISSUE]})
    return tickets


_ECHO_TOKEN_RE = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)


def _echo_tokens(text: str, n: int) -> List[str]:
    return _ECHO_TOKEN_RE.findall((text or "").lower())[:n]


def _echo_consistent(echo: str, source_text: str) -> bool:
    """Lenient check: do the model's echoed leading source words match the source
    line? Returns True when there is no echo (can't disprove) so non-compliance is
    a safe no-op."""
    e = _echo_tokens(echo, 4)
    if not e:
        return True
    s = set(_echo_tokens(source_text, 8))
    if not s:
        return True
    hits = sum(1 for t in e if t in s)
    return hits / len(e) >= 0.5


def filter_by_source_echo(parsed_items: List[Dict], source_texts: List[str],
                          max_delta: int = 2):
    """Drop parsed items whose model-provided source echo clearly belongs to a
    *different* local line — i.e. the model told us it translated a line other than
    the index it used (a real shift). Returns ``(kept_items, dropped_count)``.

    Conservative by design: an item is dropped only when its echo is inconsistent
    with its own claimed source AND consistent with a neighbour within
    ``±max_delta``. An echo that matches nowhere (model paraphrased its echo) is
    kept, to avoid false drops. Dropped lines become safe gaps that the repair pass
    re-translates from their own source. ``source_texts`` is the local 0-based
    source list aligned to the request's 1-based indices.
    """
    n = len(source_texts)
    kept: List[Dict] = []
    dropped = 0
    for item in parsed_items:
        echo = item.get("src_head") or ""
        i0 = item.get("index", 0) - 1
        if not echo or not (0 <= i0 < n):
            kept.append(item)
            continue
        if _echo_consistent(echo, source_texts[i0]):
            kept.append(item)
            continue
        matches_neighbour = any(
            _echo_consistent(echo, source_texts[i0 + d])
            for d in range(-max_delta, max_delta + 1)
            if d != 0 and 0 <= i0 + d < n
        )
        if matches_neighbour:
            dropped += 1  # leave as a gap → repaired from source, never mis-assigned
        else:
            kept.append(item)
    return kept, dropped


def flag_drift(
    parsed_srt: List[Dict],
    target_lang_code: str,
    verbatim_terms: Optional[List[str]] = None,
) -> int:
    """Flag detected shift spans ``needs_review`` (no repair). For lanes without a
    repair pass (e.g. the Batch API drainer) so drift is at least visible. Returns
    the number of lines flagged.
    """
    flagged = 0
    note = "possible alignment shift — verify this line against its source"
    for f in audit_alignment(parsed_srt, target_lang_code, verbatim_terms):
        for idx in range(f["start"], f["end"] + 1):
            line = parsed_srt[idx]
            line["needs_review"] = True
            existing = line.get("review_issues") or ""
            line["review_issues"] = (existing + "; " + note) if existing else note
            flagged += 1
    return flagged
