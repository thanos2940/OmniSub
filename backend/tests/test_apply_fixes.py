"""Manual 'Apply common fixes' rebuild logic (SubtitleEdit), with the SE CLI mocked.

These cover the part we can test without SubtitleEdit installed: rendering a track to
SRT, re-reading a (re-segmented) output, and re-attaching the counterpart track by
timecode overlap — including the line-count-changing split case.
"""

import pytest
from unittest.mock import patch

from utils import subtitle_fixer
from utils.subtitle_fixer import apply_common_fixes, SubtitleEditUnavailable


def _cue(cid, tc, original, translated=""):
    return {
        "id": cid, "timecode": tc, "original": original,
        "translated": translated,
        "translations": ({"el": translated} if translated else {}),
    }


# Three cues; the middle one is long and the mocked SE splits it into two.
_ROWS = [
    _cue("1", "00:00:01,000 --> 00:00:02,000", "Hello there", "Γεια σου"),
    _cue("2", "00:00:03,000 --> 00:00:06,000", "This is a very long line that gets split", "Μια πολύ μακριά γραμμή"),
    _cue("3", "00:00:07,000 --> 00:00:08,000", "Bye", "Αντίο"),
]

_SPLIT_OUTPUT = (
    "1\n00:00:01,000 --> 00:00:02,000\nHello there\n\n"
    "2\n00:00:03,000 --> 00:00:04,500\nThis is a very long line\n\n"
    "3\n00:00:04,600 --> 00:00:06,000\nthat gets split\n\n"
    "4\n00:00:07,000 --> 00:00:08,000\nBye\n"
)


def test_unconfigured_se_raises():
    with patch.object(subtitle_fixer, "_se_executable", return_value=None):
        with pytest.raises(SubtitleEditUnavailable):
            apply_common_fixes(_ROWS, "source", "el", "el")


def test_source_split_rebuilds_and_flags_new_line():
    with patch.object(subtitle_fixer, "_run_se_cli", return_value=_SPLIT_OUTPUT):
        new_data, stats = apply_common_fixes(_ROWS, "source", "el", "el")

    assert stats == {"before": 3, "after": 4, "changed": True, "track": "source"}
    # Fixed text becomes the source on each cue.
    assert [c["original"] for c in new_data] == [
        "Hello there", "This is a very long line", "that gets split", "Bye",
    ]
    # Untouched cues keep their translation.
    assert new_data[0]["translated"] == "Γεια σου"
    assert new_data[3]["translated"] == "Αντίο"
    # First half of a split carries the old translation; the new half is flagged.
    assert new_data[1]["translated"] == "Μια πολύ μακριά γραμμή"
    assert new_data[2]["translated"] == ""
    assert new_data[2]["needs_review"] is True


def test_target_attaches_source_by_timecode():
    # Same split, but on the translation track: fixed text → translations[el],
    # source re-attached by timecode overlap for editor context.
    with patch.object(subtitle_fixer, "_run_se_cli", return_value=_SPLIT_OUTPUT):
        new_data, stats = apply_common_fixes(_ROWS, "target", "el", "el")

    assert stats["after"] == 4 and stats["track"] == "target"
    # The two split cues both overlap the long middle source cue.
    assert new_data[1]["original"] == "This is a very long line that gets split"
    assert new_data[2]["original"] == "This is a very long line that gets split"
    # Fixed translation text lands on the language track.
    assert new_data[1]["translations"]["el"] == "This is a very long line"
    assert new_data[2]["translations"]["el"] == "that gets split"
    assert new_data[1]["translated"] == "This is a very long line"


def test_no_change_keeps_line_count():
    identity = (
        "1\n00:00:01,000 --> 00:00:02,000\nHello there\n\n"
        "2\n00:00:03,000 --> 00:00:06,000\nThis is a very long line that gets split\n\n"
        "3\n00:00:07,000 --> 00:00:08,000\nBye\n"
    )
    with patch.object(subtitle_fixer, "_run_se_cli", return_value=identity):
        new_data, stats = apply_common_fixes(_ROWS, "source", "el", "el")
    assert stats["changed"] is False
    assert len(new_data) == 3
    assert all(not c.get("needs_review") for c in new_data)
    assert [c["translated"] for c in new_data] == ["Γεια σου", "Μια πολύ μακριά γραμμή", "Αντίο"]
