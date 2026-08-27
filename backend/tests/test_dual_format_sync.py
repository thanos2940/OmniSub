"""Tests for the dual-format subtitle synchronization and sibling-episode lifecycle.

Covers Sonarr/Radarr synchronization, webhook processing, same-format target matching,
sticky sibling pruning, side-by-side export of distinct extensions, and queue enqueuing.
"""

import os
import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from utils import storage
from integrations.media_sync_engine import MediaSyncEngine, SyncResult, secondary_episode_name
from integrations.subtitle_scanner import SubtitleScannerService
from integrations.path_resolver import PathResolver
from integrations.sonarr import SonarrConfig
from integrations.radarr import RadarrConfig

SAMPLE_SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello World\n"
SAMPLE_ASS = """[Script Info]
Title: Test
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hello World
"""

@pytest.fixture
def setup_storage(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)
    
    from utils import metadata_index
    monkeypatch.setattr(metadata_index, "DB_FILE", tmp_path / "omnisub_test.db")
    metadata_index._init()
    
    monkeypatch.setattr(storage, "load_global_config", lambda: {
        "source_clean_enabled": False,
        "merge_split_cues": False,
        "strip_sdh": False,
        "preserve_italics": False,
        "incremental_retranslate_enabled": False,
    })
    return projects_dir

@pytest.fixture
def mock_media_dir(tmp_path):
    tv_dir = tmp_path / "tv" / "Show Title" / "Season 01"
    tv_dir.mkdir(parents=True, exist_ok=True)
    
    video_file = tv_dir / "Show Title.S01E01.mkv"
    video_file.touch()

    srt_sub = tv_dir / "Show Title.S01E01.en.srt"
    srt_sub.write_text(SAMPLE_SRT, encoding="utf-8")
    
    ass_sub = tv_dir / "Show Title.S01E01.en.ass"
    ass_sub.write_text(SAMPLE_ASS, encoding="utf-8")

    el_srt_sub = tv_dir / "Show Title.S01E01.el.srt"
    el_srt_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nΓεια σου κόσμε\n", encoding="utf-8")

    el_ass_sub = tv_dir / "Show Title.S01E01.el.ass"
    el_ass_sub.write_text(SAMPLE_ASS.replace("Hello World", "Γεια σου κόσμε"), encoding="utf-8")

    return {
        "dir": tv_dir,
        "video": video_file,
        "en_srt": srt_sub,
        "en_ass": ass_sub,
        "el_srt": el_srt_sub,
        "el_ass": el_ass_sub
    }

@pytest.fixture
def mock_movie_dir(tmp_path):
    movie_dir = tmp_path / "movies" / "Movie Title"
    movie_dir.mkdir(parents=True, exist_ok=True)
    
    video_file = movie_dir / "Movie Title.mkv"
    video_file.touch()

    srt_sub = movie_dir / "Movie Title.en.srt"
    srt_sub.write_text(SAMPLE_SRT, encoding="utf-8")
    
    ass_sub = movie_dir / "Movie Title.en.ass"
    ass_sub.write_text(SAMPLE_ASS, encoding="utf-8")

    return {
        "dir": movie_dir,
        "video": video_file,
        "en_srt": srt_sub,
        "en_ass": ass_sub
    }

@pytest.fixture
def mock_arr_clients(monkeypatch):
    mock_get_all_series = AsyncMock(return_value=[
        {"id": 1, "title": "Show Title", "path": "/mock/tv/Show Title", "seriesType": "standard"}
    ])
    mock_get_episodes = AsyncMock(return_value=[
        {"id": 101, "seriesId": 1, "seasonNumber": 1, "episodeNumber": 1, "title": "Episode 1", "hasFile": True, "episodeFileId": 201}
    ])
    mock_get_episode_files = AsyncMock(return_value=[
        {"id": 201, "path": "/mock/tv/Show Title/Season 01/Show Title.S01E01.mkv"}
    ])
    mock_get_all_movies = AsyncMock(return_value=[
        {"id": 1, "title": "Movie Title", "movieFile": {"path": "/mock/movies/Movie Title/Movie Title.mkv"}}
    ])

    monkeypatch.setattr("integrations.media_sync_engine.get_all_series", mock_get_all_series)
    monkeypatch.setattr("integrations.media_sync_engine.get_episodes", mock_get_episodes)
    monkeypatch.setattr("integrations.media_sync_engine.get_episode_files", mock_get_episode_files)
    monkeypatch.setattr("integrations.media_sync_engine.get_all_movies", mock_get_all_movies)

    return {
        "get_all_series": mock_get_all_series,
        "get_episodes": mock_get_episodes,
        "get_episode_files": mock_get_episode_files,
        "get_all_movies": mock_get_all_movies
    }


def test_same_format_target_lookup():
    """Verify that SubtitleScannerService.same_format_target correctly matches
    target files of the exact same format extension.
    """
    svc = SubtitleScannerService(source_lang_code="en", target_lang_code="el")
    
    files = [
        Path("Show.S01E01.en.ass"),
        Path("Show.S01E01.el.srt"),
        Path("Show.S01E01.el.ass"),
    ]
    
    target_ass = svc.same_format_target(files, "Show.S01E01", ".ass")
    assert target_ass == "Show.S01E01.el.ass"
    
    target_srt = svc.same_format_target(files, "Show.S01E01", ".srt")
    assert target_srt == "Show.S01E01.el.srt"

    target_ssa = svc.same_format_target(files, "Show.S01E01", ".ssa")
    assert target_ssa is None


@pytest.mark.asyncio
async def test_toggle_on_creates_sibling_per_format(
    setup_storage, mock_media_dir, mock_arr_clients
):
    """Verify sync creates a base (.ass) and sibling (.srt) episode when toggle is ON."""
    project_name = "Show Title"
    storage.create_project(project_name, {
        "show_name": "Show Title",
        "target_language": "Greek",
        "settings": {"translate_all_source_formats": True}
    })

    resolver = PathResolver(mappings=[
        {"remote": "/mock/tv/Show Title", "local": str(mock_media_dir["dir"].parent)}
    ])

    engine = MediaSyncEngine(
        sonarr_config=SonarrConfig(api_key="key", enabled=True),
        resolver=resolver,
        target_lang_code="el",
        target_language="Greek"
    )

    result = await engine.full_sync()

    episodes = storage.list_episodes(project_name)
    assert "S01E01" in episodes
    assert "S01E01 [srt]" in episodes

    base_meta = storage.load_episode_metadata(project_name, "S01E01")
    assert base_meta.get("original_format") == "ass"
    assert base_meta.get("arr_secondary_of") is None
    assert base_meta.get("arr_alt_formats") == ["srt"]

    sib_meta = storage.load_episode_metadata(project_name, "S01E01 [srt]")
    assert sib_meta.get("original_format") == "srt"
    assert sib_meta.get("arr_secondary_of") == "S01E01"
    assert sib_meta.get("arr_source_format") == "srt"
    assert result.new_episodes == 2


@pytest.mark.asyncio
async def test_toggle_on_dedupes_same_extension_secondaries(
    setup_storage, mock_media_dir, mock_arr_clients
):
    """Two source files sharing an extension (e.g. ".en.srt" and ".eng.srt") must not
    fight over the same sibling episode name. Only one "[srt]" sibling should be
    created, and re-syncing must not keep flip-flopping its fingerprint/content."""
    project_name = "Show Title"
    storage.create_project(project_name, {
        "show_name": "Show Title",
        "target_language": "Greek",
        "settings": {"translate_all_source_formats": True}
    })

    # A second, duplicate-extension source file alongside the existing ".en.srt".
    dupe_srt = mock_media_dir["dir"] / "Show Title.S01E01.eng.srt"
    dupe_srt.write_text(SAMPLE_SRT.replace("Hello World", "Hello World (dupe)"), encoding="utf-8")

    resolver = PathResolver(mappings=[
        {"remote": "/mock/tv/Show Title", "local": str(mock_media_dir["dir"].parent)}
    ])

    engine = MediaSyncEngine(
        sonarr_config=SonarrConfig(api_key="key", enabled=True),
        resolver=resolver,
        target_lang_code="el",
        target_language="Greek"
    )

    result1 = await engine.full_sync()

    episodes = storage.list_episodes(project_name)
    srt_siblings = [e for e in episodes if e == "S01E01 [srt]"]
    assert len(srt_siblings) == 1, "duplicate-extension sources must collapse to one sibling"

    base_meta = storage.load_episode_metadata(project_name, "S01E01")
    assert base_meta.get("arr_alt_formats") == ["srt"], "alt-formats list must not contain duplicate entries"

    sib_meta_1 = storage.load_episode_metadata(project_name, "S01E01 [srt]")
    fp_1 = sib_meta_1.get("arr_sub_fingerprint")

    # Re-sync: with nothing changed on disk, the sibling pick must be stable (no
    # fingerprint flapping / no spurious re-import) rather than alternating between
    # the two same-extension source files each pass.
    result2 = await engine.full_sync()
    sib_meta_2 = storage.load_episode_metadata(project_name, "S01E01 [srt]")
    assert sib_meta_2.get("arr_sub_fingerprint") == fp_1
    assert result2.updated_episodes == 0


@pytest.mark.asyncio
async def test_toggle_off_single_source(
    setup_storage, mock_media_dir, mock_arr_clients
):
    """Verify sync only creates base episode and ignores secondary formats when toggle is OFF."""
    project_name = "Show Title"
    storage.create_project(project_name, {
        "show_name": "Show Title",
        "target_language": "Greek",
        "settings": {"translate_all_source_formats": False}
    })

    resolver = PathResolver(mappings=[
        {"remote": "/mock/tv/Show Title", "local": str(mock_media_dir["dir"].parent)}
    ])

    engine = MediaSyncEngine(
        sonarr_config=SonarrConfig(api_key="key", enabled=True),
        resolver=resolver,
        target_lang_code="el",
        target_language="Greek"
    )

    result = await engine.full_sync()

    episodes = storage.list_episodes(project_name)
    assert "S01E01" in episodes
    assert "S01E01 [srt]" not in episodes

    base_meta = storage.load_episode_metadata(project_name, "S01E01")
    assert base_meta.get("original_format") == "ass"
    assert result.new_episodes == 1


@pytest.mark.asyncio
async def test_toggle_off_preserves_existing_sibling(
    setup_storage, mock_media_dir, mock_arr_clients
):
    """Verify that toggle OFF does not delete pre-existing siblings on disk (sticky lifecycle)."""
    project_name = "Show Title"
    storage.create_project(project_name, {
        "show_name": "Show Title",
        "target_language": "Greek",
        "settings": {"translate_all_source_formats": False}
    })

    # Pre-create base and sibling in storage
    from utils.source_clean import import_and_clean_srt
    import_and_clean_srt(project_name, "S01E01", SAMPLE_ASS, filename="Show Title.S01E01.en.ass")
    base_meta = storage.load_episode_metadata(project_name, "S01E01")
    base_meta.update({"arr_source": True, "arr_sub_path": str(mock_media_dir["en_ass"]), "arr_media_path": str(mock_media_dir["video"])})
    storage.save_episode_metadata(project_name, "S01E01", base_meta)

    import_and_clean_srt(project_name, "S01E01 [srt]", SAMPLE_SRT, filename="Show Title.S01E01.en.srt")
    sib_meta = storage.load_episode_metadata(project_name, "S01E01 [srt]")
    sib_meta.update({
        "arr_source": True, 
        "arr_secondary_of": "S01E01", 
        "arr_source_format": "srt", 
        "arr_sub_path": str(mock_media_dir["en_srt"]), 
        "arr_media_path": str(mock_media_dir["video"])
    })
    storage.save_episode_metadata(project_name, "S01E01 [srt]", sib_meta)

    resolver = PathResolver(mappings=[
        {"remote": "/mock/tv/Show Title", "local": str(mock_media_dir["dir"].parent)}
    ])

    engine = MediaSyncEngine(
        sonarr_config=SonarrConfig(api_key="key", enabled=True),
        resolver=resolver,
        target_lang_code="el",
        target_language="Greek"
    )

    result = await engine.full_sync()

    episodes = storage.list_episodes(project_name)
    assert "S01E01" in episodes
    assert "S01E01 [srt]" in episodes  # Preserved!


@pytest.mark.asyncio
async def test_sibling_pruned_when_source_removed(
    setup_storage, mock_media_dir, mock_arr_clients
):
    """Verify that when a sibling's source file disappears, it is pruned and its target is deleted."""
    project_name = "Show Title"
    storage.create_project(project_name, {
        "show_name": "Show Title",
        "target_language": "Greek",
        "settings": {"translate_all_source_formats": True}
    })

    # Pre-create base and sibling
    from utils.source_clean import import_and_clean_srt
    import_and_clean_srt(project_name, "S01E01", SAMPLE_ASS, filename="Show Title.S01E01.en.ass")
    base_meta = storage.load_episode_metadata(project_name, "S01E01")
    base_meta.update({"arr_source": True, "arr_sub_path": str(mock_media_dir["en_ass"]), "arr_media_path": str(mock_media_dir["video"])})
    storage.save_episode_metadata(project_name, "S01E01", base_meta)

    target_path = mock_media_dir["dir"] / "Show Title.S01E01.el.srt"
    target_path.touch()

    import_and_clean_srt(project_name, "S01E01 [srt]", SAMPLE_SRT, filename="Show Title.S01E01.en.srt")
    sib_meta = storage.load_episode_metadata(project_name, "S01E01 [srt]")
    sib_meta.update({
        "arr_source": True, 
        "arr_secondary_of": "S01E01", 
        "arr_source_format": "srt", 
        "arr_sub_path": str(mock_media_dir["en_srt"]), 
        "arr_media_path": str(mock_media_dir["video"]),
        "arr_target_path": str(target_path)
    })
    storage.save_episode_metadata(project_name, "S01E01 [srt]", sib_meta)

    # Remove the source subtitle of the sibling
    os.remove(mock_media_dir["en_srt"])

    resolver = PathResolver(mappings=[
        {"remote": "/mock/tv/Show Title", "local": str(mock_media_dir["dir"].parent)}
    ])

    engine = MediaSyncEngine(
        sonarr_config=SonarrConfig(api_key="key", enabled=True),
        resolver=resolver,
        target_lang_code="el",
        target_language="Greek"
    )

    result = await engine.full_sync()

    episodes = storage.list_episodes(project_name)
    assert "S01E01" in episodes
    assert "S01E01 [srt]" not in episodes  # Pruned!
    assert not target_path.exists()  # Orphaned target deleted!
    assert result.removed_episodes == 1


@pytest.mark.asyncio
async def test_resync_refreshes_stale_media_path(
    setup_storage, mock_media_dir, mock_arr_clients
):
    """Media moved by *arr (e.g. to another share) must refresh arr_media_path on re-sync.

    The export writes next to arr_media_path, so a stale one makes every export fail
    silently — the exporter logs and swallows the error. An already-imported episode
    with an unchanged source fingerprint only ever takes the "no import" branch, so the
    refresh has to happen there.
    """
    project_name = "Show Title"
    storage.create_project(project_name, {
        "show_name": "Show Title",
        "target_language": "Greek",
        "settings": {"translate_all_source_formats": True},
    })

    resolver = PathResolver(mappings=[
        {"remote": "/mock/tv/Show Title", "local": str(mock_media_dir["dir"].parent)}
    ])
    engine = MediaSyncEngine(
        sonarr_config=SonarrConfig(api_key="key", enabled=True),
        resolver=resolver,
        target_lang_code="el",
        target_language="Greek",
    )

    await engine.full_sync()
    real_media_path = storage.load_episode_metadata(project_name, "S01E01")["arr_media_path"]

    # Poison both the base and the sibling with a path on a share that no longer exists.
    stale = str(Path("//old-share/d") / Path(real_media_path).name)
    for ep in ("S01E01", "S01E01 [srt]"):
        meta = storage.load_episode_metadata(project_name, ep)
        meta["arr_media_path"] = stale
        storage.save_episode_metadata(project_name, ep, meta)

    # Source content is untouched, so this re-sync takes the "already imported" branch.
    result = await engine.full_sync()
    assert result.new_episodes == 0

    for ep in ("S01E01", "S01E01 [srt]"):
        assert storage.load_episode_metadata(project_name, ep)["arr_media_path"] == real_media_path


@pytest.mark.asyncio
async def test_movie_resync_refreshes_stale_media_path(
    setup_storage, mock_movie_dir, mock_arr_clients
):
    """Same stale-media-path refresh as the series case, on the Radarr path."""
    project_name = "Movie Title (id-1)"
    storage.create_project(project_name, {
        "show_name": "Movie Title",
        "target_language": "Greek",
        "settings": {"translate_all_source_formats": False},
    })

    resolver = PathResolver(mappings=[
        {"remote": "/mock/movies/Movie Title", "local": str(mock_movie_dir["dir"])}
    ])
    engine = MediaSyncEngine(
        radarr_config=RadarrConfig(api_key="key", enabled=True),
        resolver=resolver,
        target_lang_code="el",
        target_language="Greek",
    )

    await engine.full_sync()
    meta = storage.load_episode_metadata(project_name, project_name)
    real_media_path = meta["arr_media_path"]

    meta["arr_media_path"] = str(Path("//old-share/d") / Path(real_media_path).name)
    storage.save_episode_metadata(project_name, project_name, meta)

    await engine.full_sync()
    assert storage.load_episode_metadata(project_name, project_name)["arr_media_path"] == real_media_path


def test_import_persists_extra_metadata_in_the_same_write(setup_storage):
    """extra_metadata must land in the episode's metadata.json via import_and_clean_srt
    itself, not a follow-up save."""
    from utils.source_clean import import_and_clean_srt

    storage.create_project("P", {"show_name": "P", "target_language": "Greek"})
    import_and_clean_srt(
        "P", "S01E01", SAMPLE_SRT,
        filename="P.S01E01.en.srt",
        extra_metadata={"arr_source": True, "arr_media_path": "/media/P.S01E01.mkv"},
    )

    meta = storage.load_episode_metadata("P", "S01E01")
    assert meta["arr_media_path"] == "/media/P.S01E01.mkv"
    assert meta["arr_source"] is True


@pytest.mark.asyncio
async def test_import_metadata_complete_without_followup_save(
    setup_storage, mock_media_dir, mock_arr_clients, monkeypatch
):
    """An imported episode must never be left without arr_media_path / arr_source.

    The sync engine used to enrich the metadata returned by import_and_clean_srt and
    save a *second* time. Anything that interrupted the run in between (the concurrent
    set-mutation crash did exactly this) left an episode with original.srt + data.json
    but no arr_media_path — the background worker rejects it forever, and prune skips
    it because arr_source is missing too, so it can never be cleaned up automatically.

    Neutralising the follow-up save must therefore change nothing about the result.
    """
    project_name = "Show Title"
    storage.create_project(project_name, {
        "show_name": "Show Title",
        "target_language": "Greek",
        "settings": {"translate_all_source_formats": True},
    })

    monkeypatch.setattr(storage, "save_episode_metadata", lambda *a, **k: None)

    resolver = PathResolver(mappings=[
        {"remote": "/mock/tv/Show Title", "local": str(mock_media_dir["dir"].parent)}
    ])
    engine = MediaSyncEngine(
        sonarr_config=SonarrConfig(api_key="key", enabled=True),
        resolver=resolver,
        target_lang_code="el",
        target_language="Greek",
    )
    await engine.full_sync()

    for ep in ("S01E01", "S01E01 [srt]"):
        meta = storage.load_episode_metadata(project_name, ep)
        assert meta, f"{ep} was not imported at all"
        assert meta.get("arr_media_path"), f"{ep} imported without arr_media_path"
        assert meta.get("arr_source") is True, f"{ep} imported without arr_source"


def test_sibling_export_extension(setup_storage, mock_media_dir):
    """Verify that exporting translated siblings writes the correct extension without collisions."""
    project_name = "Show Title"
    storage.create_project(project_name, {"show_name": "Show Title", "target_language": "Greek"})
    
    from utils.source_clean import import_and_clean_srt
    
    import_and_clean_srt(project_name, "S01E01", SAMPLE_ASS, filename="Show Title.S01E01.en.ass")
    import_and_clean_srt(project_name, "S01E01 [srt]", SAMPLE_SRT, filename="Show Title.S01E01.en.srt")
    
    ep_data_base = storage.load_episode(project_name, "S01E01")["data"]
    ep_data_base[0]["translated"] = "Γεια"
    ep_data_base[0]["translations"] = {"el": "Γεια"}
    ep_meta_base = storage.load_episode_metadata(project_name, "S01E01")
    ep_meta_base.update({
        "arr_source": True,
        "arr_media_path": str(mock_media_dir["video"]),
        "arr_sub_path": str(mock_media_dir["en_ass"])
    })
    
    ep_data_sib = storage.load_episode(project_name, "S01E01 [srt]")["data"]
    ep_data_sib[0]["translated"] = "Γεια"
    ep_data_sib[0]["translations"] = {"el": "Γεια"}
    ep_meta_sib = storage.load_episode_metadata(project_name, "S01E01 [srt]")
    ep_meta_sib.update({
        "arr_source": True,
        "arr_secondary_of": "S01E01",
        "arr_source_format": "srt",
        "arr_media_path": str(mock_media_dir["video"]),
        "arr_sub_path": str(mock_media_dir["en_srt"])
    })

    from services.translation_service import auto_export_translated_subtitle
    
    base_exported_path = auto_export_translated_subtitle(project_name, "S01E01", ep_data_base, ep_meta_base)
    sib_exported_path = auto_export_translated_subtitle(project_name, "S01E01 [srt]", ep_data_sib, ep_meta_sib)
    
    assert base_exported_path.endswith(".el.ass")
    assert sib_exported_path.endswith(".el.srt")
    assert Path(base_exported_path).exists()
    assert Path(sib_exported_path).exists()


@pytest.mark.asyncio
async def test_webhook_download_creates_both_formats(
    setup_storage, mock_media_dir, mock_arr_clients
):
    """Verify Sonarr webhook import creates base and sibling when toggle is ON."""
    project_name = "Show Title"
    storage.create_project(project_name, {
        "show_name": "Show Title",
        "target_language": "Greek",
        "settings": {"translate_all_source_formats": True}
    })

    resolver = PathResolver(mappings=[
        {"remote": "/mock/tv/Show Title", "local": str(mock_media_dir["dir"].parent)}
    ])

    engine = MediaSyncEngine(
        sonarr_config=SonarrConfig(api_key="key", enabled=True),
        resolver=resolver,
        target_lang_code="el",
        target_language="Greek"
    )

    webhook_payload = {
        "eventType": "Download",
        "series": {"id": 1, "title": "Show Title"},
        "episodes": [{"id": 101, "seasonNumber": 1, "episodeNumber": 1, "title": "Episode 1"}],
        "episodeFile": {"path": "/mock/tv/Show Title/Season 01/Show Title.S01E01.mkv"}
    }

    from utils.srt_parser import parse_srt
    await engine.process_sonarr_webhook(webhook_payload, storage, parse_srt)

    episodes = storage.list_episodes(project_name)
    assert "S01E01" in episodes
    assert "S01E01 [srt]" in episodes


@pytest.mark.asyncio
async def test_stats_calculation_excludes_siblings(
    setup_storage, mock_media_dir, mock_arr_clients
):
    """Verify project episode stats do not count sibling episodes to avoid duplication."""
    project_name = "Show Title"
    storage.create_project(project_name, {
        "show_name": "Show Title",
        "target_language": "Greek",
        "settings": {"translate_all_source_formats": True}
    })

    resolver = PathResolver(mappings=[
        {"remote": "/mock/tv/Show Title", "local": str(mock_media_dir["dir"].parent)}
    ])

    engine = MediaSyncEngine(
        sonarr_config=SonarrConfig(api_key="key", enabled=True),
        resolver=resolver,
        target_lang_code="el",
        target_language="Greek"
    )

    await engine.full_sync()

    meta = storage.load_project_metadata(project_name)
    # 1 base + 1 sibling on disk, but total count must stay 1
    assert meta.get("arr_total_episodes") == 1
    assert meta.get("arr_translated_episodes") == 1


def test_sibling_enqueuing(setup_storage, mock_media_dir, monkeypatch):
    """Verify that enqueue_missing_for_project enqueues both base and sibling episodes
    if they are missing targets.
    """
    from services.queue_service import enqueue_missing_for_project, TranslationQueue
    
    # Patch TranslationQueue init to use our temp db.
    original_init = TranslationQueue.__init__
    db_path = setup_storage.parent / "omnisub.db"
    def mock_init(self, *args, **kwargs):
        if len(args) < 1 and "db_path" not in kwargs:
            kwargs["db_path"] = db_path
        original_init(self, *args, **kwargs)
    monkeypatch.setattr(TranslationQueue, "__init__", mock_init)

    project_name = "Show Title"
    storage.create_project(project_name, {
        "show_name": "Show Title",
        "target_language": "Greek",
        "settings": {"translate_all_source_formats": True}
    })

    # Pre-create base and sibling.
    from utils.source_clean import import_and_clean_srt
    import_and_clean_srt(project_name, "S01E01", SAMPLE_ASS, filename="Show Title.S01E01.en.ass")
    base_meta = storage.load_episode_metadata(project_name, "S01E01")
    base_meta.update({"arr_source": True, "arr_sub_path": str(mock_media_dir["en_ass"]), "arr_media_path": str(mock_media_dir["video"])})
    storage.save_episode_metadata(project_name, "S01E01", base_meta)

    import_and_clean_srt(project_name, "S01E01 [srt]", SAMPLE_SRT, filename="Show Title.S01E01.en.srt")
    sib_meta = storage.load_episode_metadata(project_name, "S01E01 [srt]")
    sib_meta.update({
        "arr_source": True, 
        "arr_secondary_of": "S01E01", 
        "arr_source_format": "srt", 
        "arr_sub_path": str(mock_media_dir["en_srt"]), 
        "arr_media_path": str(mock_media_dir["video"])
    })
    storage.save_episode_metadata(project_name, "S01E01 [srt]", sib_meta)

    # Let's call enqueue_missing_for_project
    enqueued = enqueue_missing_for_project(project_name)
    assert len(enqueued) == 2
    
    # Verify the contents of the queue database
    queue = TranslationQueue(db_path)
    with queue._get_connection() as conn:
        rows = conn.execute("SELECT episode_name FROM translation_queue").fetchall()
        episodes_in_queue = [row["episode_name"] for row in rows]
    
    assert "S01E01" in episodes_in_queue
    assert "S01E01 [srt]" in episodes_in_queue


@pytest.mark.asyncio
async def test_movie_sync_creates_sibling_per_format(
    setup_storage, mock_movie_dir, mock_arr_clients
):
    """Verify movie (Radarr) sync and settings behavior for dual-format (base and sibling)."""
    project_name = "Movie Title (id-1)"
    
    # 1. Test with toggle OFF (translate_all_source_formats = False)
    storage.create_project(project_name, {
        "show_name": "Movie Title",
        "target_language": "Greek",
        "settings": {"translate_all_source_formats": False}
    })

    resolver = PathResolver(mappings=[
        {"remote": "/mock/movies/Movie Title", "local": str(mock_movie_dir["dir"])}
    ])

    engine = MediaSyncEngine(
        radarr_config=RadarrConfig(api_key="key", enabled=True),
        resolver=resolver,
        target_lang_code="el",
        target_language="Greek"
    )

    result_off = await engine.full_sync()

    episodes_off = storage.list_episodes(project_name)
    assert "Movie Title (id-1)" in episodes_off
    assert "Movie Title (id-1) [srt]" not in episodes_off
    assert result_off.new_episodes == 1

    # 2. Test with toggle ON (translate_all_source_formats = True)
    meta = storage.load_project_metadata(project_name)
    meta["settings"]["translate_all_source_formats"] = True
    storage.save_project_metadata(project_name, meta)

    result_on = await engine.full_sync()

    episodes_on = storage.list_episodes(project_name)
    assert "Movie Title (id-1)" in episodes_on
    assert "Movie Title (id-1) [srt]" in episodes_on
    assert result_on.new_episodes == 1

    base_meta = storage.load_episode_metadata(project_name, "Movie Title (id-1)")
    assert base_meta.get("original_format") == "ass"
    assert base_meta.get("arr_secondary_of") is None
    assert base_meta.get("arr_alt_formats") == ["srt"]

    sib_meta = storage.load_episode_metadata(project_name, "Movie Title (id-1) [srt]")
    assert sib_meta.get("original_format") == "srt"
    assert sib_meta.get("arr_secondary_of") == "Movie Title (id-1)"
    assert sib_meta.get("arr_source_format") == "srt"


@pytest.mark.asyncio
async def test_movie_sync_adopts_legacy_project_by_radarr_id(
    setup_storage, mock_movie_dir, mock_arr_clients
):
    """A movie project created before the "Title (year)"/"Title (id-N)" disambiguation
    naming scheme (bare "Movie Title", no suffix) must be ADOPTED by radarr id on the
    next sync — not duplicated into a second "Movie Title (id-1)" project, and its
    existing translated episode must not be orphaned/renamed out from under it.
    """
    from unittest.mock import patch

    legacy_project_name = "Movie Title"
    storage.create_project(legacy_project_name, {
        "show_name": "Movie Title",
        "target_language": "Greek",
        "type": "movie",
        "arr_source": True,
        "arr_media_type": "movie",
        "arr_radarr_id": 1,  # matches mock_arr_clients' get_all_movies id=1
    })
    resolver = PathResolver(mappings=[
        {"remote": "/mock/movies/Movie Title", "local": str(mock_movie_dir["dir"])}
    ])
    resolved_media_path = resolver.resolve("/mock/movies/Movie Title/Movie Title.mkv")

    # Pre-existing translated content, previously imported by an earlier sync (hence
    # arr_media_path + a stored fingerprint) — this must survive the adoption untouched.
    storage.save_episode(legacy_project_name, legacy_project_name, [
        {"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hello World", "translated": "Γεια σου κόσμε"}
    ], {
        "arr_source": True, "translated": True, "translation_status": "completed",
        "arr_media_path": resolved_media_path, "arr_sub_fingerprint": "fixed_fp_123",
    })

    engine = MediaSyncEngine(
        radarr_config=RadarrConfig(api_key="key", enabled=True),
        resolver=resolver,
        target_lang_code="el",
        target_language="Greek"
    )

    with patch.object(engine, "_fingerprint", return_value="fixed_fp_123"):
        result = await engine.full_sync()

    all_projects = storage.list_projects()
    assert legacy_project_name in all_projects
    assert "Movie Title (id-1)" not in all_projects, "adoption must reuse the legacy project, not spawn a duplicate"
    assert result.new_projects == 0

    episodes = storage.list_episodes(legacy_project_name)
    assert episodes == [legacy_project_name], "episode name must mirror the (adopted) project name, not a freshly-suffixed one"

    ep_data = storage.load_episode(legacy_project_name, legacy_project_name)
    assert ep_data["data"][0]["translated"] == "Γεια σου κόσμε", "pre-existing translation must survive the sync"

    # The legacy project must not have been disabled either (it was "seen" via adoption).
    meta = storage.load_project_metadata(legacy_project_name)
    assert not meta.get("arr_disabled")


def test_scanner_extension_priority():
    """Verify that SubtitleScannerService extension priority resolution (.ass > .ssa > .srt)
    works correctly in isolation.
    """
    svc = SubtitleScannerService(source_lang_code="en", target_lang_code="el")
    stem = "Show.S01E01"

    # Scenario 1: All three formats exist. Priority: .ass > .ssa > .srt
    files = [
        Path("Show.S01E01.en.srt"),
        Path("Show.S01E01.en.ssa"),
        Path("Show.S01E01.en.ass"),
    ]
    src_path, src_ext, tgt_path, src_all = svc._match_source_target(files, stem)
    assert src_ext == ".ass"
    assert src_path == "Show.S01E01.en.ass"
    assert src_all == ["Show.S01E01.en.ass", "Show.S01E01.en.ssa", "Show.S01E01.en.srt"]

    # Scenario 2: Only .ssa and .srt exist. Priority: .ssa > .srt
    files = [
        Path("Show.S01E01.en.srt"),
        Path("Show.S01E01.en.ssa"),
    ]
    src_path, src_ext, tgt_path, src_all = svc._match_source_target(files, stem)
    assert src_ext == ".ssa"
    assert src_path == "Show.S01E01.en.ssa"
    assert src_all == ["Show.S01E01.en.ssa", "Show.S01E01.en.srt"]

    # Scenario 3: Only .srt exists
    files = [
        Path("Show.S01E01.en.srt"),
    ]
    src_path, src_ext, tgt_path, src_all = svc._match_source_target(files, stem)
    assert src_ext == ".srt"
    assert src_path == "Show.S01E01.en.srt"
    assert src_all == ["Show.S01E01.en.srt"]


# ==============================================================================
# Adversarial Hardening Tests
# ==============================================================================
from unittest.mock import MagicMock, patch

@pytest.fixture
def temp_storage(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)
    
    from utils import metadata_index
    monkeypatch.setattr(metadata_index, "DB_FILE", tmp_path / "omnisub_test.db")
    metadata_index._init()
    
    monkeypatch.setattr(storage, "load_global_config", lambda: {
        "source_clean_enabled": False,
        "merge_split_cues": False,
        "strip_sdh": False,
        "preserve_italics": False,
        "incremental_retranslate_enabled": False,
    })
    return storage

@pytest.mark.asyncio
async def test_sync_engine_handles_api_failure_without_disabling_projects(temp_storage, monkeypatch):
    # Setup: Create an existing active project
    project_name = "ExistingShow"
    temp_storage.create_project(project_name, {
        "show_name": "Existing Show",
        "target_language": "Greek",
        "arr_source": True,
        "arr_disabled": False,
        "arr_media_type": "series"
    })
    
    # Configure Sonarr with enabled client, but mock api call to raise exception
    sonarr_cfg = SonarrConfig(base_url="http://mock-sonarr", api_key="key", enabled=True)
    
    engine = MediaSyncEngine(sonarr_config=sonarr_cfg, target_lang_code="el")
    
    # Mock get_all_series to raise simulated failure
    with monkeypatch.context() as m:
        m.setattr("integrations.media_sync_engine.get_all_series", AsyncMock(side_effect=Exception("API Down")))
        
        result = await engine.full_sync()
        
        # Verify that the project was NOT disabled because it was an API failure
        meta = temp_storage.load_project_metadata(project_name)
        assert meta.get("arr_disabled") is False, "Project disabled during API failure!"
        assert len(result.errors) > 0


@pytest.mark.asyncio
async def test_partial_outage_does_not_prune_inaccessible_episodes(temp_storage, tmp_path):
    project_name = "ShowName"
    temp_storage.create_project(project_name, {"show_name": "Show Name", "target_language": "Greek"})
    
    # Save S01 (accessible) and S02 (inaccessible)
    temp_storage.save_episode(project_name, "S01E01", [{"original": "Hi"}], {"arr_source": True})
    temp_storage.save_episode(project_name, "S02E01", [{"original": "Hello"}], {
        "arr_source": True,
        "arr_media_path": str(tmp_path / "inaccessible_folder" / "Show Name.S02E01.mkv")
    })
    
    # We only see S01E01 in this sync. S02 folder is unmounted (inaccessible)
    seen_episodes = {"S01E01"}
    
    engine = MediaSyncEngine(target_lang_code="el")
    
    # Add inaccessible folder to unreachable_paths
    result = SyncResult()
    result.unreachable_paths.append(str(tmp_path / "inaccessible_folder"))
    
    # Simulate pruning. S02E01 should NOT be pruned because it's a partial folder outage
    removed = engine._prune_removed_episodes(project_name, seen_episodes, result)
    
    assert "S02E01" in temp_storage.list_episodes(project_name), "Episode pruned during partial directory outage!"
    assert removed == 0


@pytest.mark.asyncio
async def test_sync_result_populates_unreachable_paths(temp_storage, tmp_path, monkeypatch):
    non_existent_dir = tmp_path / "non_existent_folder"
    non_existent_path = str(non_existent_dir / "media.mkv")
    
    scanner = SubtitleScannerService()
    # Verify scanner detects path is unreachable
    scan_res = scanner.scan_media_file(non_existent_path)
    assert scan_res.has_source_sub is False
    
    # Sync engine should capture this unreachable directory in SyncResult
    engine = MediaSyncEngine(
        sonarr_config=SonarrConfig(api_key="key", enabled=True),
        resolver=PathResolver(),
        target_lang_code="el"
    )
    
    # Setup project in temp_storage
    project_name = "UnreachableShow"
    temp_storage.create_project(project_name, {
        "show_name": "Unreachable Show",
        "target_language": "Greek",
        "arr_source": True,
        "arr_disabled": False,
    })
    
    # Mock sonarr client to return one episode pointing to non_existent_path
    mock_get_all_series = AsyncMock(return_value=[
        {"id": 1, "title": "Unreachable Show", "path": "/mock/tv/Unreachable Show", "seriesType": "standard"}
    ])
    mock_get_episodes = AsyncMock(return_value=[
        {"id": 101, "seriesId": 1, "seasonNumber": 1, "episodeNumber": 1, "title": "Episode 1", "hasFile": True, "episodeFileId": 201}
    ])
    mock_get_episode_files = AsyncMock(return_value=[
        {"id": 201, "path": "/mock/tv/Unreachable Show/Season 01/Unreachable Show.S01E01.mkv"}
    ])
    
    monkeypatch.setattr("integrations.media_sync_engine.get_all_series", mock_get_all_series)
    monkeypatch.setattr("integrations.media_sync_engine.get_episodes", mock_get_episodes)
    monkeypatch.setattr("integrations.media_sync_engine.get_episode_files", mock_get_episode_files)
    
    # Mock PathResolver to resolve remote to non_existent_path
    monkeypatch.setattr(engine.resolver, "resolve", lambda p: non_existent_path)
    
    result = await engine.full_sync()
    
    # Verify parent folder is added to result.unreachable_paths
    assert str(non_existent_dir) in result.unreachable_paths


def test_scanner_deterministic_target_selection():
    stem = "Movie.2026"
    scanner = SubtitleScannerService(source_lang_code="en", target_lang_code="el")
    
    # Two valid Greek subtitles present
    files = [
        Path("Movie.2026.greek.srt"),
        Path("Movie.2026.el.srt")
    ]
    
    # Test matching deterministic priority (should prioritize code variant ".el" over ".greek")
    src, ext, tgt, _all = scanner._match_source_target(files, stem)
    assert tgt == "Movie.2026.el.srt"


def test_path_resolver_prefix_slash_mismatch():
    # Slash mismatch: remote has trailing, local does not
    mappings = [{"remote": "/media/", "local": "D:\\Media"}]
    resolver = PathResolver(mappings)
    
    remote_path = "/media/Show/S01E01.mkv"
    resolved = resolver.resolve(remote_path)
    
    assert "MediaShow" not in resolved
    assert resolved == "D:\\Media\\Show\\S01E01.mkv"


@pytest.mark.asyncio
async def test_fingerprint_os_error_prevents_spurious_update_counts(temp_storage, tmp_path):
    project_name = "ShowName"
    temp_storage.create_project(project_name, {"show_name": "Show Name", "target_language": "Greek"})
    
    # Save existing episode with fingerprint
    temp_storage.save_episode(project_name, "S01E01", [{"original": "Hi"}], {
        "arr_source": True,
        "arr_sub_fingerprint": "123456_789",
        "arr_media_path": str(tmp_path / "media.mkv")
    })
    
    engine = MediaSyncEngine(target_lang_code="el")
    
    # Mock _fingerprint to raise OSError (returns None)
    with patch.object(engine, "_fingerprint", return_value=None):
        result = SyncResult()
        
        scan_mock = MagicMock(has_source_sub=True, source_sub_path="some_path", has_target_sub=False)
        
        await engine._import_episode_file(
            project_name=project_name,
            series_title="Show Name",
            episode_name="S01E01",
            media_path="media_path",
            scan=scan_mock,
            ep_meta={},
            result=result,
            storage=temp_storage,
            parse_srt=None,
            media_type="series"
        )
        
        assert result.updated_episodes == 0, "Spurious update registered due to fingerprint OSError"


def test_read_file_handles_utf16_and_does_not_mask_with_latin1(tmp_path):
    utf16_file = tmp_path / "utf16.srt"
    utf16_content = "1\n00:00:01,000 --> 00:00:02,000\nΓεια σου κόσμε\n"
    utf16_file.write_text(utf16_content, encoding="utf-16")
    
    engine = MediaSyncEngine(target_lang_code="el")
    
    content = engine._read_file(str(utf16_file))
    
    assert content is not None
    assert "Γεια" in content, "Failed to decode UTF-16 subtitle correctly"


@pytest.mark.asyncio
async def test_webhook_processing_handles_null_payload_fields():
    engine = MediaSyncEngine(target_lang_code="el")
    
    # Sonarr webhook payload with null "episodeFile" (common when files are deleted or testing)
    sonarr_payload = {
        "eventType": "Download",
        "series": {"id": 1, "title": "Show Title"},
        "episodes": [{"id": 2, "seasonNumber": 1, "episodeNumber": 1, "title": "Ep"}],
        "episodeFile": None
    }
    
    # Verify it returns None safely instead of throwing AttributeError
    result = await engine.process_sonarr_webhook(sonarr_payload, storage=None, parse_srt=None)
    assert result is None


@pytest.mark.asyncio
async def test_sibling_inheritance_ignores_corrupt_metadata(temp_storage, monkeypatch):
    # Create sibling project
    temp_storage.create_project("SiblingShow", {"show_name": "Show Title", "target_language": "Greek"})
    
    # Corrupt another project's metadata manually
    corrupt_meta_path = temp_storage.PROJECTS_DIR / "SiblingShow" / "metadata.json"
    corrupt_meta_path.write_text("INVALID_JSON{", encoding="utf-8")
    
    engine = MediaSyncEngine(target_lang_code="el")
    
    # Mock API calls to return empty
    monkeypatch.setattr("integrations.media_sync_engine.get_episodes", AsyncMock(return_value=[]))
    monkeypatch.setattr("integrations.media_sync_engine.get_episode_files", AsyncMock(return_value=[]))
    
    try:
        series_data = {"id": 1, "title": "Show Title", "path": "path", "seriesType": "standard"}
        result = SyncResult()
        await engine._sync_series(
            series_data=series_data,
            project_name="NewShow",
            result=result,
            storage=temp_storage,
            parse_srt=None,
            existing_projects_set={"SiblingShow"}
        )
    except Exception as e:
        pytest.fail(f"Sync series crashed due to corrupt sibling project metadata: {e}")


@pytest.mark.asyncio
async def test_duplicate_movie_titles_do_not_collide(temp_storage, tmp_path, monkeypatch):
    # Setup mock movies in RadarrConfig
    radarr_cfg = RadarrConfig(base_url="http://mock-radarr", api_key="key", enabled=True)
    engine = MediaSyncEngine(radarr_config=radarr_cfg, target_lang_code="el")
    
    # Movie 1 (2020) and Movie 2 (2025) share the exact title
    movie1 = {"id": 101, "title": "The Power", "movieFile": {"path": str(tmp_path / "The Power 2020.mkv")}}
    movie2 = {"id": 102, "title": "The Power", "movieFile": {"path": str(tmp_path / "The Power 2025.mkv")}}
    
    # Touch files so scanner detects they exist
    Path(movie1["movieFile"]["path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(movie1["movieFile"]["path"]).touch()
    Path(movie2["movieFile"]["path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(movie2["movieFile"]["path"]).touch()
    
    # Mock get_all_movies
    monkeypatch.setattr("integrations.media_sync_engine.get_all_movies", AsyncMock(return_value=[movie1, movie2]))
    
    result = await engine.full_sync()
    
    # Verify that we have two distinct projects synced
    project_names = [m["project_name"] for m in result.synced_movies]
    assert len(project_names) == 2
    assert "The Power (2020)" in project_names
    assert "The Power (2025)" in project_names

