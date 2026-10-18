"""'Translate missing' for a Sonarr/Radarr title (services.on_demand.translate_missing).

Episodes OmbiSub never imported (no sidecar was ever there) get an entry made from the media
file and are routed like an on-demand request. Network-free: Sonarr/Radarr calls are mocked,
container probes come from a fake probe cache and the queue lives in the per-test DB.
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from main import app
from utils import paths, storage
from utils.translation_queue import (
    KIND_EXTRACTION,
    KIND_TRANSLATION,
    PRIORITY_BACKLOG,
    TranslationQueue,
)

SAMPLE_SRT = """1
00:00:01,000 --> 00:00:04,000
Hello, Mark.

2
00:00:05,000 --> 00:00:08,000
Welcome to Lumon.
"""

ENG_ASS_TRACK = {"index": 2, "codec": "ass", "language": "eng", "title": "Full Subtitles", "frames": 400}
PGS_TRACK = {"index": 3, "codec": "hdmv_pgs_subtitle", "language": "eng", "title": "", "frames": 400}


def _episode(number, has_file=True):
    return {
        "id": 100 + number, "seriesId": 7, "seasonNumber": 1, "episodeNumber": number,
        "title": f"Episode {number}", "hasFile": has_file, "episodeFileId": 200 + number if has_file else 0,
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A Sonarr series with five media files and a Radarr movie, none of them imported yet."""
    import services.queue_service as queue_service
    from integrations import embedded_subs
    from utils import metadata_index

    # The derived index binds its database at import; keep this test's projects out of the shared one.
    monkeypatch.setattr(metadata_index, "DB_FILE", tmp_path / "metadata_index.db")
    metadata_index._init()
    db = paths.DB_FILE
    monkeypatch.setattr(queue_service, "TranslationQueue", lambda *a, **k: TranslationQueue(db))
    monkeypatch.setattr(queue_service, "_wake_worker", lambda: None)

    media_root = tmp_path / "media"
    storage.save_global_config({
        "sonarr_enabled": True, "sonarr_api_key": "k", "sonarr_url": "http://sonarr",
        "radarr_enabled": True, "radarr_api_key": "k", "radarr_url": "http://radarr",
        "default_target_language": "Greek", "arr_source_language": "en",
        "embedded_extraction_enabled": True,
        "arr_path_mappings": [{"remote": "/remote", "local": str(media_root)}],
    })

    season = media_root / "Severance" / "Season 01"
    season.mkdir(parents=True)
    probe_cache = {}

    def media(folder, stem, *sidecars, tracks=()):
        (folder / f"{stem}.mkv").write_bytes(b"\x1aE\xdf\xa3")
        for lang in sidecars:
            (folder / f"{stem}.{lang}.srt").write_text(SAMPLE_SRT, encoding="utf-8")
        probe_cache[f"{stem}.mkv"] = [dict(t) for t in tracks]

    media(season, "Severance.S01E01", "en")                    # already imported below
    media(season, "Severance.S01E02", tracks=[ENG_ASS_TRACK])   # muxed ASS only
    media(season, "Severance.S01E03", "el")                    # Greek already there
    media(season, "Severance.S01E05", tracks=[PGS_TRACK])       # image subtitles only
    media(season, "Severance.S01E06", "en")                    # English sidecar, never imported

    movie_dir = media_root / "Movies" / "Arrival (2016)"
    movie_dir.mkdir(parents=True)
    media(movie_dir, "Arrival.2016", tracks=[ENG_ASS_TRACK])    # a movie with only a muxed track

    monkeypatch.setattr("utils.media_probe_cache.get",
                        lambda path, fp, *a, **k: probe_cache.get(Path(path).name))
    monkeypatch.setattr("utils.media_probe_cache.put", lambda *a, **k: None)
    monkeypatch.setattr(embedded_subs, "resolve_tools",
                        lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))

    series = {"id": 7, "title": "Severance", "year": 2022, "tvdbId": 371980,
              "path": "/remote/Severance", "seriesType": "standard"}
    episodes = [_episode(1), _episode(2), _episode(3), _episode(4, has_file=False), _episode(5), _episode(6)]
    files = [{"id": 200 + n, "path": f"/remote/Severance/Season 01/Severance.S01E{n:02d}.mkv"}
             for n in (1, 2, 3, 5, 6)]
    movie = {"id": 55, "title": "Arrival", "year": 2016, "tmdbId": 329865, "hasFile": True,
             "movieFile": {"path": "/remote/Movies/Arrival (2016)/Arrival.2016.mkv"}}

    monkeypatch.setattr("integrations.sonarr.get_series", AsyncMock(return_value=series))
    monkeypatch.setattr("integrations.radarr.get_movie", AsyncMock(return_value=movie))
    for target in ("integrations.sonarr", "integrations.media_sync_engine"):
        monkeypatch.setattr(f"{target}.get_episodes", AsyncMock(return_value=episodes))
        monkeypatch.setattr(f"{target}.get_episode_files", AsyncMock(return_value=files))

    # The show and its first episode were imported earlier; everything else was never seen.
    storage.create_project("Severance", {
        "show_name": "Severance", "target_language": "Greek", "arr_source": True,
        "arr_media_type": "series", "arr_sonarr_id": 7})
    storage.save_episode("Severance", "S01E01", [{"index": 1, "text": "Hello, Mark."}],
                         {"arr_source": True, "line_count": 1,
                          "arr_media_path": str(season / "Severance.S01E01.mkv")})
    storage.save_original_subtitle("Severance", "S01E01", SAMPLE_SRT, "Severance.S01E01.en.srt")
    storage.create_project("Arrival (2016)", {
        "show_name": "Arrival", "target_language": "Greek", "arr_source": True,
        "arr_media_type": "movie", "type": "movie", "arr_radarr_id": 55})

    return {"queue": TranslationQueue(db), "season": season, "movie_dir": movie_dir}


def _options(row):
    return json.loads(row["options"])


@pytest.mark.asyncio
async def test_series_routes_every_missing_episode_by_what_it_has(env):
    from services import on_demand

    report = await on_demand.translate_missing("Severance")

    assert report["status"] == "success" and "error" not in report
    assert report["queued_for_translation"] == 2      # E01 (imported) and E06 (sidecar, never imported)
    assert report["queued_for_extraction"] == 1       # E02 (muxed ASS)
    assert report["already_available"] == 1           # E03 has Greek beside it
    assert report["no_usable_source"] == 1            # E05 has image subtitles only
    assert report["unavailable"] == [{"episode": "S01E05", "reason": "image_subtitles_only"}]
    assert report["enqueued_count"] == 3

    queue = env["queue"]
    for ep in ("S01E01", "S01E06"):
        row = queue.find_item("Severance", ep, "", KIND_TRANSLATION)
        assert row["priority"] == PRIORITY_BACKLOG
    assert storage.original_subtitle_exists("Severance", "S01E06"), "the sidecar was imported by the sync"

    extraction = queue.find_item("Severance", "S01E02", "", KIND_EXTRACTION)
    assert extraction["priority"] == PRIORITY_BACKLOG
    opts = _options(extraction)
    assert opts["stream_index"] == 2
    assert opts["translate_after"]["lang"] == ""
    assert opts["translate_after"]["options"]["ensure_context"] is True
    assert opts["translate_after"]["options"]["target_language_name"] == "Greek"
    assert queue.find_item("Severance", "S01E02", "", KIND_TRANSLATION) is None  # the lane chains it later

    for ep in ("S01E03", "S01E04", "S01E05"):
        assert queue.find_item("Severance", ep, "", KIND_TRANSLATION) is None


@pytest.mark.asyncio
async def test_episodes_that_were_never_imported_are_the_only_ones_synced(env):
    from services import on_demand

    await on_demand.translate_missing("Severance")
    # An entry now exists for the episodes that were created from their media file.
    assert storage.load_episode_metadata("Severance", "S01E02")["arr_media_path"].endswith("Severance.S01E02.mkv")
    assert storage.load_episode_metadata("Severance", "S01E06") is not None


@pytest.mark.asyncio
async def test_running_it_twice_does_not_queue_duplicates(env):
    from services import on_demand

    await on_demand.translate_missing("Severance")
    await on_demand.translate_missing("Severance")
    queue = env["queue"]
    assert len([r for r in queue.get_all(limit=100, include_all_kinds=True)
                if r["kind"] == KIND_EXTRACTION and r["episode_name"] == "S01E02"]) == 1
    assert len([r for r in queue.get_all(limit=100, include_all_kinds=True)
                if r["kind"] == KIND_TRANSLATION and r["episode_name"] == "S01E06"]) == 1


@pytest.mark.asyncio
async def test_with_the_extraction_lane_off_the_translation_is_queued_to_extract_inline(env):
    from services import on_demand

    storage.save_global_config({"embedded_extraction_enabled": False})
    report = await on_demand.translate_missing("Severance")
    assert report["queued_for_extraction"] == 0 and report["queued_for_translation"] == 3
    assert env["queue"].find_item("Severance", "S01E02", "", KIND_TRANSLATION) is not None
    assert env["queue"].find_item("Severance", "S01E02", "", KIND_EXTRACTION) is None


@pytest.mark.asyncio
async def test_a_movie_with_only_a_muxed_track_goes_to_extraction(env):
    from services import on_demand

    report = await on_demand.translate_missing("Arrival (2016)")
    assert report["queued_for_extraction"] == 1 and report["queued_for_translation"] == 0
    row = env["queue"].find_item("Arrival (2016)", "Arrival (2016)", "", KIND_EXTRACTION)
    assert row["priority"] == PRIORITY_BACKLOG
    assert _options(row)["translate_after"]["options"]["ensure_context"] is True
    assert storage.load_episode_metadata("Arrival (2016)", "Arrival (2016)")["arr_media_path"].endswith("Arrival.2016.mkv")


@pytest.mark.asyncio
async def test_a_movie_with_a_sidecar_is_imported_and_translated(env):
    from services import on_demand

    (env["movie_dir"] / "Arrival.2016.en.srt").write_text(SAMPLE_SRT, encoding="utf-8")
    report = await on_demand.translate_missing("Arrival (2016)")
    assert report["queued_for_translation"] == 1 and report["queued_for_extraction"] == 0
    assert storage.original_subtitle_exists("Arrival (2016)", "Arrival (2016)")
    assert env["queue"].find_item("Arrival (2016)", "Arrival (2016)", "", KIND_TRANSLATION)


@pytest.mark.asyncio
async def test_a_movie_without_any_source_is_reported_with_the_reason(env, monkeypatch):
    from services import on_demand

    # No sidecar and a container with no subtitle streams at all.
    monkeypatch.setattr("utils.media_probe_cache.get", lambda path, fp, *a, **k: [])
    report = await on_demand.translate_missing("Arrival (2016)")
    assert report["no_usable_source"] == 1 and report["enqueued_count"] == 0
    assert report["unavailable"] == [{"episode": "Arrival (2016)", "reason": "no_subtitles"}]


@pytest.mark.asyncio
async def test_sonarr_down_still_queues_what_was_already_imported(env, monkeypatch):
    from services import on_demand

    monkeypatch.setattr("integrations.sonarr.get_series", AsyncMock(side_effect=RuntimeError("connection refused")))
    report = await on_demand.translate_missing("Severance")
    assert report["queued_for_translation"] == 1                 # S01E01, imported before
    assert "connection refused" in report["error"]
    assert env["queue"].find_item("Severance", "S01E01", "", KIND_TRANSLATION) is not None


@pytest.mark.asyncio
async def test_a_project_that_is_not_from_an_arr_only_queues_imported_episodes(env):
    from services import on_demand

    storage.create_project("Manual", {"show_name": "Manual", "target_language": "Greek"})
    storage.save_episode("Manual", "E01", [{"index": 1, "text": "Hi"}], {"line_count": 1})
    storage.save_original_subtitle("Manual", "E01", SAMPLE_SRT, "E01.en.srt")
    report = await on_demand.translate_missing("Manual")
    assert report["queued_for_translation"] == 1 and report["queued_for_extraction"] == 0
    assert "error" not in report


@pytest.mark.asyncio
async def test_an_ignored_title_queues_nothing(env):
    from services import on_demand

    meta = storage.load_project_metadata("Severance")
    meta["arr_disabled"] = True
    storage.save_project_metadata("Severance", meta)
    report = await on_demand.translate_missing("Severance")
    assert report["enqueued_count"] == 0
    assert env["queue"].find_item("Severance", "S01E02", "", KIND_EXTRACTION) is None


@pytest.mark.asyncio
async def test_an_unknown_project_is_a_404(env):
    from services import on_demand

    with pytest.raises(on_demand.OnDemandError) as exc:
        await on_demand.translate_missing("Nope")
    assert exc.value.status_code == 404


def test_the_library_endpoint_returns_the_report(env):
    client = TestClient(app)
    res = client.post("/integrations/arr/library/Severance/enqueue-missing")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success" and body["enqueued_count"] == 3
    assert body["queued_for_translation"] == 2 and body["queued_for_extraction"] == 1
    assert client.post("/integrations/arr/library/Nope/enqueue-missing").status_code == 404
