"""
Subtitle conformance (Plan 13) — reading-speed (CPS), line length, line count.

Measures translated lines against configurable limits, can re-wrap over-long lines
(balanced split, timings untouched), and flags residual violations for review.
"""

import re
from typing import Dict, List

from utils.srt_parser import timecode_to_ms

_TAG_RE = re.compile(r"<[^>]+>")


def _duration_seconds(timecode: str) -> float:
    try:
        a, b = timecode.split("-->")
        ms = timecode_to_ms(b.strip()) - timecode_to_ms(a.strip())
        return max(0.001, ms / 1000.0)
    except Exception:
        return 0.0


def measure_line(text: str, timecode: str) -> Dict:
    plain = _TAG_RE.sub("", text or "")
    visual_lines = plain.split("\n")
    char_count = len(plain.replace("\n", ""))
    dur = _duration_seconds(timecode)
    cps = (char_count / dur) if dur > 0 else 0.0
    return {
        "char_count": char_count,
        "cps": round(cps, 1),
        "line_count": len([l for l in visual_lines if l.strip()]),
        "max_line_len": max((len(l) for l in visual_lines), default=0),
        "duration": round(dur, 2),
    }


def default_limits(config: Dict) -> Dict:
    return {
        "max_cps": config.get("max_cps", 17.0),
        "max_chars_per_line": config.get("max_chars_per_line", 42),
        "max_lines": config.get("max_lines", 2),
    }


def measure(parsed_srt: List[Dict], limits: Dict) -> Dict:
    max_cps = limits["max_cps"]
    max_chars = limits["max_chars_per_line"]
    max_lines = limits["max_lines"]
    violations = []
    for i, line in enumerate(parsed_srt):
        text = line.get("translated") or ""
        if not text.strip():
            continue
        m = measure_line(text, line.get("timecode", ""))
        issues = []
        if m["cps"] > max_cps:
            issues.append(f"reading speed {m['cps']} CPS > {max_cps}")
        if m["max_line_len"] > max_chars:
            issues.append(f"line {m['max_line_len']} > {max_chars} chars")
        if m["line_count"] > max_lines:
            issues.append(f"{m['line_count']} > {max_lines} lines")
        if issues:
            violations.append({"index": i, "metrics": m, "issues": issues})
    return {"violations": violations, "count": len(violations)}


# Characters that make a good break point when they end the word before the break
# (SubtitleEdit's auto-balance prefers splitting after clause punctuation too).
_BREAK_PUNCT = ".!?;:,…»\"')"

# A line that opens with a dialogue dash — two-speaker cues keep their breaks.
_DASH_LINE_RE = re.compile(r"^\s*[-–—]")


def _visible_len(s: str) -> int:
    """Length of a string as the viewer sees it (formatting tags don't count)."""
    return len(_TAG_RE.sub("", s))


def _balanced_split(words: List[str], n: int, total: int) -> List[str]:
    """Split ``words`` into ``n`` lines of roughly equal visible length.

    Greedy: each of the n-1 breaks goes after the word whose cumulative length is
    closest to the k/n fraction of the total, with a bonus for breaking after clause
    punctuation. Every line keeps at least one word.
    """
    cum: List[int] = []
    c = 0
    for i, w in enumerate(words):
        c += _visible_len(w) + (1 if i else 0)
        cum.append(c)

    breaks: List[int] = []
    start = 0
    for k in range(1, n):
        target = total * k / n
        best_i, best_score = None, None
        # leave at least one word per remaining line
        for i in range(start, len(words) - (n - k)):
            score = abs(cum[i] - target)
            if words[i] and words[i][-1] in _BREAK_PUNCT:
                score -= 3.0
            if best_score is None or score < best_score:
                best_i, best_score = i, score
        if best_i is None:
            return [" ".join(words)]
        breaks.append(best_i)
        start = best_i + 1

    lines: List[str] = []
    s = 0
    for b in breaks:
        lines.append(" ".join(words[s:b + 1]))
        s = b + 1
    lines.append(" ".join(words[s:]))
    return lines


def wrap_text(text: str, max_chars: int, max_lines: int = 2) -> str:
    """Re-flow a block of text into the minimal number of balanced lines.

    SubtitleEdit-style auto-balance: join any existing breaks, then split into the
    fewest lines that keep every line within ``max_chars``, balanced around equal
    lengths and preferring breaks after punctuation. ``max_lines`` is the preferred
    cap — when the text simply cannot fit ``max_lines`` × ``max_chars``, more lines
    are used rather than emitting an over-long line (a 3rd line beats a marquee).
    """
    flat = " ".join((text or "").split())
    if _visible_len(flat) <= max_chars:
        return flat
    words = flat.split(" ")
    if len(words) < 2:
        return flat

    total = _visible_len(flat)
    n0 = max(2, -(-total // max_chars))  # ceil division
    best: List[str] = []
    best_overflow = None
    # A balanced split can overshoot max_chars when a long word lands on a boundary —
    # try one extra line before settling for the least-overflowing attempt.
    for n in range(n0, min(n0 + 2, len(words)) + 1):
        lines = _balanced_split(words, n, total)
        overflow = max(0, max(_visible_len(l) for l in lines) - max_chars)
        if overflow == 0:
            return "\n".join(lines)
        if best_overflow is None or overflow < best_overflow:
            best, best_overflow = lines, overflow
    return "\n".join(best) if best else flat


def is_dash_dialogue(text: str) -> bool:
    """True when a cue holds two speakers (>=2 lines opening with a dialogue dash)."""
    return sum(1 for l in (text or "").split("\n") if _DASH_LINE_RE.match(l)) >= 2


def split_text_parts(text: str, max_chars: int, max_lines: int = 2) -> List[str]:
    """Split a cue's text into parts that each fit ``max_lines`` × ``max_chars``.

    SubtitleEdit's SplitLongLines equivalent, text side: when balanced wrapping would
    still need more than ``max_lines`` lines, cut the text into the fewest balanced
    parts (preferring cuts after punctuation) and re-wrap each part. Returns a list
    with one element when no split is needed — callers turn multi-part results into
    extra cues sharing the original duration.
    """
    flat = " ".join((text or "").split())
    wrapped = wrap_text(flat, max_chars, max_lines)
    n_lines = len(wrapped.split("\n"))
    if n_lines <= max_lines:
        return [wrapped]

    words = flat.split(" ")
    parts_n = -(-n_lines // max_lines)  # ceil: 3-4 lines -> 2 parts, 5-6 -> 3, ...
    if parts_n >= len(words):
        return [wrapped]
    parts = _balanced_split(words, parts_n, _visible_len(flat))
    return [wrap_text(p, max_chars, max_lines) for p in parts]


def autobalance_rows(rows: List[Dict], limits: Dict) -> int:
    """Auto-balance / auto-split translated lines for OUTPUT (mutates ``rows``).

    Equivalent of SubtitleEdit's batch auto-balance: cues whose visual lines exceed
    the char limit, or that use more lines than allowed, are re-flowed into balanced
    lines. Compliant cues and two-speaker dash-dialogue cues are left untouched.
    Only line breaks change — timings and cue structure never do. Returns the number
    of cues rewritten.
    """
    max_chars = limits["max_chars_per_line"]
    max_lines = limits["max_lines"]
    changed = 0
    for row in rows:
        t = row.get("translated") or ""
        if not t.strip():
            continue
        lines = t.split("\n")
        # Two speakers in one cue — their break is semantic, keep it.
        if sum(1 for l in lines if _DASH_LINE_RE.match(l)) >= 2:
            continue
        too_long = any(_visible_len(l) > max_chars for l in lines)
        too_many = len([l for l in lines if l.strip()]) > max_lines
        if not (too_long or too_many):
            continue
        new = wrap_text(t, max_chars, max_lines)
        if new != t:
            row["translated"] = new
            changed += 1
    return changed


def apply_wrapping(parsed_srt: List[Dict], limits: Dict) -> int:
    """Re-wrap translated lines whose visual lines exceed the char limit. Returns count changed."""
    max_chars = limits["max_chars_per_line"]
    max_lines = limits["max_lines"]
    changed = 0
    for line in parsed_srt:
        t = line.get("translated") or ""
        if not t.strip():
            continue
        if any(len(_TAG_RE.sub("", vl)) > max_chars for vl in t.split("\n")):
            new = wrap_text(t, max_chars, max_lines)
            if new != t:
                line["translated"] = new
                changed += 1
    return changed


def flag_violations(parsed_srt: List[Dict], limits: Dict) -> int:
    """Flag residual conformance violations as needs_review. Returns count flagged."""
    res = measure(parsed_srt, limits)
    for v in res["violations"]:
        line = parsed_srt[v["index"]]
        line["needs_review"] = True
        msg = "Conformance: " + "; ".join(v["issues"])
        prev = line.get("review_issues") or ""
        line["review_issues"] = f"{prev} | {msg}" if prev else msg
    return res["count"]


def conformance_prompt_hint(limits: Dict) -> str:
    return (
        f"Keep subtitles readable: aim for <= {limits['max_chars_per_line']} characters per line, "
        f"<= {limits['max_lines']} lines, and a reading speed under {int(limits['max_cps'])} "
        f"characters/second. Prefer concise, natural phrasing over literal length."
    )
