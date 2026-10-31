"""
An update that waits for the work in progress
=============================================
A GitHub push, a scheduled check or the Install button can find OmbiSub busy. The update then waits for
a moment with nothing in progress and is installed then (update_service.request_update_when_idle); a
server that stays busy holds new work (services/work_hold.py) so the work in hand can finish. These tests
pin that it is installed once the work ends, that one waiter serves any number of requests, that nothing
in progress is cut off, that the hold is lifted whenever the update will not go in, and that the replies
promise only what happens.
"""

import asyncio
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from main import app
from services import agy_llm, update_service as update_module, work_hold
from services.update_service import UpdateService, update_service
from utils import storage
from utils.jobs_manager import JobStatus, jobs
from utils.translation_queue import TranslationQueue

from test_agent_runner import (  # noqa: F401  (fixtures are picked up by name)
    _join, _prompts, _runner, _show, _wait, empty_queue, fake_env, gemini_api, no_bootstrap,
)


@pytest.fixture
def fast(monkeypatch):
    """Waiter timings in tenths of a second instead of minutes, with automatic updates on (the settings
    default is off, which drops a push or a scheduled check)."""
    storage.save_global_config({"auto_update_enabled": True})
    monkeypatch.setattr(update_module, "IDLE_CHECK_SECONDS", 0.05)
    monkeypatch.setattr(update_module, "HOLD_AFTER_SECONDS", 3600)
    monkeypatch.setattr(update_module, "HOLD_MAX_SECONDS", 3600)
    monkeypatch.setattr(update_module, "RETRY_SECONDS", 0.1)


class FakeServer:
    """An UpdateService whose busy state, check and install are scripted (no git, no restart)."""

    def __init__(self, monkeypatch, busy=(), update_available=True, results=None):
        self.svc = UpdateService()
        self.busy = list(busy)
        self.update_available = update_available
        self.results = list(results or [{"success": True, "status": "restarting"}])
        self.installs = []          # the hold reason in force during each install
        self.checks = 0
        monkeypatch.setattr(self.svc, "busy_reasons", lambda: list(self.busy))

        async def check(force=False, release_only=None):
            self.checks += 1
            return {"checked": True, "update_available": self.update_available}

        async def install(force, target_tag):
            self.installs.append(work_hold.reason())
            return self.results.pop(0) if len(self.results) > 1 else self.results[0]

        monkeypatch.setattr(self.svc, "check_for_updates", check)
        monkeypatch.setattr(self.svc, "_install", install)

    async def until(self, condition, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return True
            await asyncio.sleep(0.01)
        return False


def _signed_push(client, secret, commit="abcdef1234567"):
    body = json.dumps({"ref": "refs/heads/main", "head_commit": {"id": commit, "message": "New things"},
                       "sender": {"login": "someone"}}).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/api/webhooks/github", content=body, headers={
        "X-GitHub-Event": "push", "X-Hub-Signature-256": sig, "Content-Type": "application/json"})


# ===========================================================================
# 1. The waiter
# ===========================================================================

class TestWaiter:

    @pytest.mark.asyncio
    async def test_a_push_while_busy_waits_and_installs_once_the_work_ends(self, monkeypatch, fast):
        server = FakeServer(monkeypatch, busy=["Show S01E02 (agy runner)"])
        answer = server.svc.request_update_when_idle("GitHub push abcdef1", "abcdef1")
        assert answer["status"] == "deferred"
        assert "Show S01E02 (agy runner)" in answer["message"]
        await asyncio.sleep(0.3)
        assert server.installs == [], "installed while an episode was in a lane's hands"
        assert server.svc.pending_update_info()["state"] == "waiting"

        server.busy.clear()
        assert await server.until(lambda: server.installs)
        assert server.svc.pending_update_info()["state"] == "restarting"
        assert len(server.installs) == 1

    @pytest.mark.asyncio
    async def test_a_push_while_idle_installs_right_away(self, monkeypatch, fast):
        monkeypatch.setattr(update_module, "IDLE_CHECK_SECONDS", 30)   # no waiting between checks may be involved
        server = FakeServer(monkeypatch)
        answer = server.svc.request_update_when_idle("GitHub push abcdef1", "abcdef1")
        assert answer["status"] == "updating"
        assert await server.until(lambda: server.installs, timeout=2)

    @pytest.mark.asyncio
    async def test_repeated_pushes_start_one_waiter(self, monkeypatch, fast):
        server = FakeServer(monkeypatch, busy=["Show S01E02 (agy runner)"])
        server.svc.request_update_when_idle("GitHub push 1111111", "1111111")
        first = server.svc._waiter
        for commit in ("2222222", "3333333"):
            server.svc.request_update_when_idle(f"GitHub push {commit}", commit)
            assert server.svc._waiter is first
        waiters = [t for t in asyncio.all_tasks() if t.get_coro().__qualname__.endswith("_wait_and_install")]
        assert len(waiters) == 1
        assert server.svc.pending_update_info()["commit"] == "3333333"

        server.busy.clear()
        assert await server.until(lambda: server.installs)
        await asyncio.sleep(0.2)
        assert len(server.installs) == 1

    @pytest.mark.asyncio
    async def test_a_new_request_wakes_a_sleeping_waiter(self, monkeypatch, fast):
        monkeypatch.setattr(update_module, "IDLE_CHECK_SECONDS", 30)
        server = FakeServer(monkeypatch, busy=["Show S01E02 (agy runner)"])
        server.svc.request_update_when_idle("GitHub push 1111111", "1111111")
        await asyncio.sleep(0.05)            # the waiter found work in progress and sleeps for 30 s
        server.busy.clear()
        server.svc.request_update_when_idle("GitHub push 2222222", "2222222")
        assert await server.until(lambda: server.installs, timeout=2)

    @pytest.mark.asyncio
    async def test_shutdown_cancels_the_waiter_and_lifts_its_hold(self, monkeypatch, fast):
        server = FakeServer(monkeypatch, busy=["Show S01E02 (agy runner)"])
        server.svc.request_update_when_idle("Install button", hold_now=True, automatic=False)
        waiter = server.svc._waiter
        assert work_hold.reason() == update_module.HOLD_MESSAGE
        server.svc.shutdown()
        await asyncio.sleep(0.05)
        assert waiter.cancelled()
        assert work_hold.reason() is None
        server.busy.clear()
        await asyncio.sleep(0.2)
        assert server.installs == []

    @pytest.mark.asyncio
    async def test_nothing_to_install_ends_the_wait_without_a_restart(self, monkeypatch, fast):
        server = FakeServer(monkeypatch, update_available=False)
        server.svc.request_update_when_idle("GitHub push abcdef1", "abcdef1")
        assert await server.until(lambda: server.svc.pending_update_info() is None)
        assert server.installs == []
        assert server.svc.last_deferred_update()["status"] == "up_to_date"

    @pytest.mark.asyncio
    async def test_switching_automatic_updates_off_drops_an_automatic_request(self, monkeypatch, fast):
        server = FakeServer(monkeypatch, busy=["Show S01E02 (agy runner)"])
        server.svc.request_update_when_idle("scheduled check")
        storage.save_global_config({"auto_update_enabled": False})
        assert await server.until(lambda: server.svc.pending_update_info() is None)
        assert server.svc.last_deferred_update()["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_a_request_of_a_person_survives_automatic_updates_being_off(self, monkeypatch, fast):
        storage.save_global_config({"auto_update_enabled": False})
        server = FakeServer(monkeypatch, busy=["Show S01E02 (agy runner)"])
        server.svc.request_update_when_idle("Install button", hold_now=True, automatic=False)
        await asyncio.sleep(0.2)
        assert server.svc.pending_update_info() is not None
        server.busy.clear()
        assert await server.until(lambda: server.installs)


# ===========================================================================
# 2. The hold on new work
# ===========================================================================

class TestHold:

    @pytest.mark.asyncio
    async def test_a_server_that_stays_busy_holds_new_work_after_a_while(self, monkeypatch, fast):
        monkeypatch.setattr(update_module, "HOLD_AFTER_SECONDS", 0.2)
        server = FakeServer(monkeypatch, busy=["Show S01E02 (agy runner)"])
        answer = server.svc.request_update_when_idle("GitHub push abcdef1", "abcdef1")
        assert work_hold.reason() is None, "new work is held before the update has waited at all"
        assert "new work is held" in answer["message"]
        assert await server.until(lambda: work_hold.reason() == update_module.HOLD_MESSAGE)
        info = server.svc.pending_update_info()
        assert info["holding"] is True and info["state"] == "holding"
        assert "No new work starts" in info["message"]
        assert server.installs == []

        server.busy.clear()   # the episode in hand is submitted
        assert await server.until(lambda: server.installs)
        assert server.installs == [update_module.INSTALL_MESSAGE]
        assert work_hold.reason() is not None, "new work started between the install and the restart"

    @pytest.mark.asyncio
    async def test_install_pressed_holds_new_work_at_once(self, monkeypatch, fast):
        server = FakeServer(monkeypatch, busy=["Show S01E02 (agy runner)"])
        answer = server.svc.request_update_when_idle("Install button", hold_now=True, automatic=False)
        assert work_hold.reason() == update_module.HOLD_MESSAGE
        assert answer["pending_update"]["holding"] is True

    @pytest.mark.asyncio
    async def test_a_hold_that_lasts_too_long_is_lifted_and_the_update_waits_for_a_gap(self, monkeypatch, fast):
        monkeypatch.setattr(update_module, "HOLD_MAX_SECONDS", 0.2)
        server = FakeServer(monkeypatch, busy=["Show S01E02: Translating"])
        server.svc.request_update_when_idle("Install button", hold_now=True, automatic=False)
        assert work_hold.reason() is not None
        assert await server.until(lambda: work_hold.reason() is None)
        info = server.svc.pending_update_info()
        assert info["hold_lifted"] is True and info["state"] == "waiting"
        assert "no longer held" in info["message"]
        await asyncio.sleep(0.2)
        assert work_hold.reason() is None, "the hold came back on its own"

        server.busy.clear()   # a natural gap
        assert await server.until(lambda: server.installs)

    @pytest.mark.asyncio
    async def test_cancelling_lifts_the_hold(self, monkeypatch, fast):
        server = FakeServer(monkeypatch, busy=["Show S01E02 (agy runner)"])
        server.svc.request_update_when_idle("Install button", hold_now=True, automatic=False)
        assert work_hold.reason() is not None
        result = server.svc.cancel_pending_update()
        assert result["cancelled"] is True
        assert work_hold.reason() is None
        assert server.svc.pending_update_info() is None
        server.busy.clear()
        await asyncio.sleep(0.2)
        assert server.installs == []

    @pytest.mark.asyncio
    async def test_a_refused_install_ends_the_wait_and_lifts_the_hold(self, monkeypatch, fast):
        refusal = {"success": False, "status": "local_changes", "message": "2 file(s) have uncommitted changes"}
        server = FakeServer(monkeypatch, busy=["Show S01E02 (agy runner)"], results=[refusal])
        server.svc.request_update_when_idle("Install button", hold_now=True, automatic=False)
        server.busy.clear()
        assert await server.until(lambda: server.svc.pending_update_info() is None)
        assert work_hold.reason() is None
        assert server.svc.last_deferred_update()["status"] == "local_changes"
        assert len(server.installs) == 1

    @pytest.mark.asyncio
    async def test_a_failed_fetch_is_tried_again_without_holding_meanwhile(self, monkeypatch, fast):
        monkeypatch.setattr(update_module, "RETRY_SECONDS", 0.3)
        failure = {"success": False, "status": "fetch_failed", "message": "git fetch failed: no network"}
        server = FakeServer(monkeypatch, results=[failure, {"success": True, "status": "restarting"}])
        server.svc.request_update_when_idle("Install button", hold_now=True, automatic=False)
        assert await server.until(lambda: len(server.installs) == 1)
        assert await server.until(lambda: server.svc.pending_update_info()["state"] == "retrying")
        info = server.svc.pending_update_info()
        assert info["holding"] is False and work_hold.reason() is None
        assert "no network" in info["message"]
        assert await server.until(lambda: len(server.installs) == 2)
        assert server.svc.pending_update_info()["state"] == "restarting"

    @pytest.mark.asyncio
    async def test_install_holds_new_work_from_the_busy_check_to_the_restart(self, monkeypatch):
        svc = UpdateService()
        seen = []

        async def install(force, target_tag):
            seen.append(work_hold.reason())
            return {"success": False, "status": "wrong_branch", "message": "on another branch"}

        monkeypatch.setattr(svc, "_install", install)
        result = await svc.apply_update()
        assert result["status"] == "wrong_branch"
        assert seen == [update_module.INSTALL_MESSAGE]
        assert work_hold.reason() is None and svc._updating is False

    @pytest.mark.asyncio
    async def test_a_restart_that_cannot_be_started_lifts_the_hold(self, monkeypatch, tmp_path):
        """The install went in but nothing will restart the server: new work must not stay held for ever."""
        svc = UpdateService()
        monkeypatch.setattr(svc, "has_active_jobs", lambda: False)
        monkeypatch.setattr(svc, "is_git_repo", lambda: True)
        monkeypatch.setattr(svc, "static_dir", tmp_path)
        (tmp_path / "index.html").write_text("ui", encoding="utf-8")

        class Git:
            returncode, stdout, stderr = 0, "", ""

        monkeypatch.setattr(svc, "_run_git", lambda *a, **k: Git())
        monkeypatch.setattr(svc, "trigger_restart", lambda delay_seconds=1.0: False)
        monkeypatch.setattr(update_module, "RELEASE_VERSION_FILE", tmp_path / "release_version.json")
        monkeypatch.setattr(UpdateService, "RELEASE_VERSION_FILE", tmp_path / "release_version.json")
        result = await svc.apply_update()
        assert result["success"] is True and result["status"] == "installed"
        assert "could not restart itself" in result["message"]
        assert work_hold.reason() is None and svc._updating is False

    def test_holds_are_counted_so_one_holder_letting_go_keeps_the_other(self):
        waiting = work_hold.hold("waiting")
        installing = work_hold.hold("installing")
        assert work_hold.reason() == "installing"
        work_hold.release(installing)
        assert work_hold.reason() == "waiting"
        work_hold.release(waiting)
        assert work_hold.reason() is None and work_hold.since() is None


# ===========================================================================
# 3. What counts as work in progress
# ===========================================================================

class TestBusy:

    def test_a_lane_with_an_episode_counts_whatever_the_runner_status_says(self, tmp_path, monkeypatch):
        from services import agent_runner
        runner = _runner(tmp_path)
        monkeypatch.setattr(agent_runner, "_runner", runner)
        runner.status = "waiting"   # another lane is paused: the status says waiting while this one translates
        runner.lanes = {0: {"task": "t1", "project": "Show", "episode": "S01E02"}, 1: {"task": None}}
        runner.held = {"t1": "Show"}
        assert runner.work_in_hand() == ["Show S01E02 (agy runner)"]
        assert "Show S01E02 (agy runner)" in UpdateService().busy_reasons()

        runner.lanes = {0: {"task": None}}   # claimed and held, the lane's fields not set yet
        assert runner.work_in_hand() == ["1 episode(s) being picked up by the agy runner"]

        runner.held = {}
        assert runner.work_in_hand() == []
        with runner.claim_lock:              # a lane is claiming right now
            assert runner.work_in_hand() == ["the agy runner is taking its next episode"]

    def test_a_lane_waiting_for_quota_has_nothing_in_hand(self, tmp_path, monkeypatch):
        from services import agent_runner
        runner = _runner(tmp_path)
        monkeypatch.setattr(agent_runner, "_runner", runner)
        runner.status = "running"
        runner.lanes = {0: {"task": None, "state": "waiting for quota"}}
        assert runner.work_in_hand() == []
        jobs.clear()
        assert UpdateService().has_active_jobs() is False

    def test_worker_tasks_count_before_their_job_is_registered_and_only_once(self, monkeypatch):
        from services import agent_runner, background_worker
        monkeypatch.setattr(agent_runner, "_runner", None)
        jobs.clear()
        hand = {"translations": ["bg_item1"], "extractions": ["extract_item2"], "exports": ["export_item3"]}
        monkeypatch.setattr(background_worker.worker, "work_in_hand", lambda: hand)
        assert UpdateService().busy_reasons() == ["2 Gemini worker task(s) starting", "1 subtitle export(s)"]

        jobs["bg_item1"] = JobStatus(id="bg_item1", status="running", project_name="Show", episode_name="S01E03",
                                     message="Translating scene 2/9")
        reasons = UpdateService().busy_reasons()
        assert reasons[0] == "Show S01E03: Translating scene 2/9"
        assert "1 Gemini worker task(s) starting" in reasons   # the extraction, not the translation again
        jobs.clear()

    def test_one_off_agy_requests_count(self, monkeypatch):
        from services import agent_runner
        monkeypatch.setattr(agent_runner, "_runner", None)
        jobs.clear()
        monkeypatch.setattr(agy_llm, "_live", {object(): {"purpose": "summary"}, object(): {"purpose": "bootstrap"}})
        assert UpdateService().busy_reasons() == ["1 agy request(s) (summary)"]


# ===========================================================================
# 4. The lanes and the worker under a hold
# ===========================================================================

class TestLanesUnderAHold:

    def test_the_runner_finishes_the_episode_in_hand_and_starts_no_new_one(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGY_SLEEP", "1.5")
        first, second = _show(episodes=("S01E01", "S01E02"))
        runner = _runner(tmp_path)
        runner.start({"lanes": 1, "watch": True})
        assert _wait(lambda: len(_prompts(tmp_path)) >= 1)   # the first episode is in the CLI's hands
        token = work_hold.hold(update_module.HOLD_MESSAGE)
        assert runner.work_in_hand(), "the episode in the CLI's hands does not count as work in progress"
        assert _wait(lambda: TranslationQueue().get_item(first)["status"] == "completed")
        assert _wait(lambda: runner.info()["status"] == "waiting")
        assert runner.info()["message"] == update_module.HOLD_MESSAGE
        assert runner.enabled is True, "the hold switched the runner off (it would not resume after the restart)"
        time.sleep(1.0)
        assert TranslationQueue().get_item(second)["status"] == "pending"
        assert len(_prompts(tmp_path)) == 1
        assert runner.work_in_hand() == []

        monkeypatch.setenv("FAKE_AGY_SLEEP", "0")
        work_hold.release(token)
        assert _wait(lambda: TranslationQueue().get_item(second)["status"] == "completed", timeout=60)
        runner.stop(now=True)
        _join(runner)

    @pytest.mark.asyncio
    async def test_the_gemini_worker_claims_nothing_while_held(self, monkeypatch):
        from services import background_worker
        storage.save_global_config({"translation_engine": "model"})
        storage.create_project("Held Show", {"show_name": "Held Show", "target_language": "Greek"})
        item_id = TranslationQueue().enqueue(project_name="Held Show", episode_name="S01E01", priority=1)
        worker = background_worker.BackgroundTranslationWorker()
        started = []

        async def process(item, config):
            started.append(item["id"])

        monkeypatch.setattr(worker, "_process_item", process)
        token = work_hold.hold(update_module.HOLD_MESSAGE)
        worker.start()
        try:
            await asyncio.sleep(0.6)
            assert started == []
            assert TranslationQueue().get_item(item_id)["status"] == "pending"
            work_hold.release(token)
            worker.trigger()
            end = time.monotonic() + 10
            while not started and time.monotonic() < end:
                await asyncio.sleep(0.05)
            assert started == [item_id]
        finally:
            worker.stop()
            await asyncio.gather(worker.running_task, return_exceptions=True)


# ===========================================================================
# 5. Routes and the scheduled check
# ===========================================================================

class TestRoutes:

    @pytest.fixture
    def client(self, monkeypatch):
        # The waiter is the service's business (tested above); here it must not outlive the request's loop,
        # and the status route must not start a real check (git fetch) in the background.
        monkeypatch.setattr(update_service, "_ensure_waiter", lambda: None)
        monkeypatch.setattr(update_service, "schedule_refresh", lambda: False)
        return TestClient(app)

    @pytest.fixture
    def webhook(self, monkeypatch):
        cfg = dict(storage.load_global_config())
        cfg.update({"github_webhook_secret": "s3cret", "auto_update_enabled": True, "github_webhook_enabled": True})
        monkeypatch.setattr(storage, "load_global_config", lambda: cfg)
        return "s3cret"

    def test_a_push_while_busy_is_deferred_with_an_honest_reply(self, client, webhook, monkeypatch):
        monkeypatch.setattr(update_service, "busy_reasons", lambda: ["Show S01E02 (agy runner)"])
        resp = _signed_push(client, webhook)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "deferred"
        assert body["busy"] == ["Show S01E02 (agy runner)"]
        assert "when queue is idle" not in body["message"]
        assert "Waiting for a moment with nothing in progress" in body["message"]
        assert update_service.pending_update_info()["source"] == "GitHub push abcdef1"

    def test_a_second_push_refreshes_the_waiting_update(self, client, webhook, monkeypatch):
        monkeypatch.setattr(update_service, "busy_reasons", lambda: ["Show S01E02 (agy runner)"])
        _signed_push(client, webhook, commit="1111111aaaa")
        first = update_service._pending
        _signed_push(client, webhook, commit="2222222bbbb")
        assert update_service._pending is first
        assert update_service.pending_update_info()["commit"] == "2222222"

    def test_a_push_while_idle_installs(self, client, webhook, monkeypatch):
        monkeypatch.setattr(update_service, "busy_reasons", lambda: [])
        resp = _signed_push(client, webhook)
        assert resp.json()["status"] == "updating"

    def test_a_push_with_automatic_updates_off_only_refreshes_the_check(self, client, webhook, monkeypatch):
        cfg = storage.load_global_config()
        cfg["auto_update_enabled"] = False
        noted = []
        monkeypatch.setattr(update_service, "note_remote_change", lambda: noted.append(True))
        resp = _signed_push(client, webhook)
        assert resp.json()["status"] == "received"
        assert noted == [True] and update_service.pending_update_info() is None

    def test_install_while_busy_waits_holds_new_work_and_can_be_cancelled(self, client, monkeypatch):
        monkeypatch.setattr(update_service, "busy_reasons", lambda: ["Show S01E02 (agy runner)"])
        resp = client.post("/api/system/updates/apply", json={"force": False})
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "deferred" and body["pending_update"]["holding"] is True
        assert work_hold.reason() == update_module.HOLD_MESSAGE

        status = client.get("/api/system/updates/status").json()
        assert status["pending_update"]["source"] == "Install button"

        resp = client.delete("/api/system/updates/pending")
        assert resp.status_code == 200 and resp.json()["cancelled"] is True
        assert work_hold.reason() is None
        status = client.get("/api/system/updates/status").json()
        assert status["pending_update"] is None
        assert status["last_deferred_update"]["status"] == "cancelled"

    def test_an_install_in_progress_cannot_be_cancelled(self, client, monkeypatch):
        monkeypatch.setattr(update_service, "busy_reasons", lambda: ["Show S01E02 (agy runner)"])
        client.post("/api/system/updates/apply", json={"force": False})
        update_service._pending["state"] = "installing"
        resp = client.delete("/api/system/updates/pending")
        assert resp.status_code == 409

    def test_a_release_picked_by_tag_is_not_deferred(self, client, monkeypatch):
        monkeypatch.setattr(update_service, "busy_reasons", lambda: ["Show S01E02 (agy runner)"])
        resp = client.post("/api/system/updates/apply", json={"force": False, "target_tag": "v1.2.0"})
        assert resp.status_code == 409
        assert update_service.pending_update_info() is None

    @pytest.mark.asyncio
    async def test_a_scheduled_check_that_finds_an_update_while_busy_queues_it(self, monkeypatch):
        import main
        storage.save_global_config({"auto_update_enabled": True})
        monkeypatch.setattr(update_service, "busy_reasons", lambda: ["Show S01E02 (agy runner)"])

        async def check(force=False, release_only=None):
            return {"checked": True, "update_available": True, "latest_commit": "abc1234", "branch": "main"}

        monkeypatch.setattr(update_service, "check_for_updates", check)
        monkeypatch.setattr(update_service, "_ensure_waiter", lambda: None)
        await main._auto_update_tick()
        info = update_service.pending_update_info()
        assert info["source"] == "scheduled check" and info["commit"] == "abc1234"
        assert info["holding"] is False


def test_the_activity_snapshot_says_new_work_is_held():
    """The top bar shows why the queue does not move while an update waits (both engines)."""
    from routers import activity
    assert activity.snapshot(force=True)["worker"]["held"] is None
    token = work_hold.hold(update_module.HOLD_MESSAGE)
    try:
        assert activity.snapshot(force=True)["worker"]["held"] == update_module.HOLD_MESSAGE
    finally:
        work_hold.release(token)


@pytest.mark.asyncio
async def test_an_update_requested_while_the_runner_translates_goes_in_between_episodes(tmp_path, monkeypatch, fast):
    """The whole path with the real runner (fake CLI): Install pressed while a lane translates holds new work, the
    episode in hand is finished and submitted, the next one is not started, and then the update is installed."""
    from services import agent_runner
    monkeypatch.setenv("FAKE_AGY_SLEEP", "1.5")
    first, second = _show(episodes=("S01E01", "S01E02"))
    runner = _runner(tmp_path)
    monkeypatch.setattr(agent_runner, "_runner", runner)
    svc = UpdateService()
    installs = []

    async def check(force=False, release_only=None):
        return {"checked": True, "update_available": True}

    async def install(force, target_tag):
        q = TranslationQueue()
        installs.append((q.get_item(first)["status"], q.get_item(second)["status"], work_hold.reason()))
        return {"success": True, "status": "restarting"}

    monkeypatch.setattr(svc, "check_for_updates", check)
    monkeypatch.setattr(svc, "_install", install)
    runner.start({"lanes": 1, "watch": True})
    try:
        end = time.monotonic() + 30
        while not runner.work_in_hand() and time.monotonic() < end:
            await asyncio.sleep(0.05)
        answer = svc.request_update_when_idle("Install button", hold_now=True, automatic=False)
        assert answer["status"] == "deferred" and "Runner Show S01E01 (agy runner)" in answer["busy"]

        end = time.monotonic() + 60
        while not installs and time.monotonic() < end:
            await asyncio.sleep(0.05)
        assert installs == [("completed", "pending", update_module.INSTALL_MESSAGE)]
        assert svc.pending_update_info()["state"] == "restarting"
        await asyncio.sleep(1.0)   # until the process exits, nothing new starts
        assert TranslationQueue().get_item(second)["status"] == "pending"
    finally:
        runner.stop(now=True)
        for t in list(runner.threads):
            await asyncio.to_thread(t.join, 60)
