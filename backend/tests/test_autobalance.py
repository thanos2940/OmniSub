"""Tests for SubtitleEdit-style export line auto-balancing.

Covers:
  - wrap_text: balanced minimal-line re-flow, punctuation preference, no mega-lines
  - autobalance_rows: only violating cues touched; dash-dialogue kept intact
  - reconstruct_cleaned_srt: balancing lands in the OUTPUT of every export seam,
    for SRT rows and ASS dialogue events (signs/karaoke untouched), and never
    leaks back into the caller's row list
"""

import pytest

import utils.storage as storage
from utils.subtitle_conformance import wrap_text, autobalance_rows, _visible_len


# ---------------------------------------------------------------- wrap_text

def test_wrap_text_short_text_untouched():
    assert wrap_text("Hello there", 42) == "Hello there"


def test_wrap_text_two_balanced_lines_under_limit():
    text = "This is a fairly long sentence that no longer fits on a single line"
    out = wrap_text(text, 42)
    lines = out.split("\n")
    assert len(lines) == 2
    assert all(len(l) <= 42 for l in lines)
    # Balanced: neither line is a tiny orphan.
    assert min(len(l) for l in lines) > len(text) * 0.25


def test_wrap_text_three_lines_when_two_cannot_fit():
    # ~100 chars can't fit 2×42 — a balanced 3rd line beats one over-long line.
    text = ("It takes a lot more courage to get your bottom off the ground "
            "and take a proper stance than people think")
    out = wrap_text(text, 42)
    lines = out.split("\n")
    assert len(lines) == 3
    assert all(len(l) <= 42 for l in lines)


def test_wrap_text_prefers_breaking_after_punctuation():
    # Comma near the midpoint: the break should snap to it rather than a plain
    # space a couple of characters closer to the exact middle.
    text = "I was walking home very late, when it suddenly started raining"
    out = wrap_text(text, 42)
    lines = out.split("\n")
    assert len(lines) == 2
    assert lines[0].endswith(",")
    assert all(len(l) <= 42 for l in lines)


def test_wrap_text_single_long_word_left_alone():
    word = "x" * 60
    assert wrap_text(word, 42) == word


def test_wrap_text_tags_do_not_count_toward_length():
    text = "<i>" + "a b " * 9 + "end</i>"  # visible ~40 chars, raw > 42
    out = wrap_text(text, 42)
    assert "\n" not in out  # visible length fits one line


# ---------------------------------------------------------- autobalance_rows

LIMITS = {"max_cps": 17.0, "max_chars_per_line": 42, "max_lines": 2}


def _row(text):
    return {"timecode": "00:00:01,000 --> 00:00:04,000", "original": "src", "translated": text}


def test_autobalance_leaves_compliant_cues_untouched():
    rows = [_row("Short line"), _row("First line\nSecond line")]
    assert autobalance_rows(rows, LIMITS) == 0
    assert rows[0]["translated"] == "Short line"
    assert rows[1]["translated"] == "First line\nSecond line"


def test_autobalance_wraps_overlong_single_line():
    long = "ου παίρνει να σηκώσεις τον κώλο σου από το έδαφος και να πάρεις μια σωστή στάση"
    rows = [_row(long)]
    assert autobalance_rows(rows, LIMITS) == 1
    lines = rows[0]["translated"].split("\n")
    assert len(lines) >= 2
    assert all(_visible_len(l) <= 42 for l in lines)


def test_autobalance_collapses_three_short_lines_to_two():
    rows = [_row("One short\npiece of\ntext here")]
    assert autobalance_rows(rows, LIMITS) == 1
    assert len(rows[0]["translated"].split("\n")) <= 2


def test_autobalance_keeps_dash_dialogue_breaks():
    rows = [_row("- Are you coming with us tonight or staying home?\n- I am staying.")]
    # First dash line is over 42 chars, but the two-speaker break is semantic.
    assert autobalance_rows(rows, LIMITS) == 0
    assert rows[0]["translated"].startswith("- Are")


# ----------------------------------------------- reconstruct_cleaned_srt seam

LONG_GREEK = ("Θα χρειαστεί πολύ περισσότερο θάρρος για να σηκωθείς από το έδαφος "
              "και να πάρεις μια σωστή στάση απ' όσο νομίζεις")

ORIGINAL_SRT = """1
00:00:01,000 --> 00:00:04,000
A long English source line for the first cue

2
00:00:05,000 --> 00:00:06,000
Short
"""

SAMPLE_ASS = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1
Style: Sign,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,8,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:04.00,Default,,0,0,0,,A long English dialogue line goes here
Dialogue: 0,0:00:05.00,0:00:07.00,Sign,,0,0,0,,{\\pos(960,80)}SHOP SIGN
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path / "projects")
    import utils.metadata_index as metadata_index
    monkeypatch.setattr(metadata_index, "upsert", lambda *a, **k: None)
    monkeypatch.setattr(metadata_index, "count_translated", lambda *a, **k: None)
    monkeypatch.setattr(metadata_index, "set_review_count", lambda *a, **k: None)
    monkeypatch.setattr(storage, "load_global_config", lambda: {
        "autobalance_export_lines": True,
        "max_chars_per_line": 42, "max_lines": 2, "max_cps": 17.0,
        "source_clean_enabled": False, "incremental_retranslate_enabled": False,
    })
    storage.create_project("TP", {"target_language": "Greek"})
    return tmp_path


def test_srt_export_balances_but_editor_rows_untouched(env):
    from utils.source_clean import reconstruct_cleaned_srt
    data = [
        {"id": "1", "timecode": "00:00:01,000 --> 00:00:04,000",
         "original": "A long English source line for the first cue",
         "translated": LONG_GREEK, "translations": {"el": LONG_GREEK}},
        {"id": "2", "timecode": "00:00:05,000 --> 00:00:06,000",
         "original": "Short", "translated": "Σύντομο", "translations": {"el": "Σύντομο"}},
    ]
    meta = {"clean_to_orig_map": {"0": [0], "1": [1]}, "line_count": 2}
    storage.save_episode("TP", "E01", data, meta)
    ep_dir = storage.PROJECTS_DIR / "TP" / "episodes" / "E01"
    (ep_dir / "original.srt").write_text(ORIGINAL_SRT, encoding="utf-8")

    out = reconstruct_cleaned_srt("TP", "E01", data, "el")

    # Output: the long cue is wrapped into balanced lines within limits.
    block = next(b for b in out.split("\n\n") if "θάρρος" in b)
    text_lines = block.splitlines()[2:]
    assert len(text_lines) >= 2
    assert all(len(l) <= 42 for l in text_lines)
    # Editor rows keep the raw single-line translation (1:1 invariant).
    assert data[0]["translated"] == LONG_GREEK
    assert "\n" not in data[0]["translated"]


def test_ass_export_balances_dialogue_but_not_signs(env):
    from utils.source_clean import import_and_clean_srt, reconstruct_cleaned_srt
    import_and_clean_srt("TP", "E02", SAMPLE_ASS, filename="video.ass")
    ep = storage.load_episode("TP", "E02")
    data = ep["data"]
    for d in data:
        if d["_ass"]["kind"] == "dialogue":
            d["translated"] = LONG_GREEK
        elif d["_ass"]["kind"] == "sign":
            d["translated"] = "ΤΑΜΠΕΛΑ ΚΑΤΑΣΤΗΜΑΤΟΣ ΠΟΥ ΕΙΝΑΙ ΑΡΚΕΤΑ ΜΕΓΑΛΗ ΓΙΑ ΝΑ ΞΕΠΕΡΝΑ ΤΟ ΟΡΙΟ"

    out = reconstruct_cleaned_srt("TP", "E02", data, "el")

    # Dialogue got \N breaks; the positioned sign kept its exact (long) text.
    assert "\\N" in out
    assert "ΤΑΜΠΕΛΑ ΚΑΤΑΣΤΗΜΑΤΟΣ ΠΟΥ ΕΙΝΑΙ ΑΡΚΕΤΑ ΜΕΓΑΛΗ ΓΙΑ ΝΑ ΞΕΠΕΡΝΑ ΤΟ ΟΡΙΟ" in out


# ------------------------------------------------------- cue auto-splitting

# ~110 visible chars: needs 3 lines at 42 chars — must be SPLIT into two cues.
VERY_LONG_GREEK = ("Πόση ώρα σου παίρνει να σηκώσεις τον κώλο σου από το έδαφος "
                   "και να πάρεις μια σωστή στάση όπως πρέπει επιτέλους")


def test_split_text_parts_two_parts_each_two_lines():
    from utils.subtitle_conformance import split_text_parts
    parts = split_text_parts(VERY_LONG_GREEK, 42, 2)
    assert len(parts) == 2
    for p in parts:
        lines = p.split("\n")
        assert len(lines) <= 2
        assert all(_visible_len(l) <= 42 for l in lines)


def test_split_text_parts_no_split_when_two_lines_fit():
    from utils.subtitle_conformance import split_text_parts
    text = "This is a fairly long sentence that no longer fits on a single line"
    assert len(split_text_parts(text, 42, 2)) == 1


def test_srt_export_splits_long_cue_sharing_duration(env):
    from utils.source_clean import reconstruct_cleaned_srt
    from utils.srt_parser import timecode_to_ms
    data = [
        {"id": "1", "timecode": "00:00:01,000 --> 00:00:07,000",
         "original": "A long English source line for the first cue",
         "translated": VERY_LONG_GREEK, "translations": {"el": VERY_LONG_GREEK}},
        {"id": "2", "timecode": "00:00:08,000 --> 00:00:09,000",
         "original": "Short", "translated": "Σύντομο", "translations": {"el": "Σύντομο"}},
    ]
    meta = {"clean_to_orig_map": {"0": [0], "1": [1]}, "line_count": 2}
    storage.save_episode("TP", "E04", data, meta)
    ep_dir = storage.PROJECTS_DIR / "TP" / "episodes" / "E04"
    (ep_dir / "original.srt").write_text(
        ORIGINAL_SRT.replace("00:00:01,000 --> 00:00:04,000", "00:00:01,000 --> 00:00:07,000"),
        encoding="utf-8")

    out = reconstruct_cleaned_srt("TP", "E04", data, "el")
    blocks = [b for b in out.strip().split("\n\n") if b.strip()]
    # 2 source cues -> 3 output cues (the long one split in two).
    assert len(blocks) == 3
    # Sequential renumbering.
    assert [b.splitlines()[0] for b in blocks] == ["1", "2", "3"]
    # The two parts share the original duration contiguously.
    tc1, tc2 = blocks[0].splitlines()[1], blocks[1].splitlines()[1]
    s1, e1 = (t.strip() for t in tc1.split("-->"))
    s2, e2 = (t.strip() for t in tc2.split("-->"))
    assert timecode_to_ms(s1) == 1000
    assert e1 == s2
    assert timecode_to_ms(e2) == 7000
    # Each part has at most 2 lines, all within the char limit.
    for b in blocks[:2]:
        lines = b.splitlines()[2:]
        assert 1 <= len(lines) <= 2
        assert all(_visible_len(l) <= 42 for l in lines)


def test_ass_export_splits_long_dialogue_into_cloned_events(env):
    from utils.source_clean import import_and_clean_srt, reconstruct_cleaned_srt
    import pysubs2
    long_ass = SAMPLE_ASS.replace("0:00:01.00,0:00:04.00", "0:00:01.00,0:00:08.00")
    import_and_clean_srt("TP", "E05", long_ass, filename="video.ass")
    ep = storage.load_episode("TP", "E05")
    data = ep["data"]
    for d in data:
        if d["_ass"]["kind"] == "dialogue":
            d["translated"] = VERY_LONG_GREEK
        elif d["_ass"]["kind"] == "sign":
            d["translated"] = "ΤΑΜΠΕΛΑ"

    out = reconstruct_cleaned_srt("TP", "E05", data, "el")
    subs = pysubs2.SSAFile.from_string(out)
    # 2 source events -> 3 exported events (dialogue split, sign untouched).
    assert len(subs.events) == 3
    dlg = [e for e in subs.events if e.style == "Default"]
    assert len(dlg) == 2
    # Parts are contiguous and span the original event exactly.
    dlg.sort(key=lambda e: e.start)
    assert dlg[0].start == 1000
    assert dlg[0].end == dlg[1].start
    assert dlg[1].end == 8000
    # Both parts fit the line cap; the style/clone carries the same style.
    for e in dlg:
        segs = e.text.split("\\N")
        assert 1 <= len(segs) <= 2
    # Sign untouched, single event.
    assert sum(1 for e in subs.events if "ΤΑΜΠΕΛΑ" in e.text) == 1


def test_no_split_when_cue_too_short_to_share(env):
    """A cue too brief for two readable parts keeps its balanced 3rd line."""
    from utils.source_clean import reconstruct_cleaned_srt
    data = [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000",
             "original": "A long English source line for the first cue",
             "translated": VERY_LONG_GREEK, "translations": {"el": VERY_LONG_GREEK}}]
    meta = {"clean_to_orig_map": {"0": [0]}, "line_count": 1}
    storage.save_episode("TP", "E06", data, meta)
    ep_dir = storage.PROJECTS_DIR / "TP" / "episodes" / "E06"
    (ep_dir / "original.srt").write_text(
        ORIGINAL_SRT.replace("00:00:01,000 --> 00:00:04,000", "00:00:01,000 --> 00:00:02,000"),
        encoding="utf-8")
    out = reconstruct_cleaned_srt("TP", "E06", data, "el")
    blocks = [b for b in out.strip().split("\n\n") if b.strip()]
    # Not split (1s duration) — balanced 3-liner instead. Exactly ONE block: the
    # unmapped "Short" raw cue is dropped from the export (unmapped cues used to be
    # emitted untranslated in English — see test_export_unmapped_cues.py).
    assert len(blocks) == 1
    long_block = blocks[0]
    lines = long_block.splitlines()[2:]
    assert len(lines) == 3
    assert all(_visible_len(l) <= 42 for l in lines)


def test_autobalance_disabled_leaves_output_as_is(env, monkeypatch):
    monkeypatch.setattr(storage, "load_global_config", lambda: {
        "autobalance_export_lines": False,
        "max_chars_per_line": 42, "max_lines": 2, "max_cps": 17.0,
        "source_clean_enabled": False, "incremental_retranslate_enabled": False,
    })
    from utils.source_clean import reconstruct_cleaned_srt
    data = [{"id": "1", "timecode": "00:00:01,000 --> 00:00:04,000",
             "original": "A long English source line for the first cue",
             "translated": LONG_GREEK, "translations": {"el": LONG_GREEK}}]
    meta = {"clean_to_orig_map": {"0": [0]}, "line_count": 1}
    storage.save_episode("TP", "E03", data, meta)
    ep_dir = storage.PROJECTS_DIR / "TP" / "episodes" / "E03"
    (ep_dir / "original.srt").write_text(ORIGINAL_SRT, encoding="utf-8")

    out = reconstruct_cleaned_srt("TP", "E03", data, "el")
    assert LONG_GREEK in out  # still one unwrapped line
