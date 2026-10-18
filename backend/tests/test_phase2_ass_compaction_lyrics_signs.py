"""
TDD Tests for Phase 2: Zero-Duplication ASS Storage, Lazy & Batch Compaction, Song Lyrics Memory, and Sign Fitting.
Covers F-01, D-02, D-05, F-11, F-18.
"""

import json
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils import ass_parser
from utils import ass_sign_fit
from utils import storage
from services import agent_workspace as aw
import scripts.compact_ass_episodes as compactor


SAMPLE_ASS_WITH_BLOAT = """[Script Info]
Title: Attack on Titan Sample
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1
Style: Sign,Arial,40,&H00FFFF00,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,8,10,10,10,1
Style: OP-ENG,Arial,36,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,8,10,10,10,1
Style: OP-Romaji,Arial,36,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,8,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Dialogue line one
Dialogue: 0,0:00:03.50,0:00:05.50,Sign,,0,0,0,,{\\pos(960,100)}District Wall Maria
Dialogue: 0,0:00:06.00,0:00:08.00,Default,,0,0,0,,{\\p1}m 0 0 l 100 0 100 100 0 100{\\p0}
Dialogue: 0,0:00:08.50,0:00:10.50,Default,,0,0,0,,{\\k20}Sei{\\k25}kund{\\k30}en
Dialogue: 0,0:00:11.00,0:00:13.00,OP-ENG,,0,0,0,,Like a scarlet arrow in the sky
Dialogue: 0,0:00:11.00,0:00:13.00,OP-Romaji,,0,0,0,,Guren no Yumiya
Dialogue: 0,0:00:14.00,0:00:16.00,Default,,0,0,0,,Dialogue line two
"""


# ============================================================================
# 1. Zero-Duplication Storage & Passthrough Preservation
# ============================================================================

def test_parse_ass_excludes_passthrough_by_default():
    """Verify parse_ass excludes drawing and karaoke by default, keeping only translatable cues."""
    entries = ass_parser.parse_ass(SAMPLE_ASS_WITH_BLOAT, include_passthrough=False)

    # Must contain: Dialogue line one, District Wall Maria, Like a scarlet arrow in the sky, Dialogue line two
    texts = [e["original"] for e in entries]
    assert "Dialogue line one" in texts
    assert "District Wall Maria" in texts
    assert "Like a scarlet arrow in the sky" in texts
    assert "Dialogue line two" in texts

    # Must NOT contain drawing or syllable/romaji karaoke
    assert not any("m 0 0" in e["original"] for e in entries)
    assert not any("Seikunden" in e["original"] for e in entries)
    assert not any("Guren no Yumiya" in e["original"] for e in entries)

    # Indices must map back to original event indices in subs.events
    assert entries[0]["_ass"]["index"] == 0
    assert entries[1]["_ass"]["index"] == 1
    assert entries[2]["_ass"]["index"] == 4
    assert entries[3]["_ass"]["index"] == 6


def test_parse_ass_includes_passthrough_when_requested():
    """Verify parse_ass includes drawing and karaoke when include_passthrough=True."""
    entries = ass_parser.parse_ass(SAMPLE_ASS_WITH_BLOAT, include_passthrough=True)
    assert len(entries) == 7

    drawing_entry = next(e for e in entries if e["_ass"]["kind"] == "drawing")
    assert drawing_entry["_ass"]["translatable"] is False

    karaoke_entry = next(e for e in entries if "Sei" in e["_ass"]["raw_text"])
    assert karaoke_entry["_ass"]["kind"] == "karaoke"
    assert karaoke_entry["_ass"]["translatable"] is False


def test_reconstruct_ass_preserves_passthrough_from_original():
    """Verify reconstruct_ass outputs 100% of drawings and karaoke from original.ass even when parsed_data has only translatable cues."""
    entries = ass_parser.parse_ass(SAMPLE_ASS_WITH_BLOAT, include_passthrough=False)
    assert len(entries) == 4

    # Translate the translatable cues
    entries[0]["translated"] = "Πρώτη γραμμή διαλόγου"
    entries[1]["translated"] = "Τείχος Μαρία"
    entries[2]["translated"] = "Σαν κόκκινο βέλος"
    entries[3]["translated"] = "Δεύτερη γραμμή διαλόγου"

    out = ass_parser.reconstruct_ass(entries, original_content=SAMPLE_ASS_WITH_BLOAT, target_lang_code="el")

    # Translations present
    assert "Πρώτη γραμμή διαλόγου" in out
    assert "Τείχος Μαρία" in out
    assert "Σαν κόκκινο βέλος" in out
    assert "Δεύτερη γραμμή διαλόγου" in out

    # Untranslated source dialogue replaced
    assert "Dialogue line one" not in out
    assert "District Wall Maria" not in out

    # Drawings and karaoke preserved intact from original.ass
    assert "{\\p1}m 0 0 l 100 0 100 100 0 100{\\p0}" in out
    assert "{\\k20}Sei{\\k25}kund{\\k30}en" in out
    assert "Guren no Yumiya" in out


# ============================================================================
# 2. Lazy Compaction on load_episode & Batch Compaction Script
# ============================================================================

def test_load_episode_lazy_compaction(tmp_path, monkeypatch):
    """Verify load_episode strips legacy drawing/karaoke bloat and updates metadata line_count."""
    proj = "TestShow"
    ep = "S01E01"
    ep_dir = tmp_path / proj / "episodes" / ep
    ep_dir.mkdir(parents=True)

    (ep_dir / "original.ass").write_text(SAMPLE_ASS_WITH_BLOAT, encoding="utf-8")

    # Write bloated data.json
    bloated_entries = [
        {"id": "1", "original": "Line 1", "translated": "Γραμμή 1", "_ass": {"index": 0, "translatable": True, "reason": "dialogue"}},
        {"id": "2", "original": "drawing", "translated": "", "_ass": {"index": 2, "translatable": False, "reason": "drawing"}},
        {"id": "3", "original": "karaoke", "translated": "", "_ass": {"index": 3, "translatable": False, "reason": "karaoke"}},
        {"id": "4", "original": "Line 2", "translated": "Γραμμή 2", "_ass": {"index": 6, "translatable": True, "reason": "dialogue"}},
    ]
    (ep_dir / "data.json").write_text(json.dumps({"data": bloated_entries}), encoding="utf-8")
    (ep_dir / "metadata.json").write_text(json.dumps({"line_count": 4}), encoding="utf-8")

    monkeypatch.setattr("utils.storage_modules.episode_store._safe_episode_dir", lambda p, e: ep_dir)
    monkeypatch.setattr(storage, "_safe_episode_dir", lambda p, e: ep_dir)
    monkeypatch.setattr(storage, "load_project_metadata", lambda p: {"target_language": "Greek"})

    loaded = storage.load_episode(proj, ep)
    assert loaded is not None
    assert len(loaded["data"]) == 2
    assert loaded["data"][0]["original"] == "Line 1"
    assert loaded["data"][1]["original"] == "Line 2"

    # Verify on-disk file was compacted
    disk_data = json.loads((ep_dir / "data.json").read_text(encoding="utf-8"))["data"]
    assert len(disk_data) == 2

    # Verify metadata.json was updated
    disk_meta = json.loads((ep_dir / "metadata.json").read_text(encoding="utf-8"))
    assert disk_meta["line_count"] == 2


def test_batch_compaction_script(tmp_path):
    """Verify scripts.compact_ass_episodes batch compaction across library."""
    proj_dir = tmp_path / "AnimeProject"
    ep1_dir = proj_dir / "episodes" / "E01"
    ep2_dir = proj_dir / "episodes" / "E02"
    ep1_dir.mkdir(parents=True)
    ep2_dir.mkdir(parents=True)

    # Ep 1: ASS with bloat
    (ep1_dir / "original.ass").write_text(SAMPLE_ASS_WITH_BLOAT, encoding="utf-8")
    ep1_data = [
        {"id": "1", "original": "D1", "_ass": {"index": 0, "translatable": True, "reason": "dialogue"}},
        {"id": "2", "original": "D2", "_ass": {"index": 1, "translatable": False, "reason": "drawing"}},
        {"id": "3", "original": "D3", "_ass": {"index": 2, "translatable": False, "reason": "karaoke"}},
    ]
    (ep1_dir / "data.json").write_text(json.dumps({"data": ep1_data}, indent=2), encoding="utf-8")
    (ep1_dir / "metadata.json").write_text(json.dumps({"line_count": 3}), encoding="utf-8")

    # Ep 2: SRT episode (no ASS) - should not be touched
    ep2_data = [{"id": "1", "original": "SRT line"}]
    (ep2_dir / "original.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nSRT line", encoding="utf-8")
    (ep2_dir / "data.json").write_text(json.dumps({"data": ep2_data}, indent=2), encoding="utf-8")

    # Dry run first
    dry_res = compactor.compact_library(projects_dir=tmp_path, dry_run=True)
    assert dry_res["episodes_compacted"] == 1
    assert dry_res["rows_removed"] == 2
    assert dry_res["bytes_saved"] > 0
    # Files should NOT be mutated yet
    assert len(json.loads((ep1_dir / "data.json").read_text(encoding="utf-8"))["data"]) == 3

    # Live run
    live_res = compactor.compact_library(projects_dir=tmp_path, dry_run=False)
    assert live_res["episodes_compacted"] == 1
    assert live_res["rows_removed"] == 2
    assert live_res["bytes_saved"] > 0

    # Ep1 compacted
    ep1_final = json.loads((ep1_dir / "data.json").read_text(encoding="utf-8"))["data"]
    assert len(ep1_final) == 1
    assert ep1_final[0]["original"] == "D1"

    # Ep2 untouched
    ep2_final = json.loads((ep2_dir / "data.json").read_text(encoding="utf-8"))["data"]
    assert len(ep2_final) == 1


# ============================================================================
# 3. Song Lyrics Memory & Agent Workspace Toggle
# ============================================================================

def test_translate_lyrics_toggle_in_parse_ass():
    """Verify translate_lyrics=False demotes lyrics cues to passthrough."""
    entries_with_lyrics = ass_parser.parse_ass(SAMPLE_ASS_WITH_BLOAT, include_passthrough=False, translate_lyrics=True)
    assert any(e["original"] == "Like a scarlet arrow in the sky" for e in entries_with_lyrics)

    entries_without_lyrics = ass_parser.parse_ass(SAMPLE_ASS_WITH_BLOAT, include_passthrough=False, translate_lyrics=False)
    assert not any(e["original"] == "Like a scarlet arrow in the sky" for e in entries_without_lyrics)


def test_agent_workspace_translate_lyrics_setting():
    """Verify agent_workspace respects translate_lyrics=True vs False."""
    cues = [
        {"original": "Hello", "_ass": {"kind": "dialogue", "style": "Default", "translatable": True}},
        {"original": "Shinzou wo Sasageyo", "_ass": {"kind": "lyrics", "style": "OP-English", "translatable": True}},
    ]

    # Enabled (default)
    units_on, stats_on = aw.build_units(list(cues), "el", translate_lyrics=True)
    assert len(units_on) == 2
    assert any(u.text == "Shinzou wo Sasageyo" for u in units_on)

    # Disabled
    units_off, stats_off = aw.build_units(list(cues), "el", translate_lyrics=False)
    assert len(units_off) == 1
    assert units_off[0].text == "Hello"


def test_song_lyrics_memory_record_and_apply():
    """Verify recording translated song lyrics and pre-filling them in subsequent episodes."""
    proj_meta = {}

    ep1_cues = [
        {"original": "Dedicate your hearts", "translated": "Αφιερώστε τις καρδιές σας", "_ass": {"kind": "lyrics", "style": "OP-English"}},
        {"original": "Normal dialogue", "translated": "Κανονικός διάλογος", "_ass": {"kind": "dialogue", "style": "Default"}},
    ]

    # Record into memory
    modified = ass_parser.record_song_lyrics_memory(ep1_cues, proj_meta, target_lang_code="el")
    assert modified is True
    assert "Dedicate your hearts" in proj_meta["song_lyrics_memory"]
    assert proj_meta["song_lyrics_memory"]["Dedicate your hearts"] == "Αφιερώστε τις καρδιές σας"
    assert "Normal dialogue" not in proj_meta["song_lyrics_memory"]

    # Ep 2 has untranslated song lyrics
    ep2_cues = [
        {"original": "Dedicate your hearts", "translated": "", "_ass": {"kind": "lyrics", "style": "OP-English"}},
        {"original": "New line", "translated": "", "_ass": {"kind": "dialogue", "style": "Default"}},
    ]

    applied = ass_parser.apply_song_lyrics_memory(ep2_cues, proj_meta, target_lang_code="el")
    assert applied == 1
    assert ep2_cues[0]["translated"] == "Αφιερώστε τις καρδιές σας"
    assert ep2_cues[0]["from_lyrics_memory"] is True
    assert ep2_cues[1]["translated"] == ""

    # build_workspace pre-fills and builds units
    units, stats = aw.build_workspace(ep2_cues, proj_meta, target_code="el", full=False)
    # The pre-filled lyrics cue should not need re-translation in gaps mode
    assert len(units) == 1
    assert units[0].text == "New line"


# ============================================================================
# 4. Multi-Frame Panning/Animated Sign Fitting
# ============================================================================

def test_multiframe_sign_fitting_consistency():
    """Verify multi-frame panning signs receive identical line wrap and scaling across frames."""
    import pysubs2

    ass_text = """[Script Info]
Title: Panning Sign
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sign,Arial,50,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,8,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # 5 frames panning across the screen: x moves from 100 to 1800
    for i, x in enumerate([100, 300, 700, 1200, 1750]):
        ass_text += f"Dialogue: 0,0:00:0{i}.00,0:00:0{i+1}.00,Sign,,0,0,0,,{{\\pos({x},100)}}Very Long Sign Title Over The Gate\n"

    subs = pysubs2.SSAFile.from_string(ass_text)

    # All frames translated to the same long Greek text
    long_greek_text = "Πολύ Μεγάλη Επιγραφή Πάνω Από Την Κεντρική Πύλη Της Περιοχής"
    raw_items = []
    for ev in subs.events:
        prefix = ev.text[:ev.text.find("}")+1]
        raw_items.append((ev, prefix, long_greek_text, "", ev.start, ev.end))

    changed = ass_sign_fit.fit_sign_events(subs, raw_items)
    assert changed == 5

    # Check that all frames received the EXACT same text formatting and tag injection
    fitted_texts = [ev.text[ev.text.find("}")+1:] for ev in subs.events]
    assert len(set(fitted_texts)) == 1, f"Jitter detected: frames received different fittings: {fitted_texts}"
