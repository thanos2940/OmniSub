"""
TDD Tests for Phase 1: Review Queue TM Recording, Debounced NAS Export, and ASS deleted=True Cue Skipping.
Covers F-05 and F-19.
"""

import asyncio
from unittest.mock import MagicMock
import pytest

from routers import review
from routers.review import (
    resolve_review_item,
    resolve_all_review_items,
    save_review_line,
    _export_tasks,
)
from utils import storage
from utils.ass_parser import parse_ass, reconstruct_ass, export_ass


@pytest.fixture(autouse=True)
def cleanup_export_tasks():
    """Cancel and clear any pending export tasks after each test."""
    yield
    for task in list(_export_tasks.values()):
        if not task.done():
            task.cancel()
    _export_tasks.clear()


# ============================================================================
# F-05: Review Queue TM Recording & Immediate/Debounced Export
# ============================================================================

@pytest.mark.asyncio
async def test_resolve_review_item_records_tm_when_text_changed(monkeypatch):
    """Verify resolve_review_item records user edits to TM when translated_text changes."""
    project_name = "test_proj"
    episode_name = "S01E01"

    lines = [
        {
            "index": 0,
            "original": "Good morning",
            "translated": "Καλημέρα παλιό",
            "translations": {"el": "Καλημέρα παλιό"},
            "needs_review": True,
            "review_issues": "low confidence",
        }
    ]
    saved_data = []

    monkeypatch.setattr(storage, "load_episode", lambda p, e: {"data": lines})
    monkeypatch.setattr(storage, "load_project_metadata", lambda p: {"target_language": "Greek"})
    monkeypatch.setattr(storage, "load_episode_metadata", lambda p, e: {})
    monkeypatch.setattr(storage, "save_episode", lambda p, e, d, m: saved_data.append((d, m)))

    mock_record_user_edits = MagicMock()
    monkeypatch.setattr("services.translation.helpers._record_user_edits", mock_record_user_edits)

    mock_auto_export = MagicMock(return_value="/media/test.el.srt")
    monkeypatch.setattr("services.translation.export_formatter.auto_export_translated_subtitle", mock_auto_export)

    res = await resolve_review_item(
        project_name=project_name,
        episode_name=episode_name,
        line_index=0,
        translated_text="Καλημέρα καινούριο",
    )

    assert res["status"] == "resolved"
    assert res["all_resolved"] is True
    assert res["delivered"] is True
    assert res["output_path"] == "/media/test.el.srt"

    # TM was updated
    assert mock_record_user_edits.called
    call_args = mock_record_user_edits.call_args
    assert call_args[0][0] == project_name
    assert call_args[0][1] == episode_name
    assert call_args[0][2][0]["translated"] == "Καλημέρα παλιό"
    assert call_args[0][3][0]["translated"] == "Καλημέρα καινούριο"
    assert call_args[1]["lang"] == "el"

    # line flags cleared
    assert "needs_review" not in lines[0]


@pytest.mark.asyncio
async def test_resolve_review_item_no_tm_when_text_unchanged(monkeypatch):
    """Verify resolve_review_item does NOT record TM when text is unchanged."""
    project_name = "test_proj"
    episode_name = "S01E01"

    lines = [
        {
            "index": 0,
            "original": "Good morning",
            "translated": "Καλημέρα",
            "translations": {"el": "Καλημέρα"},
            "needs_review": True,
        }
    ]

    monkeypatch.setattr(storage, "load_episode", lambda p, e: {"data": lines})
    monkeypatch.setattr(storage, "load_project_metadata", lambda p: {"target_language": "Greek"})
    monkeypatch.setattr(storage, "load_episode_metadata", lambda p, e: {})
    monkeypatch.setattr(storage, "save_episode", lambda *a, **k: None)

    mock_record_user_edits = MagicMock()
    monkeypatch.setattr("services.translation.helpers._record_user_edits", mock_record_user_edits)
    monkeypatch.setattr("services.translation.export_formatter.auto_export_translated_subtitle", lambda *a, **k: None)

    res = await resolve_review_item(
        project_name=project_name,
        episode_name=episode_name,
        line_index=0,
        translated_text="Καλημέρα",
    )

    assert res["status"] == "resolved"
    assert not mock_record_user_edits.called


@pytest.mark.asyncio
async def test_resolve_review_item_debounced_when_lines_remain(monkeypatch):
    """Verify debounced export is scheduled when lines still have needs_review=True."""
    project_name = "test_proj"
    episode_name = "S01E01"

    lines = [
        {"index": 0, "original": "Hi", "translated": "Γεια", "needs_review": True},
        {"index": 1, "original": "Bye", "translated": "Αντίο", "needs_review": True},
    ]

    monkeypatch.setattr(storage, "load_episode", lambda p, e: {"data": lines})
    monkeypatch.setattr(storage, "load_project_metadata", lambda p: {"target_language": "Greek"})
    monkeypatch.setattr(storage, "load_episode_metadata", lambda p, e: {})
    monkeypatch.setattr(storage, "save_episode", lambda *a, **k: None)

    mock_auto_export = MagicMock()
    monkeypatch.setattr("services.translation.export_formatter.auto_export_translated_subtitle", mock_auto_export)

    res = await resolve_review_item(
        project_name=project_name,
        episode_name=episode_name,
        line_index=0,
    )

    # All resolved is False because line 1 still needs review
    assert res["all_resolved"] is False
    assert res["delivered"] is False
    # Immediate auto_export should NOT be called
    assert not mock_auto_export.called
    # A debounced task is registered
    key = (project_name, episode_name)
    assert key in _export_tasks
    assert not _export_tasks[key].done()


@pytest.mark.asyncio
async def test_save_review_line_records_tm_and_schedules_debounce(monkeypatch):
    """Verify save_review_line records TM edit and schedules debounced export."""
    project_name = "test_proj"
    episode_name = "S01E01"

    lines = [
        {
            "index": 0,
            "original": "Good afternoon",
            "translated": "Παλιά μετάφραση",
            "translations": {"el": "Παλιά μετάφραση"},
            "needs_review": True,
        }
    ]

    monkeypatch.setattr(storage, "load_episode", lambda p, e: {"data": lines})
    monkeypatch.setattr(storage, "load_project_metadata", lambda p: {"target_language": "Greek"})
    monkeypatch.setattr(storage, "load_episode_metadata", lambda p, e: {})
    monkeypatch.setattr(storage, "save_episode", lambda *a, **k: None)

    mock_record_user_edits = MagicMock()
    monkeypatch.setattr("services.translation.helpers._record_user_edits", mock_record_user_edits)

    res = await save_review_line(
        project_name=project_name,
        episode_name=episode_name,
        line_index=0,
        translated_text="Νέα μετάφραση",
    )

    assert res["status"] == "saved"
    assert lines[0]["translated"] == "Νέα μετάφραση"
    assert lines[0]["translations"]["el"] == "Νέα μετάφραση"

    assert mock_record_user_edits.called
    key = (project_name, episode_name)
    assert key in _export_tasks


@pytest.mark.asyncio
async def test_resolve_all_review_items_clears_and_exports(monkeypatch):
    """Verify resolve_all_review_items uses metadata index, clears flags, and exports subtitles."""
    project_name = "test_proj"

    ep1_data = [{"index": 0, "original": "Line 1", "translated": "Γ1", "needs_review": True}]
    ep2_data = [{"index": 0, "original": "Line 2", "translated": "Γ2", "needs_review": True}]

    def fake_load_episode(p, e):
        if e == "ep1":
            return {"data": ep1_data}
        if e == "ep2":
            return {"data": ep2_data}
        return None

    monkeypatch.setattr(storage, "load_episode", fake_load_episode)
    monkeypatch.setattr(storage, "load_project_metadata", lambda p: {"target_language": "Greek"})
    monkeypatch.setattr(storage, "load_episode_metadata", lambda p, e: {})
    monkeypatch.setattr(storage, "save_episode", lambda *a, **k: None)

    # Mock metadata index to return the two episodes with review
    mock_list_with_review = MagicMock(return_value=[("test_proj", "ep1"), ("test_proj", "ep2")])
    monkeypatch.setattr("utils.metadata_index.list_with_review", mock_list_with_review)

    mock_auto_export = MagicMock(side_effect=lambda p, e, *a, **k: f"/exports/{e}.srt")
    monkeypatch.setattr("services.translation.export_formatter.auto_export_translated_subtitle", mock_auto_export)

    res = await resolve_all_review_items(project_name=project_name)

    assert res["status"] == "resolved"
    assert res["count"] == 2
    assert "ep1" in res["delivered_episodes"]
    assert "ep2" in res["delivered_episodes"]

    assert "needs_review" not in ep1_data[0]
    assert "needs_review" not in ep2_data[0]
    assert mock_auto_export.call_count == 2


# ============================================================================
# F-19: ASS deleted=True Cue Skipping in reconstruct_ass / export_ass
# ============================================================================

SAMPLE_ASS = """[Script Info]
Title: Sample
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,50,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,20,20,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,First line
Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,Second line to delete
Dialogue: 0,0:00:07.00,0:00:09.00,Default,,0,0,0,,Third line kept
"""


def test_reconstruct_ass_skips_deleted_cues_with_original_content():
    """Verify that cues with deleted=True are omitted from reconstructed ASS with original_content."""
    parsed = parse_ass(SAMPLE_ASS)
    assert len(parsed) == 3

    # Mark second line as deleted
    parsed[0]["translated"] = "Πρώτη γραμμή"
    parsed[1]["deleted"] = True
    parsed[2]["translated"] = "Τρίτη γραμμή"

    out = reconstruct_ass(parsed, original_content=SAMPLE_ASS, target_lang_code="el")

    assert "First line" not in out
    assert "Πρώτη γραμμή" in out
    assert "Second line to delete" not in out
    assert "Third line kept" not in out
    assert "Τρίτη γραμμή" in out


def test_reconstruct_ass_skips_deleted_cues_fallback_no_original():
    """Verify that cues with deleted=True are omitted in fallback (no original_content)."""
    parsed = [
        {"original": "Line 1", "translated": "Γραμμή 1", "timecode": "00:00:01,000 --> 00:00:02,000"},
        {"original": "Line 2", "translated": "Γραμμή 2", "deleted": True, "timecode": "00:00:03,000 --> 00:00:04,000"},
        {"original": "Line 3", "translated": "Γραμμή 3", "timecode": "00:00:05,000 --> 00:00:06,000"},
    ]

    out = reconstruct_ass(parsed, original_content=None, target_lang_code="el")

    assert "Γραμμή 1" in out
    assert "Γραμμή 2" not in out
    assert "Γραμμή 3" in out


def test_reconstruct_ass_sequential_fallback_skips_deleted():
    """Verify plain rows (no _ass metadata) in original_content skip deleted=True."""
    parsed = [
        {"original": "Line 1", "translated": "Γραμμή 1"},
        {"original": "Line 2", "translated": "Γραμμή 2", "deleted": True},
        {"original": "Line 3", "translated": "Γραμμή 3"},
    ]

    out = reconstruct_ass(parsed, original_content=SAMPLE_ASS, target_lang_code="el")

    assert "Γραμμή 1" in out
    assert "Second line to delete" not in out
    assert "Γραμμή 2" not in out
    assert "Γραμμή 3" in out


def test_export_ass_alias():
    """Verify export_ass is an alias for reconstruct_ass in ass_parser."""
    assert export_ass is reconstruct_ass
