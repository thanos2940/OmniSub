"""Autopilot end to end, offline.

A fake Sonarr (``httpx.MockTransport``) imports an episode; a dummy ``.mkv`` sits in a temp media
folder; the container probe and the ffmpeg extraction are faked at their seams; the extraction
job runs through the real ``process_extraction_item``; the translation is played by writing Greek
into the episode and calling the real exporter. The service, watcher, pipeline, store, ownership
registry and queue are the real ones, driven with a movable clock.
"""

import asyncio
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from integrations import embedded_subs, radarr, sonarr
from main import app
from routers import integrations as integrations_router
from services.autopilot import ownership
from services.autopilot import service as service_module
from services.autopilot.service import AutopilotService
from services.autopilot.store import AutopilotStore, iso_utc
from services.translation.export_formatter import auto_export_translated_subtitle
from services.worker.extraction_worker import process_extraction_item
from utils import storage
from utils.translation_queue import KIND_EXTRACTION, KIND_TRANSLATION, TranslationQueue

ASS = ("[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name, Fontname\nStyle: Default,Arial\n\n"
       "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
       "Dialogue: 0,0:00:01.00,0:00:04.00,Default,,0,0,0,,Hello, Mark.\n"
       "Dialogue: 0,0:00:05.00,0:00:08.00,Default,,0,0,0,,Welcome to Lumon.\n")
GREEK = ["Γεια σου, Μαρκ.", "Καλώς ήρθες στη Lumon."]
GREEK_SRT = "1\r\n00:00:01,000 --> 00:00:04,000\r\nΚαλημέρα σας, πώς είστε;\r\n\r\n"
SONARR_URL = "http://sonarr.test:8989"
SERIES = {"id": 3, "title": "Severance", "monitored": True, "tags": [], "seriesType": "standard",
          "originalLanguage": {"name": "English"}}


class Clock:
    """Real time plus a movable offset, so file times and the clock agree."""

    def __init__(self):
        self.offset = timedelta(0)

    def __call__(self):
        return datetime.now(timezone.utc) + self.offset

    def advance(self, **kw):
        self.offset += timedelta(**kw)


class FakeSonarr:
    def __init__(self):
        self.records = []      # history records, newest last
        self.episodes = {}
        self.files = {}
        self.down = False
        self.requests = []

    def add_import(self, clock, path, *, episode_id=11, file_id=77, season=1, number=1, when=None):
        when = when or clock()
        ep = {"id": episode_id, "seriesId": 3, "seasonNumber": season, "episodeNumber": number,
              "episodeFileId": file_id, "monitored": True}
        self.episodes[episode_id] = ep
        self.files[file_id] = {"id": file_id, "path": str(path)}
        self.records.append({"id": 1000 + len(self.records) + 1, "eventType": "downloadFolderImported",
                             "date": iso_utc(when).replace("Z", ".000Z"), "episodeId": episode_id, "seriesId": 3,
                             "series": dict(SERIES), "episode": dict(ep), "data": {"importedPath": str(path)}})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if self.down:
            raise httpx.ConnectError("sonarr is down")
        path = request.url.path
        if path == "/api/v3/history/since":
            since = request.url.params["date"]
            kind = request.url.params["eventType"]
            rows = [r for r in self.records if r["eventType"] == kind and r["date"] >= since]
            return httpx.Response(200, json=rows)
        if path.startswith("/api/v3/episodefile/"):
            return httpx.Response(200, json=self.files[int(path.rsplit("/", 1)[1])])
        if path.startswith("/api/v3/episode/"):
            return httpx.Response(200, json=self.episodes[int(path.rsplit("/", 1)[1])])
        if path.startswith("/api/v3/series/"):
            return httpx.Response(200, json=SERIES)
        if path == "/api/v3/tag":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={})


@pytest.fixture
def env(tmp_path, monkeypatch):
    import services.queue_service as queue_service
    monkeypatch.setattr(queue_service, "_wake_worker", lambda: None)
    service_module.reset_service()

    e = type("Env", (), {})()
    e.clock = Clock()
    e.arr = FakeSonarr()
    e.media = tmp_path / "tv" / "Severance" / "Season 01"
    e.media.mkdir(parents=True)
    e.video = e.media / "Severance.S01E01.1080p.mkv"
    e.video.write_bytes(b"\x1aE\xdf\xa3")
    e.tracks = [embedded_subs.SubtitleTrack(index=2, codec="ass", language="eng", title="Full", frames=400)]
    e.extractions = []
    e.plex_calls = []

    storage.save_global_config({
        "sonarr_enabled": True, "sonarr_url": SONARR_URL, "sonarr_api_key": "key",
        "autopilot_enabled": True, "autopilot_enabled_at": iso_utc(datetime.now(timezone.utc) - timedelta(days=1)),
        "autopilot_digest_hour": -1, "embedded_extraction_enabled": True, "arr_source_language": "en",
        "default_target_language": "Greek", "plex_refresh_enabled": True, "plex_token": "tok",
    })

    transport = httpx.MockTransport(e.arr.handler)
    monkeypatch.setattr(sonarr, "get_client", lambda: httpx.AsyncClient(transport=transport))
    monkeypatch.setattr(radarr, "get_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(404, json={}))))
    monkeypatch.setattr(embedded_subs, "resolve_tools", lambda *a, **k: embedded_subs.EmbeddedTools("ffmpeg", "ffprobe"))
    monkeypatch.setattr(embedded_subs, "probe_subtitle_tracks", lambda path, *a, **k: list(e.tracks))
    monkeypatch.setattr("utils.media_probe_cache.get", lambda *a, **k: None)
    monkeypatch.setattr("utils.media_probe_cache.put", lambda *a, **k: None)

    async def fake_extract(media_path, track, tools=None, config=None, timeout=None):
        e.extractions.append((media_path, track.index))
        return ASS

    monkeypatch.setattr(embedded_subs, "extract_track", fake_extract)

    class Plex:
        last_error = None

        def refresh_path(self, path):
            e.plex_calls.append(path)
            return True

    e.notifier = MagicMock()
    e.store = AutopilotStore(tmp_path / "autopilot-e2e.db", clock=e.clock)
    e.svc = AutopilotService(store=e.store, notifier=e.notifier, clock=e.clock, plex_factory=lambda cfg: Plex())
    service_module._service = e.svc
    e.queue = TranslationQueue()
    with e.queue._conn() as conn:
        conn.execute("DELETE FROM translation_queue")
        conn.commit()

    def rows():
        with e.queue._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM translation_queue").fetchall()]

    e.rows = rows

    async def run_extraction():
        row = e.queue.claim_next_extraction(max_priority=None)
        assert row, "no extraction job was queued"
        await process_extraction_item(e.queue, row, storage.load_global_config())

    def play_translation(project="Severance", episode="S01E01"):
        """What the engine does: Greek into the episode, export next to the video, complete the row."""
        data = storage.load_episode(project, episode)
        rows = data["data"]
        for row, text in zip(rows, GREEK):
            row["translated"] = text
            row.setdefault("translations", {})["el"] = text
        meta = storage.load_episode_metadata(project, episode)
        meta.update(translated=True, translation_status="completed", translation_source="test")
        storage.save_episode(project, episode, rows, meta)
        out = auto_export_translated_subtitle(project, episode, rows, meta)
        now = e.clock().timestamp()
        for f in e.media.glob("*.el.*"):
            os.utime(f, (now, now))  # file times follow the test clock
        row = e.queue.find_item(project, episode, "", KIND_TRANSLATION)
        e.queue.update_status(row["id"], status="completed", completed_at=e.clock().isoformat())
        return out

    e.run_extraction, e.play_translation = run_extraction, play_translation
    yield e
    service_module.reset_service()
    e.store.close()


def names(folder):
    return sorted(p.name for p in folder.iterdir())


@pytest.mark.asyncio
async def test_new_episode_goes_from_sonarr_import_to_greek_subtitles(env):
    svc = env.svc
    # First poll only sets the cursor ("the past is the reconcile's job").
    assert (await svc.poll_now())["sonarr"].get("initialized") is True
    env.clock.advance(seconds=30)
    imported_at = env.clock()
    env.arr.add_import(env.clock, env.video, when=imported_at)
    env.clock.advance(seconds=30)

    summary = await svc.poll_now()
    assert summary["sonarr"]["created"] == 1
    [item] = env.store.list()[0]
    assert item["state"] == "waiting" and item["kind"] == "episode" and item["project_name"] == "Severance"
    assert item["episode_name"] == "S01E01" and item["media_path"] == str(env.video)
    due = datetime.fromisoformat(item["due_at"].replace("Z", "+00:00"))
    assert abs((due - imported_at).total_seconds() - 300) < 2  # import time + the 5 minute grace

    # Inside the grace period nothing happens (Bazarr's window).
    env.clock.advance(minutes=3)
    assert await env.svc.pipeline.process_due() == 0
    assert env.rows() == [] and env.store.get(item["id"])["state"] == "waiting"

    # Grace over: checking -> extraction queued.
    env.clock.advance(minutes=3)
    assert await svc.run_loop_once("pipeline") is True
    item = env.store.get(item["id"])
    assert item["state"] == "extracting" and item["source_kind"] == "embedded_ass"
    [job] = env.rows()
    assert job["kind"] == KIND_EXTRACTION and json.loads(job["options"])["stream_index"] == 2

    # The extraction job runs: sidecar written, episode imported, translation queued behind it.
    await env.run_extraction()
    assert env.extractions == [(str(env.video), 2)]
    assert (env.media / "Severance.S01E01.1080p.en.ass").exists()
    await svc.run_loop_once("pipeline")
    assert env.store.get(item["id"])["state"] == "translating"
    translation = env.queue.find_item("Severance", "S01E01", "", KIND_TRANSLATION)
    assert translation and translation["status"] == "pending"

    # Not done while the engine has not translated.
    await svc.run_loop_once("pipeline")
    assert env.store.get(item["id"])["state"] == "translating"

    # The engine translates and exports.
    env.play_translation()
    assert await svc.run_loop_once("pipeline") is True
    done = env.store.get(item["id"])
    assert done["state"] == "done", (done["state"], done["detail"])
    ass, srt = env.media / "Severance.S01E01.1080p.el.ass", env.media / "Severance.S01E01.1080p.el.srt"
    assert names(env.media) == sorted([
        "Severance.S01E01.1080p.mkv", "Severance.S01E01.1080p.en.ass", ass.name, srt.name])
    assert done["outputs"] == [str(ass), str(srt)]
    assert GREEK[0] in ass.read_text(encoding="utf-8-sig") and GREEK[1] in srt.read_text(encoding="utf-8-sig")
    assert ownership.is_ours(ass) and ownership.is_ours(srt)

    # Integrations ran: Plex was told to rescan the folder, Discord was notified.
    assert [Path(p) for p in env.plex_calls] == [env.media]
    env.notifier.item_done.assert_called_once()
    assert env.notifier.item_done.call_args.args[0]["id"] == item["id"]
    assert env.svc.status()["counts"]["done"] == 1

    # The same import seen again (a second poll, a webhook) does not start over.
    assert (await svc.poll_now())["sonarr"].get("created", 0) == 0
    assert env.store.counts()["waiting"] == 0 and len(env.store.list()[0]) == 1


@pytest.mark.asyncio
async def test_a_foreign_greek_subtitle_at_check_time_stands_the_autopilot_down(env):
    await env.svc.poll_now()
    env.clock.advance(seconds=30)
    env.arr.add_import(env.clock, env.video)
    (env.media / "Severance.S01E01.1080p.el.srt").write_bytes(GREEK_SRT.encode("utf-8"))  # Bazarr was quick
    env.clock.advance(seconds=30)
    await env.svc.poll_now()
    env.clock.advance(minutes=6)
    await env.svc.run_loop_once("pipeline")
    [item] = env.store.list()[0]
    assert (item["state"], item["reason"]) == ("has_greek", "greek_sidecar")
    assert env.rows() == [] and env.extractions == []
    assert (env.media / "Severance.S01E01.1080p.el.srt").read_bytes() == GREEK_SRT.encode("utf-8")
    env.notifier.item_done.assert_not_called()


@pytest.mark.asyncio
async def test_an_image_only_episode_ends_no_source_and_alerts(env):
    env.tracks = [embedded_subs.SubtitleTrack(index=2, codec="hdmv_pgs_subtitle", language="eng", frames=300)]
    await env.svc.poll_now()
    env.clock.advance(seconds=30)
    env.arr.add_import(env.clock, env.video)
    env.clock.advance(seconds=30)
    await env.svc.poll_now()
    env.clock.advance(minutes=6)
    await env.svc.run_loop_once("pipeline")
    [item] = env.store.list()[0]
    assert (item["state"], item["reason"]) == ("no_source", "image_only")
    env.notifier.no_source.assert_called_once()


@pytest.mark.asyncio
async def test_restart_resumes_an_item_that_was_being_checked(env, tmp_path):
    await env.svc.poll_now()
    env.clock.advance(seconds=30)
    env.arr.add_import(env.clock, env.video)
    env.clock.advance(seconds=30)
    await env.svc.poll_now()
    [item] = env.store.list()[0]
    env.store.transition(item["id"], "checking")  # the process died in the middle of the check
    env.store.close()

    storage.save_global_config({"autopilot_enabled": False})  # let start() recover without running loops
    store2 = AutopilotStore(tmp_path / "autopilot-e2e.db", clock=env.clock)
    svc2 = AutopilotService(store=store2, notifier=env.notifier, clock=env.clock)
    service_module._service = svc2
    await svc2.start()
    await svc2.stop()
    assert store2.get(item["id"])["state"] == "waiting"

    storage.save_global_config({"autopilot_enabled": True})
    env.clock.advance(minutes=6)
    assert await svc2.pipeline.process_due() == 1
    assert store2.get(item["id"])["state"] == "extracting"
    # ...and the restarted service tracks the job through to the end
    await env.run_extraction()
    await svc2.pipeline.track_active()
    env.play_translation()
    await svc2.pipeline.track_active()
    assert store2.get(item["id"])["state"] == "done"
    store2.close()
    env.store = AutopilotStore(tmp_path / "autopilot-e2e.db", clock=env.clock)  # for the fixture's close()


@pytest.mark.asyncio
async def test_an_unreachable_sonarr_is_reported_in_the_status_and_recovers(env):
    await env.svc.poll_now()
    env.arr.down = True
    env.clock.advance(seconds=5)
    summary = await env.svc.poll_now()
    assert summary["sonarr"]["ok"] is False
    arr = env.svc.status()["arr"]["sonarr"]
    assert arr["configured"] is True and arr["ok"] is False and arr["error"]
    env.arr.down = False
    env.clock.advance(minutes=3)  # past the first back-off
    assert (await env.svc.poll_now())["sonarr"].get("ok") is True
    assert env.svc.status()["arr"]["sonarr"]["ok"] is True


# --- webhooks ----------------------------------------------------------------------------------

def sonarr_payload(path, event="Download", file_id=77):
    return {"eventType": event, "series": {"id": 3, "title": "Severance"},
            "episodes": [{"id": 11, "seasonNumber": 1, "episodeNumber": 1, "episodeFileId": file_id}],
            "episodeFile": {"id": file_id, "path": str(path)}}


def test_webhooks_feed_the_autopilot_when_it_is_on(env, monkeypatch):
    storage.save_global_config({"webhook_secret": "s3cret"})
    integrations_router._webhook_deduplicator.clear()
    legacy = []

    async def legacy_import(payload):
        legacy.append(payload)

    monkeypatch.setattr(integrations_router, "_process_sonarr_webhook", legacy_import)
    client = TestClient(app)

    r = client.post("/api/webhook/sonarr?token=s3cret", json=sonarr_payload(env.video))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued" and body["autopilot"] is True and body["item_id"]
    item = env.store.get(body["item_id"])
    assert item["state"] == "waiting" and item["media_path"] == str(env.video) and item["arr_file_id"] == 77
    assert legacy == []  # the old direct import stands down

    dup = client.post("/api/webhook/sonarr?token=s3cret", json=sonarr_payload(env.video))
    assert dup.json()["status"] == "ignored" and len(env.store.list()[0]) == 1

    # a rename updates the item's path
    renamed = env.media / "Severance.S01E01.1080p.WEB.mkv"
    shutil.move(env.video, renamed)
    r = client.post("/api/webhook/sonarr?token=s3cret", json=sonarr_payload(renamed, "EpisodeFileRenamed"))
    assert r.json()["autopilot"] is True
    assert env.store.get(item["id"])["media_path"] == str(renamed)

    # a delete marks it gone
    r = client.post("/api/webhook/sonarr?token=s3cret", json=sonarr_payload(renamed, "EpisodeFileDelete"))
    assert r.json()["autopilot"] is True and env.store.get(item["id"])["state"] == "gone"

    # the connection test and bad tokens behave as before
    assert client.post("/api/webhook/sonarr?token=s3cret", json={"eventType": "Test"}).json()["status"] == "success"
    assert client.post("/api/webhook/sonarr?token=bad", json=sonarr_payload(env.video)).status_code == 401


def test_webhooks_keep_the_legacy_import_when_the_autopilot_is_off(env, monkeypatch):
    storage.save_global_config({"webhook_secret": "s3cret", "autopilot_enabled": False})
    integrations_router._webhook_deduplicator.clear()
    legacy = []

    async def legacy_import(payload):
        legacy.append(payload)

    monkeypatch.setattr(integrations_router, "_process_sonarr_webhook", legacy_import)
    r = TestClient(app).post("/api/webhook/sonarr?token=s3cret", json=sonarr_payload(env.video))
    assert r.json() == {"status": "queued"} and len(legacy) == 1
    assert env.store.list()[0] == []


def test_radarr_webhook_feeds_the_autopilot(env):
    storage.save_global_config({"webhook_secret": "s3cret", "radarr_enabled": True, "radarr_api_key": "k"})
    integrations_router._webhook_deduplicator.clear()
    movie = env.media / "Dune.2021.mkv"
    movie.write_bytes(b"x")
    payload = {"eventType": "Download", "movie": {"id": 5, "title": "Dune", "year": 2021, "tmdbId": 438631},
               "movieFile": {"id": 9, "path": str(movie)}}
    r = TestClient(app).post("/api/webhook/radarr?token=s3cret", json=payload)
    assert r.status_code == 200 and r.json()["autopilot"] is True
    item = env.store.get(r.json()["item_id"])
    assert item["kind"] == "movie" and item["source"] == "radarr" and item["media_path"] == str(movie)


# --- the scheduled sync stands down ------------------------------------------------------------

@pytest.mark.parametrize("autopilot,enqueued", [(False, True), (True, False)])
def test_scheduled_sync_skips_enqueueing_while_the_autopilot_is_on(env, monkeypatch, autopilot, enqueued):
    import main
    import services.queue_service as queue_service

    storage.save_global_config({"autopilot_enabled": autopilot, "arr_sync_interval": 1})
    storage.create_project("Severance", {"show_name": "Severance", "target_language": "Greek"})
    calls = {"sync": 0, "enqueue": []}

    class Engine:
        async def full_sync(self):
            calls["sync"] += 1
            return type("R", (), {"new_projects": 0, "new_episodes": 0})()

    real_sleep = asyncio.sleep

    async def fast_sleep(seconds, *a, **k):
        await real_sleep(0)

    monkeypatch.setattr(integrations_router, "_build_arr_engine", lambda *a, **k: Engine())
    monkeypatch.setattr(queue_service, "enqueue_missing_for_project",
                        lambda project, priority=None: calls["enqueue"].append(project) or [])
    monkeypatch.setattr(main.asyncio, "sleep", fast_sleep)

    async def go():
        task = asyncio.create_task(main._scheduled_arr_sync_loop())
        for _ in range(200):
            if calls["sync"] >= 2:
                break
            await real_sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(go())
    assert calls["sync"] >= 2  # the full sync always runs
    assert bool(calls["enqueue"]) is enqueued
