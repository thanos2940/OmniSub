"""Tests for permanent subtitle line deletion (delete-lines endpoint).

Covers:
  - removing lines from episode data and re-keying clean_to_orig_map
  - recording deleted original cue indexes in deleted_orig_indexes
  - reconstruct_cleaned_srt dropping deleted cues and renumbering sequentially
"""

import asyncio
import pytest

import utils.storage as storage
import utils.metadata_index as metadata_index


ORIGINAL_SRT = """1
00:00:01,000 --> 00:00:02,000
[music]

2
00:00:03,000 --> 00:00:04,000
Hello there

3
00:00:05,000 --> 00:00:06,000
How are you

4
00:00:07,000 --> 00:00:08,000
I am fine

5
00:00:09,000 --> 00:00:10,000
Goodbye
"""


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(metadata_index, "upsert", lambda *a, **k: None)
    monkeypatch.setattr(metadata_index, "count_translated", lambda *a, **k: None)
    monkeypatch.setattr(metadata_index, "set_review_count", lambda *a, **k: None)

    storage.create_project("TP", {"target_language": "Greek"})

    # Clean data: original cue 0 ([music]) was stripped during import, so the
    # 4 clean lines map to original cues 1..4.
    data = [
        {"id": str(i + 1), "timecode": f"00:00:0{i},000 --> 00:00:0{i},500",
         "original": text, "translated": f"ΜΕΤ {i}", "translations": {"el": f"ΜΕΤ {i}"}}
        for i, text in enumerate(["Hello there", "How are you", "I am fine", "Goodbye"])
    ]
    # Production clean_to_orig_map values are LISTS (a cleaned cue can map to several
    # raw cues after the merge pass), so the fixture must use the list shape.
    meta = {
        "clean_to_orig_map": {"0": [1], "1": [2], "2": [3], "3": [4]},
        "line_count": 4,
    }
    storage.save_episode("TP", "E01", data, meta)

    ep_dir = storage.PROJECTS_DIR / "TP" / "episodes" / "E01"
    (ep_dir / "original.srt").write_text(ORIGINAL_SRT, encoding="utf-8")


def test_delete_lines_rekeys_map_and_records_deleted_origs(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from routers.episodes import delete_episode_lines
    from routers.schemas import DeleteLinesRequest

    # Delete clean lines 1 ("How are you") and 3 ("Goodbye")
    result = asyncio.run(delete_episode_lines("TP", "E01", DeleteLinesRequest(indexes=[1, 3])))
    assert result["status"] == "success"
    assert result["deleted"] == 2
    assert result["line_count"] == 2

    ep = storage.load_episode("TP", "E01")
    originals = [l["original"] for l in ep["data"]]
    assert originals == ["Hello there", "I am fine"]

    meta = storage.load_episode_metadata("TP", "E01")
    assert meta["clean_to_orig_map"] == {"0": [1], "1": [3]}
    assert meta["deleted_orig_indexes"] == [2, 4]
    assert meta["line_count"] == 2


def test_delete_lines_invalid_indexes_raises(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from fastapi import HTTPException
    from routers.episodes import delete_episode_lines
    from routers.schemas import DeleteLinesRequest

    with pytest.raises(HTTPException):
        asyncio.run(delete_episode_lines("TP", "E01", DeleteLinesRequest(indexes=[99])))


def test_reconstruct_skips_deleted_cues_and_renumbers(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from routers.episodes import delete_episode_lines
    from routers.schemas import DeleteLinesRequest
    from utils.source_clean import reconstruct_cleaned_srt

    asyncio.run(delete_episode_lines("TP", "E01", DeleteLinesRequest(indexes=[1, 3])))

    ep = storage.load_episode("TP", "E01")
    srt = reconstruct_cleaned_srt("TP", "E01", ep["data"], "el")

    # Deleted cues are gone entirely (not even original-language fallback)
    assert "How are you" not in srt
    assert "Goodbye" not in srt
    # Surviving translations are present
    assert "ΜΕΤ 0" in srt
    assert "ΜΕΤ 2" in srt
    # Cue numbering is sequential with no gaps
    blocks = [b for b in srt.strip().split("\n\n") if b.strip()]
    numbers = [int(b.splitlines()[0]) for b in blocks]
    assert numbers == list(range(1, len(blocks) + 1))
