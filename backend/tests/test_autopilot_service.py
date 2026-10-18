"""Autopilot service (services/autopilot/service.py): loops, health alerts, digest, event wiring.

Offline: fakes for the watcher, pipeline, notifier, agent runner and Plex client; a movable
clock; the real (isolated) store.
"""

import asyncio
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from services.autopilot import service as service_module
from services.autopilot.service import AutopilotService
from services.autopilot.store import AutopilotStore, iso_utc
from utils import storage

T0 = datetime(2026, 10, 14, 6, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start=T0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


class FakeWatcher:
    def __init__(self):
        self.polls = self.reconciles = 0
        self.fail = None
        self._health = {"sonarr": {"configured": True, "ok": True, "since": None, "error": None, "cursor": None},
                        "radarr": {"configured": False, "ok": None, "since": None, "error": None, "cursor": None}}

    async def poll_once(self):
        if self.fail:
            raise RuntimeError(self.fail)
        self.polls += 1
        return {"sonarr": {"ok": True}}

    async def reconcile_once(self):
        self.reconciles += 1
        return {"created": 0}

    def health(self):
        return {k: dict(v) for k, v in self._health.items()}

    def set_sonarr(self, ok, since=None, error=None):
        self._health["sonarr"].update(ok=ok, since=iso_utc(since) if since else None, error=error)

    def _cancel_queue_rows(self, item):
        self.cancelled = item["id"]


class FakePipeline:
    def __init__(self):
        self.due = self.tracked = 0

    async def process_due(self, limit=10):
        self.due += 1
        return 0

    async def track_active(self):
        self.tracked += 1
        return 0


class FakeRunner:
    def __init__(self):
        self.state = {"status": "stopped", "running": False, "message": ""}

    def info(self):
        return dict(self.state)


class FakePlex:
    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok
        self.last_error = None if ok else "Plex answered HTTP 401"

    def refresh_path(self, path):
        self.calls.append(path)
        return self.ok


@pytest.fixture(autouse=True)
def fresh_singleton():
    service_module.reset_service()
    yield
    service_module.reset_service()


@pytest.fixture
def env(tmp_path):
    e = types.SimpleNamespace()
    e.clock = Clock()
    e.store = AutopilotStore(tmp_path / "ap.db", clock=e.clock)
    e.watcher, e.pipeline, e.runner = FakeWatcher(), FakePipeline(), FakeRunner()
    e.notifier = MagicMock()
    e.plex = FakePlex()
    e.plex_enabled = True
    e.svc = AutopilotService(store=e.store, watcher=e.watcher, pipeline=e.pipeline, notifier=e.notifier,
                             clock=e.clock, runner_provider=lambda: e.runner,
                             plex_factory=lambda cfg: e.plex if e.plex_enabled else None)
    storage.save_global_config({"autopilot_enabled": True, "autopilot_enabled_at": iso_utc(T0),
                                "autopilot_digest_hour": -1})
    e.n = 0

    def item(state="waiting", **kw):
        e.n += 1
        it, _ = e.store.upsert_detection(
            source="sonarr", kind="episode", arr_item_id=e.n, arr_parent_id=1, arr_file_id=1000 + e.n,
            media_path=f"/tv/Show.S01E{e.n:02d}.mkv", project_name="Show", episode_name=f"S01E{e.n:02d}",
            title="Show", season=1, episode=e.n, detected_at=e.clock(), grace_seconds=300)
        if state != "waiting":
            it = e.store.transition(it["id"], state, **kw)
        return it

    e.item = item
    yield e
    e.store.close()


# --- loops ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loops_do_nothing_while_disabled_and_start_when_enabled(env, monkeypatch):
    monkeypatch.setattr(service_module, "IDLE_SECONDS", 0.02)
    monkeypatch.setattr(service_module, "PIPELINE_SECONDS", 0.02)
    monkeypatch.setattr(service_module, "RECONCILE_FIRST_DELAY", 0.02)
    storage.save_global_config({"autopilot_enabled": False})
    await env.svc.start()
    try:
        assert env.svc.running
        await asyncio.sleep(0.3)
        assert (env.watcher.polls, env.watcher.reconciles, env.pipeline.due, env.pipeline.tracked) == (0, 0, 0, 0)
        assert env.svc.status()["loops"]["poll"]["last_run"] is None

        storage.save_global_config({"autopilot_enabled": True})
        for _ in range(100):
            if env.watcher.polls and env.pipeline.tracked and env.watcher.reconciles:
                break
            await asyncio.sleep(0.05)
        assert env.watcher.polls == 1 and env.watcher.reconciles == 1  # poll/reconcile intervals are long
        assert env.pipeline.due >= 1 and env.pipeline.tracked >= 1
        loops = env.svc.status()["loops"]
        assert loops["poll"]["last_ok"] is True and loops["poll"]["last_run"]
    finally:
        await env.svc.stop()
    assert not env.svc.running
    polls = env.watcher.polls
    await asyncio.sleep(0.1)
    assert env.watcher.polls == polls


@pytest.mark.asyncio
async def test_a_failing_tick_is_recorded_and_the_next_one_recovers(env):
    env.watcher.fail = "sonarr exploded"
    assert await env.svc.run_loop_once("poll") is False
    loop = env.svc.status()["loops"]["poll"]
    assert loop["last_ok"] is False and "sonarr exploded" in loop["last_error"]
    env.watcher.fail = None
    assert await env.svc.run_loop_once("poll") is True
    loop = env.svc.status()["loops"]["poll"]
    assert loop["last_ok"] is True and loop["last_error"] is None


@pytest.mark.asyncio
async def test_start_recovers_items_interrupted_while_checking(env):
    it = env.item(state="checking")
    await env.svc.start()
    try:
        assert env.store.get(it["id"])["state"] == "waiting"
    finally:
        await env.svc.stop()


@pytest.mark.asyncio
async def test_enabled_at_is_set_once_when_the_autopilot_is_switched_on(env):
    storage.save_global_config({"autopilot_enabled_at": ""})
    stamp = env.svc._ensure_enabled_at()
    assert stamp == iso_utc(T0) and storage.load_global_config()["autopilot_enabled_at"] == stamp
    env.clock.advance(days=1)
    assert env.svc._ensure_enabled_at() == stamp
    storage.save_global_config({"autopilot_enabled": False, "autopilot_enabled_at": ""})
    assert env.svc._ensure_enabled_at() is None  # off: nothing is recorded
    assert not storage.load_global_config().get("autopilot_enabled_at")


# --- arr outage ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_arr_outage_alerts_once_after_ten_minutes_and_announces_recovery(env):
    env.watcher.set_sonarr(False, since=env.clock(), error="connection refused")
    await env.svc.run_loop_once("health")
    env.clock.advance(minutes=9)
    await env.svc.run_loop_once("health")
    env.notifier.arr_unreachable.assert_not_called()
    env.clock.advance(minutes=2)
    await env.svc.run_loop_once("health")
    env.notifier.arr_unreachable.assert_called_once_with("sonarr", "connection refused", iso_utc(T0))
    env.clock.advance(minutes=30)
    await env.svc.run_loop_once("health")
    assert env.notifier.arr_unreachable.call_count == 1
    env.notifier.arr_recovered.assert_not_called()

    env.watcher.set_sonarr(True, since=env.clock())
    await env.svc.run_loop_once("health")
    env.notifier.arr_recovered.assert_called_once_with("sonarr")
    await env.svc.run_loop_once("health")
    assert env.notifier.arr_recovered.call_count == 1


@pytest.mark.asyncio
async def test_a_short_outage_is_never_announced(env):
    env.watcher.set_sonarr(False, since=env.clock(), error="timeout")
    await env.svc.run_loop_once("health")
    env.clock.advance(minutes=3)
    env.watcher.set_sonarr(True, since=env.clock())
    await env.svc.run_loop_once("health")
    env.notifier.arr_unreachable.assert_not_called()
    env.notifier.arr_recovered.assert_not_called()


# --- engine / runner -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_engine_missing_when_items_wait_and_nothing_can_serve_them(env):
    env.item(state="translating")
    env.item(state="extracting")
    storage.save_global_config({"queue_worker_paused": True})
    await env.svc.run_loop_once("health")
    env.notifier.engine_missing.assert_called_once_with(2)
    status = env.svc.status()["engine"]
    assert status["ok"] is False and status["worker_paused"] is True and "waiting" in status["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("runner_status,paused,agent_mode,unserved", [
    ("running", True, False, 1),    # the runner translates, but extraction needs the (paused) worker
    ("waiting", False, True, 0),
    ("idle", False, True, 0),
    ("stopped", False, False, 0),   # the Gemini worker is unpaused
    ("stopped", False, True, 1),    # agent mode: the Gemini worker does not translate
    ("stopped", True, False, 2),
])
async def test_engine_missing_matrix(env, runner_status, paused, agent_mode, unserved):
    env.item(state="translating")
    env.item(state="extracting")
    env.runner.state = {"status": runner_status, "running": runner_status != "stopped", "message": ""}
    storage.save_global_config({"queue_worker_paused": paused,
                                "translation_engine": "agent" if agent_mode else "model"})
    await env.svc.run_loop_once("health")
    if unserved:
        env.notifier.engine_missing.assert_called_once_with(unserved)
    else:
        env.notifier.engine_missing.assert_not_called()


@pytest.mark.asyncio
async def test_no_engine_alert_without_routed_items(env):
    storage.save_global_config({"queue_worker_paused": True})
    env.item()  # only a waiting item: not routed yet
    await env.svc.run_loop_once("health")
    env.notifier.engine_missing.assert_not_called()


@pytest.mark.asyncio
async def test_runner_error_is_forwarded(env):
    env.runner.state = {"status": "error", "running": False, "message": "sign-in expired"}
    await env.svc.run_loop_once("health")
    env.notifier.runner_problem.assert_called_once_with("error", "sign-in expired")
    assert env.svc.status()["engine"]["ok"] is False


@pytest.mark.asyncio
async def test_runner_waiting_longer_than_six_hours_is_forwarded(env):
    env.runner.state = {"status": "waiting", "running": True, "message": "paused for quota"}
    await env.svc.run_loop_once("health")
    env.clock.advance(hours=5, minutes=59)
    await env.svc.run_loop_once("health")
    env.notifier.runner_problem.assert_not_called()
    env.clock.advance(minutes=2)
    await env.svc.run_loop_once("health")
    env.notifier.runner_problem.assert_called_once_with("waiting", "paused for quota")
    # running again resets the clock
    env.runner.state = {"status": "running", "running": True, "message": ""}
    await env.svc.run_loop_once("health")
    env.runner.state = {"status": "waiting", "running": True, "message": "again"}
    env.notifier.runner_problem.reset_mock()
    env.clock.advance(hours=1)
    await env.svc.run_loop_once("health")
    env.notifier.runner_problem.assert_not_called()


# --- digest / prune ------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daily_digest_at_the_configured_local_hour_once_a_day(env):
    local_hour = T0.astimezone().hour
    storage.save_global_config({"autopilot_digest_hour": (local_hour + 1) % 24})
    await env.svc.run_loop_once("health")
    env.notifier.daily_digest.assert_not_called()

    env.item(state="done")
    env.item(state="has_greek")
    env.item(state="failed", detail="boom")
    old = env.item(state="done")
    env.store._conn.execute("UPDATE items SET updated_at = ? WHERE id = ?",
                            (iso_utc(T0 - timedelta(days=3)), old["id"]))
    storage.save_global_config({"autopilot_digest_hour": local_hour})
    await env.svc.run_loop_once("health")
    env.notifier.daily_digest.assert_called_once()
    stats = env.notifier.daily_digest.call_args.args[0]
    assert (stats["done"], stats["has_greek"], stats["failed"], stats["no_source"], stats["skipped"]) == (1, 1, 1, 0, 0)
    assert len(stats["attention"]) == 1 and "boom" in stats["attention"][0] and "Show S01E03" in stats["attention"][0]

    env.clock.advance(minutes=20)
    await env.svc.run_loop_once("health")
    assert env.notifier.daily_digest.call_count == 1  # not twice the same day
    env.clock.advance(days=1)
    await env.svc.run_loop_once("health")
    assert env.notifier.daily_digest.call_count == 2


@pytest.mark.asyncio
async def test_digest_can_be_switched_off(env):
    storage.save_global_config({"autopilot_digest_hour": -1})
    await env.svc.run_loop_once("health")
    env.notifier.daily_digest.assert_not_called()


@pytest.mark.asyncio
async def test_old_finished_items_are_pruned_once_a_day(env):
    old = env.item(state="done")
    keep = env.item(state="done")
    env.store._conn.execute("UPDATE items SET updated_at = ? WHERE id = ?",
                            (iso_utc(T0 - timedelta(days=120)), old["id"]))
    await env.svc.run_loop_once("health")
    assert env.store.get(old["id"]) is None and env.store.get(keep["id"]) is not None
    stale = env.item(state="done")
    env.store._conn.execute("UPDATE items SET updated_at = ? WHERE id = ?",
                            (iso_utc(T0 - timedelta(days=120)), stale["id"]))
    env.clock.advance(hours=2)
    await env.svc.run_loop_once("health")
    assert env.store.get(stale["id"]) is not None  # not yet: pruning runs once a day
    env.clock.advance(days=1)
    await env.svc.run_loop_once("health")
    assert env.store.get(stale["id"]) is None


# --- pipeline events -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_done_refreshes_plex_with_the_outputs_folder_and_notifies(env):
    storage.save_global_config({"plex_refresh_enabled": True, "plex_token": "tok"})
    it = env.item()
    await env.svc.on_event("done", it, outputs=["/tv/Show/S01/Show.S01E01.el.ass", "/tv/Show/S01/Show.S01E01.el.srt"])
    assert len(env.plex.calls) == 1 and env.plex.calls[0].replace("\\", "/") == "/tv/Show/S01"
    env.notifier.item_done.assert_called_once_with(it)
    assert env.svc.status()["plex"] == {"enabled": True, "ok": True, "error": None}


@pytest.mark.asyncio
async def test_plex_failure_is_reported_in_the_status_not_raised(env):
    storage.save_global_config({"plex_refresh_enabled": True, "plex_token": "tok"})
    env.plex.ok = False
    env.plex.last_error = "Plex answered HTTP 401"
    await env.svc.on_event("done", env.item(), outputs=["/tv/a/x.el.srt"])
    assert env.svc.status()["plex"] == {"enabled": True, "ok": False, "error": "Plex answered HTTP 401"}
    env.notifier.item_done.assert_called_once()


@pytest.mark.asyncio
async def test_no_plex_client_means_no_refresh(env):
    env.plex_enabled = False
    await env.svc.on_event("done", env.item(), outputs=["/tv/a/x.el.srt"])
    assert env.plex.calls == []
    assert env.svc.status()["plex"]["enabled"] is False


@pytest.mark.asyncio
async def test_foreign_repaired_refreshes_plex_notifies_and_forgets_the_files(env, monkeypatch):
    forgotten = []
    monkeypatch.setattr("services.autopilot.ownership.forget", lambda p: forgotten.append(p))
    it = env.item()
    await env.svc.on_event("foreign_repaired", it, paths=["/tv/s/x.el.srt", "/tv/s/x.gre.srt"])
    assert len(env.plex.calls) == 1
    assert [c.args for c in env.notifier.foreign_repaired.call_args_list] == [
        (it, "/tv/s/x.el.srt"), (it, "/tv/s/x.gre.srt")]
    assert forgotten == ["/tv/s/x.el.srt", "/tv/s/x.gre.srt"]


@pytest.mark.asyncio
async def test_no_source_failed_and_other_events(env):
    it = env.item()
    await env.svc.on_event("no_source", it, reason="image_only")
    env.notifier.no_source.assert_called_once_with(it)
    await env.svc.on_event("failed", it, error="x")
    env.notifier.item_failed.assert_called_once_with(it)
    await env.svc.on_event("skipped", it, reason="skip_tag")
    await env.svc.on_event("has_greek", it)
    assert env.plex.calls == [] and env.notifier.item_done.call_count == 0


def test_the_service_wires_its_own_pipeline_to_on_event(tmp_path):
    svc = AutopilotService(store=AutopilotStore(tmp_path / "x.db"))
    assert svc.pipeline._on_event == svc.on_event
    assert svc.watcher.store is svc.store


# --- status --------------------------------------------------------------------------------

def test_status_shape(env):
    storage.save_global_config({"plex_refresh_enabled": True, "plex_token": "secret-token"})
    for n in range(7):
        env.clock.advance(minutes=1)
        env.item()
    env.item(state="done")
    status = env.svc.status()
    assert set(status) == {"enabled", "running", "settings", "loops", "arr", "engine", "plex", "counts", "next_due"}
    assert status["enabled"] is True and status["running"] is False
    assert status["settings"]["plex_token"] == "***" and status["settings"]["grace_minutes"] == 5.0
    assert set(status["loops"]) == {"poll", "pipeline", "reconcile", "health"}
    assert set(status["loops"]["poll"]) == {"last_run", "last_ok", "last_error"}
    assert set(status["arr"]) == {"sonarr", "radarr"}
    assert set(status["engine"]) == {"runner_status", "runner_running", "worker_paused", "ok", "message"}
    assert set(status["plex"]) == {"enabled", "ok", "error"} and status["plex"]["enabled"] is True
    assert status["counts"]["waiting"] == 7 and status["counts"]["done"] == 1
    due = [i["due_at"] for i in status["next_due"]]
    assert len(due) == 5 and due == sorted(due)
    assert "secret-token" not in str(status)
