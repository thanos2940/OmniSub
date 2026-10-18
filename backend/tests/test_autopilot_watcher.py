"""Autopilot watcher (services/autopilot/watcher.py): history polling, webhooks, reconcile.

Sonarr/Radarr are faked by replacing the client functions; nothing touches the network.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from integrations import radarr as radarr_api
from integrations import sonarr as sonarr_api
from services.autopilot.store import AutopilotStore, iso_utc, parse_iso
from services.autopilot.watcher import ArrMediaInfo, ArrWatcher
from utils import storage
from utils.translation_queue import KIND_EXTRACTION, PRIORITY_WEBHOOK, TranslationQueue

T0 = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)
SONARR_ROOT = "/remote/tv"


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


class FakeArr:
    """In-memory Sonarr + Radarr behind the client functions the watcher uses."""

    def __init__(self, monkeypatch):
        self.history = {"sonarr": [], "radarr": []}
        self.episodes = {}      # id -> episode dict
        self.episode_files = {}  # id -> file dict
        self.series = {}
        self.movies = {}
        self.movie_files = {}
        self.tags = []
        self.down = {"sonarr": False, "radarr": False}
        self.fail_episode = set()
        self.history_calls = []

        async def s_history(cfg, since, event_type):
            self.history_calls.append(("sonarr", since, event_type))
            return self._history("sonarr", since, event_type)

        async def r_history(cfg, since, event_type):
            self.history_calls.append(("radarr", since, event_type))
            return self._history("radarr", since, event_type)

        async def get_episode(cfg, episode_id):
            if self.down["sonarr"] or episode_id in self.fail_episode:
                return None
            return self.episodes.get(episode_id)

        async def get_episode_file(cfg, file_id):
            return self.episode_files.get(file_id)

        async def get_series(cfg, series_id):
            return self.series.get(series_id)

        async def get_all_series(cfg):
            if self.down["sonarr"]:
                raise ConnectionError("sonarr down")
            return list(self.series.values())

        async def get_episodes(cfg, series_id):
            return [e for e in self.episodes.values() if e["seriesId"] == series_id]

        async def get_episode_files(cfg, series_id):
            return [f for f in self.episode_files.values() if f.get("seriesId") == series_id]

        async def get_movie(cfg, movie_id):
            return self.movies.get(movie_id)

        async def get_movie_file(cfg, file_id):
            return self.movie_files.get(file_id)

        async def get_all_movies(cfg):
            if self.down["radarr"]:
                raise ConnectionError("radarr down")
            return list(self.movies.values())

        async def get_tags(cfg):
            return self.tags

        for name, fn in dict(get_history_since=s_history, get_episode=get_episode,
                             get_episode_file=get_episode_file, get_series=get_series,
                             get_all_series=get_all_series, get_episodes=get_episodes,
                             get_episode_files=get_episode_files, get_tags=get_tags).items():
            monkeypatch.setattr(sonarr_api, name, fn)
        for name, fn in dict(get_history_since=r_history, get_movie=get_movie, get_movie_file=get_movie_file,
                             get_all_movies=get_all_movies, get_tags=get_tags).items():
            monkeypatch.setattr(radarr_api, name, fn)

    def _history(self, source, since, event_type):
        if self.down[source]:
            return None
        cut = parse_iso(since)
        return [dict(r) for r in self.history[source]
                if r["eventType"] == event_type and parse_iso(r["date"]) >= cut]

    # -- builders --
    def add_series(self, sid=7, title="Severance", **extra):
        self.series[sid] = {"id": sid, "title": title, "monitored": True, "seriesType": "standard",
                            "statistics": {"episodeFileCount": 1}, **extra}

    def add_episode(self, ep_id, number, file_id, path, date_added, series_id=7, season=1, monitored=True):
        self.episodes[ep_id] = {"id": ep_id, "seriesId": series_id, "seasonNumber": season,
                                "episodeNumber": number, "title": f"Ep {number}", "hasFile": bool(file_id),
                                "episodeFileId": file_id, "monitored": monitored}
        if file_id:
            self.episode_files[file_id] = {"id": file_id, "seriesId": series_id, "path": path,
                                           "dateAdded": date_added}

    def sonarr_event(self, rec_id, date, event, episode_id, series_id=7, **extra):
        self.history["sonarr"].append({
            "id": rec_id, "date": date, "eventType": event, "episodeId": episode_id, "seriesId": series_id,
            "data": extra.pop("data", {}), "episode": dict(self.episodes.get(episode_id, {})) or None,
            "series": dict(self.series.get(series_id, {})) or None, **extra})

    def add_movie(self, mid, title, year, file_id, path, date_added, **extra):
        mf = {"id": file_id, "path": path, "dateAdded": date_added} if file_id else None
        self.movies[mid] = {"id": mid, "title": title, "year": year, "hasFile": bool(file_id), "monitored": True,
                            "movieFile": mf, **extra}

    def radarr_event(self, rec_id, date, event, movie_id):
        movie = dict(self.movies[movie_id])
        movie.pop("movieFile", None)  # history records carry the movie without its file
        self.history["radarr"].append({"id": rec_id, "date": date, "eventType": event, "movieId": movie_id,
                                       "data": {}, "movie": movie})


@pytest.fixture
def env(monkeypatch, tmp_path):
    clock = Clock()
    media = tmp_path / "tv"
    media.mkdir()
    storage.save_global_config({
        "sonarr_enabled": True, "sonarr_api_key": "k", "sonarr_url": "http://sonarr",
        "radarr_enabled": True, "radarr_api_key": "k", "radarr_url": "http://radarr",
        "arr_path_mappings": [{"remote": SONARR_ROOT, "local": str(media)}],
        "autopilot_enabled": True, "autopilot_grace_minutes": 5,
        "autopilot_enabled_at": iso_utc(T0 - timedelta(days=1)),
    })
    arr = FakeArr(monkeypatch)
    store = AutopilotStore(tmp_path / "ap.db", clock=clock)
    watcher = ArrWatcher(store, clock=clock)
    return type("Env", (), {"arr": arr, "store": store, "watcher": watcher, "clock": clock, "media": media})


def local(env, name):
    return str(env.media / name)


def make_file(env, name, content=b"x"):
    path = env.media / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


async def start(env):
    """First poll: the cursor is planted at 'now'."""
    summary = await env.watcher.poll_once()
    assert summary["sonarr"]["initialized"]
    return summary


# --- polling ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_run_starts_the_cursor_at_now_and_ignores_the_past(env):
    env.arr.add_series()
    env.arr.add_episode(101, 1, 201, f"{SONARR_ROOT}/Severance.S01E01.mkv", "2026-10-01T00:00:00Z")
    env.arr.sonarr_event(1, "2026-10-01T00:00:00Z", "downloadFolderImported", 101)
    summary = await env.watcher.poll_once()
    assert summary["sonarr"]["initialized"] and summary["radarr"]["initialized"]
    assert env.store.get_cursor("sonarr") == {"date": iso_utc(T0), "ids": []}
    assert env.store.counts()["waiting"] == 0
    assert env.arr.history_calls == []  # the first run only plants the cursor


@pytest.mark.asyncio
async def test_imported_episode_becomes_an_item_due_after_the_grace(env):
    await start(env)
    env.clock.advance(minutes=1)
    env.arr.add_series()
    env.arr.add_episode(101, 2, 201, f"{SONARR_ROOT}/Season 01/Severance.S01E02.mkv", "2026-10-14T12:00:50Z")
    env.arr.sonarr_event(1, "2026-10-14T12:00:50.1234567Z", "downloadFolderImported", 101,
                         data={"importedPath": f"{SONARR_ROOT}/Season 01/Severance.S01E02.mkv"})
    summary = await env.watcher.poll_once()
    assert summary["sonarr"]["created"] == 1
    (item,), total = env.store.list()
    assert (item["source"], item["kind"], item["state"]) == ("sonarr", "episode", "waiting")
    assert (item["project_name"], item["episode_name"], item["title"]) == ("Severance", "S01E02", "Severance")
    assert (item["arr_item_id"], item["arr_parent_id"], item["arr_file_id"]) == (101, 7, 201)
    assert (item["season"], item["episode"]) == (1, 2)
    assert item["media_path"] == str(env.media / "Season 01" / "Severance.S01E02.mkv")  # path mapping applied
    assert item["detected_at"] == "2026-10-14T12:00:50Z"
    assert item["due_at"] == "2026-10-14T12:05:50Z"
    assert env.store.get_cursor("sonarr")["ids"] == [1]


@pytest.mark.asyncio
async def test_history_queries_ask_for_every_event_type_from_the_cursor_date(env):
    await start(env)
    await env.watcher.poll_once()
    sonarr = [c for c in env.arr.history_calls if c[0] == "sonarr"]
    assert [c[2] for c in sonarr] == ["downloadFolderImported", "episodeFileRenamed", "episodeFileDeleted"]
    assert {c[1] for c in sonarr} == {iso_utc(T0)}
    radarr = [c for c in env.arr.history_calls if c[0] == "radarr"]
    assert [c[2] for c in radarr] == ["downloadFolderImported", "movieFileRenamed", "movieFileDeleted"]


@pytest.mark.asyncio
async def test_paging_by_date_skips_ids_already_seen_at_the_cursor_date(env):
    await start(env)
    env.arr.add_series()
    for n, (ep, fid) in enumerate([(101, 201), (102, 202), (103, 203)], start=1):
        env.arr.add_episode(ep, n, fid, f"{SONARR_ROOT}/E{n}.mkv", "2026-10-14T12:01:00Z")
    env.arr.sonarr_event(1, "2026-10-14T12:01:00Z", "downloadFolderImported", 101)
    env.arr.sonarr_event(2, "2026-10-14T12:01:00Z", "downloadFolderImported", 102)
    await env.watcher.poll_once()
    assert env.store.counts()["waiting"] == 2
    assert env.store.get_cursor("sonarr") == {"date": "2026-10-14T12:01:00.000Z", "ids": [1, 2]}

    # The next poll asks from the same date again and gets records 1 and 2 back, plus a new one.
    env.arr.sonarr_event(3, "2026-10-14T12:01:00Z", "downloadFolderImported", 103)
    summary = await env.watcher.poll_once()
    assert summary["sonarr"]["records"] == 1 and summary["sonarr"]["created"] == 1
    assert env.store.counts()["waiting"] == 3
    assert env.store.get_cursor("sonarr")["ids"] == [1, 2, 3]

    # A later record moves the date on and resets the id set.
    env.arr.add_episode(104, 4, 204, f"{SONARR_ROOT}/E4.mkv", "2026-10-14T12:03:00Z")
    env.arr.sonarr_event(4, "2026-10-14T12:03:00Z", "downloadFolderImported", 104)
    await env.watcher.poll_once()
    assert env.store.get_cursor("sonarr") == {"date": "2026-10-14T12:03:00.000Z", "ids": [4]}
    assert env.store.counts()["waiting"] == 4


@pytest.mark.asyncio
async def test_replaying_the_same_records_creates_nothing_new(env):
    await start(env)
    env.arr.add_series()
    env.arr.add_episode(101, 1, 201, f"{SONARR_ROOT}/E1.mkv", "2026-10-14T12:01:00Z")
    env.arr.sonarr_event(1, "2026-10-14T12:01:00Z", "downloadFolderImported", 101)
    await env.watcher.poll_once()
    # Cursor lost (e.g. restored from an old backup): the same file is deduplicated by its id.
    env.store.set_cursor("sonarr", {"date": iso_utc(T0), "ids": []})
    summary = await env.watcher.poll_once()
    assert summary["sonarr"]["created"] == 0 and env.store.counts()["waiting"] == 1


@pytest.mark.asyncio
async def test_upgrade_import_supersedes_the_old_item(env):
    await start(env)
    env.arr.add_series()
    env.arr.add_episode(101, 1, 201, f"{SONARR_ROOT}/E1.v1.mkv", "2026-10-14T12:01:00Z")
    env.arr.sonarr_event(1, "2026-10-14T12:01:00Z", "downloadFolderImported", 101)
    await env.watcher.poll_once()
    env.arr.add_episode(101, 1, 305, f"{SONARR_ROOT}/E1.v2.mkv", "2026-10-14T12:30:00Z")
    env.arr.sonarr_event(2, "2026-10-14T12:30:00Z", "downloadFolderImported", 101)
    await env.watcher.poll_once()
    states = {i["arr_file_id"]: i["state"] for i in env.store.list()[0]}
    assert states == {201: "superseded", 305: "waiting"}


@pytest.mark.asyncio
async def test_radarr_import_resolves_the_movie_file_and_adopts_an_existing_project(env):
    storage.create_project("The Old Name", {"arr_radarr_id": 50, "show_name": "Dune"})
    await start(env)
    env.arr.add_movie(50, "Dune", 2021, 900, f"{SONARR_ROOT}/Dune.mkv", "2026-10-14T12:01:00Z")
    env.arr.movie_files[900] = env.arr.movies[50]["movieFile"]
    env.arr.movies[50]["movieFile"] = None  # only reachable through the moviefile endpoint
    env.arr.movies[50]["movieFileId"] = 900
    env.arr.radarr_event(1, "2026-10-14T12:01:00Z", "downloadFolderImported", 50)
    summary = await env.watcher.poll_once()
    assert summary["radarr"]["created"] == 1
    (item,), _ = env.store.list()
    assert (item["source"], item["kind"], item["arr_item_id"], item["arr_file_id"]) == ("radarr", "movie", 50, 900)
    assert item["project_name"] == "The Old Name" and item["episode_name"] == "The Old Name"
    assert item["season"] is None and item["media_path"] == str(env.media / "Dune.mkv")


@pytest.mark.asyncio
async def test_radarr_new_movie_gets_the_year_suffixed_project_name(env):
    await start(env)
    env.arr.add_movie(51, "Arrival", 2016, 901, f"{SONARR_ROOT}/Arrival.mkv", "2026-10-14T12:01:00Z")
    env.arr.radarr_event(1, "2026-10-14T12:01:00Z", "downloadFolderImported", 51)
    await env.watcher.poll_once()
    (item,), _ = env.store.list()
    assert item["project_name"] == "Arrival (2016)"


@pytest.mark.asyncio
async def test_renamed_video_updates_the_item_and_moves_greek_sidecars(env):
    old = make_file(env, "Severance.S01E01.old.mkv")
    greek = make_file(env, "Severance.S01E01.old.el.srt", b"greek")
    english = make_file(env, "Severance.S01E01.old.en.srt", b"english")
    await start(env)
    env.arr.add_series()
    env.arr.add_episode(101, 1, 201, f"{SONARR_ROOT}/Severance.S01E01.old.mkv", "2026-10-14T12:01:00Z")
    env.arr.sonarr_event(1, "2026-10-14T12:01:00Z", "downloadFolderImported", 101)
    await env.watcher.poll_once()
    (item,), _ = env.store.list()
    env.store.transition(item["id"], "done", outputs=[str(greek)])

    env.arr.episode_files[201]["path"] = f"{SONARR_ROOT}/Severance.S01E01.new.mkv"
    env.arr.sonarr_event(2, "2026-10-14T12:10:00Z", "episodeFileRenamed", 101)
    summary = await env.watcher.poll_once()
    assert summary["sonarr"]["renamed"] == 1
    item = env.store.get(item["id"])
    assert item["media_path"] == str(env.media / "Severance.S01E01.new.mkv") and item["state"] == "done"
    assert (env.media / "Severance.S01E01.new.el.srt").read_bytes() == b"greek"
    assert not greek.exists() and english.exists()  # only Greek files follow the rename
    assert item["outputs"] == [str(env.media / "Severance.S01E01.new.el.srt")]
    assert old.exists()


@pytest.mark.asyncio
async def test_deleted_file_marks_its_item_gone_and_cancels_its_queue_rows(env):
    await start(env)
    env.arr.add_series()
    env.arr.add_episode(101, 1, 201, f"{SONARR_ROOT}/E1.mkv", "2026-10-14T12:01:00Z")
    env.arr.sonarr_event(1, "2026-10-14T12:01:00Z", "downloadFolderImported", 101)
    await env.watcher.poll_once()
    (item,), _ = env.store.list()
    env.store.transition(item["id"], "extracting")
    queue = TranslationQueue()
    row_id = queue.enqueue("Severance", "S01E01", PRIORITY_WEBHOOK, {"x": 1}, "", KIND_EXTRACTION)

    env.arr.episodes[101]["episodeFileId"] = 0  # the file is gone from Sonarr
    env.arr.sonarr_event(2, "2026-10-14T12:20:00Z", "episodeFileDeleted", 101)
    summary = await env.watcher.poll_once()
    assert summary["sonarr"]["gone"] == 1
    item = env.store.get(item["id"])
    assert item["state"] == "gone" and item["reason"] == "file_deleted"
    assert queue.get_item(row_id)["status"] == "cancelled"
    queue.delete_item(row_id)


@pytest.mark.asyncio
async def test_deleting_the_old_file_of_an_upgrade_keeps_the_new_item(env):
    await start(env)
    env.arr.add_series()
    env.arr.add_episode(101, 1, 201, f"{SONARR_ROOT}/E1.v1.mkv", "2026-10-14T12:01:00Z")
    env.arr.sonarr_event(1, "2026-10-14T12:01:00Z", "downloadFolderImported", 101)
    await env.watcher.poll_once()
    env.arr.add_episode(101, 1, 305, f"{SONARR_ROOT}/E1.v2.mkv", "2026-10-14T12:30:00Z")
    env.arr.sonarr_event(2, "2026-10-14T12:30:00Z", "downloadFolderImported", 101)
    env.arr.sonarr_event(3, "2026-10-14T12:30:01Z", "episodeFileDeleted", 101)
    await env.watcher.poll_once()
    states = {i["arr_file_id"]: i["state"] for i in env.store.list()[0]}
    assert states == {201: "superseded", 305: "waiting"}


@pytest.mark.asyncio
async def test_unresolvable_record_holds_the_cursor_then_is_skipped(env):
    await start(env)
    env.arr.add_series()
    env.arr.add_episode(101, 1, 201, f"{SONARR_ROOT}/E1.mkv", "2026-10-14T12:01:00Z")
    env.arr.add_episode(102, 2, 202, f"{SONARR_ROOT}/E2.mkv", "2026-10-14T12:02:00Z")
    env.arr.episodes[101] = {"id": 101, "seriesId": 7}  # incomplete record data -> must be fetched
    env.arr.sonarr_event(1, "2026-10-14T12:01:00Z", "downloadFolderImported", 101)
    env.arr.sonarr_event(2, "2026-10-14T12:02:00Z", "downloadFolderImported", 102)
    env.arr.fail_episode.add(101)
    for _ in range(4):
        summary = await env.watcher.poll_once()
        assert "deferred" in summary["sonarr"]
        assert env.store.counts()["waiting"] == 0  # nothing behind the stuck record is processed either
        assert env.store.get_cursor("sonarr")["date"] == iso_utc(T0)[:-1] + ".000Z"
        assert env.watcher.health()["sonarr"]["ok"] is True
    summary = await env.watcher.poll_once()  # fifth try: give up on the record, move on
    assert "deferred" not in summary["sonarr"]
    assert [i["episode_name"] for i in env.store.list()[0]] == ["S01E02"]


@pytest.mark.asyncio
async def test_resolution_failure_that_clears_is_retried(env):
    await start(env)
    env.arr.add_series()
    env.arr.add_episode(101, 1, 201, f"{SONARR_ROOT}/E1.mkv", "2026-10-14T12:01:00Z")
    stripped = dict(env.arr.episodes[101])
    env.arr.sonarr_event(1, "2026-10-14T12:01:00Z", "downloadFolderImported", 101)
    env.arr.history["sonarr"][0]["episode"] = None
    env.arr.fail_episode.add(101)
    assert "deferred" in (await env.watcher.poll_once())["sonarr"]
    env.arr.fail_episode.clear()
    assert (await env.watcher.poll_once())["sonarr"]["created"] == 1
    assert env.arr.episodes[101] == stripped


# --- outages -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_outage_backs_off_reports_and_recovers(env):
    await start(env)
    env.arr.down["sonarr"] = True
    summary = await env.watcher.poll_once()
    assert summary["sonarr"]["ok"] is False and summary["radarr"]["ok"] is True  # per-source isolation
    health = env.watcher.health()
    assert health["sonarr"]["ok"] is False and "history request failed" in health["sonarr"]["error"]
    assert health["sonarr"]["since"] == iso_utc(T0) and health["radarr"]["ok"] is True
    assert health["sonarr"]["cursor"]["date"] == iso_utc(T0)

    calls = len(env.arr.history_calls)
    env.clock.advance(seconds=30)
    assert (await env.watcher.poll_once())["sonarr"]["skipped"] == "backoff"
    assert len([c for c in env.arr.history_calls[calls:] if c[0] == "sonarr"]) == 0

    env.clock.advance(seconds=31)  # 61 s after the first failure: one try, fails again, backs off for 2 min
    assert (await env.watcher.poll_once())["sonarr"]["ok"] is False
    env.clock.advance(seconds=100)
    assert (await env.watcher.poll_once())["sonarr"]["skipped"] == "backoff"
    env.clock.advance(seconds=21)
    env.arr.down["sonarr"] = False
    assert (await env.watcher.poll_once())["sonarr"]["ok"] is True
    health = env.watcher.health()["sonarr"]
    assert health["ok"] is True and health["error"] is None and health["since"] == iso_utc(env.clock())


@pytest.mark.asyncio
async def test_backoff_delays_grow_and_cap(env):
    await start(env)
    env.arr.down["sonarr"] = True
    waits = []
    for _ in range(6):
        await env.watcher.poll_once()
        waits.append((env.watcher._state["sonarr"].next_try - env.clock()).total_seconds())
        env.clock.advance(seconds=waits[-1])
    assert waits == [60, 120, 300, 600, 600, 600]


@pytest.mark.asyncio
async def test_unconfigured_source_is_skipped_and_reported(env):
    storage.save_global_config({"radarr_enabled": False})
    summary = await env.watcher.poll_once()
    assert summary["radarr"] == {"skipped": "not_configured"}
    health = env.watcher.health()
    assert health["radarr"]["configured"] is False and health["radarr"]["ok"] is None
    assert health["sonarr"]["configured"] is True


# --- webhooks ----------------------------------------------------------------

def sonarr_payload(**kw):
    return {"eventType": "Download", "series": {"id": 7, "title": "Severance"},
            "episodes": [{"id": 101, "seriesId": 7, "seasonNumber": 1, "episodeNumber": 3, "title": "T"}],
            "episodeFile": {"id": 201, "path": f"{SONARR_ROOT}/Severance.S01E03.mkv"}, **kw}


def test_webhook_creates_an_item_and_is_deduplicated(env):
    first = env.watcher.ingest_webhook("sonarr", sonarr_payload())
    assert first["state"] == "waiting" and first["episode_name"] == "S01E03"
    assert first["media_path"] == str(env.media / "Severance.S01E03.mkv")
    assert first["due_at"] == iso_utc(T0 + timedelta(minutes=5))
    again = env.watcher.ingest_webhook("sonarr", sonarr_payload())
    assert again["id"] == first["id"] and env.store.counts()["waiting"] == 1


@pytest.mark.asyncio
async def test_webhook_and_poll_deduplicate_each_other(env):
    await start(env)
    env.arr.add_series()
    env.arr.add_episode(101, 3, 201, f"{SONARR_ROOT}/Severance.S01E03.mkv", "2026-10-14T12:01:00Z")
    env.arr.sonarr_event(1, "2026-10-14T12:01:00Z", "downloadFolderImported", 101)
    env.watcher.ingest_webhook("sonarr", sonarr_payload())
    await env.watcher.poll_once()
    assert env.store.counts()["waiting"] == 1


def test_webhook_radarr_and_ignored_events(env):
    item = env.watcher.ingest_webhook("radarr", {
        "eventType": "Download", "movie": {"id": 60, "title": "Heat", "year": 1995},
        "movieFile": {"id": 700, "path": f"{SONARR_ROOT}/Heat.mkv"}})
    assert item["project_name"] == "Heat (1995)" and item["arr_file_id"] == 700 and item["kind"] == "movie"
    assert env.watcher.ingest_webhook("radarr", {"eventType": "Test"}) is None
    assert env.watcher.ingest_webhook("sonarr", {"eventType": "Download"}) is None
    assert env.watcher.ingest_webhook("plex", {}) is None
    assert env.watcher.ingest_webhook("sonarr", "nope") is None


def test_webhook_delete_and_rename(env):
    item = env.watcher.ingest_webhook("sonarr", sonarr_payload())
    renamed = env.watcher.ingest_webhook("sonarr", sonarr_payload(
        eventType="EpisodeFileRenamed", episodeFile={"id": 201, "path": f"{SONARR_ROOT}/Renamed.mkv"}))
    assert renamed["media_path"] == str(env.media / "Renamed.mkv")
    gone = env.watcher.ingest_webhook("sonarr", sonarr_payload(eventType="EpisodeFileDelete"))
    assert gone["id"] == item["id"] and gone["state"] == "gone"


# --- reconcile ----------------------------------------------------------------

def library(env, added_after, added_before):
    """Three episodes: one new (after enabling), two old; one of the old has Greek."""
    env.arr.add_series()
    specs = [(101, 1, 201, added_after), (102, 2, 202, added_before), (103, 3, 203, added_before)]
    for ep, n, fid, added in specs:
        make_file(env, f"S01E0{n}.mkv")
        env.arr.add_episode(ep, n, fid, f"{SONARR_ROOT}/S01E0{n}.mkv", added)


@pytest.mark.asyncio
async def test_reconcile_queues_new_files_without_greek_and_ignores_old_ones(env):
    library(env, added_after="2026-10-14T10:00:00Z", added_before="2026-01-01T00:00:00Z")
    make_file(env, "S01E02.el.srt")
    summary = await env.watcher.reconcile_once()
    assert summary["created"] == 1 and summary["backlog"] == 0
    (item,), _ = env.store.list()
    assert item["episode_name"] == "S01E01" and item["state"] == "waiting"
    assert item["due_at"] == item["detected_at"] == "2026-10-14T10:00:00Z"  # due now
    assert env.store.due_items(env.clock())[0]["id"] == item["id"]
    # idempotent
    assert (await env.watcher.reconcile_once())["created"] == 0


@pytest.mark.asyncio
async def test_reconcile_skips_new_files_that_already_have_greek(env):
    library(env, added_after="2026-10-14T10:00:00Z", added_before="2026-01-01T00:00:00Z")
    make_file(env, "S01E01.gre.forced.srt")
    assert (await env.watcher.reconcile_once())["created"] == 0


@pytest.mark.asyncio
async def test_reconcile_does_not_duplicate_what_polling_found(env):
    library(env, added_after="2026-10-14T10:00:00Z", added_before="2026-01-01T00:00:00Z")
    env.watcher.ingest_webhook("sonarr", {
        "eventType": "Download", "series": {"id": 7, "title": "Severance"},
        "episodes": [env.arr.episodes[101]], "episodeFile": {"id": 201, "path": f"{SONARR_ROOT}/S01E01.mkv"}})
    assert (await env.watcher.reconcile_once())["created"] == 0
    assert env.store.counts()["waiting"] == 1


@pytest.mark.asyncio
async def test_reconcile_respects_monitored_only(env):
    library(env, added_after="2026-10-14T10:00:00Z", added_before="2026-10-14T10:00:00Z")
    env.arr.episodes[102]["monitored"] = False
    env.arr.series[7]["monitored"] = True
    assert (await env.watcher.reconcile_once())["created"] == 2
    storage.save_global_config({"autopilot_monitored_only": False})
    assert (await env.watcher.reconcile_once())["created"] == 1


@pytest.mark.asyncio
async def test_reconcile_missing_enabled_at_never_sweeps_the_whole_library(env):
    storage.save_global_config({"autopilot_enabled_at": None})
    library(env, added_after="2026-10-14T10:00:00Z", added_before="2026-01-01T00:00:00Z")
    assert (await env.watcher.reconcile_once())["created"] == 0  # cut-off defaults to "now"
    env.clock.advance(hours=1)
    assert (await env.watcher.reconcile_once())["created"] == 0


@pytest.mark.asyncio
async def test_reconcile_requeues_no_source_items_whose_file_gained_a_source(env):
    library(env, added_after="2026-10-14T10:00:00Z", added_before="2026-01-01T00:00:00Z")
    await env.watcher.reconcile_once()
    (item,), _ = env.store.list()
    env.store.transition(item["id"], "no_source", reason="no_subtitles", attempts=2)
    assert (await env.watcher.reconcile_once())["requeued"] == 0
    make_file(env, "S01E01.en.srt")
    assert (await env.watcher.reconcile_once())["requeued"] == 1
    item = env.store.get(item["id"])
    assert item["state"] == "waiting" and item["attempts"] == 0


@pytest.mark.asyncio
async def test_backlog_newest_is_limited_per_day(env):
    storage.save_global_config({"autopilot_backlog_mode": "newest", "autopilot_backlog_daily_limit": 2})
    env.arr.add_series()
    for n, added in enumerate(["2026-01-01T00:00:00Z", "2026-03-01T00:00:00Z", "2026-02-01T00:00:00Z",
                               "2026-04-01T00:00:00Z"], start=1):
        make_file(env, f"S01E0{n}.mkv")
        env.arr.add_episode(100 + n, n, 200 + n, f"{SONARR_ROOT}/S01E0{n}.mkv", added)
    summary = await env.watcher.reconcile_once()
    assert summary["backlog"] == 2 and summary["created"] == 0
    assert sorted(i["episode_name"] for i in env.store.list()[0]) == ["S01E02", "S01E04"]  # the two newest
    assert (await env.watcher.reconcile_once())["backlog"] == 0  # the day's quota is spent
    env.clock.advance(days=1)
    assert (await env.watcher.reconcile_once())["backlog"] == 2
    assert env.store.counts()["waiting"] == 4


@pytest.mark.asyncio
async def test_backlog_oldest_first_and_greek_files_do_not_use_the_quota(env):
    storage.save_global_config({"autopilot_backlog_mode": "oldest", "autopilot_backlog_daily_limit": 1})
    env.arr.add_series()
    for n, added in enumerate(["2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z", "2026-03-01T00:00:00Z"], start=1):
        make_file(env, f"S01E0{n}.mkv")
        env.arr.add_episode(100 + n, n, 200 + n, f"{SONARR_ROOT}/S01E0{n}.mkv", added)
    make_file(env, "S01E01.el.srt")
    assert (await env.watcher.reconcile_once())["backlog"] == 1
    assert [i["episode_name"] for i in env.store.list()[0]] == ["S01E02"]


@pytest.mark.asyncio
async def test_reconcile_walks_radarr_movies(env):
    make_file(env, "Heat.mkv")
    make_file(env, "Old.mkv")
    env.arr.add_movie(60, "Heat", 1995, 700, f"{SONARR_ROOT}/Heat.mkv", "2026-10-14T09:00:00Z")
    env.arr.add_movie(61, "Old", 1950, 701, f"{SONARR_ROOT}/Old.mkv", "2020-01-01T00:00:00Z")
    env.arr.add_movie(62, "NoFile", 2000, None, "", None)
    summary = await env.watcher.reconcile_once()
    assert summary["created"] == 1
    (item,), _ = env.store.list()
    assert item["project_name"] == "Heat (1995)" and item["source"] == "radarr"


@pytest.mark.asyncio
async def test_reconcile_outage_is_reported_without_stopping_the_other_source(env):
    make_file(env, "Heat.mkv")
    env.arr.add_movie(60, "Heat", 1995, 700, f"{SONARR_ROOT}/Heat.mkv", "2026-10-14T09:00:00Z")
    env.arr.down["sonarr"] = True
    summary = await env.watcher.reconcile_once()
    assert summary["sonarr"]["ok"] is False and summary["radarr"]["ok"] is True and summary["created"] == 1
    assert env.watcher.health()["sonarr"]["ok"] is False


# --- media facts ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_media_info_reads_series_facts_with_tag_names(env):
    env.arr.add_series(tags=[1, 3], originalLanguage={"id": 1, "name": "Greek"})
    env.arr.add_episode(101, 1, 201, f"{SONARR_ROOT}/E1.mkv", "2026-10-14T12:01:00Z", monitored=False)
    env.arr.tags = [{"id": 1, "label": "No-Greek"}, {"id": 2, "label": "x"}, {"id": 3, "label": "4k"}]
    info = ArrMediaInfo()
    facts = await info({"source": "sonarr", "arr_parent_id": 7, "arr_item_id": 101})
    assert facts == {"title": "Severance", "original_language": "Greek", "tags": ["No-Greek", "4k"],
                     "monitored": False, "series_type": "standard"}


@pytest.mark.asyncio
async def test_media_info_radarr_and_unconfigured(env):
    env.arr.add_movie(60, "Heat", 1995, 700, "/x.mkv", "2026-10-14T09:00:00Z", tags=[2],
                      originalLanguage={"name": "English"})
    env.arr.tags = [{"id": 2, "label": "ombisub-skip"}]
    facts = await ArrMediaInfo()({"source": "radarr", "arr_item_id": 60, "arr_parent_id": None})
    assert facts["tags"] == ["ombisub-skip"] and facts["original_language"] == "English" and facts["monitored"]
    storage.save_global_config({"radarr_enabled": False})
    assert await ArrMediaInfo()({"source": "radarr", "arr_item_id": 60}) == {}
    with pytest.raises(RuntimeError):
        await ArrMediaInfo()({"source": "sonarr", "arr_parent_id": 999, "arr_item_id": 1})
