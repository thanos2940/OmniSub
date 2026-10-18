"""
Prompts for the direct runner — one request per chunk of an episode, all context inline.

Every prompt starts with the same block for a show (role, answer format, how to translate, the
show's context guide, the style rules), so the CLIs' prompt caching can reuse it from chunk to
chunk and episode to episode; what changes (story so far, characters, glossary terms, the lines)
comes last. Fix rounds resend only the lines that are missing or wrong, with the reason.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from services import agent_workspace as aw

_REPLY_LINE = re.compile(r"^\s*(?:[-*]\s*)?(\d+)\s*[|｜][ \t]?(.*?)\s*$")
_TAG_LINE = re.compile(r"^\s*([SG])\s*[|｜][ \t]?(.*?)\s*$")
_TERM_LINE = re.compile(r"^\s*\+\s*(.+?)\s*\|\s*(.+?)\s*(?:\|\s*([^|]*?)\s*)?(?:\|\s*([^|]*?)\s*)?(?:\|\s*(.*?))?\s*$")
_FENCE = re.compile(r"^\s*```")
TERM_TYPES = ("character", "location", "organization", "object", "technique", "other")


def escape(text: str) -> str:
    """One subtitle line per prompt line: a cue's own line break is written as ``\\n``."""
    return (text or "").replace("\\", "\\\\").replace("\r\n", "\n").replace("\n", "\\n")


def unescape(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "\\":
                out.append("\\")
                i += 2
                continue
        out.append(c)
        i += 1
    return "".join(out)


JOB_CONTEXT = (
    "## About this job\n"
    "This is professional subtitle translation of a published film or TV series, for the people who watch it. "
    "Every line is dialogue spoken by fictional characters, or text shown on screen in the work. Translate each "
    "line faithfully and completely, in the same tone and intensity: violence, threats, insults, crude language "
    "and mature themes are part of the story and are translated as written, without softening, skipping or "
    "adding comments."
)


def static_part(target_name: str, guide: str, style_guide: str) -> str:
    """The part every request for a show shares (cached by the CLI between requests)."""
    out = [
        f"You are an expert subtitle translator, translating from English into {target_name}. "
        f"Accuracy, natural spoken {target_name} and consistency matter more than speed.",
        JOB_CONTEXT,
        "Do not use tools and do not open or write files: everything you need is in this message. "
        "Answer with plain text only, in exactly the format below.",
        "## How to translate\n"
        "- Translate what each line means in its scene, not word by word. Read all the lines first: a line "
        "answers the one before it, and who speaks to whom, their mood and the situation decide the wording, "
        "the register and the address form.\n"
        "- Subtitles split sentences across lines. Keep each line's text in its own line; you may move a word "
        "or two between the lines of one sentence when the word order needs it, but never move a whole line's "
        "meaning to another number.\n"
        "- The glossary gives the agreed rendering of names and terms. Use it where a line uses the word as "
        "that name or term, declined as the sentence needs. The same English word can also be an everyday word, "
        "a verb or part of an idiom: then translate it by its meaning in the sentence. A term marked "
        "\"only as …\" applies only in that sense.",
        "## Answer format\n"
        "- One answer line per input line: `N| translation`, with the same number N. Never skip, merge, split or "
        "reorder lines. No code fences, no comments, no English in the answer.\n"
        "- Before each answer, look again at the line with that number: if one line is skipped, every later "
        "answer lands on the wrong line.\n"
        "- A line with two parts written `\\n` (a two-line subtitle) keeps the `\\n` between the two parts; "
        "two speakers stay two lines, each starting with its dash.\n"
        "- Keep italics and tags such as <i>…</i> or {\\an8} exactly where they belong.\n"
        "- On-screen text (signs, titles, captions) is short: translate the meaning concisely.",
    ]
    if guide.strip():
        out.append("## Show context\n" + guide.strip())
    if style_guide.strip():
        out.append(f"## {target_name} style rules\n" + style_guide.strip())
    return "\n\n".join(out)


def _line_block(units: List[aw.Unit], current: Optional[Dict[int, str]] = None,
                problems: Optional[Dict[int, str]] = None) -> List[str]:
    """The lines, with a blank line at scene changes; on-screen text in its own section."""
    out: List[str] = []
    dialogue = [u for u in units if u.kind == "dialogue"]
    signs = [u for u in units if u.kind != "dialogue"]
    prev_end = None
    for u in dialogue:
        if prev_end is not None and u.start_ms - prev_end > aw.SCENE_GAP_MS:
            out.append("")
        out.append(f"{u.id}| {escape(u.text)}")
        if problems and u.id in problems:
            if current and (current.get(u.id) or "").strip():
                out.append(f"#   your answer: {escape(current[u.id])}")
            out.append(f"#   problem: {problems[u.id]}")
        prev_end = max(u.end_ms, prev_end or 0)
    if signs:
        out += ["", f"# On-screen text ({len(signs)}) — signs, titles, captions"]
        for u in signs:
            out.append(f"{u.id}| {escape(u.text)}")
            if problems and u.id in problems:
                if current and (current.get(u.id) or "").strip():
                    out.append(f"#   your answer: {escape(current[u.id])}")
                out.append(f"#   problem: {problems[u.id]}")
    return out


Neighbours = Dict[int, Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]]


def _around_block(units: List[aw.Unit], around: Neighbours) -> List[str]:
    """For lines filled into an already translated episode: the translated lines around each."""
    out: List[str] = []
    for u in units:
        before, after = around.get(u.id, ([], []))
        if not before and not after:
            continue
        out.append(f"Around {u.id}:")
        out += [f"- {escape(s)} → {escape(t)}" for s, t in before]
        out.append(f"- ({u.id}: {escape(u.text)})")
        out += [f"- {escape(s)} → {escape(t)}" for s, t in after]
    return out


def translate_prompt(static: str, *, title: str, story: str, characters: str, glossary_lines: List[str],
                     glossary_note: str, earlier: List[Tuple[str, str]], units: List[aw.Unit],
                     want_summary: bool, want_guide: bool, part: str = "",
                     around: Optional[Neighbours] = None, episode_so_far: str = "") -> str:
    parts = [static, f"## Now translating\n{title}{f' — {part}' if part else ''}"]
    if story.strip():
        parts.append("## Previous episodes\n" + story.strip())
    if episode_so_far.strip():
        parts.append("## This episode so far\n" + episode_so_far.strip())
    if characters.strip():
        parts.append("## Characters in these lines\n" + characters.strip())
    if glossary_lines:
        parts.append("## Glossary terms in these lines\n" + glossary_note + "\n" + "\n".join(glossary_lines))
    if earlier:
        parts.append("## Earlier in this episode (already translated, for context only — do not answer these)\n"
                     + "\n".join(f"- {escape(s)} → {escape(t)}" for s, t in earlier))
    context = _around_block(units, around or {})
    if context:
        parts.append("## The episode is already translated except these lines. Around each of them (for context "
                     "only — do not answer these; match their wording, names and address forms)\n" + "\n".join(context))
    parts.append("\n".join([f"## Lines to translate ({len(units)})", *_line_block(units)]))
    extra = []
    if want_summary and part:
        extra.append("- After the last line, sum up what has happened in this episode so far (the earlier parts "
                     "too) in 2–4 plain sentences, each on its own line starting with `S| `.")
    elif want_summary:
        extra.append("- After the last line, add 2–3 plain sentences about what happens in this episode, each on "
                     "its own line starting with `S| `.")
    if want_guide:
        extra.append("- The show has no context guide yet: after that, add 3–6 lines about the show (premise, "
                     "setting, tone, how the characters address each other), each starting with `G| `.")
    extra.append("- At the very end you may list recurring names or terms that are NOT in the glossary, one per line: "
                 f"`+ term | translation | type | gender | used as` (type: {', '.join(TERM_TYPES)}; gender of the "
                 "person or noun; \"used as\" only for a word that also has an everyday meaning). Only names and fixed "
                 "terms — never everyday words, and never a rendering that only fits one scene (a nickname, an insult).")
    parts.append("## After the lines\n" + "\n".join(extra))
    return "\n\n".join(parts) + "\n"


def fix_prompt(static: str, *, title: str, glossary_lines: List[str], glossary_note: str, units: List[aw.Unit],
               current: Dict[int, str], problems: Dict[int, str], around: Optional[Neighbours] = None) -> str:
    parts = [static, f"## Now translating\n{title}"]
    if glossary_lines:
        parts.append("## Glossary terms in these lines\n" + glossary_note + "\n" + "\n".join(glossary_lines))
    context = _around_block(units, around or {})
    if context:
        parts.append("## Around these lines (already translated, for context only — do not answer these)\n"
                     + "\n".join(context))
    parts.append("\n".join(["## Lines to fix",
                            "Your previous answer left these lines missing or wrong. Answer with the corrected lines "
                            "only, in the same format.", *_line_block(units, current, problems)]))
    return "\n\n".join(parts) + "\n"


def parse_reply(text: str, allowed: Optional[set] = None, two_line: Optional[set] = None) -> Dict:
    """``N| text`` answers, ``S|`` summary lines, ``G|`` context-guide lines and ``+ term | …`` lines
    from a reply. Chatter, code fences and numbers outside ``allowed`` are ignored (and counted).
    A two-line subtitle (number in ``two_line``) answered on two lines instead of with ``\\n``
    gets its second line back."""
    answers: Dict[int, str] = {}
    summary: List[str] = []
    guide: List[str] = []
    terms: List[Dict] = []
    extra = 0
    last: Optional[int] = None  # the answer the previous line belonged to
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip() or _FENCE.match(line):
            last = None
            continue
        m = _REPLY_LINE.match(line)
        if m:
            n = int(m.group(1))
            last = None
            if allowed is not None and n not in allowed:
                extra += 1
                continue
            answers[n] = unescape(m.group(2)).strip()
            last = n
            continue
        if (last is not None and two_line and last in two_line and "\n" not in answers[last]
                and not line.lstrip().startswith(("#", "+")) and not _TAG_LINE.match(line)):
            answers[last] = f"{answers[last]}\n{unescape(line.strip())}"
            last = None
            continue
        last = None
        t = _TAG_LINE.match(line)
        if t:
            (summary if t.group(1) == "S" else guide).append(t.group(2).strip())
            continue
        tm = _TERM_LINE.match(line)
        if tm:
            fields = dict(zip(("term", "translation", "type", "gender", "sense"), tm.groups()))
            entry = {k: v.strip() for k, v in fields.items() if v and v.strip()}
            if entry.get("term") and entry.get("translation"):
                if entry.get("type") and entry["type"].lower() not in TERM_TYPES:
                    entry["type"] = "other"
                terms.append(entry)
    return {"answers": answers, "summary": " ".join(s for s in summary if s), "guide": "\n".join(g for g in guide if g),
            "terms": terms, "extra": extra}
