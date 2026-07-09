"""SubtitleEdit fixes: in-house auto-balancing + valid CLI flags.

SubtitleEdit's command line only accepts /fixcommonerrors for line work — the GUI
Batch-Convert actions (split/balance/merge) are NOT /convert parameters
(SubtitleEdit#7555). We therefore balance lines ourselves; these tests cover that
the balancing happens, that dialogue cues are preserved, and that the SE command no
longer passes the invalid flags.
"""

from pathlib import Path
from unittest.mock import patch

from utils import subtitle_fixer as sf


# --- in-house balancing -------------------------------------------------------

def test_balance_block_balances_long_single_line():
    long_line = "The quick brown fox jumps over the lazy dog near the river bank today"
    out = sf._balance_block(long_line, max_chars=42, max_lines=2)
    assert "\n" in out                        # it got wrapped
    parts = out.split("\n")
    assert len(parts) == 2                     # onto two lines
    assert all(len(p) <= 42 for p in parts)    # each within the limit
    assert abs(len(parts[0]) - len(parts[1])) <= 8   # genuinely balanced, not greedy
    assert " ".join(parts) == long_line        # no words lost/added


def test_balance_block_leaves_dialogue_cue_untouched():
    dialogue = "- Where have you been all this time?\n- Out looking for you, obviously."
    assert sf._balance_block(dialogue, max_chars=42, max_lines=2) == dialogue


def test_balance_block_leaves_short_line_untouched():
    short = "Short enough."
    assert sf._balance_block(short, max_chars=42, max_lines=2) == short


def test_balance_srt_preserves_cue_count_and_timecodes():
    srt = (
        "1\n00:00:01,000 --> 00:00:03,000\n"
        "This is a very long single line of subtitle text that clearly exceeds the limit\n\n"
        "2\n00:00:04,000 --> 00:00:05,000\n- One speaker.\n- Another speaker here.\n"
    )
    with patch("utils.subtitle_fixer.load_global_config",
               return_value={"max_chars_per_line": 42, "max_lines": 2}):
        out = sf._balance_srt(srt)

    from utils.srt_parser import parse_srt
    cues = parse_srt(out)
    assert len(cues) == 2                                  # cue count preserved
    assert cues[0]["timecode"] == "00:00:01,000 --> 00:00:03,000"
    assert "\n" in cues[0]["original"]                     # cue 1 balanced
    assert cues[1]["original"] == "- One speaker.\n- Another speaker here."  # dialogue intact


# --- CLI flag correctness -----------------------------------------------------

def test_run_se_cli_omits_gui_only_flags_and_balances(tmp_path):
    captured = {}

    def fake_run(cmd, *a, **k):
        captured["cmd"] = cmd
        outdir = next(c.split(":", 1)[1] for c in cmd if c.startswith("/outputfolder:"))
        Path(outdir, "input.srt").write_text(
            "1\n00:00:01,000 --> 00:00:03,000\n"
            "This is a very long single line of subtitle text that clearly exceeds the limit\n",
            encoding="utf-8",
        )

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    with patch("utils.subtitle_fixer._se_executable", return_value=str(tmp_path / "SE.exe")), \
         patch("utils.subtitle_fixer.subprocess.run", side_effect=fake_run), \
         patch("utils.subtitle_fixer.load_global_config",
               return_value={"max_chars_per_line": 42, "max_lines": 2}):
        result = sf._run_se_cli("1\n00:00:01,000 --> 00:00:03,000\nwhatever\n")

    cmd = captured["cmd"]
    assert "/fixcommonerrors" in cmd
    assert "/splitlonglines" not in cmd        # GUI-only, removed
    assert "/balancelines" not in cmd          # GUI-only, removed
    assert "/mergesametexts" not in cmd        # GUI-only, removed
    # SE output was auto-balanced on the way back out.
    assert "\n" in result.split("00:00:01,000 --> 00:00:03,000\n", 1)[1]
