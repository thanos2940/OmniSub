"""
Unit tests for Task 1.6: In-Place Retranslation, ETag handling, and 1-Click Revert.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from utils import storage
from services.translation.batch_ops import _process_retranslation_for_comparison


@pytest.fixture
def mock_env(tmp_path, monkeypatch):
    """Fixture providing an isolated project directory."""
    proj_dir = tmp_path / "projects"
    proj_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage, "PROJECTS_DIR", proj_dir)

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    from utils import paths
    monkeypatch.setattr(paths, "DATA_DIR", str(data_dir))

    proj = "TestProject"
    ep = "S01E01"
    
    storage.create_project(proj, {
        "show_name": "Test Show",
        "target_language": "Greek",
        "target_languages": ["Greek"],
        "glossary": {"terms": []},
        "settings": {"temperature": 0.3}
    })
    
    initial_data = [
        {
            "index": 1,
            "id": "cue-1",
            "timecode": "00:00:01,000 --> 00:00:03,000",
            "original": "Good morning my friend.",
            "translated": "Καλημέρα φίλε μου.",
            "translations": {"el": "Καλημέρα φίλε μου."}
        },
        {
            "index": 2,
            "id": "cue-2",
            "timecode": "00:00:04,000 --> 00:00:06,000",
            "original": "How are you today?",
            "translated": "Πώς είσαι σήμερα;",
            "translations": {"el": "Πώς είσαι σήμερα;"}
        }
    ]
    storage.save_episode(proj, ep, initial_data)
    storage.save_episode_metadata(proj, ep, {
        "has_source_sub": True,
        "line_count": 2,
        "translated": True
    })
    return proj, ep, tmp_path


@pytest.mark.asyncio
async def test_in_place_retranslate_saves_previous_and_exports(mock_env, monkeypatch):
    proj, ep, _ = mock_env

    # Mock the LLM translation call inside _process_retranslation_for_comparison
    # by mocking rate_limited_call to return structured json response
    async def fake_rate_limited_call(func, rate_limiter=None):
        return '[{"index": 1, "text": "Καλημέρα, καλέ μου φίλε!"}, {"index": 2, "text": "Πώς είσαι σήμερα;"}]'

    monkeypatch.setattr("services.translation.batch_ops.rate_limited_call", fake_rate_limited_call)

    exported_calls = []
    def fake_export(p, e, data, meta, force=True):
        exported_calls.append((p, e, len(data), force))
        return "/fake/path/exported.el.srt"

    monkeypatch.setattr("services.translation.batch_ops.auto_export_translated_subtitle", fake_export)

    await _process_retranslation_for_comparison(
        job_id="test_job_1",
        project_name=proj,
        episode_name=ep,
        target_lang="Greek",
        model_name="gemini-2.5-flash",
        comparison_key="compare_123"
    )

    loaded = storage.load_episode(proj, ep)
    lines = loaded["data"]

    # Line 1 had different translation: previous_translated must be preserved
    assert lines[0]["translated"] == "Καλημέρα, καλέ μου φίλε!"
    assert lines[0]["previous_translated"] == "Καλημέρα φίλε μου."
    assert lines[0]["retranslated_text"] == "Καλημέρα, καλέ μου φίλε!"
    assert lines[0]["translations"]["el"] == "Καλημέρα, καλέ μου φίλε!"

    # Line 2 was identical: previous_translated should NOT be set
    assert lines[1]["translated"] == "Πώς είσαι σήμερα;"
    assert lines[1].get("previous_translated") is None

    # Auto-export must have been called with force=True
    assert len(exported_calls) == 1
    assert exported_calls[0] == (proj, ep, 2, True)


@pytest.mark.asyncio
async def test_retranslate_single_line_endpoint(mock_env):
    proj, ep, _ = mock_env
    from routers.episodes import retranslate_single_line

    payload = {
        "line_index": 0,
        "new_text": "Καλημέρα αγαπητέ μου φίλε!"
    }

    resp = await retranslate_single_line(proj, ep, payload)
    import json
    data = json.loads(resp.body.decode("utf-8"))

    assert data["status"] == "success"
    assert data["translated"] == "Καλημέρα αγαπητέ μου φίλε!"
    assert data["previous_translated"] == "Καλημέρα φίλε μου."
    assert data.get("etag") is not None
    assert "ETag" in resp.headers

    loaded = storage.load_episode(proj, ep)
    assert loaded["data"][0]["translated"] == "Καλημέρα αγαπητέ μου φίλε!"
    assert loaded["data"][0]["previous_translated"] == "Καλημέρα φίλε μου."


@pytest.mark.asyncio
async def test_save_episode_etag_conflict_409(mock_env):
    proj, ep, _ = mock_env
    from routers.episodes import save_episode, SaveEpisodeRequest
    from starlette.requests import Request
    from fastapi import HTTPException

    loaded = storage.load_episode(proj, ep)
    lines = loaded["data"]
    lines[0]["translated"] = "Updated Greek text"

    save_req = SaveEpisodeRequest(data=lines)

    # Request with mismatched If-Match header -> must raise 409
    scope = {
        "type": "http",
        "headers": [(b"if-match", b'"wrong-etag-value"')],
    }
    raw_req = Request(scope)

    with pytest.raises(HTTPException) as exc_info:
        await save_episode(proj, ep, save_req, raw_req)
    assert exc_info.value.status_code == 409

    # Request with correct (or no) If-Match -> must succeed and return ETag
    scope_wildcard = {
        "type": "http",
        "headers": [(b"if-match", b"*")],
    }
    raw_req_wildcard = Request(scope_wildcard)
    success_resp = await save_episode(proj, ep, save_req, raw_req_wildcard)
    import json
    resp_data = json.loads(success_resp.body.decode("utf-8"))
    assert resp_data["status"] == "success"
    assert "etag" in resp_data
    assert "ETag" in success_resp.headers
