"""End-to-end wiring tests for ASS/SSA support.

Covers the fixes that route ASS through the pipeline without SRT-shaped damage:
  * ingestion bypasses source cleaning + cue-merging (strict 1:1 event mapping),
  * reconstruction preserves script info / styles / karaoke and only rewrites
    translatable dialogue,
  * the scanner treats a target as "already translated" only when it matches the
    source's subtitle format (decision D2).
"""

from pathlib import Path

import pytest

from utils import storage
from utils.source_clean import import_and_clean_srt, reconstruct_cleaned_srt
from integrations.subtitle_scanner import SubtitleScannerService


# Two adjacent dialogue events that the SRT clean/merge pass WOULD fold together
# (first line has no terminal punctuation, gap < 500 ms), plus a karaoke event.
SAMPLE_ASS = """[Script Info]
Title: Pipeline Test
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,I was walking home
Dialogue: 0,0:00:02.10,0:00:03.00,Default,,0,0,0,,when it started to rain
Dialogue: 0,0:00:05.00,0:00:07.00,Default,,0,0,0,,{\\k50}la {\\k50}la {\\k50}la
"""


@pytest.fixture
def temp_projects_dir(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)
    # Deterministic config: cleaning + merging ON so the ASS *bypass* is what's tested.
    monkeypatch.setattr(storage, "load_global_config", lambda: {
        "source_clean_enabled": True,
        "merge_split_cues": True,
        "strip_sdh": True,
        "preserve_italics": True,
        "incremental_retranslate_enabled": False,
    })
    return projects_dir


def test_ass_import_bypasses_clean_and_merge(temp_projects_dir):
    """ASS keeps a strict 1:1 event mapping — no cue merging that would later
    strand the merged-away event as untranslated source text."""
    import_and_clean_srt("proj", "E01", SAMPLE_ASS, filename="video.ass")

    ep = storage.load_episode("proj", "E01")
    data = ep["data"]

    # All three events survive as distinct entries (the two mergeable dialogue lines
    # were NOT folded together).
    assert len(data) == 3

    meta = storage.load_episode_metadata("proj", "E01")
    assert meta.get("original_format") == "ass"
    # clean_to_orig map is identity (each cleaned cue maps to exactly its own raw cue).
    cmap = meta.get("clean_to_orig_map") or {}
    assert len(cmap) == 3
    for k, v in cmap.items():
        assert storage and (v == int(k) or v == [int(k)])

    # The karaoke event is flagged non-translatable so it never reaches the LLM.
    karaoke = data[2]
    assert karaoke.get("_ass", {}).get("translatable") is False
    assert karaoke.get("_ass", {}).get("kind") == "karaoke"


def test_ass_reconstruction_preserves_styles_and_karaoke(temp_projects_dir):
    """Export re-injects translations into the original document, preserving script
    info / styles and leaving karaoke events verbatim."""
    import_and_clean_srt("proj", "E02", SAMPLE_ASS, filename="video.ass")

    ep = storage.load_episode("proj", "E02")
    data = ep["data"]
    # Translate the first dialogue line only.
    data[0]["translated"] = "Περπατούσα προς το σπίτι"
    data[0]["translations"] = {"el": "Περπατούσα προς το σπίτι"}

    out = reconstruct_cleaned_srt("proj", "E02", data, "el")

    # Preserved structural sections + style.
    assert "[Script Info]" in out
    assert "[V4+ Styles]" in out
    assert "Style: Default" in out
    # Translation landed on the dialogue event.
    assert "Περπατούσα προς το σπίτι" in out
    # Karaoke markup passed through untouched (not translated, not stripped).
    assert "\\k50" in out
    # Output is an ASS document, not SRT (no "-->" arrow-style cue block).
    assert "Dialogue:" in out


def test_ass_line_deletion_drops_cue_from_export(temp_projects_dir):
    """Deleting a cue in the editor removes it from the reconstructed .ass export
    instead of leaving the untranslated source text behind (was the last ASS gap)."""
    import asyncio
    from routers.episodes import delete_episode_lines
    from routers.schemas import DeleteLinesRequest

    import_and_clean_srt("proj", "E03", SAMPLE_ASS, filename="video.ass")

    ep = storage.load_episode("proj", "E03")
    data = ep["data"]
    for e in data:
        if e.get("_ass", {}).get("translatable"):
            e["translated"] = "[T] " + e["original"]
    storage.save_episode("proj", "E03", data, storage.load_episode_metadata("proj", "E03"))

    # Delete the second dialogue line ("when it started to rain").
    target_idx = next(i for i, e in enumerate(data) if "rain" in e["original"])
    asyncio.run(delete_episode_lines("proj", "E03", DeleteLinesRequest(indexes=[target_idx])))

    ep = storage.load_episode("proj", "E03")
    out = reconstruct_cleaned_srt("proj", "E03", ep["data"], "el")

    # The deleted cue is gone entirely — neither its translation nor its raw source.
    assert "rain" not in out
    assert "[T] when it started to rain" not in out
    # The surviving dialogue and the untouched karaoke event remain.
    assert "[T] I was walking home" in out
    assert "\\k50" in out


def _scan(files, stem, src="en", tgt="el"):
    svc = SubtitleScannerService(source_lang_code=src, target_lang_code=tgt)
    return svc._match_source_target([Path(f) for f in files], stem)


def test_scanner_same_format_target_detection():
    stem = "Show.S01E01"

    # ASS source, only an SRT target present -> mismatched format is NOT counted.
    src, ext, tgt, _all = _scan([f"{stem}.en.ass", f"{stem}.el.srt"], stem)
    assert src is not None and ext == ".ass"
    assert tgt is None

    # ASS source with an ASS target -> counted.
    src, ext, tgt, _all = _scan([f"{stem}.en.ass", f"{stem}.el.ass"], stem)
    assert tgt is not None and tgt.endswith(".el.ass")

    # SRT source with an SRT target -> unchanged classic behavior.
    src, ext, tgt, _all = _scan([f"{stem}.en.srt", f"{stem}.el.srt"], stem)
    assert ext == ".srt" and tgt is not None and tgt.endswith(".el.srt")

    # No source present -> fall back to any supported target extension.
    src, ext, tgt, _all = _scan([f"{stem}.el.ass"], stem)
    assert src is None
    assert tgt is not None and tgt.endswith(".el.ass")


def test_scanner_deterministic_primary_and_all_sources():
    """When several source formats coexist for one media file, the primary pick is
    deterministic (richest format first) and every source file is surfaced."""
    stem = "Show.S01E01"

    # .srt listed first, but .ass must win the primary slot (richest-first).
    src, ext, tgt, allsrc = _scan([f"{stem}.en.srt", f"{stem}.en.ass"], stem)
    assert ext == ".ass"
    assert len(allsrc) == 2
    assert allsrc[0].endswith(".en.ass")                 # primary first
    assert any(p.endswith(".en.srt") for p in allsrc)    # secondary surfaced

    # Single source -> just that one, no phantom entries.
    src, ext, tgt, allsrc = _scan([f"{stem}.en.srt"], stem)
    assert allsrc == [f"{stem}.en.srt"]
