"""
ASS / SSA Subtitle Adapter (first version)

Advanced SubStation Alpha (.ass) tangles the spoken text together with
presentation markup (override tags in ``{...}``, positioning, karaoke timing,
vector drawings) inside a single ``Text`` field. This adapter normalises an
.ass file into the *same* entry shape the rest of the pipeline already uses for
SRT — ``{id, timecode, original, translated, is_edited, needs_review}`` with an
SRT-format timecode string — so scene chunking (``build_scene_ast``), the
translation memory, glossary enforcement and the review UI all work unchanged.

What this first version handles:
  * Plain spoken dialogue.
  * In-scene text / "signs" (still translated, just classified differently).
  * Leading/trailing override-tag blocks (``{\\pos(...)}``, ``{\\fad(...)}``,
    italics that wrap the whole line, etc.) — preserved verbatim around the
    translated text.
  * Hard line breaks (``\\N``) <-> newlines for the model.

What it deliberately does NOT translate (passed through untouched and flagged):
  * Karaoke lines (``\\k`` / ``\\kf`` / ``\\ko``) — per-syllable timing cannot
    survive translation.
  * Vector drawing blocks (``\\p1`` ... ``\\p0``) — those are coordinates.
  * ``Comment:`` events — kept in the file but not surfaced for translation.

Known v1 limitation: override tags that sit *inside* a line (e.g. a single
italicised word in the middle of a sentence) are dropped from the translated
output and the line is flagged ``needs_review`` so a human can re-apply them.

Reconstruction preserves the original ``[Script Info]`` and ``[V4+ Styles]``
sections by re-injecting translations into the original document
(``reconstruct_ass(data, original_content)``). When the original is not
available it falls back to building a minimal self-contained document from the
per-entry metadata.
"""

from __future__ import annotations

import copy as _copy
import re
from typing import List, Dict, Optional, Tuple

import pysubs2

# A single override block: ``{ ... }`` (non-greedy, never spans a closing brace).
_TAG_BLOCK_RE = re.compile(r"\{[^}]*\}")

# Karaoke timing tags: \k, \K, \kf, \ko (followed by a duration digit).
_KARAOKE_RE = re.compile(r"\\(?:[kK]o?|kf)\d", re.IGNORECASE)

# Drawing mode turned ON: \p1, \p2, ... (\p0 turns it off). The text that
# follows is vector drawing commands, not words.
_DRAWING_ON_RE = re.compile(r"\\p[1-9]\d*\b", re.IGNORECASE)

# The ASS ``Effect`` field is the most precise karaoke signal: Aegisub's kara-templater
# tags every generated syllable line "fx" (some setups use "karaoke"/"template"/"code"/
# "furigana"). This is what distinguishes thousands of animated syllable events from real
# text. IMPORTANT: the standard readable-motion effects ("Scroll up/down", "Banner",
# "Movement") are NOT karaoke — they are moving on-screen text that must still be
# translated — so they are deliberately excluded from this pattern.
_KARAOKE_EFFECT_RE = re.compile(
    r"^\s*(?:k(?:ara)?fx|fx|karaoke|template|code|furi(?:gana)?)\b", re.IGNORECASE
)

# Style names that signal KARAOKE / song-lyric typesetting whose text is NOT
# translatable — romanised Japanese ("romaji"), on-screen kanji syllables, and
# generic karaoke layers. These are usually rendered as thousands of per-syllable
# animated events (single letters positioned with \pos/\t), so without this they get
# misread as translatable "signs" and blow up the line count. Note: matching is on the
# STYLE name, not the visible text, so a real sign that happens to say "karaoke" is
# unaffected. OP/ED lyric layers written in the target-alphabet ("...-English"/plain)
# are intentionally NOT matched here — those are readable and stay translatable.
_KARAOKE_STYLE_RE = re.compile(
    r"roma[jn]i|\bkanji\b|karaoke|\bkara\b|\bk-?fx\b|\bkfx\b", re.IGNORECASE
)

# Style names that signal non-dialogue typesetting (signs, titles, captions).
_SIGN_STYLE_RE = re.compile(
    r"sign|title|caption|note", re.IGNORECASE
)


def _ms_to_srt_timecode(start_ms: int, end_ms: int) -> str:
    """Render an ASS start/end pair (milliseconds) as an SRT timecode string."""

    def fmt(ms: int) -> str:
        ms = max(0, int(ms))
        h, ms = divmod(ms, 3600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    return f"{fmt(start_ms)} --> {fmt(end_ms)}"


def _tokenize(text: str) -> List[Tuple[str, str]]:
    """Split a Text field into ordered ('tag'|'text', value) tokens."""
    tokens: List[Tuple[str, str]] = []
    pos = 0
    for m in _TAG_BLOCK_RE.finditer(text):
        if m.start() > pos:
            tokens.append(("text", text[pos:m.start()]))
        tokens.append(("tag", m.group(0)))
        pos = m.end()
    if pos < len(text):
        tokens.append(("text", text[pos:]))
    return tokens


def _split_tags(text: str) -> Tuple[str, str, str, List[str]]:
    """Separate a Text field into (prefix_tags, suffix_tags, visible, inline_tags).

    ``prefix_tags`` / ``suffix_tags`` are the contiguous tag blocks at the very
    start/end of the line (positioning, fades, line-wide italics). ``visible``
    is the human-readable text with ``\\N`` converted to newlines and ``\\h`` to
    spaces. ``inline_tags`` are any tag blocks left in the middle — dropped in
    this first version but reported so the caller can flag the line.
    """
    tokens = _tokenize(text)

    start = 0
    while start < len(tokens) and tokens[start][0] == "tag":
        start += 1
    end = len(tokens)
    while end > start and tokens[end - 1][0] == "tag":
        end -= 1

    prefix = "".join(t[1] for t in tokens[:start])
    suffix = "".join(t[1] for t in tokens[end:])

    middle = tokens[start:end]
    inline_tags = [t[1] for t in middle if t[0] == "tag"]
    visible_raw = "".join(t[1] for t in middle if t[0] == "text")

    visible = _ass_text_to_plain(visible_raw)
    return prefix, suffix, visible, inline_tags


def _ass_text_to_plain(text: str) -> str:
    """Convert ASS escapes to plain display text for the model."""
    text = text.replace("\\N", "\n").replace("\\n", "\n")
    text = text.replace("\\h", " ")
    return text.strip()


def _plain_to_ass_text(text: str) -> str:
    """Convert plain (possibly multi-line) text back to an ASS Text field body."""
    return text.replace("\r\n", "\n").replace("\n", "\\N")


def _classify(style: str, raw_text: str, effect: str = "") -> str:
    """Return the kind of event: dialogue | sign | karaoke | drawing.

    Ordering matters. Karaoke/effect detection runs first because templaters emit
    thousands of per-syllable events that carry ``\\pos``/``\\t`` but no ``\\k`` tag —
    keying off the tag alone would let them fall through to the ``\\pos``→"sign" rule and
    be (mis)translated. Three independent karaoke signals, most precise first:
      1. the ``Effect`` field ("fx"/"karaoke"/"template"/…) — the templater's own marker;
      2. the style name ("...Romaji...", "...Kanji...", "Karaoke");
      3. ``\\k``/``\\kf``/``\\ko`` timing tags in the text.
    Everything positioned but readable — signs, moving text (``\\move``/Banner/Scroll),
    typeset text on books/panels/screens — is classified "sign" and stays translatable.
    """
    if effect and _KARAOKE_EFFECT_RE.search(effect):
        return "karaoke"
    if style and _KARAOKE_STYLE_RE.search(style):
        return "karaoke"
    if _KARAOKE_RE.search(raw_text):
        return "karaoke"
    if _DRAWING_ON_RE.search(raw_text):
        return "drawing"
    if style and _SIGN_STYLE_RE.search(style):
        return "sign"
    # Positioned / moving events are on-screen signs (books, panels, moving text)
    # rather than dialogue — readable, so translatable.
    if re.search(r"\\(?:pos|move|clip)\b", raw_text):
        return "sign"
    return "dialogue"


def parse_ass(content: str) -> List[Dict]:
    """Parse .ass/.ssa content into pipeline-compatible subtitle entries.

    Entries are sorted by start time and assigned sequential string ids, exactly
    like ``parse_srt`` output. Each entry carries a private ``_ass`` block with
    everything ``reconstruct_ass`` needs to rebuild that event.
    """
    if not content:
        return []

    subs = pysubs2.SSAFile.from_string(content)

    raw_entries: List[Dict] = []
    for idx, ev in enumerate(subs.events):
        # Comments stay in the file but are never offered for translation.
        if ev.is_comment:
            continue

        raw_text = ev.text or ""
        kind = _classify(ev.style or "", raw_text, getattr(ev, "effect", "") or "")
        prefix, suffix, visible, inline_tags = _split_tags(raw_text)

        # Karaoke and drawing pass through untouched; so do empty/blank events.
        passthrough = kind in ("karaoke", "drawing") or not visible.strip()
        translatable = not passthrough

        raw_entries.append({
            "id": "",  # assigned after sort
            "timecode": _ms_to_srt_timecode(ev.start, ev.end),
            "original": visible if translatable else _ass_text_to_plain(raw_text),
            "translated": "",
            "is_edited": False,
            # Flag lines where mid-line tags were dropped so a human re-applies them.
            "needs_review": bool(inline_tags) and translatable,
            "_ass": {
                "index": idx,
                "kind": kind,
                "translatable": translatable,
                "prefix_tags": prefix,
                "suffix_tags": suffix,
                "inline_tags_dropped": inline_tags,
                "raw_text": raw_text,
                "start_ms": int(ev.start),
                "end_ms": int(ev.end),
                "style": ev.style,
                "name": ev.name,
                "layer": int(getattr(ev, "layer", 0) or 0),
                "margin_l": int(getattr(ev, "marginl", 0) or 0),
                "margin_r": int(getattr(ev, "marginr", 0) or 0),
                "margin_v": int(getattr(ev, "marginv", 0) or 0),
                "effect": getattr(ev, "effect", "") or "",
            },
        })

    # Sort by start time (ASS events are frequently out of order / overlapping),
    # then renumber so ids match display order — the reconstruction mapping uses
    # the stored event index, so reordering entries is safe.
    raw_entries.sort(key=lambda e: (e["_ass"]["start_ms"], e["_ass"]["index"]))
    for i, entry in enumerate(raw_entries, start=1):
        entry["id"] = str(i)

    return raw_entries


def unify_layer_duplicates(entries: List[Dict], lang_code: Optional[str] = None) -> int:
    """Make layered typesetting share ONE translation (mutates ``entries`` in place).

    Typesetters draw a single on-screen sign/lyric as several stacked events — a border
    layer, a glow/shadow layer, a fill layer — each an identical visible string at the
    same timecode but a different style. Translated independently they can diverge (the
    border says one thing, the fill another), which renders as a broken sign. Group the
    translatable events by ``(start_ms, end_ms, visible original)`` and, for every group
    with more than one member, copy the first non-empty translation onto all of them.

    This is an OUTPUT-level fix, so it runs on the export copy in ``reconstruct_*`` — the
    editor's 1:1 ``data.json`` keeps each layer as its own row. Returns how many entries
    were rewritten (for logging/tests).
    """
    groups: Dict[Tuple, List[Dict]] = {}
    for e in entries:
        meta = e.get("_ass") or {}
        if meta.get("translatable") is False:
            continue  # karaoke / drawing — never translated, so nothing to unify
        original = (e.get("original") or "").strip()
        if not original:
            continue
        key = (meta.get("start_ms"), meta.get("end_ms"), original)
        groups.setdefault(key, []).append(e)

    def _text(entry: Dict) -> str:
        if lang_code:
            return (entry.get("translations", {}) or {}).get(lang_code) or entry.get("translated") or ""
        return entry.get("translated") or ""

    rewritten = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        winner = next((_text(m) for m in members if _text(m).strip()), "")
        if not winner:
            continue
        for m in members:
            if _text(m) != winner:
                m["translated"] = winner
                if lang_code:
                    m.setdefault("translations", {})[lang_code] = winner
                rewritten += 1
    return rewritten


def _ass_target_font(lang_code: Optional[str], custom_font: Optional[str] = None) -> Optional[str]:
    """Return the preferred font for the target language in ASS styling.

    Greek (and other non-Latin scripts) require fonts with complete Unicode glyph
    coverage. Custom fansub fonts in English releases (e.g. 'Fontin Sans Rg') lack
    Greek letters like 'π' and 'μ', causing them to render as blank spaces in video players.
    'Arial' is universally available across all operating systems, smart TVs, MPV,
    VLC, MPC-HC, Plex, and Jellyfin with 100% Greek Unicode coverage and clean styling.
    """
    if custom_font:
        return custom_font
    if lang_code and str(lang_code).lower().strip() in ("el", "gre", "ell", "greek"):
        return "Arial"
    return None


def reconstruct_ass(
    parsed_data: List[Dict],
    original_content: Optional[str] = None,
    font_scale: float = 1.0,
    target_font: Optional[str] = None,
    target_lang_code: Optional[str] = None,
) -> str:
    """Rebuild an .ass file, re-injecting translations into translatable events.

    When ``original_content`` is provided the original document (script info,
    styles, comments, karaoke, drawings) is preserved and only translatable
    events are rewritten. Otherwise a minimal self-contained document is built
    from the per-entry metadata.

    ``font_scale`` (default 1.0) multiplies every style's Fontsize. Use a value
    below 1.0 (e.g. 0.85) for target scripts like Greek that render visually
    larger than Latin at the same nominal size.

    ``target_lang_code`` / ``target_font`` remaps style font families to a font
    with full Unicode coverage (e.g. Arial for Greek), fixing missing characters
    like 'π' and 'μ' in players.
    """
    chosen_font = _ass_target_font(target_lang_code, target_font)

    if original_content:
        subs = pysubs2.SSAFile.from_string(original_content)

        # --- Scale style font sizes for the target language ---------------
        if font_scale != 1.0:
            for style in subs.styles.values():
                style.fontsize = max(1, round(style.fontsize * font_scale))

        # --- Remap styles to target font if language requires Unicode replacement ---
        if chosen_font:
            for style in subs.styles.values():
                style.fontname = chosen_font
                style.encoding = 1

        # An index can map to SEVERAL entries: export auto-splitting cuts an over-long
        # cue into parts that share the source event's duration. The first part reuses
        # (and, when flagged ``split``, retimes) the original event; extra parts become
        # cloned events inserted right after it.
        by_index: Dict[int, List[Dict]] = {}
        for e in parsed_data:
            if isinstance(e.get("_ass"), dict) and "index" in e["_ass"]:
                by_index.setdefault(e["_ass"]["index"], []).append(e)
        # ``parse_ass`` surfaces *every* non-comment event, so when the entries carry
        # ``_ass`` indexes a non-comment event missing from ``by_index`` was deleted in
        # the editor and must be dropped — otherwise its untranslated source text would
        # leak into the styled output. Comments are never surfaced, so they are always
        # kept untouched. If no entry carries an index (e.g. a structure-authoritative
        # rebuild with plain rows) we can't tell deletions from never-surfaced events,
        # so every event is preserved as before.
        can_prune = bool(by_index)
        kept_events = []
        for idx, ev in enumerate(subs.events):
            entries = by_index.get(idx)
            if not entries:
                if can_prune and not ev.is_comment:
                    continue  # surfaced dialogue/sign/effect deleted in editor — drop
                kept_events.append(ev)  # comment (or index-less rebuild) — leave untouched
                continue
            for part_no, entry in enumerate(entries):
                meta = entry["_ass"]
                target = ev if part_no == 0 else _copy.copy(ev)
                if meta.get("translatable"):
                    text = entry.get("translated") or entry.get("original", "")
                    prefix = meta.get("prefix_tags", "")
                    if chosen_font and "\\fn" in prefix:
                        prefix = re.sub(r"\\fn[^\}\\]+", lambda m: f"\\fn{chosen_font}", prefix)
                    target.text = (
                        prefix
                        + _plain_to_ass_text(text)
                        + meta.get("suffix_tags", "")
                    )
                # else karaoke / drawing — leave raw
                if meta.get("split"):
                    target.start = int(meta.get("start_ms", target.start))
                    target.end = int(meta.get("end_ms", target.end))
                kept_events.append(target)
        subs.events = kept_events
        return subs.to_string("ass")

    # --- Fallback: no original document available -------------------------
    subs = pysubs2.SSAFile()
    default_style = pysubs2.SSAStyle()
    if chosen_font:
        default_style.fontname = chosen_font
        default_style.encoding = 1
    if font_scale != 1.0:
        default_style.fontsize = max(1, round(default_style.fontsize * font_scale))
    subs.styles["Default"] = default_style

    for entry in parsed_data:
        meta = entry.get("_ass", {}) or {}
        if meta.get("translatable", True):
            text = entry.get("translated") or entry.get("original", "")
            prefix = meta.get("prefix_tags", "")
            if chosen_font and "\\fn" in prefix:
                prefix = re.sub(r"\\fn[^\}\\]+", lambda m: f"\\fn{chosen_font}", prefix)
            body = (
                prefix
                + _plain_to_ass_text(text)
                + meta.get("suffix_tags", "")
            )
        else:
            body = meta.get("raw_text", entry.get("original", ""))

        # Derive timing from metadata, falling back to the SRT timecode string.
        start_ms = meta.get("start_ms")
        end_ms = meta.get("end_ms")
        if start_ms is None or end_ms is None:
            start_ms, end_ms = _srt_timecode_to_ms(entry.get("timecode", ""))

        subs.events.append(pysubs2.SSAEvent(
            start=start_ms,
            end=end_ms,
            text=body,
            style=meta.get("style") or "Default",
            name=meta.get("name") or "",
            layer=meta.get("layer", 0) or 0,
            marginl=meta.get("margin_l", 0) or 0,
            marginr=meta.get("margin_r", 0) or 0,
            marginv=meta.get("margin_v", 0) or 0,
            effect=meta.get("effect", "") or "",
        ))
    return subs.to_string("ass")


def _srt_timecode_to_ms(timecode: str) -> Tuple[int, int]:
    """Parse an ``HH:MM:SS,mmm --> HH:MM:SS,mmm`` string into (start_ms, end_ms)."""
    def one(tc: str) -> int:
        tc = tc.strip().replace(".", ",")
        m = re.match(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", tc)
        if not m:
            return 0
        h, mn, s, ms = (int(x) for x in m.groups())
        return ((h * 60 + mn) * 60 + s) * 1000 + ms

    parts = timecode.split("-->")
    if len(parts) != 2:
        return 0, 0
    return one(parts[0]), one(parts[1])
