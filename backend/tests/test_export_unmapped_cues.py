"""Regression: exported subtitles must never resurrect English source text for cues
that source cleaning removed or merged away.

Real-world case (Fate/Grand Order Babylonia S01E19): the raw .srt had 489 cues, the
cleaned editor data 305. The 184 removed/absorbed cues (karaoke lyrics, SDH, merged
split-fragments) had no entry in clean_to_orig_map, and the exporter kept them with an
empty translation — which the serializer then filled with the ENGLISH source text.
Result: 183 English cues interleaved with the Greek output, including duplicates
overlapping the merged Greek cue on screen.
"""
import pytest

from utils import storage
from utils.source_clean import reconstruct_cleaned_srt


RAW_SRT = """1
00:12:44,510 --> 00:12:47,420
Even if everything in this era had been called into service,

2
00:12:47,420 --> 00:12:50,340
I have a feeling it would've ended here.

3
00:12:50,900 --> 00:12:52,000
The bottom so distant kurai soko wa

4
00:12:52,050 --> 00:12:56,420
You are a foreigner.
"""

MERGED_GREEK = "Ακόμα κι αν ολόκληρη αυτή η εποχή είχε επιστρατευτεί, έχω την αίσθηση πως θα είχε τελειώσει εδώ."
FOREIGNER_GREEK = "Είσαι ένας ξένος."

CLEANED_DATA = [
    {
        "id": "1",
        "timecode": "00:12:44,510 --> 00:12:50,340",  # merged span of raw cues 1+2
        "original": "Even if everything in this era had been called into service, I have a feeling it would've ended here.",
        "translated": MERGED_GREEK,
        "translations": {"el": MERGED_GREEK},
    },
    {
        "id": "2",
        "timecode": "00:12:52,050 --> 00:12:56,420",
        "original": "You are a foreigner.",
        "translated": FOREIGNER_GREEK,
        "translations": {"el": FOREIGNER_GREEK},
    },
]


@pytest.fixture
def episode(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)
    storage.create_project("TP")
    storage.save_original_subtitle("TP", "E01", RAW_SRT, filename="e01.en.srt")
    return projects_dir


def _export_with_map(clean_to_orig_map):
    meta = storage.load_episode_metadata("TP", "E01") or {}
    if clean_to_orig_map is not None:
        meta["clean_to_orig_map"] = clean_to_orig_map
    else:
        meta.pop("clean_to_orig_map", None)
    storage.save_episode_metadata("TP", "E01", meta)
    return reconstruct_cleaned_srt("TP", "E01", CLEANED_DATA, target_lang_code="el")


def test_legacy_primary_only_map_drops_absorbed_and_removed_cues(episode):
    """Old imports recorded only the primary original index per clean cue. The
    absorbed fragment (raw cue 2) and removed karaoke line (raw cue 3) are unmapped
    and must be dropped — not exported as English."""
    out = _export_with_map({"0": 0, "1": 3})
    assert MERGED_GREEK.split()[0] in out              # Greek present
    assert FOREIGNER_GREEK in out
    assert "I have a feeling" not in out               # absorbed fragment gone
    assert "kurai soko" not in out                     # removed karaoke gone
    assert "called into service," not in out.replace(MERGED_GREEK, "")  # no English primary either


def test_modern_multi_index_map_same_result(episode):
    out = _export_with_map({"0": [0, 1], "1": [3]})
    assert MERGED_GREEK.split()[0] in out
    assert "I have a feeling" not in out
    assert "kurai soko" not in out


def test_merged_timecode_spans_both_fragments(episode):
    out = _export_with_map({"0": [0, 1], "1": [3]})
    assert "00:12:44,510" in out
    # The absorbed fragment's standalone start time must not appear as a cue start
    # (it may appear as the merged cue's end — assert on the start position only).
    assert "\n00:12:47,420 -->" not in out


def test_missing_map_falls_back_to_cleaned_rows(episode):
    """Episodes imported before the map existed: emit the cleaned/translated rows
    directly instead of treating every raw cue as unmapped (which would now export
    an empty file)."""
    out = _export_with_map(None)
    assert MERGED_GREEK.split()[0] in out
    assert FOREIGNER_GREEK in out
    assert "I have a feeling" not in out
    assert "kurai soko" not in out


def test_gap_lines_still_fall_back_to_source(episode):
    """A mapped cue whose translation genuinely failed (flagged gap) keeps the
    existing English-fallback behavior — that's a separate, deliberate policy."""
    data = [dict(CLEANED_DATA[0]), dict(CLEANED_DATA[1])]
    data[1] = {**data[1], "translated": "", "translations": {}}
    meta = storage.load_episode_metadata("TP", "E01") or {}
    meta["clean_to_orig_map"] = {"0": [0, 1], "1": [3]}
    storage.save_episode_metadata("TP", "E01", meta)
    out = reconstruct_cleaned_srt("TP", "E01", data, target_lang_code="el")
    assert "You are a foreigner." in out
