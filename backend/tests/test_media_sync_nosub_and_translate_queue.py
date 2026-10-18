import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from utils import storage
from integrations.media_sync_engine import MediaSyncEngine
from integrations.sonarr import SonarrConfig
from integrations.radarr import RadarrConfig
from integrations.path_resolver import PathResolver
from routers.schemas import AutoPipelineRequest
from routers.pipeline import start_auto_translate, _process_auto_translate


SAMPLE_SRT = """1
00:00:01,000 --> 00:00:04,000
Hello world.
"""


@pytest.mark.asyncio
async def test_sync_series_retains_episodes_without_subtitles(tmp_path, monkeypatch):
    """Episodes with media files but no subtitles on disk must be created with has_source_sub=False."""
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path / "projects")
    project_name = "Bleach Series"
    storage.create_project(project_name, {
        "show_name": "Bleach Series",
        "target_language": "Greek",
    })

    series_dir = tmp_path / "tv" / "Bleach Series" / "Season 17"
    series_dir.mkdir(parents=True, exist_ok=True)
    
    # S17E01 has a video file AND a subtitle
    ep1_mkv = series_dir / "Bleach Series.S17E01.mkv"
    ep1_mkv.touch()
    ep1_srt = series_dir / "Bleach Series.S17E01.en.srt"
    ep1_srt.write_text(SAMPLE_SRT, encoding="utf-8")

    # S17E02 has a video file but NO subtitle
    ep2_mkv = series_dir / "Bleach Series.S17E02.mkv"
    ep2_mkv.touch()

    sonarr_cfg = SonarrConfig(base_url="http://mock-sonarr", api_key="mock-key", enabled=True)
    resolver = PathResolver(mappings=[
        {"remote": "/mock/tv/Bleach Series", "local": str(tmp_path / "tv" / "Bleach Series")}
    ])
    engine = MediaSyncEngine(sonarr_config=sonarr_cfg, resolver=resolver, target_lang_code="el")

    mock_series = [{"id": 1, "title": "Bleach Series", "path": "/mock/tv/Bleach Series", "seriesType": "anime"}]
    mock_episodes = [
        {"id": 101, "seriesId": 1, "seasonNumber": 17, "episodeNumber": 1, "title": "The Blood Warfare", "hasFile": True, "episodeFileId": 201},
        {"id": 102, "seriesId": 1, "seasonNumber": 17, "episodeNumber": 2, "title": "Foundation Stones", "hasFile": True, "episodeFileId": 202},
    ]
    mock_files = [
        {"id": 201, "path": "/mock/tv/Bleach Series/Season 17/Bleach Series.S17E01.mkv"},
        {"id": 202, "path": "/mock/tv/Bleach Series/Season 17/Bleach Series.S17E02.mkv"},
    ]

    monkeypatch.setattr("integrations.media_sync_engine.get_all_series", AsyncMock(return_value=mock_series))
    monkeypatch.setattr("integrations.media_sync_engine.get_episodes", AsyncMock(return_value=mock_episodes))
    monkeypatch.setattr("integrations.media_sync_engine.get_episode_files", AsyncMock(return_value=mock_files))

    result = await engine.full_sync()

    eps = storage.list_episodes_with_metadata(project_name)
    assert len(eps) == 2

    ep1_info = next(e for e in eps if e["name"] == "S17E01")
    ep2_info = next(e for e in eps if e["name"] == "S17E02")

    assert ep1_info["has_source"] is True
    assert ep1_info["line_count"] > 0
    assert ep1_info["has_media"] is True

    assert ep2_info["has_source"] is False
    assert ep2_info["line_count"] == 0
    assert ep2_info["has_media"] is True
    assert ep2_info["media_path"] == str(ep2_mkv)


@pytest.mark.asyncio
async def test_sync_movie_retains_movie_without_subtitles(tmp_path, monkeypatch):
    """Movie with media file but no subtitle on disk must be retained and not pruned."""
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path / "projects")
    movie_dir = tmp_path / "movies" / "Bleach Movie"
    movie_dir.mkdir(parents=True, exist_ok=True)
    
    movie_mkv = movie_dir / "Bleach Movie.mkv"
    movie_mkv.touch()

    radarr_cfg = RadarrConfig(base_url="http://mock-radarr", api_key="mock-key", enabled=True)
    resolver = PathResolver(mappings=[
        {"remote": "/mock/movies/Bleach Movie", "local": str(movie_dir)}
    ])
    engine = MediaSyncEngine(radarr_config=radarr_cfg, resolver=resolver, target_lang_code="el")

    mock_movies = [
        {"id": 999, "title": "Bleach Movie", "movieFile": {"path": "/mock/movies/Bleach Movie/Bleach Movie.mkv"}}
    ]
    monkeypatch.setattr("integrations.media_sync_engine.get_all_movies", AsyncMock(return_value=mock_movies))

    result = await engine.full_sync()

    proj_name = "Bleach Movie (id-999)"
    eps = storage.list_episodes_with_metadata(proj_name)
    assert len(eps) == 1
    assert eps[0]["has_source"] is False
    assert eps[0]["has_media"] is True


@pytest.mark.asyncio
async def test_auto_translate_queues_only_specified_episodes(tmp_path, monkeypatch):
    """Calling auto-translate with 1 episode must enqueue ONLY that 1 episode."""
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path / "projects")
    project_name = "Bleach Translation Test"
    storage.create_project(project_name, {
        "show_name": "Bleach Translation Test",
        "target_language": "Greek",
        "settings": {"translation_model": "gemini-2.5-flash"},
    })

    # Create 3 episodes: ep1 and ep2 have subtitles, ep3 has no subtitle
    storage.save_episode(project_name, "S17E01", [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hi", "translated": ""}], metadata={"has_source_sub": True, "line_count": 1})
    storage.save_episode(project_name, "S17E02", [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hello", "translated": ""}], metadata={"has_source_sub": True, "line_count": 1})
    storage.save_episode(project_name, "S17E03", [], metadata={"has_source_sub": False, "line_count": 0})

    from utils.jobs_manager import create_job, jobs
    job_id1 = create_job("auto_translate", project_name)
    job_id2 = create_job("auto_translate", project_name)

    enqueued = []
    def mock_enqueue(proj, ep, prio, opts=None, lang=""):
        enqueued.append((proj, ep))
        return f"item_{ep}"

    monkeypatch.setattr("services.queue_service.enqueue_translation", mock_enqueue)

    # 1. Single episode translation request
    req1 = AutoPipelineRequest(episodes=["S17E01"], skip_context=True, skip_glossary=True)
    await _process_auto_translate(job_id1, project_name, "gemini-2.5-flash", "gemini-2.5-flash", "gemini-2.5-flash", req1.episodes, True, True, False)

    assert len(enqueued) == 1
    assert enqueued[0] == (project_name, "S17E01")

    enqueued.clear()

    # 2. Batch request for all (with no episode_names specified) -> should filter out S17E03 (no subtitles)
    req2 = AutoPipelineRequest(skip_context=True, skip_glossary=True)
    await _process_auto_translate(job_id2, project_name, "gemini-2.5-flash", "gemini-2.5-flash", "gemini-2.5-flash", req2.episodes, True, True, False)

    assert len(enqueued) == 2
    assert (project_name, "S17E01") in enqueued
    assert (project_name, "S17E02") in enqueued
    assert (project_name, "S17E03") not in enqueued




