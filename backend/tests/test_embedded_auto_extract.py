import pytest
from pathlib import Path
from utils import storage
from services.embedded_extractor import auto_extract_embedded_subs_for_episodes


@pytest.mark.asyncio
async def test_auto_extract_embedded_subs_pipeline(tmp_path, monkeypatch):
    """Test auto_extract_embedded_subs_for_episodes probes and extracts missing subs."""
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path / "projects")
    project_name = "AutoExtractShow"
    storage.create_project(project_name, {"target_language": "Greek"})

    # Episode 1 has media file but no source subtitle
    ep1_name = "S01E01"
    video_file = tmp_path / "S01E01.mkv"
    video_file.write_text("fake video container")
    
    storage.save_episode(
        project_name,
        ep1_name,
        [],
        {
            "arr_media_path": str(video_file),
            "has_source_sub": False,
            "line_count": 0,
        }
    )

    # Mock extraction function
    extracted_eps = []

    async def fake_extract(project_name, episode_name, **kwargs):
        extracted_eps.append(episode_name)
        storage.save_episode(
            project_name,
            episode_name,
            [{"id": "1", "timecode": "00:00:01,000 --> 00:00:03,000", "original": "Hello"}],
            {"has_source_sub": True, "line_count": 1, "arr_media_path": str(video_file)}
        )
        return {"status": "success", "episode_name": episode_name, "line_count": 1}

    monkeypatch.setattr("services.embedded_extractor.extract_embedded_for_episode", fake_extract)


    count = await auto_extract_embedded_subs_for_episodes(project_name, [ep1_name])
    assert count == 1
    assert ep1_name in extracted_eps

    ep_meta = storage.load_episode_metadata(project_name, ep1_name)
    assert ep_meta["has_source_sub"] is True
    assert ep_meta["line_count"] == 1
