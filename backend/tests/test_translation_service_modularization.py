"""
Unit tests for translation service decomposition into single-responsibility submodules.
"""

import pytest
from services.translation.helpers import (
    _parse_json_response,
    _merge_glossaries,
    _select_golden_ratio_files,
)
from services.translation.atomic_runner import infer_speaker
from services.translation.export_formatter import _apply_output_conformance


def test_parse_json_response_handles_markdown():
    raw_markdown = "```json\n{\"translated\": \"γεια\", \"score\": 0.95}\n```"
    parsed = _parse_json_response(raw_markdown)
    assert parsed == {"translated": "γεια", "score": 0.95}

    plain_json = "{\"status\": \"ok\"}"
    assert _parse_json_response(plain_json) == {"status": "ok"}


def test_merge_glossaries():
    existing = {"terms": [{"term": "Alice", "translation": "Άλις"}]}
    new = {"terms": [
        {"term": "alice", "translation": "Άλις (lower)"},
        {"term": "Bob", "translation": "Μπομπ"}
    ]}

    merged = _merge_glossaries(existing, new)
    terms = merged["terms"]
    assert len(terms) == 2
    assert terms[0]["term"] == "Alice"
    assert terms[1]["term"] == "Bob"


def test_select_golden_ratio_files():
    episodes = [f"E{i:02d}" for i in range(1, 21)]
    selected = _select_golden_ratio_files(episodes)
    assert len(selected) > 0
    assert selected[0] == "E01"
    assert selected[-1] == "E20"


def test_infer_speaker():
    assert infer_speaker("JOHN: Let's go!") == "JOHN"
    assert infer_speaker("[MARY] Be careful.") == "MARY"
    assert infer_speaker("Look out!", chars=["Arthur", "Merlin"]) == ""
    assert infer_speaker("Where is Arthur heading?", chars=["Arthur", "Merlin"]) == "Arthur"


def test_apply_output_conformance():
    rows = [
        {"index": 1, "translated": "A short line."},
        {"index": 2, "translated": "This is a very long line that should be wrapped according to subtitles standard maximum line length formatting constraints."},
    ]
    global_config = {"conformance_enabled": True, "conformance_max_cpl": 37}
    _apply_output_conformance(rows, "el", global_config)
    assert len(rows) == 2
    assert "translated" in rows[0]
