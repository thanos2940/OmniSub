"""On-demand subtitle requests (services/on_demand.py, routers/on_demand.py).

Network-free: Sonarr/Radarr calls are mocked, container probes are served from a
fake probe cache, and the queue lives in the per-test DB conftest provides.
"""
import asyncio
import json
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from utils import paths, storage
from utils.translation_queue import (
    KIND_EXTRACTION,
    KIND_TRANSLATION,
    PRIORITY_MANUAL,
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


def _episode(number, has_file=True, file_id=None):
    return {
        "id": 100 + number, "seriesId": 7, "seasonNumber": 1, "episodeNumber": number,
        "title": f"Episode {number}", "hasFile": has_file,
        "episodeFileId": file_id if has_file else 0,
    }


@pytest.fixture
def od_env(tmp_path, monkeypatch):
    """Isolated config, queue, probe cache and a small Sonarr series on disk."""
    import services.queue_service as queue_service
    from integrations import embedded_subs

    db = paths.DB_FILE
    monkeypatch.setattr(queue_service, "TranslationQueue", lambda *a, **k: TranslationQueue(db))
    monkeypatch.setattr(queue_service, "_wake_worker", lambda: None)

    media_root = tmp_path / "media"
    storage.save_global_config({
        "sonarr_enabled": True, "sonarr_api_key": "k", "sonarr_url": "http://sonarr",
        "radarr_enabled": True, "radarr_api_key": "k", "radarr_url": "http://radarr",
        "default_target_language": "Greek",
        "arr_source_language": "en",
        "embedded_extraction_enabled": True,
        "arr_path_mappings": [{"remote": "/remote", "local": str(media_root)}],
    })

    season = media_root / "Severance" / "Season 01"
    season.mkdir(parents=True)
    probe_cache = {}

    def media(number, *sidecars, tracks=()):
        stem = f"Severance.S01E{number:02d}"
        (season / f"{stem}.mkv").write_bytes(b"\x1aE\xdf\xa3")
        for lang in sidecars:
            (season / f"{stem}.{lang}.srt").write_text(SAMPLE_SRT, encoding="utf-8")
        probe_cache[f"{stem}.mkv"] = [dict(t) for t in tracks]

    media(1, "en")                          # English sidecar -> translate
    media(2, tracks=[ENG_ASS_TRACK])        # muxed ASS only -> extract, then translate
    media(3, "el")                          # Greek already there
    media(5, tracks=[PGS_TRACK])            # image subtitles only
    media(6, "en")                          # never requested: must not be imported

    monkeypatch.setattr("utils.media_probe_cache.get",
                        lambda path, fp, *a, **k: probe_cache.get(Path(path).name))
    monkeypatch.setattr("utils.media_probe_cache.put", lambda *a, **k: None)
    monkeypatch.setattr(embedded_subs, "resolve_tools",
                        lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))

    series = {"id": 7, "title": "Severance", "year": 2022, "tvdbId": 371980,
              "path": "/remote/Severance", "seriesType": "standard"}
    episodes = [_episode(1, file_id=201), _episode(2, file_id=202), _episode(3, file_id=203),
                _episode(4, has_file=False), _episode(5, file_id=205), _episode(6, file_id=206)]
    files = [{"id": 200 + n, "path": f"/remote/Severance/Season 01/Severance.S01E{n:02d}.mkv"}
             for n in (1, 2, 3, 5, 6)]

    monkeypatch.setattr("integrations.sonarr.find_series_by_tvdb", AsyncMock(return_value=series))
    for target in ("integrations.sonarr", "integrations.media_sync_engine"):
        monkeypatch.setattr(f"{target}.get_episodes", AsyncMock(return_value=episodes))
        monkeypatch.setattr(f"{target}.get_episode_files", AsyncMock(return_value=files))

    return {"queue": TranslationQueue(db), "season_dir": season, "tmp": tmp_path}


def _states(response):
    return {e["episode_name"]: (e["state"], e.get("detail")) for e in response["episodes"]}


def _options(row):
    return json.loads(row["options"])


# ---------------------------------------------------------------------------
# Creating a request
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_series_request_routes_each_episode_to_the_right_entry_point(od_env):
    from services import on_demand

    response = await on_demand.create_request(
        on_demand.MediaQuery(media_type="series", tvdb_id=371980, season=1, episodes=[1, 2, 3, 4, 5]),
        requested_by="nikos", client="morpicehub", client_ref="42",
    )

    assert response["project_name"] == "Severance"
    assert response["language"] == "el"
    assert _states(response) == {
        "S01E01": ("queued", None),
        "S01E02": ("extracting", None),
        "S01E03": ("available", "Severance.S01E03.el.srt"),
        "S01E04": ("not_downloaded", None),
        "S01E05": ("unavailable", "image_subtitles_only"),
    }
    assert response["status"] == "in_progress"

    queue = od_env["queue"]
    translation = queue.find_item("Severance", "S01E01", "", KIND_TRANSLATION)
    assert translation["priority"] == PRIORITY_MANUAL
    opts = _options(translation)
    assert opts["ensure_context"] is True
    assert opts["on_demand_request_id"] == response["id"]
    assert opts["target_language_name"] == "Greek"

    extraction = queue.find_item("Severance", "S01E02", "", KIND_EXTRACTION)
    assert extraction["priority"] == PRIORITY_MANUAL
    ext_opts = _options(extraction)
    assert ext_opts["stream_index"] == 2
    assert ext_opts["translate_after"]["options"]["ensure_context"] is True
    assert ext_opts["translate_after"]["lang"] == ""

    # Nothing was queued for the episodes that need nothing / cannot be done.
    for ep in ("S01E03", "S01E04", "S01E05"):
        assert queue.find_item("Severance", ep, "", KIND_TRANSLATION) is None


@pytest.mark.asyncio
async def test_request_only_imports_requested_episodes_and_never_prunes(od_env):
    from services import on_demand

    # An episode the project already had, which this request does not cover.
    storage.create_project("Severance", {"show_name": "Severance", "target_language": "Greek",
                                         "arr_source": True, "arr_media_type": "series"})
    storage.episode_dir("Severance", "S01E09").mkdir(parents=True, exist_ok=True)
    storage.save_episode("Severance", "S01E09", [], {"arr_source": True, "line_count": 0})

    await on_demand.create_request(on_demand.MediaQuery(media_type="series", tvdb_id=371980, season=1, episodes=[1]))

    assert storage.load_episode_metadata("Severance", "S01E09") is not None, "filtered sync must not prune"
    assert storage.load_episode_metadata("Severance", "S01E06") is None, "unrequested episode was imported"
    assert storage.original_subtitle_exists("Severance", "S01E01")


@pytest.mark.asyncio
async def test_whole_season_request_covers_downloaded_episodes_only(od_env):
    from services import on_demand

    response = await on_demand.create_request(on_demand.MediaQuery(media_type="series", tvdb_id=371980, season=1))
    names = [e["episode_name"] for e in response["episodes"]]
    assert names == ["S01E01", "S01E02", "S01E03", "S01E05", "S01E06"]


@pytest.mark.asyncio
async def test_request_over_the_episode_limit_is_refused_before_any_work(od_env):
    from services import on_demand

    storage.save_global_config({"on_demand_max_episodes": 2})
    with pytest.raises(on_demand.OnDemandError) as exc:
        await on_demand.create_request(on_demand.MediaQuery(media_type="series", tvdb_id=371980, season=1))
    assert exc.value.code == "too_many_episodes"
    assert storage.load_project_metadata("Severance") is None


@pytest.mark.asyncio
async def test_extraction_lane_off_queues_translation_for_inline_extraction(od_env):
    from services import on_demand

    storage.save_global_config({"embedded_extraction_enabled": False})
    response = await on_demand.create_request(
        on_demand.MediaQuery(media_type="series", tvdb_id=371980, season=1, episodes=[2])
    )
    assert _states(response)["S01E02"] == ("queued", None)
    assert od_env["queue"].find_item("Severance", "S01E02", "", KIND_TRANSLATION) is not None
    assert od_env["queue"].find_item("Severance", "S01E02", "", KIND_EXTRACTION) is None


@pytest.mark.asyncio
async def test_unknown_series_is_a_404_not_a_crash(od_env, monkeypatch):
    from services import on_demand

    monkeypatch.setattr("integrations.sonarr.find_series_by_tvdb", AsyncMock(return_value=None))
    with pytest.raises(on_demand.OnDemandError) as exc:
        await on_demand.create_request(on_demand.MediaQuery(media_type="series", tvdb_id=1, season=1))
    assert exc.value.status_code == 404
    assert exc.value.code == "not_in_library"


@pytest.mark.asyncio
async def test_movie_request_uses_radarr_and_the_disambiguated_project_name(od_env, monkeypatch):
    from services import on_demand

    movie_dir = od_env["tmp"] / "media" / "Movies" / "Arrival (2016)"
    movie_dir.mkdir(parents=True)
    (movie_dir / "Arrival.2016.mkv").write_bytes(b"\x1aE\xdf\xa3")
    (movie_dir / "Arrival.2016.en.srt").write_text(SAMPLE_SRT, encoding="utf-8")
    movie = {"id": 55, "title": "Arrival", "year": 2016, "tmdbId": 329865, "hasFile": True,
             "movieFile": {"path": "/remote/Movies/Arrival (2016)/Arrival.2016.mkv"}}
    monkeypatch.setattr("integrations.radarr.find_movie_by_tmdb", AsyncMock(return_value=movie))

    response = await on_demand.create_request(on_demand.MediaQuery(media_type="movie", tmdb_id=329865))

    assert response["project_name"] == "Arrival (2016)"
    assert _states(response) == {"Arrival (2016)": ("queued", None)}
    assert od_env["queue"].find_item("Arrival (2016)", "Arrival (2016)", "", KIND_TRANSLATION)


# ---------------------------------------------------------------------------
# Live status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_follows_the_queue_through_to_completion(od_env):
    from services import on_demand

    created = await on_demand.create_request(
        on_demand.MediaQuery(media_type="series", tvdb_id=371980, season=1, episodes=[1, 2, 3, 5])
    )
    queue = od_env["queue"]
    tr1 = queue.find_item("Severance", "S01E01", "", KIND_TRANSLATION)
    ex2 = queue.find_item("Severance", "S01E02", "", KIND_EXTRACTION)

    queue.update_status(tr1["id"], status="running")
    assert _states(on_demand.get_request(created["id"]))["S01E01"][0] == "translating"

    # Extraction finished; the chained translation is on its way.
    queue.update_status(ex2["id"], status="completed")
    assert _states(on_demand.get_request(created["id"]))["S01E02"][0] == "queued"

    for ep in ("S01E01", "S01E02"):
        row = queue.find_item("Severance", ep, "", KIND_TRANSLATION) or \
            {"id": queue.enqueue("Severance", ep, PRIORITY_MANUAL)}
        queue.update_status(row["id"], status="completed")
        meta = storage.load_episode_metadata("Severance", ep)
        meta.update({"arr_has_target": True, "arr_target_path": f"/x/Severance.{ep}.el.srt"})
        storage.save_episode_metadata("Severance", ep, meta)

    final = on_demand.get_request(created["id"])
    assert _states(final) == {
        "S01E01": ("completed", "Severance.S01E01.el.srt"),
        "S01E02": ("completed", "Severance.S01E02.el.srt"),
        "S01E03": ("available", "Severance.S01E03.el.srt"),
        "S01E05": ("unavailable", "image_subtitles_only"),
    }
    assert final["status"] == "partial"


@pytest.mark.asyncio
async def test_failed_translation_surfaces_a_short_reason(od_env):
    from services import on_demand

    created = await on_demand.create_request(
        on_demand.MediaQuery(media_type="series", tvdb_id=371980, season=1, episodes=[1])
    )
    row = od_env["queue"].find_item("Severance", "S01E01", "", KIND_TRANSLATION)
    od_env["queue"].update_status(row["id"], status="failed",
                                  error="Model refused\n\nTraceback (most recent call last): ...")
    status = on_demand.get_request(created["id"])
    assert _states(status)["S01E01"] == ("failed", "Model refused")
    assert status["status"] == "failed"


def test_rollup():
    from services.on_demand import _rollup
    assert _rollup(["queued", "available"]) == "in_progress"
    assert _rollup(["available", "completed"]) == "completed"
    assert _rollup(["completed", "unavailable"]) == "partial"
    assert _rollup(["unavailable", "not_downloaded"]) == "unavailable"
    assert _rollup(["failed", "unavailable"]) == "failed"


# ---------------------------------------------------------------------------
# Lookup (read-only)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lookup_reports_per_episode_state_without_side_effects(od_env):
    from services import on_demand

    result = await on_demand.lookup(
        on_demand.MediaQuery(media_type="series", tvdb_id=371980, season=1, episodes=[1, 2, 3, 4, 5])
    )
    rows = {e["episode_name"]: (e["state"], e.get("source"), e.get("detail")) for e in result["episodes"]}
    assert rows == {
        "S01E01": ("requestable", "sidecar", None),
        "S01E02": ("requestable", "embedded", None),
        "S01E03": ("available", None, "Severance.S01E03.el.srt"),
        "S01E04": ("not_downloaded", None, None),
        "S01E05": ("unavailable", None, "image_subtitles_only"),
    }
    assert result["project"]["exists"] is False
    assert storage.load_project_metadata("Severance") is None
    assert od_env["queue"].find_item("Severance", "S01E01", "", KIND_TRANSLATION) is None


@pytest.mark.asyncio
async def test_lookup_reports_work_already_in_progress(od_env):
    from services import on_demand

    await on_demand.create_request(on_demand.MediaQuery(media_type="series", tvdb_id=371980, season=1, episodes=[1]))
    result = await on_demand.lookup(on_demand.MediaQuery(media_type="series", tvdb_id=371980, season=1, episodes=[1]))
    assert result["episodes"][0]["state"] == "in_progress"
    assert result["episodes"][0]["detail"] == "queued"


@pytest.mark.asyncio
async def test_lookup_whole_series_summarises_by_season(od_env):
    from services import on_demand

    result = await on_demand.lookup(on_demand.MediaQuery(media_type="series", tvdb_id=371980))
    assert result["episodes"] == []
    assert result["seasons"] == [{"season": 1, "episodes": 6, "downloaded": 5, "with_subtitle": 1}]


def test_ambiguous_title_needs_a_year():
    from services.on_demand import MediaQuery, OnDemandError, _match_library

    library = [{"title": "Dune", "year": 1984}, {"title": "Dune", "year": 2021}]
    with pytest.raises(OnDemandError) as exc:
        _match_library(library, MediaQuery(media_type="movie", title="dune"))
    assert exc.value.code == "ambiguous"
    assert _match_library(library, MediaQuery(media_type="movie", title="Dune", year=2021))["year"] == 2021
    assert _match_library(library, MediaQuery(media_type="movie", tmdb_id=5, title="x")) is None


# ---------------------------------------------------------------------------
# Extraction chains into translation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_demand_extraction_chains_straight_into_translation(od_env, monkeypatch):
    from services.worker.extraction_worker import process_extraction_item
    import services.queue_service as queue_service

    season = od_env["season_dir"]
    media_path = season / "Severance.S01E02.mkv"
    (season / "Severance.S01E02.en.srt").write_text(SAMPLE_SRT, encoding="utf-8")  # already extracted
    storage.create_project("Severance", {"show_name": "Severance", "target_language": "Greek"})

    captured = []
    monkeypatch.setattr(queue_service, "enqueue_translation",
                        lambda *a, **k: captured.append((a, k)) or "id")

    item = {
        "id": "x1", "project_name": "Severance", "episode_name": "S01E02", "attempts": 1, "priority": 1,
        "options": json.dumps({
            "media_path": str(media_path), "stream_index": 2, "source_lang_code": "en",
            "output_format": "srt", "track": {"codec": "subrip"},
            "translate_after": {"options": {"ensure_context": True}, "lang": ""},
        }),
    }
    await process_extraction_item(od_env["queue"], item, storage.load_global_config())

    assert storage.original_subtitle_exists("Severance", "S01E02")
    assert captured == [(("Severance", "S01E02", 1, {"ensure_context": True}), {"lang": ""})]


@pytest.mark.asyncio
async def test_an_extraction_without_a_follow_up_translates_nothing(od_env, monkeypatch):
    """The old library-wide auto-translate flag on a queued extraction is ignored: only the Autopilot,
    an on-demand request or an explicit "Translate missing" (all via ``translate_after``) chain a translation."""
    from services.worker.extraction_worker import process_extraction_item
    import services.queue_service as queue_service

    season = od_env["season_dir"]
    media_path = season / "Severance.S01E02.mkv"
    (season / "Severance.S01E02.en.srt").write_text(SAMPLE_SRT, encoding="utf-8")
    storage.create_project("Severance", {"show_name": "Severance", "target_language": "Greek"})

    captured = []
    monkeypatch.setattr(queue_service, "enqueue_translation", lambda *a, **k: captured.append((a, k)) or "id")

    item = {
        "id": "x2", "project_name": "Severance", "episode_name": "S01E02", "attempts": 1, "priority": 2,
        "options": json.dumps({
            "media_path": str(media_path), "stream_index": 2, "source_lang_code": "en",
            "output_format": "srt", "track": {"codec": "subrip"}, "auto_translate": True,
        }),
    }
    await process_extraction_item(od_env["queue"], item, storage.load_global_config())

    assert storage.original_subtitle_exists("Severance", "S01E02")
    assert captured == []


# ---------------------------------------------------------------------------
# Project context bootstrap
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_models(monkeypatch):
    calls = Counter()

    async def research(show, lines, target, model_name=None):
        calls["research"] += 1
        await asyncio.sleep(0.01)
        return {"findings": f"{show} findings ({len(lines)} lines)"}, {}

    async def enhance(findings, show, target_language=None, model_name=None):
        calls["enhance"] += 1
        return "TONE: dry corporate satire.", {}

    async def glossary(lines, show, target, model_name=None, enable_research=False, **_):
        calls["glossary"] += 1
        assert isinstance(lines, list), "glossary input must be a list of lines"
        return {"terms": [{"term": "Lumon", "translation": "Lumon"}]}, {}

    monkeypatch.setattr("adk_agents.operations.research_project_adk", research)
    monkeypatch.setattr("adk_agents.operations.enhance_context_guide_adk", enhance)
    monkeypatch.setattr("adk_agents.operations.generate_glossary_adk", glossary)

    class _Sessions:
        async def update_context(self, *a, **k):
            pass

        async def update_glossary(self, *a, **k):
            pass

    import adk_config
    monkeypatch.setattr(adk_config, "adk_session_manager", _Sessions(), raising=False)
    return calls


def _project_with_episode(name="Severance"):
    storage.create_project(name, {"show_name": name, "target_language": "Greek"})
    storage.episode_dir(name, "S01E01").mkdir(parents=True, exist_ok=True)
    storage.save_episode(name, "S01E01", [
        {"id": 1, "start": "00:00:01,000", "end": "00:00:04,000", "original": "Hello, Mark."},
        {"id": 2, "start": "00:00:05,000", "end": "00:00:08,000", "original": "Welcome to Lumon."},
    ], {"line_count": 2})


@pytest.mark.asyncio
async def test_bootstrap_builds_guide_and_glossary_once_under_concurrency(fake_models):
    from services.project_bootstrap import ensure_project_context

    _project_with_episode()
    results = await asyncio.gather(*[
        ensure_project_context("Severance", "S01E01", "Greek", want_context=True) for _ in range(4)
    ])

    assert fake_models == Counter({"research": 1, "enhance": 1, "glossary": 1})
    assert all(guide == "TONE: dry corporate satire." for _, guide in results)
    meta = storage.load_project_metadata("Severance")
    assert meta["context_guide"] == "TONE: dry corporate satire."
    assert meta["glossary"]["terms"][0]["term"] == "Lumon"


@pytest.mark.asyncio
async def test_bootstrap_only_seeds_glossary_unless_context_is_wanted(fake_models):
    from services.project_bootstrap import ensure_project_context

    _project_with_episode()
    glossary, guide = await ensure_project_context("Severance", "S01E01", "Greek", want_context=False)
    assert fake_models == Counter({"glossary": 1})
    assert guide == ""
    assert glossary["terms"]


@pytest.mark.asyncio
async def test_bootstrap_is_a_no_op_for_a_ready_project(fake_models):
    from services.project_bootstrap import ensure_project_context

    _project_with_episode()
    meta = storage.load_project_metadata("Severance")
    meta.update({"context_guide": "existing", "glossary": {"terms": [{"term": "Kier"}]}})
    storage.save_project_metadata("Severance", meta)

    glossary, guide = await ensure_project_context("Severance", "S01E01", "Greek", want_context=True)
    assert not fake_models
    assert guide == "existing" and glossary["terms"][0]["term"] == "Kier"


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import on_demand as on_demand_router

    app = FastAPI()
    app.include_router(on_demand_router.router)
    return TestClient(app)


def test_router_validates_media_type_and_identity(client):
    assert client.post("/api/on-demand/subtitles", json={"media_type": "cartoon", "title": "x"}).status_code == 422
    assert client.post("/api/on-demand/subtitles", json={"media_type": "movie"}).status_code == 422


def test_router_maps_service_errors_to_status_and_code(client, monkeypatch):
    from services import on_demand

    async def refuse(*a, **k):
        raise on_demand.OnDemandError(404, "not_in_library", "Not here.")

    monkeypatch.setattr(on_demand, "create_request", refuse)
    response = client.post("/api/on-demand/subtitles", json={"media_type": "show", "tvdb_id": 1, "season": 1})
    assert response.status_code == 404
    assert response.json() == {"detail": "Not here.", "code": "not_in_library"}


def test_router_unknown_request_id_is_404(client):
    assert client.get("/api/on-demand/subtitles/nope").status_code == 404


# ---------------------------------------------------------------------------
# What a waiting viewer sees: progress, place in the queue, cancel
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_progress_of_an_episode_the_agent_runner_translates(od_env, monkeypatch):
    """The agent runner keeps no worker job, so the progress comes from the lane that holds the episode."""
    import threading
    from types import SimpleNamespace
    from services import agent_runner, on_demand

    created = await on_demand.create_request(
        on_demand.MediaQuery(media_type="series", tvdb_id=371980, season=1, episodes=[1]))
    row = od_env["queue"].find_item("Severance", "S01E01", "", KIND_TRANSLATION)
    od_env["queue"].update_status(row["id"], status="running")
    lanes = {0: {"project": "Severance", "episode": "S01E01", "units_done": 3, "units_total": 12}}
    monkeypatch.setattr(agent_runner, "_runner", SimpleNamespace(lock=threading.RLock(), lanes=lanes))
    episode = on_demand.get_request(created["id"])["episodes"][0]
    assert (episode["state"], episode["progress"]) == ("translating", 25)


@pytest.mark.asyncio
async def test_a_waiting_episode_says_how_many_are_ahead_of_it(od_env):
    from services import on_demand

    queue = od_env["queue"]
    queue.enqueue("Other Show", "S01E01", PRIORITY_MANUAL)   # queued earlier, same priority: ahead
    queue.enqueue("Other Show", "S01E02", 3)                  # backlog: behind
    created = await on_demand.create_request(
        on_demand.MediaQuery(media_type="series", tvdb_id=371980, season=1, episodes=[1]))
    episode = on_demand.get_request(created["id"])["episodes"][0]
    assert (episode["state"], episode["ahead"]) == ("queued", 1)


@pytest.mark.asyncio
async def test_cancelling_a_request_stops_only_its_own_unfinished_work(od_env):
    from services import on_demand

    first = await on_demand.create_request(
        on_demand.MediaQuery(media_type="series", tvdb_id=371980, season=1, episodes=[1, 2, 3]),
        client="morpicehub", client_ref="a")
    # A second viewer asks for episode 1 too: its queue row now serves that request.
    second = await on_demand.create_request(
        on_demand.MediaQuery(media_type="series", tvdb_id=371980, season=1, episodes=[1]),
        client="morpicehub", client_ref="b")

    cancelled = on_demand.cancel_request(first["id"])
    states = _states(cancelled)
    assert states["S01E02"] == ("cancelled", None)                     # the extraction it queued
    assert states["S01E01"][0] == "queued"                               # taken over by the second request
    assert states["S01E03"] == ("available", "Severance.S01E03.el.srt")  # already done, untouched
    assert cancelled["status"] == "in_progress"   # episode 1 still comes, for the second viewer
    assert _states(on_demand.get_request(second["id"]))["S01E01"][0] == "queued"

    only = on_demand.cancel_request(second["id"])
    assert only["status"] == "cancelled" and _states(only)["S01E01"] == ("cancelled", None)
    assert on_demand.cancel_request("nope") is None


def test_router_cancel_of_an_unknown_request_is_404(client):
    assert client.post("/api/on-demand/subtitles/nope/cancel").status_code == 404


def test_rollup_of_a_cancelled_request():
    from services.on_demand import _rollup
    assert _rollup(["cancelled", "cancelled"]) == "cancelled"
    assert _rollup(["cancelled", "not_downloaded"]) == "cancelled"
    assert _rollup(["cancelled", "failed"]) == "failed"


@pytest.mark.asyncio
async def test_an_extraction_cancelled_while_it_ran_does_not_chain_a_translation(od_env, monkeypatch):
    from services.worker.extraction_worker import process_extraction_item
    import services.queue_service as queue_service

    season = od_env["season_dir"]
    media_path = season / "Severance.S01E02.mkv"
    (season / "Severance.S01E02.en.srt").write_text(SAMPLE_SRT, encoding="utf-8")
    storage.create_project("Severance", {"show_name": "Severance", "target_language": "Greek"})
    captured = []
    monkeypatch.setattr(queue_service, "enqueue_translation", lambda *a, **k: captured.append((a, k)) or "id")

    queue = od_env["queue"]
    item_id = queue.enqueue("Severance", "S01E02", PRIORITY_MANUAL, kind=KIND_EXTRACTION)
    queue.cancel(item_id)   # cancelled while it was being extracted
    item = {
        "id": item_id, "project_name": "Severance", "episode_name": "S01E02", "attempts": 1, "priority": 1,
        "options": json.dumps({
            "media_path": str(media_path), "stream_index": 2, "source_lang_code": "en",
            "output_format": "srt", "track": {"codec": "subrip"},
            "translate_after": {"options": {"ensure_context": True}, "lang": ""},
        }),
    }
    await process_extraction_item(queue, item, storage.load_global_config())
    assert captured == []
    assert queue.get_item(item_id)["status"] == "cancelled"
    assert storage.original_subtitle_exists("Severance", "S01E02")   # the extracted source is kept
