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
_TERM_LINE = re.compile(r"^\s*\+\s*(.+?)\s*\|\s*(.+?)\s*(?:\|\s*([^|]*?)\s*)?(?:\|\s*([^|]*?)\s*)?(?:\|\s*([^|]*?)\s*)?"
                        r"(?:\|\s*(.*?))?\s*$")
_FENCE = re.compile(r"^\s*```")
TERM_TYPES = ("character", "location", "organization", "object", "technique", "other")
GUIDE_MAX_ADDITIONS = 5        # lines a translated episode may add to the context guide
_TYPE_ALIASES = {"person": "character", "name": "character", "place": "location", "org": "organization",
                 "group": "organization", "faction": "organization", "item": "object", "artifact": "object"}


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
                     around: Optional[Neighbours] = None, episode_so_far: str = "",
                     want_additions: bool = False) -> str:
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
    if want_additions:
        extra.append(f"- The show context above is the guide so far. If this episode brought something new that "
                     f"later episodes need (a new recurring character, a changed relationship, a new place), add at "
                     f"most {GUIDE_MAX_ADDITIONS} short lines, one fact each, each starting with `G| `. Leave out "
                     f"anything the guide already says, and add none when nothing is new.")
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


def line_prompt(static: str, *, title: str, text: str, before: List[Tuple[str, str]], after: List[Tuple[str, str]],
                characters: str, glossary_lines: List[str], glossary_note: str, current: str = "") -> str:
    """One line translated again (the editor's "retranslate this line"): the lines around it with the
    translations they have, so the answer fits the scene, plus the show's glossary hits and characters."""
    parts = [static, f"## Now translating\n{title}"]
    if characters.strip():
        parts.append("## Characters in this line\n" + characters.strip())
    if glossary_lines:
        parts.append("## Glossary terms in this line\n" + glossary_note + "\n" + "\n".join(glossary_lines))

    def pair(src: str, tgt: str) -> str:
        return f"- {escape(src)} → {escape(tgt)}" if tgt else f"- {escape(src)}"

    if before or after:
        parts.append("## The lines around it (for context only — do not answer these; match their wording, names "
                     "and address forms)\n" + "\n".join([*(pair(s, t) for s, t in before), "- (the line you translate goes here)",
                                                         *(pair(s, t) for s, t in after)]))
    parts.append(f"## Line to translate (1)\n1| {escape(text)}")
    if current.strip():
        parts.append("## The current translation\n"
                     f"{escape(current)}\n"
                     "A person asked for another attempt: write a fresh translation of the line. Repeat the current "
                     "one only if it is clearly the only correct rendering.")
    parts.append("## After the line\nAnswer with exactly one line, `1| translation`, and nothing else.")
    return "\n\n".join(parts) + "\n"


def context_prompt(*, show: str, target_name: str, brief: Dict, lines: List[str], want_guide: bool, want_terms: bool,
                   guide: str = "", known_terms: Optional[List[str]] = None, episodes: int = 0) -> str:
    """The brief a translator reads before a show: a context guide (``G|`` lines) and/or glossary entries
    (``+ term | …`` lines), built from what Sonarr/Radarr know of the show, a spread of its dialogue and the
    model's own knowledge of it. ``guide``: the current guide, to be improved rather than started again."""
    kind = "film" if brief.get("movie") else "TV series"
    facts = [f"Title: {show}" + (f" ({brief['year']})" if brief.get("year") else ""), f"Kind: {kind}"]
    if brief.get("genres"):
        facts.append("Genres: " + ", ".join(str(g) for g in brief["genres"]))
    if brief.get("overview"):
        facts.append("Overview: " + str(brief["overview"]).strip())
    parts = [
        f"You are preparing the translation brief for a {kind} whose English subtitles will be translated into "
        f"{target_name}. A translator reads this brief before every episode, so it must be accurate, short and "
        f"practical.",
        JOB_CONTEXT,
        "Do not use tools and do not open or write files: everything you need is in this message. Answer with plain "
        "text only, in exactly the format below.",
        "## The work\n" + "\n".join(facts),
    ]
    if lines:
        where = f" from the first {episodes} episodes" if episodes > 1 else ""
        parts.append(f"## Dialogue sample ({len(lines)} lines{where}; context only — do not translate them)\n"
                     + "\n".join(escape(t) for t in lines))
    else:
        parts.append("## Dialogue sample\nNo dialogue is available yet: work from the facts above and what you know "
                     "of the work. Leave out anything you are not sure of.")
    if guide.strip():
        parts.append("## The current context guide (keep what is right, fix what is wrong, add what is missing)\n"
                     + guide.strip())
    todo = []
    if want_guide:
        todo.append(
            "### Context guide\n"
            "Write 12–25 lines in English, each starting with `G| `, as instructions to the translator: the setting and "
            "premise in two or three lines; the tone; how the main characters relate to each other and address each "
            f"other (formal or familiar, the singular or plural “you” in {target_name}); the register and how strong "
            f"the language is, and how to render it in {target_name}; how to handle honorifics, titles, forms of "
            f"address and names; recurring jokes or fixed phrases. Do not list name renderings here — the glossary "
            f"does that.")
    if want_terms:
        known = f" Already in the glossary (do not repeat): {', '.join(known_terms[:150])}." if known_terms else ""
        todo.append(
            "### Glossary\n"
            f"List up to 60 recurring names and fixed terms, one per line: `+ term | {target_name} rendering | type | "
            f"gender | used as | note`. Type is one of {', '.join(TERM_TYPES)}. For a character give the gender "
            f"(masculine, feminine, neuter, or unknown) — {target_name} agreement depends on it. Leave `used as` "
            "empty unless the word is also an everyday word (then say in which sense it is the term). `note`: at most "
            "twelve words on who or what it is. Only character names, places, organisations, invented or fixed terms "
            f"— never everyday words. Use the established {target_name} rendering when the work has one, otherwise "
            f"transliterate.{known}")
    parts.append("## What to write\n" + "\n\n".join(todo) + "\n\nAnswer with only the `G|` and `+` lines.")
    return "\n\n".join(parts) + "\n"


NO_TOOLS = ("Do not use tools and do not open or write files: everything you need is in this message. Answer with "
            "plain text only, in exactly the format below.")


def summary_prompt(show: str, episode: str, lines: List[str]) -> str:
    """A short summary of one episode from a spread of its lines, as `S|` lines."""
    return "\n\n".join([
        "You summarise episodes of a film or TV series for the translator who works on the next one.",
        JOB_CONTEXT, NO_TOOLS,
        f"## Episode\n{show} — {episode}",
        f"## Subtitle text (a spread of {len(lines)} lines; context only)\n" + "\n".join(escape(t) for t in lines),
        "## What to write\nSum up what happens in this episode in 2–3 plain sentences (at most 50 words): the key "
        "events, what changes for the characters, new recurring names. Do not quote dialogue. Write it in the "
        "language of the subtitle text above, each sentence on its own line starting with `S| `. Answer with only "
        "those lines."]) + "\n"


def harvest_prompt(target_name: str, pairs: List[Tuple[str, str]], known: List[str]) -> str:
    """New glossary terms found in translated lines (English → target), as `+ term | …` lines."""
    sample = "\n".join(f"{i}| {escape(s)}  →  {escape(t)}" for i, (s, t) in enumerate(pairs, 1))
    return "\n\n".join([
        f"You are a terminology analyst for subtitles translated from English into {target_name}.",
        JOB_CONTEXT, NO_TOOLS,
        f"## Translated lines ({len(pairs)})\n{sample}",
        "## What to write\nList the recurring character names, places, organisations, objects and invented terms in "
        "these lines that a glossary should fix, one per line: `+ term | rendering | type | gender | used as | "
        f"note`. The rendering is the {target_name} form the translation uses (or should use). Type is one of "
        f"{', '.join(TERM_TYPES)}; gender is masculine, feminine, neuter or unknown (for characters it matters in "
        f"{target_name}); leave `used as` empty unless the word also has an everyday meaning; `note`: at most twelve "
        "words. Never list everyday words."
        + (f" Already in the glossary (do not repeat): {', '.join(known[:150])}." if known else "")
        + " Answer with only the `+` lines."]) + "\n"


def repair_prompt(body: str) -> str:
    """``body`` (the numbered correction request of ``utils.qc_funnel``) with the job framing the CLI needs."""
    return "\n\n".join([JOB_CONTEXT, NO_TOOLS, body]) + "\n"


def parse_reply(text: str, allowed: Optional[set] = None, two_line: Optional[set] = None) -> Dict:
    """``N| text`` answers, ``S|`` summary lines, ``G|`` context-guide lines and ``+ term | rendering | type |
    gender | used as | note`` lines (everything after the rendering optional) from a reply. Chatter, code
    fences and numbers outside ``allowed`` are ignored (and counted). A two-line subtitle (number in ``two_line``) answered on two lines instead of with ``\\n``
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
            fields = dict(zip(("term", "translation", "type", "gender", "sense", "notes"), tm.groups()))
            entry = {k: v.strip() for k, v in fields.items() if v and v.strip()}
            if entry.get("term") and entry.get("translation"):
                if entry.get("type"):
                    kind = entry["type"].lower()
                    entry["type"] = kind if kind in TERM_TYPES else _TYPE_ALIASES.get(kind, "other")
                terms.append(entry)
    return {"answers": answers, "summary": " ".join(s for s in summary if s), "guide": "\n".join(g for g in guide if g),
            "terms": terms, "extra": extra}
