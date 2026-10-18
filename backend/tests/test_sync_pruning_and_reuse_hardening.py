"""
Tests for Milestone 1 Media Integrations & Storage Hardening:
- INT-P0-01: Greek subtitle safeguards in sync pruning.
- INT-P1-03: Unified atomic subtitle sidecar write with retry backoff.
- INT-P1-04: Storage translation reuse upon media upgrade/rename.
"""

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from utils import storage
from integrations.media_sync_engine import MediaSyncEngine, SyncResult
from integrations.embedded_subs import write_sidecar
from integrations.sonarr import SonarrConfig


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path / "projects")
    storage.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    name = "Show"
    storage.create_project(name, {"show_name": name, "target_language": "Greek"})
    return name


def _rows():
    return [
        {"id": 1, "index": 1, "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hello", "translated": "Γεια", "translations": {"el": "Γεια"}},
        {"id": 2, "index": 2, "timecode": "00:00:03,000 --> 00:00:04,000", "original": "World", "translated": "Κόσμε", "translations": {"el": "Κόσμε"}},
    ]


def _engine():
    eng = MediaSyncEngine.__new__(MediaSyncEngine)
    eng.target_language = "Greek"
    return eng


def test_prune_safeguards_greek_target_subtitles(proj, tmp_path):
    """INT-P0-01: Under no circumstances should Greek target subtitles (.el.srt) be deleted during pruning."""
    target = tmp_path / "Show.S01E02.el.srt"
    target.write_text("1\n00:00:01,000 --> 00:00:02,000\nΓεια\n", encoding="utf-8")
    storage.save_episode(proj, "S01E01", _rows(), {"arr_source": True})
    storage.save_episode(proj, "S01E02", _rows(), {"arr_source": True, "arr_target_path": str(target)})

    result = SyncResult()
    # S01E01 was seen on disk, S01E02 was not seen
    removed = _engine()._prune_removed_episodes(proj, {"S01E01"}, result)

    assert removed == 1
    assert "S01E01" in storage.list_episodes(proj)
    assert "S01E02" not in storage.list_episodes(proj)
    # The file on disk must still exist!
    assert target.exists()
    assert "Γεια" in target.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_sync_series_aborts_pruning_on_episode_files_none(proj, monkeypatch):
    """INT-P0-01: If get_episode_files returns None (API error), abort pruning to safeguard subtitles."""
    engine = MediaSyncEngine(
        sonarr_config=SonarrConfig(base_url="http://localhost:8989", api_key="test", enabled=True),
        radarr_config=None,
    )

    storage.save_episode(proj, "S01E01", _rows(), {"arr_source": True})
    storage.save_episode(proj, "S01E02", _rows(), {"arr_source": True})

    # Mock get_episodes to return 2 episodes with hasFile=True
    async def mock_get_episodes(cfg, s_id):
        return [
            {"id": 101, "seasonNumber": 1, "episodeNumber": 1, "hasFile": True, "episodeFileId": 1},
            {"id": 102, "seasonNumber": 1, "episodeNumber": 2, "hasFile": True, "episodeFileId": 2},
        ]

    # Mock get_episode_files to return None (simulating API failure / error)
    async def mock_get_episode_files(cfg, s_id):
        return None

    monkeypatch.setattr("integrations.media_sync_engine.get_episodes", mock_get_episodes)
    monkeypatch.setattr("integrations.media_sync_engine.get_episode_files", mock_get_episode_files)

    result = SyncResult()
    series_data = {"id": 1, "title": "Show", "path": "/data/Show"}
    from utils.srt_parser import parse_srt

    await engine._sync_series(series_data, proj, result, storage, parse_srt, {proj})

    # Pruning must have been aborted
    assert any("aborting series pruning to safeguard subtitles" in w for w in result.warnings)
    assert result.removed_episodes == 0
    # Both episodes must still exist in storage
    episodes = storage.list_episodes(proj)
    assert "S01E01" in episodes
    assert "S01E02" in episodes


@pytest.mark.asyncio
async def test_sync_series_aborts_pruning_on_empty_anomaly(proj, monkeypatch):
    """INT-P0-01: If episodes report hasFile=True but episode_files returns [], abort pruning."""
    engine = MediaSyncEngine(
        sonarr_config=SonarrConfig(base_url="http://localhost:8989", api_key="test", enabled=True),
        radarr_config=None,
    )

    storage.save_episode(proj, "S01E01", _rows(), {"arr_source": True})

    async def mock_get_episodes(cfg, s_id):
        return [{"id": 101, "seasonNumber": 1, "episodeNumber": 1, "hasFile": True, "episodeFileId": 1}]

    async def mock_get_episode_files(cfg, s_id):
        return []  # 0 files returned while hasFile=True

    monkeypatch.setattr("integrations.media_sync_engine.get_episodes", mock_get_episodes)
    monkeypatch.setattr("integrations.media_sync_engine.get_episode_files", mock_get_episode_files)

    result = SyncResult()
    series_data = {"id": 1, "title": "Show", "path": "/data/Show"}
    from utils.srt_parser import parse_srt

    await engine._sync_series(series_data, proj, result, storage, parse_srt, {proj})

    assert any("aborting series pruning to safeguard subtitles" in w for w in result.warnings)
    assert result.removed_episodes == 0
    assert "S01E01" in storage.list_episodes(proj)


def test_write_sidecar_exponential_retry_on_sharing_violation(tmp_path, monkeypatch):
    """INT-P1-03: write_sidecar retries with backoff upon Windows sharing violation (WinError 32)."""
    target = tmp_path / "Show.el.srt"
    replace_attempts = {"count": 0}
    real_replace = os.replace

    def mock_replace(src, dst):
        replace_attempts["count"] += 1
        if replace_attempts["count"] < 3:
            raise PermissionError(13, "WinError 32: The process cannot access the file")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", mock_replace)

    content = "1\n00:00:01,000 --> 00:00:02,000\nΓεια σου κόσμε\n"
    write_sidecar(target, content)

    assert target.exists()
    assert target.read_text(encoding="utf-8-sig") == content
    assert replace_attempts["count"] == 3
    # No lingering temp files
    assert not list(tmp_path.glob("*.omnisub-tmp*"))


@pytest.mark.asyncio
async def test_reuse_existing_translation_on_sonarr_upgrade(proj, tmp_path, monkeypatch):
    """INT-P1-04: Existing translated cues in storage are re-exported on media upgrade/rename."""
    # Seed storage with completed translation
    storage.save_episode(proj, "S01E01", _rows(), {
        "arr_source": True,
        "translated": True,
        "translation_status": "completed",
        "original_filename": "Show.S01E01.720p.mkv",
    })

    # Prepare new upgraded media file on disk
    media_dir = tmp_path / "Show" / "Season 1"
    media_dir.mkdir(parents=True, exist_ok=True)
    upgraded_media = media_dir / "Show.S01E01.1080p.WEB-DL.mkv"
    upgraded_media.write_text("fake video data")
    upgraded_source_sub = media_dir / "Show.S01E01.1080p.WEB-DL.en.srt"
    upgraded_source_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")

    expected_target = media_dir / "Show.S01E01.1080p.WEB-DL.el.srt"
    assert not expected_target.exists()

    engine = MediaSyncEngine(
        sonarr_config=SonarrConfig(base_url="http://localhost:8989", api_key="test", enabled=True),
        radarr_config=None,
    )

    payload = {
        "eventType": "Download",
        "series": {"id": 1, "title": "Show"},
        "episodes": [{"id": 101, "seasonNumber": 1, "episodeNumber": 1, "title": "Pilot"}],
        "episodeFile": {"id": 201, "path": str(upgraded_media)},
    }

    from utils.srt_parser import parse_srt
    location = await engine.process_sonarr_webhook(payload, storage, parse_srt)

    # Re-exported target subtitle must exist on disk with the new stem
    assert expected_target.exists()
    assert "Γεια" in expected_target.read_text(encoding="utf-8-sig")

    # Metadata must indicate that the episode is already translated and has target
    meta = storage.load_episode_metadata(proj, "S01E01")
    assert meta["arr_has_target"] is True
    assert meta["translated"] is True
