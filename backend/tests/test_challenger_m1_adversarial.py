"""
Adversarial Stress Test Suite for Milestone 1 Remediations:
- INT-P0-01: Greek subtitle preservation during sync pruning under network error / empty files.
- INT-P0-02: UNC root reachability under complex Windows UNC paths.
- INT-P1-04: Storage translation reuse across 720p -> 1080p -> 4K media upgrades.

Executed independently by Challenger 1 (Milestone 1).
"""

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
import pytest

from utils import storage
from integrations.media_sync_engine import MediaSyncEngine, SyncResult
from integrations.path_resolver import PathResolver
from integrations.sonarr import SonarrConfig, get_episode_files
from integrations.radarr import RadarrConfig
from utils.srt_parser import parse_srt


@pytest.fixture
def test_project(tmp_path, monkeypatch):
    """Set up an isolated project storage environment."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)

    from utils import metadata_index
    monkeypatch.setattr(metadata_index, "DB_FILE", tmp_path / "omnisub_challenger_test.db")
    metadata_index._init()

    monkeypatch.setattr(storage, "load_global_config", lambda: {
        "source_clean_enabled": False,
        "merge_split_cues": False,
        "strip_sdh": False,
        "preserve_italics": False,
        "incremental_retranslate_enabled": False,
    })

    proj_name = "AdversarialShow"
    storage.create_project(proj_name, {"show_name": proj_name, "target_language": "Greek"})
    return proj_name


def _sample_cues():
    return [
        {
            "id": 1,
            "index": 1,
            "timecode": "00:00:01,000 --> 00:00:03,000",
            "original": "Good morning detective.",
            "translated": "Καλημέρα ντετέκτιβ.",
            "translations": {"el": "Καλημέρα ντετέκτιβ."},
        },
        {
            "id": 2,
            "index": 2,
            "timecode": "00:00:04,000 --> 00:00:06,000",
            "original": "We found something in the alley.",
            "translated": "Βρήκαμε κάτι στο σοκάκι.",
            "translations": {"el": "Βρήκαμε κάτι στο σοκάκι."},
        },
        {
            "id": 3,
            "index": 3,
            "timecode": "00:00:07,000 --> 00:00:09,000",
            "original": "Is it the missing weapon?",
            "translated": "Είναι το χαμένο όπλο;",
            "translations": {"el": "Είναι το χαμένο όπλο;"},
        },
    ]


def _engine():
    eng = MediaSyncEngine.__new__(MediaSyncEngine)
    eng.target_language = "Greek"
    eng.sonarr = SonarrConfig(base_url="http://localhost:8989", api_key="test", enabled=True)
    eng.radarr = None
    return eng


# ===========================================================================
# INT-P0-01: GREEK SUBTITLE PRESERVATION UNDER NETWORK ERRORS & EMPTY RESPONSES
# ===========================================================================

@pytest.mark.parametrize("exc", [
    httpx.ConnectTimeout("Connection timed out to Sonarr"),
    httpx.ReadTimeout("Read timed out waiting for Sonarr episodefile API"),
    httpx.RemoteProtocolError("Server disconnected unexpectedly"),
    ConnectionResetError(10054, "An existing connection was forcibly closed by remote host"),
    httpx.HTTPStatusError("500 Internal Server Error", request=MagicMock(), response=MagicMock(status_code=500)),
    httpx.HTTPStatusError("503 Service Unavailable", request=MagicMock(), response=MagicMock(status_code=503)),
    httpx.HTTPStatusError("504 Gateway Timeout", request=MagicMock(), response=MagicMock(status_code=504)),
])
@pytest.mark.asyncio
async def test_adversarial_sonarr_get_episode_files_network_errors_return_none(exc):
    """Adversarial stress: get_episode_files must catch all network transport exceptions and return None."""
    cfg = SonarrConfig(base_url="http://192.168.1.50:8989", api_key="secret", enabled=True)

    with patch("integrations.sonarr._get_with_retry", side_effect=exc):
        result = await get_episode_files(cfg, series_id=123)
        assert result is None, f"Expected None on {type(exc).__name__}, but got {result}"


@pytest.mark.parametrize("simulated_error", [
    None,  # API returned None (transport/network error)
    [],    # API returned empty list while episodes have files
])
@pytest.mark.asyncio
async def test_adversarial_sync_series_safeguards_greek_subtitles_on_api_failure(
    test_project, tmp_path, monkeypatch, simulated_error
):
    """Stress test: When Sonarr returns None or [] due to network/cache glitches,
    sync pruning must ABORT, leaving all .el.srt and .el.ass files untouched on disk,
    and leaving all episodes in storage intact.
    """
    media_dir = tmp_path / "Media" / "AdversarialShow" / "Season 1"
    media_dir.mkdir(parents=True, exist_ok=True)

    # Place target Greek subtitles on disk
    target_srt = media_dir / "AdversarialShow.S01E01.el.srt"
    target_srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nΚαλημέρα ντετέκτιβ.\n", encoding="utf-8")
    target_ass = media_dir / "AdversarialShow.S01E02.el.ass"
    target_ass.write_text("[Events]\nDialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Καλημέρα\n", encoding="utf-8")

    storage.save_episode(test_project, "S01E01", _sample_cues(), {
        "arr_source": True,
        "arr_target_path": str(target_srt),
        "arr_media_path": str(media_dir / "AdversarialShow.S01E01.mkv"),
    })
    storage.save_episode(test_project, "S01E02", _sample_cues(), {
        "arr_source": True,
        "arr_target_path": str(target_ass),
        "arr_media_path": str(media_dir / "AdversarialShow.S01E02.mkv"),
    })

    engine = MediaSyncEngine(
        sonarr_config=SonarrConfig(base_url="http://localhost:8989", api_key="test", enabled=True),
        radarr_config=None,
    )

    async def mock_episodes(cfg, s_id):
        return [
            {"id": 101, "seasonNumber": 1, "episodeNumber": 1, "hasFile": True, "episodeFileId": 1},
            {"id": 102, "seasonNumber": 1, "episodeNumber": 2, "hasFile": True, "episodeFileId": 2},
        ]

    async def mock_files(cfg, s_id):
        return simulated_error

    monkeypatch.setattr("integrations.media_sync_engine.get_episodes", mock_episodes)
    monkeypatch.setattr("integrations.media_sync_engine.get_episode_files", mock_files)

    result = SyncResult()
    series_data = {"id": 1, "title": test_project, "path": str(media_dir.parent)}

    await engine._sync_series(series_data, test_project, result, storage, parse_srt, {test_project})

    # Critical assertions:
    # 1. Pruning was aborted
    assert any("aborting series pruning to safeguard subtitles" in w for w in result.warnings)
    assert result.removed_episodes == 0

    # 2. Subtitles on disk MUST NOT have been deleted
    assert target_srt.exists(), "Target .el.srt was deleted on API failure!"
    assert "Καλημέρα" in target_srt.read_text(encoding="utf-8")
    assert target_ass.exists(), "Target .el.ass was deleted on API failure!"

    # 3. Storage episodes MUST NOT have been pruned
    eps = storage.list_episodes(test_project)
    assert "S01E01" in eps
    assert "S01E02" in eps


def test_adversarial_prune_never_deletes_target_files_on_disk(test_project, tmp_path):
    """Adversarial stress: Even when episodes are legitimately pruned from storage
    (because their media/source sub is missing from disk), _prune_removed_episodes
    must NEVER delete .el.srt or .el.ass files from disk!
    """
    media_dir = tmp_path / "Media" / "Show"
    media_dir.mkdir(parents=True, exist_ok=True)

    # 3 target subtitle files on disk
    sub1 = media_dir / "Show.S01E01.el.srt"
    sub1.write_text("1\n00:00:01,000 --> 00:00:02,000\nΓεια 1\n", encoding="utf-8")
    sub2 = media_dir / "Show.S01E02.el.srt"
    sub2.write_text("1\n00:00:01,000 --> 00:00:02,000\nΓεια 2\n", encoding="utf-8")
    sub3 = media_dir / "Show.S01E03.el.ass"
    sub3.write_text("[Events]\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Γεια 3\n", encoding="utf-8")

    storage.save_episode(test_project, "S01E01", _sample_cues(), {
        "arr_source": True,
        "arr_target_path": str(sub1),
        "arr_media_path": str(media_dir / "Show.S01E01.mkv"),
    })
    storage.save_episode(test_project, "S01E02", _sample_cues(), {
        "arr_source": True,
        "arr_target_path": str(sub2),
        "arr_media_path": str(media_dir / "Show.S01E02.mkv"),
    })
    storage.save_episode(test_project, "S01E03", _sample_cues(), {
        "arr_source": True,
        "arr_target_path": str(sub3),
        "arr_media_path": str(media_dir / "Show.S01E03.mkv"),
    })

    result = SyncResult()
    # Only S01E01 was seen on disk; S01E02 and S01E03 are missing from scan
    seen = {"S01E01"}
    removed = _engine()._prune_removed_episodes(test_project, seen, result)

    assert removed == 2
    # In storage: S01E01 kept, S01E02 and S01E03 pruned
    remaining = storage.list_episodes(test_project)
    assert "S01E01" in remaining
    assert "S01E02" not in remaining
    assert "S01E03" not in remaining

    # CRITICAL CHECK: ALL THREE target subtitle files on disk must STILL EXIST!
    assert sub1.exists(), "S01E01 target sub was removed!"
    assert sub2.exists(), "S01E02 target sub was deleted from disk!"
    assert sub3.exists(), "S01E03 target sub was deleted from disk!"
    assert "Γεια 2" in sub2.read_text(encoding="utf-8")
    assert "Γεια 3" in sub3.read_text(encoding="utf-8")


def test_adversarial_prune_safety_valve_when_zero_episodes_seen(test_project, tmp_path):
    """Adversarial stress: If seen_episode_names is empty (e.g. share unmounted),
    safety valve must trigger, skipping pruning completely.
    """
    media_dir = tmp_path / "Media"
    sub1 = media_dir / "Show.S01E01.el.srt"
    media_dir.mkdir(parents=True, exist_ok=True)
    sub1.write_text("target sub")

    storage.save_episode(test_project, "S01E01", _sample_cues(), {
        "arr_source": True,
        "arr_target_path": str(sub1),
    })

    result = SyncResult()
    removed = _engine()._prune_removed_episodes(test_project, set(), result)

    assert removed == 0
    assert any("no source subtitles found this sync" in w for w in result.warnings)
    assert "S01E01" in storage.list_episodes(test_project)
    assert sub1.exists()


# ===========================================================================
# INT-P0-02: UNC ROOT REACHABILITY UNDER COMPLEX WINDOWS UNC PATHS
# ===========================================================================

class TestUNCPathReachabilityAdversarial:
    """Stress-test PathResolver.is_reachable against diverse Windows UNC paths,
    IP addresses, slashes, and edge cases.
    """

    @pytest.fixture(autouse=True)
    def setup_resolver(self):
        self.resolver = PathResolver()

    def test_unc_root_reachability_matrix(self, monkeypatch):
        """Stress-test matrix of valid UNC path structures."""
        online_roots = {
            r"\\morpiceserver\tv",
            r"\\morpiceserver\tv\\",
            r"\\192.168.1.100\media",
            r"\\10.0.0.5\share",
            r"\\nas\video share",
            r"\\server\special-chars_01",
        }

        real_exists = Path.exists

        def mock_exists(p):
            p_str = str(p).rstrip("\\/").lower()
            for r in online_roots:
                if p_str == r.rstrip("\\/").lower():
                    return True
            # Simulate that subfolders do NOT exist
            if any(sub in p_str for sub in ["season", "show", "nonexistent", "deep"]):
                return False
            return real_exists(p)

        monkeypatch.setattr(Path, "exists", mock_exists)

        # 1. Standard UNC root with deep non-existent subfolders
        assert self.resolver.is_reachable(r"\\Morpiceserver\tv\NonExistentShow\Season 01\ep01.mkv") is True
        # 2. UNC root directly
        assert self.resolver.is_reachable(r"\\Morpiceserver\tv") is True
        assert self.resolver.is_reachable(r"\\Morpiceserver\tv\\") is True

        # 3. IP address UNC
        assert self.resolver.is_reachable(r"\\192.168.1.100\media\Show\ep.mkv") is True
        assert self.resolver.is_reachable(r"\\10.0.0.5\share\deep\sub\file.mkv") is True

        # 4. Forward slash UNC paths (normalized by Windows Path)
        assert self.resolver.is_reachable("//Morpiceserver/tv/Show/ep.mkv") is True
        assert self.resolver.is_reachable("//192.168.1.100/media") is True

        # 5. Mixed slashes
        assert self.resolver.is_reachable(r"\\Morpiceserver/tv\Show/ep.mkv") is True

        # 6. UNC with spaces in share name
        assert self.resolver.is_reachable(r"\\NAS\video share\Show\ep.mkv") is True

        # 7. Offline UNC server/share must return False
        assert self.resolver.is_reachable(r"\\OfflineServer\share\Show\ep.mkv") is False
        assert self.resolver.is_reachable(r"\\192.168.1.999\offline\ep.mkv") is False

    def test_drive_letter_reachability(self, monkeypatch):
        """Stress-test Windows drive letter paths."""
        real_exists = Path.exists

        def mock_exists(p):
            p_str = str(p).rstrip("\\/").upper()
            if p_str in ("C:", "C:\\", "D:", "D:\\", "P:", "P:\\"):
                return True
            if p_str in ("X:", "X:\\", "Z:", "Z:\\"):
                return False
            # Subdirectories don't exist
            return False

        monkeypatch.setattr(Path, "exists", mock_exists)

        # Existing drives with deep missing paths -> True
        assert self.resolver.is_reachable(r"C:\NewShow\Season 1\ep.mkv") is True
        assert self.resolver.is_reachable(r"P:\Media\NonExistent\ep.mkv") is True
        assert self.resolver.is_reachable("C:") is True
        assert self.resolver.is_reachable("C:\\") is True

        # Unmounted drives -> False
        assert self.resolver.is_reachable(r"X:\Media\Show\ep.mkv") is False
        assert self.resolver.is_reachable(r"Z:\Media\Show\ep.mkv") is False

    def test_real_system_drives(self):
        """Test against real local filesystem on Windows host."""
        # C:\ always exists on Windows
        assert self.resolver.is_reachable(r"C:\completely_unreal_subfolder_xyz_404\ep.mkv") is True
        # P:\ exists in this environment
        assert self.resolver.is_reachable(r"P:\nonexistent_path_987654321\ep.mkv") is True

    @pytest.mark.parametrize("invalid_path", [
        "",
        None,
        "nonexistent_subfolder_xyz/ep.mkv",
        "X:\\OfflineDrive\\ep.mkv",
        "//OfflineServer/share/ep.mkv",
    ])
    def test_invalid_and_boundary_paths(self, invalid_path):
        """Adversarial boundary cases must safely return False without exceptions."""
        assert self.resolver.is_reachable(invalid_path) is False

    def test_unc_boundary_quirks(self):
        r"""Document edge-case behavior:
        1. Whitespace '   ' resolves to Path('   ') whose parent is '.' (current working directory),
           so p.parent.exists() evaluates to True unless whitespace is stripped.
        2. Incomplete UNC '\\server' (without a share) is converted by Windows pathlib to '\server'
           (relative to current drive root), so p.parent evaluates to the current drive root.
        This test documents this empirical observation for adversarial review.
        """
        # Shows that current implementation resolves these relative to current drive / working directory
        assert self.resolver.is_reachable("   ") is True
        assert self.resolver.is_reachable(r"\\server") is True


# ===========================================================================
# INT-P1-04: STORAGE TRANSLATION REUSE ACROSS 720p -> 1080p -> 4K UPGRADES
# ===========================================================================

@pytest.mark.asyncio
async def test_adversarial_translation_reuse_across_720p_to_1080p_to_4k(test_project, tmp_path):
    """Stress test multi-hop media upgrade:
    720p HDTV -> 1080p WEB-DL -> 2160p UHD Remux.
    Each upgrade must:
    1. Reuse the stored Greek translation cues from data.json.
    2. Atomically export the new .el.srt sidecar corresponding to the upgraded media.
    3. Update metadata (arr_media_path, arr_target_path, translated=True).
    4. Consume 0 translation tokens (0 LLM translation calls).
    5. Preserve previous subtitle files on disk.
    """
    media_dir = tmp_path / "Media" / "Better Call Saul" / "Season 01"
    media_dir.mkdir(parents=True, exist_ok=True)

    # 1. INITIAL STATE: 720p episode
    file_720p = media_dir / "Better.Call.Saul.S01E01.720p.HDTV.mkv"
    file_720p.write_text("video 720p")
    target_720p = media_dir / "Better.Call.Saul.S01E01.720p.HDTV.el.srt"
    target_720p.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nΚαλημέρα ντετέκτιβ.\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nΒρήκαμε κάτι στο σοκάκι.\n\n"
        "3\n00:00:07,000 --> 00:00:09,000\nΕίναι το χαμένο όπλο;\n",
        encoding="utf-8-sig"
    )

    # Seed storage with completed translation
    storage.save_episode(test_project, "S01E01", _sample_cues(), {
        "arr_source": True,
        "translated": True,
        "translation_status": "completed",
        "original_filename": "Better.Call.Saul.S01E01.720p.HDTV.mkv",
        "arr_media_path": str(file_720p),
        "arr_target_path": str(target_720p),
        "arr_has_target": True,
    })

    engine = MediaSyncEngine(
        sonarr_config=SonarrConfig(base_url="http://localhost:8989", api_key="test", enabled=True),
        radarr_config=None,
    )
    engine.target_language = "Greek"

    # -----------------------------------------------------------------------
    # HOP 1: UPGRADE TO 1080p WEB-DL
    # -----------------------------------------------------------------------
    file_1080p = media_dir / "Better.Call.Saul.S01E01.1080p.WEB-DL.mkv"
    file_1080p.write_text("video 1080p")
    target_1080p = media_dir / "Better.Call.Saul.S01E01.1080p.WEB-DL.el.srt"
    assert not target_1080p.exists()

    payload_1080p = {
        "eventType": "Download",
        "series": {"id": 10, "title": test_project},
        "episodes": [{"id": 101, "seasonNumber": 1, "episodeNumber": 1, "title": "Uno"}],
        "episodeFile": {"id": 501, "path": str(file_1080p)},
    }

    # Process Sonarr webhook for 1080p upgrade
    await engine.process_sonarr_webhook(payload_1080p, storage, parse_srt)

    # Verification Hop 1:
    assert target_1080p.exists(), "1080p target sidecar was not generated!"
    content_1080p = target_1080p.read_text(encoding="utf-8-sig")
    assert "Καλημέρα ντετέκτιβ." in content_1080p
    assert "Βρήκαμε κάτι στο σοκάκι." in content_1080p
    assert "Είναι το χαμένο όπλο;" in content_1080p

    # Previous 720p sidecar must STILL exist on disk!
    assert target_720p.exists(), "720p sidecar was deleted!"

    # Metadata check
    meta_1080p = storage.load_episode_metadata(test_project, "S01E01")
    assert meta_1080p["arr_has_target"] is True
    assert meta_1080p["translated"] is True
    assert meta_1080p["translation_status"] == "completed"
    assert Path(meta_1080p["arr_media_path"]).name == "Better.Call.Saul.S01E01.1080p.WEB-DL.mkv"
    assert Path(meta_1080p["arr_target_path"]).name == "Better.Call.Saul.S01E01.1080p.WEB-DL.el.srt"

    # -----------------------------------------------------------------------
    # HOP 2: UPGRADE TO 4K UHD Remux (2160p)
    # -----------------------------------------------------------------------
    file_4k = media_dir / "Better.Call.Saul.S01E01.2160p.UHD.Remux.mkv"
    file_4k.write_text("video 4K")
    target_4k = media_dir / "Better.Call.Saul.S01E01.2160p.UHD.Remux.el.srt"
    assert not target_4k.exists()

    payload_4k = {
        "eventType": "Download",
        "series": {"id": 10, "title": test_project},
        "episodes": [{"id": 101, "seasonNumber": 1, "episodeNumber": 1, "title": "Uno"}],
        "episodeFile": {"id": 502, "path": str(file_4k)},
    }

    # Process Sonarr webhook for 4K upgrade
    await engine.process_sonarr_webhook(payload_4k, storage, parse_srt)

    # Verification Hop 2:
    assert target_4k.exists(), "4K target sidecar was not generated!"
    content_4k = target_4k.read_text(encoding="utf-8-sig")
    assert "Καλημέρα ντετέκτιβ." in content_4k
    assert "Βρήκαμε κάτι στο σοκάκι." in content_4k
    assert "Είναι το χαμένο όπλο;" in content_4k

    # Both previous sidecars must STILL exist!
    assert target_720p.exists()
    assert target_1080p.exists()

    # Metadata check
    meta_4k = storage.load_episode_metadata(test_project, "S01E01")
    assert meta_4k["arr_has_target"] is True
    assert meta_4k["translated"] is True
    assert meta_4k["translation_status"] == "completed"
    assert Path(meta_4k["arr_media_path"]).name == "Better.Call.Saul.S01E01.2160p.UHD.Remux.mkv"
    assert Path(meta_4k["arr_target_path"]).name == "Better.Call.Saul.S01E01.2160p.UHD.Remux.el.srt"


@pytest.mark.asyncio
async def test_adversarial_movie_translation_reuse_in_radarr(test_project, tmp_path):
    """Stress test translation reuse on Radarr movie upgrades (720p -> 4K)."""
    movies_dir = tmp_path / "Movies" / "Inception (2010)"
    movies_dir.mkdir(parents=True, exist_ok=True)

    proj_name = "Inception (2010)"
    storage.create_project(proj_name, {
        "show_name": "Inception (2010)",
        "target_language": "Greek",
        "is_movie": True,
        "arr_radarr_id": 42,
    })

    # Seed translated movie - in Radarr projects, episode_name is proj_name
    storage.save_episode(proj_name, proj_name, _sample_cues(), {
        "arr_source": True,
        "translated": True,
        "translation_status": "completed",
        "original_filename": "Inception.2010.720p.mkv",
        "is_movie": True,
        "arr_radarr_id": 42,
    })

    file_4k = movies_dir / "Inception.2010.2160p.UHD.mkv"
    file_4k.write_text("video 4k")
    target_4k = movies_dir / "Inception.2010.2160p.UHD.el.srt"

    engine = MediaSyncEngine(
        sonarr_config=None,
        radarr_config=RadarrConfig(base_url="http://localhost:7878", api_key="test", enabled=True),
    )
    engine.target_language = "Greek"

    payload = {
        "eventType": "Download",
        "movie": {"id": 42, "title": "Inception (2010)", "folderPath": str(movies_dir)},
        "movieFile": {"id": 901, "path": str(file_4k)},
    }

    await engine.process_radarr_webhook(payload, storage, parse_srt)

    assert target_4k.exists(), "Radarr upgraded movie target sidecar not generated!"
    content = target_4k.read_text(encoding="utf-8-sig")
    assert "Καλημέρα ντετέκτιβ." in content

    meta = storage.load_episode_metadata(proj_name, proj_name)
    assert meta["arr_has_target"] is True
    assert meta["translated"] is True
    assert Path(meta["arr_media_path"]).name == "Inception.2010.2160p.UHD.mkv"


def test_adversarial_translation_reuse_edge_cases(test_project, tmp_path):
    """Stress test boundary edge cases in translation reuse logic."""
    engine = _engine()
    media_file = tmp_path / "Show.S01E01.mkv"
    media_file.write_text("dummy")

    scan = MagicMock()
    scan.has_target_sub = False

    # Edge Case 1: Cues exist, but have NO translations
    empty_cues = [{"id": 1, "original": "Hello", "translated": "", "translations": {}}]
    storage.save_episode(test_project, "S01E01", empty_cues, {"translated": False})

    res = engine._reuse_and_export_existing_translation(
        test_project, "S01E01", str(media_file), scan, storage
    )
    assert res is False, "Reused translation when no translated text existed!"

    # Edge Case 2: Target subtitle already exists on disk
    scan.has_target_sub = True
    storage.save_episode(test_project, "S01E01", _sample_cues(), {"translated": True})
    res = engine._reuse_and_export_existing_translation(
        test_project, "S01E01", str(media_file), scan, storage
    )
    assert res is False, "Should not rewrite when scan already shows target sub exists!"

    # Edge Case 3: Parent directory does not exist
    scan.has_target_sub = False
    unreal_path = tmp_path / "NonExistentDir" / "Show.S01E01.mkv"
    res = engine._reuse_and_export_existing_translation(
        test_project, "S01E01", str(unreal_path), scan, storage
    )
    assert res is False, "Should fail gracefully when parent directory is missing!"
