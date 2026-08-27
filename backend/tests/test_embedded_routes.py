import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from main import app
from utils import storage
from integrations import embedded_subs
from integrations.embedded_subs import SubtitleTrack


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_project(tmp_path, monkeypatch):
    """Setup isolated test storage."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)

    proj_name = "TestAnime"
    storage.create_project(proj_name, {
        "show_name": "Test Anime Show",
        "target_language": "Greek",
        "type": "show",
        "series_type": "anime",
        "settings": {
            "prefer_ass_format": "auto",
        }
    })
    return proj_name, projects_dir


def test_probe_episode_embedded_no_media(client, sample_project):
    proj_name, _ = sample_project
    ep_name = "S01E01"
    storage.save_episode(proj_name, ep_name, [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hi", "translated": ""}])
    
    resp = client.get(f"/projects/{proj_name}/episodes/{ep_name}/embedded/probe")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_media"] is False
    assert "No media file linked" in data["error"]


def test_probe_episode_embedded_with_media(client, sample_project, tmp_path, monkeypatch):
    proj_name, _ = sample_project
    ep_name = "S01E01"
    media_file = tmp_path / "TestAnime.S01E01.mkv"
    media_file.write_bytes(b"dummy video container")

    ep_meta = {
        "arr_media_path": str(media_file),
        "original_format": "srt",
    }
    storage.save_episode(proj_name, ep_name, [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hi", "translated": ""}], metadata=ep_meta)

    fake_tracks = [
        SubtitleTrack(index=2, codec="ass", language="eng", title="Signs & Songs", frames=20),
        SubtitleTrack(index=3, codec="ass", language="eng", title="Full Dialogue", frames=500),
    ]

    monkeypatch.setattr(embedded_subs, "resolve_tools", lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))
    monkeypatch.setattr(embedded_subs, "probe_subtitle_tracks", lambda *a, **k: fake_tracks)

    resp = client.get(f"/projects/{proj_name}/episodes/{ep_name}/embedded/probe")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_media"] is True
    assert data["tools_available"] is True
    assert len(data["tracks"]) == 2
    assert data["recommended_stream_index"] == 3
    assert data["tracks"][1]["is_recommended"] is True


def test_extract_episode_embedded_success(client, sample_project, tmp_path, monkeypatch):
    proj_name, _ = sample_project
    ep_name = "S01E01"
    media_file = tmp_path / "TestAnime.S01E01.mkv"
    media_file.write_bytes(b"dummy video container")

    ep_meta = {
        "arr_media_path": str(media_file),
        "original_format": "srt",
        "arr_sub_path": str(tmp_path / "TestAnime.S01E01.en.srt"),
    }
    storage.save_episode(proj_name, ep_name, [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hello World", "translated": "Γεια σου κόσμε"}], metadata=ep_meta)

    fake_ass = (
        "[Script Info]\n"
        "Title: Test\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hello Extracted ASS World\n"
    )

    fake_tracks = [
        SubtitleTrack(index=2, codec="ass", language="eng", title="Full Dialogue", frames=1),
    ]

    monkeypatch.setattr(embedded_subs, "resolve_tools", lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))
    monkeypatch.setattr(embedded_subs, "probe_subtitle_tracks", lambda *a, **k: fake_tracks)

    async def fake_extract(*a, **k):
        return fake_ass

    monkeypatch.setattr(embedded_subs, "extract_track", fake_extract)

    resp = client.post(
        f"/projects/{proj_name}/episodes/{ep_name}/embedded/extract",
        json={"stream_index": 2, "force": True, "migrate_srt": True}
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["status"] == "success"
    assert res_data["stream_index"] == 2

    # Verify episode was updated
    ep = storage.load_episode(proj_name, ep_name)
    assert ep is not None
    assert "Hello Extracted ASS World" in ep["data"][0]["original"]

    updated_meta = storage.load_episode_metadata(proj_name, ep_name)
    assert updated_meta["embedded_extracted"] is True
    assert updated_meta["original_format"] == "ass"


def test_get_project_embedded_status(client, sample_project, tmp_path):
    proj_name, _ = sample_project
    ep_meta = {
        "arr_media_path": str(tmp_path / "Test.mkv"),
        "original_format": "srt",
    }
    (tmp_path / "Test.mkv").write_bytes(b"dummy")
    storage.save_episode(proj_name, "S01E01", [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hi", "translated": ""}], metadata=ep_meta)

    resp = client.get(f"/projects/{proj_name}/embedded/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_name"] == proj_name
    assert data["total_episodes"] == 1
    assert data["episodes_with_media"] == 1
    assert data["srt_episodes"] == 1


def test_test_media_probe_endpoint(client, tmp_path, monkeypatch):
    test_video = tmp_path / "sample_video.mkv"
    test_video.write_bytes(b"dummy")

    fake_tracks = [
        SubtitleTrack(index=1, codec="ass", language="eng", title="English", frames=120),
    ]
    monkeypatch.setattr(embedded_subs, "resolve_tools", lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))
    monkeypatch.setattr(embedded_subs, "probe_subtitle_tracks", lambda *a, **k: fake_tracks)

    resp = client.post("/api/settings/test-media-probe", json={"media_path": str(test_video)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["media_filename"] == "sample_video.mkv"
    assert data["ass_tracks_count"] == 1
    assert data["recommended_stream_index"] == 1


def test_batch_extract_embedded_endpoint(client, sample_project, tmp_path, monkeypatch):
    proj_name, _ = sample_project
    media_file = tmp_path / "TestAnime.S01E01.mkv"
    media_file.write_bytes(b"dummy video container")

    ep_meta = {
        "arr_media_path": str(media_file),
        "original_format": "srt",
    }
    storage.save_episode(proj_name, "S01E01", [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hi", "translated": ""}], metadata=ep_meta)

    fake_ass = (
        "[Script Info]\n"
        "Title: Test\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Batch Extracted World\n"
    )
    fake_tracks = [SubtitleTrack(index=2, codec="ass", language="eng", title="Full Dialogue", frames=1)]

    monkeypatch.setattr(embedded_subs, "resolve_tools", lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))
    monkeypatch.setattr(embedded_subs, "probe_subtitle_tracks", lambda *a, **k: fake_tracks)

    async def fake_extract(*a, **k):
        return fake_ass

    monkeypatch.setattr(embedded_subs, "extract_track", fake_extract)

    resp = client.post(
        f"/projects/{proj_name}/embedded/extract-all",
        json={"force": True, "migrate_srt": True}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data


def test_extract_missing_tools_fails(client, sample_project, tmp_path, monkeypatch):
    proj_name, _ = sample_project
    media_file = tmp_path / "TestAnime.S01E01.mkv"
    media_file.write_bytes(b"dummy")
    storage.save_episode(proj_name, "S01E01", [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hi"}], metadata={"arr_media_path": str(media_file)})

    monkeypatch.setattr(embedded_subs, "resolve_tools", lambda *a, **k: None)

    resp = client.post(f"/projects/{proj_name}/episodes/S01E01/embedded/extract", json={})
    assert resp.status_code == 400
    assert "ffmpeg / ffprobe not found" in resp.json()["detail"]


def test_full_ingest_pipeline_endpoint(client, sample_project):
    proj_name, _ = sample_project
    resp = client.post(f"/projects/{proj_name}/pipeline/full-ingest")
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data


