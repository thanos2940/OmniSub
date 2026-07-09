"""Tests for the first-version ASS subtitle adapter."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils import ass_parser
from utils import subtitle_io


SAMPLE_ASS = """\
[Script Info]
Title: Sample
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1
Style: Sign,Arial,36,&H00FFFF00,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,8,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:02.00,0:00:04.00,Default,Alice,0,0,0,,Are you sure about this?
Dialogue: 0,0:00:05.00,0:00:07.00,Default,Bob,0,0,0,,{\\pos(960,820)}I am absolutely certain.
Dialogue: 0,0:00:01.00,0:00:03.50,Sign,,0,0,0,,{\\an8\\pos(960,80)}WELCOME TO TOKYO
Dialogue: 0,0:00:08.00,0:00:10.00,Default,Carol,0,0,0,,Wait, {\\i1}what{\\i0} did you say?
Comment: 0,0:00:00.00,0:00:00.00,Default,,0,0,0,,this is a translator note
Dialogue: 0,0:00:11.00,0:00:14.00,Default,,0,0,0,,{\\k20}は{\\k15}じ{\\k25}め
"""


def test_parse_returns_srt_compatible_shape():
    entries = ass_parser.parse_ass(SAMPLE_ASS)
    assert entries, "expected parsed entries"
    for e in entries:
        assert set(["id", "timecode", "original", "translated",
                    "is_edited", "needs_review"]).issubset(e.keys())
        assert "-->" in e["timecode"]
    # Comment events are not surfaced for translation.
    assert all("translator note" not in e["original"] for e in entries)


def test_entries_sorted_by_start_and_renumbered():
    entries = ass_parser.parse_ass(SAMPLE_ASS)
    # The sign starts at 0:00:01 so it should sort first.
    assert entries[0]["original"] == "WELCOME TO TOKYO"
    assert [e["id"] for e in entries] == [str(i) for i in range(1, len(entries) + 1)]


def test_leading_position_tag_is_stripped_from_translatable_text():
    entries = ass_parser.parse_ass(SAMPLE_ASS)
    bob = next(e for e in entries if "certain" in e["original"])
    assert bob["original"] == "I am absolutely certain."
    assert bob["_ass"]["prefix_tags"] == "{\\pos(960,820)}"
    assert bob["_ass"]["translatable"] is True


def test_inline_tag_line_flagged_for_review():
    entries = ass_parser.parse_ass(SAMPLE_ASS)
    carol = next(e for e in entries if "did you say" in e["original"])
    assert carol["needs_review"] is True
    assert carol["_ass"]["inline_tags_dropped"] == ["{\\i1}", "{\\i0}"]


def test_karaoke_is_passthrough_not_translatable():
    entries = ass_parser.parse_ass(SAMPLE_ASS)
    kara = next(e for e in entries if e["_ass"]["kind"] == "karaoke")
    assert kara["_ass"]["translatable"] is False
    assert kara["needs_review"] is False


def test_romaji_karaoke_style_is_passthrough():
    """Karaoke templaters emit thousands of per-syllable events that carry \\pos/\\t but
    no \\k tag; keyed off the *style* name ("...Romaji...") they must be passthrough, not
    translated as signs (regression: a real episode had 5432 such events)."""
    ass = (
        "[Script Info]\nScriptType: v4.00+\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n"
        "Style: Opening1-Romaji-L1,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Real dialogue line\n"
        "Dialogue: 0,0:00:05.00,0:00:05.58,Opening1-Romaji-L1,,0,0,0,,{\\an5\\pos(463,68)\\t(0,60,\\fr0.02)}ko\n"
    )
    entries = ass_parser.parse_ass(ass)
    dialogue = next(e for e in entries if "Real dialogue" in e["original"])
    romaji = next(e for e in entries if e["_ass"]["style"] == "Opening1-Romaji-L1")
    assert dialogue["_ass"]["translatable"] is True
    assert romaji["_ass"]["kind"] == "karaoke"
    assert romaji["_ass"]["translatable"] is False


def test_sign_classified_but_translatable():
    entries = ass_parser.parse_ass(SAMPLE_ASS)
    sign = next(e for e in entries if e["original"] == "WELCOME TO TOKYO")
    assert sign["_ass"]["kind"] == "sign"
    assert sign["_ass"]["translatable"] is True


# Goal: karaoke/effect events are passthrough; signs, moving text and on-screen written
# text (books/panels/screens) stay translatable. `_classify(style, text, effect)`.
@pytest.mark.parametrize("style,text,effect,expected_kind", [
    # --- karaoke / effect typesetting -> passthrough ---
    ("Opening1-Romaji-L1", "{\\an5\\pos(463,68)\\t(0,60,\\fr2)}ko", "fx", "karaoke"),  # templater fx marker
    ("OP-Romaji", "{\\pos(1,2)}ka", "", "karaoke"),                                     # romaji style, no \k
    ("ED2-Kanji", "{\\pos(1,2)}空", "", "karaoke"),                                # kanji syllable layer
    ("Default", "{\\k50}la{\\k50}la", "", "karaoke"),                                   # \k timing tags
    ("SongLine", "{\\pos(1,2)}na", "karaoke", "karaoke"),                               # effect=karaoke
    ("Whatever", "{\\k10}x", "template line", "karaoke"),                               # effect=template
    # --- signs / moving text / written text -> translatable ---
    ("Sign-EpTitle", "{\\an4\\pos(700,80)}The Curtain Rises", "", "sign"),
    ("Sign-Book", "{\\pos(500,400)\\frz10}DIARY OF SHIROU", "", "sign"),                # book/panel text
    ("Sign", "{\\move(0,0,500,0)}WANTED", "", "sign"),                                  # moving text
    ("Sign", "{\\clip(1,2,3,4)\\pos(5,6)}STORE OPEN", "", "sign"),                      # clipped sign
    ("Credits", "Rolling credits text", "Banner;0;0;0", "dialogue"),                    # scrolling readable text
    ("Default", "News ticker headline", "Scroll up;0;0;0;0", "dialogue"),              # scroll effect is readable
    ("Default", "Are you sure about this?", "", "dialogue"),                            # plain dialogue
])
def test_classify_karaoke_vs_signs(style, text, effect, expected_kind):
    kind = ass_parser._classify(style, text, effect)
    assert kind == expected_kind
    translatable = kind not in ("karaoke", "drawing")
    # sanity: the two moving-text effects and signs must remain translatable
    if expected_kind in ("sign", "dialogue"):
        assert translatable is True
    else:
        assert translatable is False


def test_banner_scroll_effects_are_not_treated_as_karaoke():
    """The ASS standard motion effects (Banner/Scroll/Movement) are readable moving
    text and must be translated, unlike karaoke 'fx' events."""
    for eff in ("Banner;10;0;0", "Scroll up;0;0;0;0", "Scroll down;;", "Movement;0;0"):
        assert ass_parser._classify("Default", "Store closing sale today", eff) == "dialogue"


def test_reconstruct_preserves_styles_and_injects_translation():
    entries = ass_parser.parse_ass(SAMPLE_ASS)
    for e in entries:
        if e["_ass"]["translatable"]:
            e["translated"] = "[T] " + e["original"].replace("\n", " ")

    out = ass_parser.reconstruct_ass(entries, original_content=SAMPLE_ASS)

    # Script info and both styles survive.
    assert "[Script Info]" in out
    assert "Style: Sign" in out
    # Translations injected, with the position tag preserved in front.
    assert "{\\pos(960,820)}[T] I am absolutely certain." in out
    # Karaoke line left untouched.
    assert "{\\k20}" in out
    # Comment preserved.
    assert "translator note" in out


def test_unify_layer_duplicates_forces_one_translation():
    """Layered typesetting (same timecode + visible text, different style) must share one
    translation so a sign's border/fill layers don't diverge on screen."""
    ass = (
        "[Script Info]\nScriptType: v4.00+\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Sign-Border,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n"
        "Style: Sign-Fill,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Sign-Border,,0,0,0,,{\\pos(700,80)}The Curtain Rises\n"
        "Dialogue: 1,0:00:01.00,0:00:03.00,Sign-Fill,,0,0,0,,{\\pos(700,80)}The Curtain Rises\n"
    )
    entries = ass_parser.parse_ass(ass)
    assert len(entries) == 2
    # Simulate the two layers getting DIFFERENT translations.
    entries[0]["translated"] = "Η αυλαία ανοίγει"
    entries[1]["translated"] = "Ανοίγει η αυλαία"

    rewritten = ass_parser.unify_layer_duplicates(entries)
    assert rewritten == 1
    assert entries[0]["translated"] == entries[1]["translated"] == "Η αυλαία ανοίγει"

    # And it survives into the rebuilt document on both layers.
    out = ass_parser.reconstruct_ass(entries, original_content=ass)
    assert out.count("Η αυλαία ανοίγει") == 2
    assert "Ανοίγει η αυλαία" not in out


def test_unify_layer_duplicates_ignores_distinct_and_passthrough():
    """Different text (not a shared layer) and karaoke passthrough are left untouched."""
    ass = (
        "[Script Info]\nScriptType: v4.00+\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hello\n"
        "Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,World\n"
    )
    entries = ass_parser.parse_ass(ass)
    entries[0]["translated"] = "Γεια"
    entries[1]["translated"] = "Κόσμε"
    assert ass_parser.unify_layer_duplicates(entries) == 0
    assert entries[0]["translated"] == "Γεια"
    assert entries[1]["translated"] == "Κόσμε"


def test_reconstruct_drops_deleted_events_but_keeps_comments():
    """A surfaced (non-comment) event omitted from the entries was deleted in the
    editor and must not survive in the output — while comments, which parse_ass never
    surfaces, are always preserved."""
    entries = ass_parser.parse_ass(SAMPLE_ASS)
    for e in entries:
        if e["_ass"]["translatable"]:
            e["translated"] = "[T] " + e["original"].replace("\n", " ")

    # Simulate the user deleting the sign cue ("WELCOME TO TOKYO").
    kept = [e for e in entries if e["original"] != "WELCOME TO TOKYO"]
    out = ass_parser.reconstruct_ass(kept, original_content=SAMPLE_ASS)

    # Deleted cue is gone (neither translated nor raw source text remains).
    assert "WELCOME TO TOKYO" not in out
    assert "[T] WELCOME TO TOKYO" not in out
    # Surviving dialogue is still translated.
    assert "[T] Are you sure about this?" in out
    # Comment is preserved (it was never a surfaced entry, so it isn't a "deletion").
    assert "translator note" in out
    # Karaoke passthrough survives.
    assert "{\\k20}" in out


def test_reconstruct_fallback_without_original():
    entries = ass_parser.parse_ass(SAMPLE_ASS)
    for e in entries:
        if e["_ass"]["translatable"]:
            e["translated"] = "X"
    out = ass_parser.reconstruct_ass(entries, original_content=None)
    assert "[Events]" in out
    assert "Dialogue:" in out


def test_round_trip_without_translation_is_stable():
    """Parsing then reconstructing with no edits should keep the dialogue text."""
    entries = ass_parser.parse_ass(SAMPLE_ASS)
    out = ass_parser.reconstruct_ass(entries, original_content=SAMPLE_ASS)
    reparsed = ass_parser.parse_ass(out)
    assert [e["original"] for e in reparsed] == [e["original"] for e in entries]


def test_newline_round_trips_as_hard_break():
    ass = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,First line\\NSecond line\n"
    )
    entries = ass_parser.parse_ass(ass)
    assert entries[0]["original"] == "First line\nSecond line"
    entries[0]["translated"] = "Πρώτη\nΔεύτερη"
    out = ass_parser.reconstruct_ass(entries, original_content=ass)
    assert "Πρώτη\\NΔεύτερη" in out


# --- router ---------------------------------------------------------------

def test_detect_format_by_extension_and_content():
    assert subtitle_io.detect_format("foo.ass") == subtitle_io.ASS
    assert subtitle_io.detect_format("foo.ssa") == subtitle_io.ASS
    assert subtitle_io.detect_format("foo.srt") == subtitle_io.SRT
    assert subtitle_io.detect_format("", SAMPLE_ASS) == subtitle_io.ASS
    assert subtitle_io.detect_format("", "1\n00:00:01,000 --> 00:00:02,000\nHi\n") == subtitle_io.SRT


def test_router_parse_and_reconstruct_ass():
    entries = subtitle_io.parse_subtitle(SAMPLE_ASS, filename="ep.ass")
    assert entries
    out = subtitle_io.reconstruct_subtitle(
        entries, filename="ep.ass", original_content=SAMPLE_ASS
    )
    assert "[Script Info]" in out
