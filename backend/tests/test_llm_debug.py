"""LLM response-capture diagnosis (for the split-shift investigation)."""

import json

from utils import llm_debug


def test_detect_split_flags_overflow():
    # Model split one cue into two → 3 entries for 2 expected lines.
    parsed = [
        {"index": 1, "text": "first half"},
        {"index": 2, "text": "second half (leaked)"},
        {"index": 3, "text": "real line two"},
    ]
    diag = llm_debug.detect_split(parsed, expected_count=2)
    assert diag["overflow"] == 1
    assert diag["parsed_count"] == 3


def test_detect_split_flags_duplicates_and_missing():
    parsed = [
        {"index": 1, "text": "a"},
        {"index": 1, "text": "b"},   # duplicate index
        {"index": 3, "text": "c"},   # index 2 missing
    ]
    diag = llm_debug.detect_split(parsed, expected_count=3)
    assert diag["duplicate_indices"] == [1]
    assert diag["missing_indices"] == [2]


def test_detect_split_clean_response():
    parsed = [{"index": 1, "text": "a"}, {"index": 2, "text": "b"}]
    diag = llm_debug.detect_split(parsed, expected_count=2)
    assert diag["overflow"] == 0
    assert diag["duplicate_indices"] == []
    assert diag["missing_indices"] == []


def test_save_response_writes_inspectable_dump(tmp_path, monkeypatch):
    import utils.storage as storage
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path)

    parsed = [
        {"index": 1, "text": "Το Plex ζήτησε 120€", "src_head": "The Plex asked"},
        {"index": 2, "text": "από τις συσκευές σας", "src_head": "The Plex asked"},  # leaked
    ]
    src = ["The Plex asked for 120 to continue", "If you use it often"]

    path = llm_debug.save_response(
        "proj", "S01E01", "whole_episode",
        prompt="P", raw_response='{"lines":[...]}', parsed=parsed,
        expected_count=3, source_texts=src,   # expected 3 but got 2 → overflow -1
        extra={"verify_echo": True},
    )
    assert path is not None
    data = json.loads((tmp_path / "proj" / "episodes" / "S01E01" / "debug").glob("whole_episode_*.json").__next__().read_text(encoding="utf-8"))
    assert data["count_delta"] == -1
    # The breakdown pairs each entry's echo with the source it SHOULD map to.
    assert data["parsed_entries"][1]["expected_source"].startswith("If you use it often")
    assert data["parsed_entries"][1]["translated"].startswith("από τις συσκευές")
    assert data["extra"]["verify_echo"] is True
