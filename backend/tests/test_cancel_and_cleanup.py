"""
Cancelling, giving work back, and cleaning up after it
======================================================
A cancel anywhere in the app (the queue's Cancel and Delete, "cancel everything", a job's cancel, the
Autopilot when a file is deleted) reaches whoever works on the episode: the worker's task is aborted, an
agent's workspace is closed, and a runner lane on it lets go at once instead of finishing, submitting and
bringing the row back as completed. The runner waits for an item's retry time, does not make the other
lanes wait while it prepares an episode, and gives up visibly. Processes the server starts are stopped
with it, a restart does not cancel what it interrupts, and finished workspaces do not pile up.
"""

import asyncio
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from utils import paths, storage
from utils.translation_queue import TranslationQueue, KIND_TRANSLATION, PRIORITY_MANUAL
from services import agent_cli, agent_runner, agy_llm, queue_service
from services import agent_task_service as agent_task_module
from services.agent_task_service import AgentTaskService
from services.agent_runner import AgentRunner

FAKE = [sys.executable, str(Path(__file__).with_name("fake_agent_cli.py"))]
REPO_ROOT = Path(__file__).resolve().parents[2]
LINES = [
    "Where were you last night?", "I was at the station, waiting for Johnny.", "He never came.",
    "That's not like him at all.", "I know. I called him three times.", "Did he answer?",
    "No. His phone was off.", "We should go to his place.",
]


@pytest.fixture(autouse=True)
def empty_queue():
    def wipe():
        with TranslationQueue()._conn() as conn:
            conn.execute("DELETE FROM translation_queue")
            conn.commit()
    wipe()
    yield
    wipe()


@pytest.fixture(autouse=True)
def fake_env(monkeypatch, tmp_path):
    for var in ("FAKE_AGY_SLEEP", "FAKE_AGY_SKIP_AT", "FAKE_AGY_QUOTA_MODELS", "FAKE_AGY_BLOCK_MODELS",
                "FAKE_AGY_BLOCK_WORD", "FAKE_AGY_CUT", "FAKE_AGY_LINGER", "FAKE_AGY_STALL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FAKE_AGY_LOG", str(tmp_path / "calls"))
    monkeypatch.setitem(agent_runner.DEFAULT_CONFIG, "bootstrap", False)
    storage.save_global_config({"tm_enabled": False})  # no embedding model in these tests


@pytest.fixture
def service(tmp_path, monkeypatch):
    """The process's agent task service, on this test's folder (queue_service and the routers find it by name)."""
    svc = AgentTaskService(tasks_dir=tmp_path / "agent_tasks")
    monkeypatch.setattr(agent_task_module, "agent_task_service", svc)
    return svc


@pytest.fixture
def runner(tmp_path, service, monkeypatch):
    """The process's runner (a cancel reaches it), driving the fake agy."""
    r = AgentRunner(service=service, exe_override={"antigravity": FAKE}, state_dir=tmp_path / "state")
    monkeypatch.setattr(agent_runner, "_runner", r)
    yield r
    r.stop(now=True)
    for t in list(r.threads):
        t.join(timeout=30)


def _cues(lines, start=1000):
    cues, t = [], start
    for i, text in enumerate(lines):
        a, b = t, t + 1800
        cues.append({"index": i + 1, "start": f"00:{a // 60000:02d}:{a // 1000 % 60:02d},{a % 1000:03d}",
                     "end": f"00:{b // 60000:02d}:{b // 1000 % 60:02d},{b % 1000:03d}", "original": text})
        t = b + 300
    return cues


def _show(name="Cancel Show", episodes=("S01E01",), lines=LINES):
    storage.create_project(name, {"show_name": name, "target_language": "Greek"})
    ids = []
    for ep in episodes:
        storage.save_episode(name, ep, _cues(lines))
        ids.append(TranslationQueue().enqueue(project_name=name, episode_name=ep, priority=PRIORITY_MANUAL,
                                              kind=KIND_TRANSLATION))
    return ids


def _greek(project="Cancel Show", episode="S01E01"):
    return [(c.get("translations") or {}).get("el") for c in storage.load_episode_data(project, episode)]


def _join(runner, timeout=60):
    for t in list(runner.threads):
        t.join(timeout=timeout)
    assert not runner.running(), "the runner did not finish"


def _wait(condition, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


def _log(runner):
    return "\n".join(e["text"] for e in runner.info()["log"])


def _translate_work_file(folder):
    work = Path(folder) / "translation.txt"
    text = work.read_text(encoding="utf-8")
    work.write_text(re.sub(r"^(\d+)\| (.*)$", lambda m: f"{m.group(1)}| Ελ: {m.group(2)}", text, flags=re.M),
                    encoding="utf-8")


# ===========================================================================
# A cancel reaches the agent runner
# ===========================================================================

class TestCancelReachesTheRunner:

    def test_cancelling_an_episode_a_lane_is_translating_stops_it_and_saves_nothing(self, runner, monkeypatch):
        monkeypatch.setenv("FAKE_AGY_SLEEP", "30")   # the request is still in flight when the cancel comes
        [queue_id] = _show()
        runner.start({"lanes": 1, "fallback_models": []})
        assert _wait(lambda: 0 in runner.procs), "the lane never sent its request"
        started = time.time()
        assert queue_service.cancel_item(queue_id) is True
        _join(runner, timeout=20)
        assert time.time() - started < 15, "the lane waited for the reply instead of letting go"
        assert TranslationQueue().get_item(queue_id)["status"] == "cancelled"   # not brought back as completed
        assert all(el is None for el in _greek())                              # nothing saved or exported
        log = _log(runner)
        assert "taken back from the queue" in log
        assert " failed (" not in log and runner.consecutive_errors == 0       # not the model's failure

    def test_a_submit_after_the_row_was_cancelled_saves_nothing(self, service):
        [queue_id] = _show()
        prep = service.prepare_task_workspace(queue_item_id=queue_id)
        _translate_work_file(prep["workspace_dir"])        # the agent did the work...
        TranslationQueue().cancel_all("active")           # ...while the row was cancelled behind its back
        res = service.submit_task_result(prep["task_id"])
        assert (res["success"], res["status"]) == (False, "cancelled")
        assert TranslationQueue().get_item(queue_id)["status"] == "cancelled"
        assert all(el is None for el in _greek())
        assert service._read_manifest(Path(prep["workspace_dir"]))["status"] == "cancelled"

    def test_a_submit_after_the_row_was_removed_saves_nothing(self, service):
        [queue_id] = _show()
        prep = service.prepare_task_workspace(queue_item_id=queue_id)
        _translate_work_file(prep["workspace_dir"])
        TranslationQueue().clear("all")                   # "Clear the whole queue"
        assert service.submit_task_result(prep["task_id"])["status"] == "cancelled"
        assert all(el is None for el in _greek())
        assert TranslationQueue().get_item(queue_id) is None

    def test_cancel_all_honours_its_status_and_closes_agent_workspaces(self, service):
        ids = _show(episodes=("S01E01", "S01E02"))
        prep = service.prepare_task_workspace(queue_item_id=ids[0])   # S01E01 is held by an agent
        from main import app
        client = TestClient(app)
        res = client.post("/api/queue/cancel-all", params={"status": "pending"})
        assert res.status_code == 200 and res.json()["target_status"] == "pending"
        assert TranslationQueue().get_item(ids[0])["status"] == "running"     # waiting items only
        assert TranslationQueue().get_item(ids[1])["status"] == "cancelled"
        res = client.post("/api/queue/cancel-all")                            # default: everything active
        assert res.status_code == 200 and res.json()["cancelled"] >= 0
        assert TranslationQueue().get_item(ids[0])["status"] == "cancelled"
        assert service._read_manifest(Path(prep["workspace_dir"]))["status"] == "cancelled"
        _translate_work_file(prep["workspace_dir"])
        assert service.submit_task_result(prep["task_id"])["success"] is False

    def test_release_puts_an_orphaned_claim_back_in_the_queue(self, service):
        [queue_id] = _show()
        prep = service.prepare_task_workspace(queue_item_id=queue_id)   # claimed, and nobody works on it
        assert TranslationQueue().get_item(queue_id)["status"] == "running"
        assert queue_service.release_item(queue_id) is True
        row = TranslationQueue().get_item(queue_id)
        assert row["status"] == "pending" and not json.loads(row["options"] or "{}").get("agent_task_id")
        assert service._read_manifest(Path(prep["workspace_dir"]))["status"] == "released"
        assert queue_service.release_item(queue_id) is False   # only a running row can be released
        from main import app
        res = TestClient(app).post(f"/api/queue/{queue_id}/release")
        assert res.status_code == 409 and "cancel it instead" in res.json()["detail"]


# ===========================================================================
# How the runner takes work
# ===========================================================================

class TestRunnerClaims:

    def test_the_runner_waits_for_an_items_retry_time(self, runner):
        ids = _show(episodes=("S01E01", "S01E02"))
        later = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        TranslationQueue().update_status(ids[0], next_retry_at=later)   # e.g. its media share was offline
        runner.start({"lanes": 1, "fallback_models": []})
        _join(runner)
        rows = {r["episode_name"]: r for r in TranslationQueue().get_all(limit=10)}
        assert rows["S01E02"]["status"] == "completed"
        assert (rows["S01E01"]["status"], rows["S01E01"]["attempts"]) == ("pending", 0)
        assert all(el is None for el in _greek(episode="S01E01"))

    def test_a_slow_workspace_build_does_not_hold_up_the_other_lanes(self, runner, service, monkeypatch):
        _show("Show A")
        _show("Show B")
        b_claimed = threading.Event()
        waited = []
        original = service.build_claimed_workspace

        def build(item):
            if item["project_name"] == "Show A":   # its subtitle is pulled out of the video first (minutes)
                waited.append(b_claimed.wait(timeout=20))
            res = original(item)
            if item["project_name"] == "Show B":
                b_claimed.set()
            return res

        monkeypatch.setattr(service, "build_claimed_workspace", build)
        runner.start({"lanes": 2, "fallback_models": []})
        _join(runner)
        assert waited == [True], "the other lane could not claim while Show A was being prepared"
        assert all(el for el in _greek("Show A")) and all(el for el in _greek("Show B"))

    def test_per_show_counts_an_episode_whose_workspace_is_being_built(self, runner, service, monkeypatch):
        ids = _show(episodes=("S01E01", "S01E02"))
        seen = []
        original = service.build_claimed_workspace

        def build(item):
            if item["episode_name"] == "S01E01":
                time.sleep(1.0)   # meanwhile the other lane looks for work
                seen.append(TranslationQueue().get_item(ids[1])["status"])
            return original(item)

        monkeypatch.setattr(service, "build_claimed_workspace", build)
        runner.start({"lanes": 2, "per_show": 1, "fallback_models": []})
        _join(runner)
        assert seen == ["pending"]
        assert all(r["status"] == "completed" for r in TranslationQueue().get_all(limit=10))

    def test_an_episode_being_prepared_is_not_an_orphan_and_a_cancel_meanwhile_holds(self, runner, service,
                                                                                      monkeypatch):
        from routers import activity
        [queue_id] = _show()
        building, go_on = threading.Event(), threading.Event()
        original = service.build_claimed_workspace

        def build(item):   # e.g. the subtitle is being pulled out of the video
            building.set()
            go_on.wait(timeout=20)
            return original(item)

        monkeypatch.setattr(service, "build_claimed_workspace", build)
        runner.start({"lanes": 1, "fallback_models": []})
        assert building.wait(timeout=20)
        [row] = [r for r in activity.snapshot(force=True)["queue"] if r["id"] == queue_id]
        assert row["status"] == "running" and "prepared by the runner" in row["message"]   # not "orphaned"
        [lane] = [lane for lane in runner.info()["lanes"] if lane.get("task")]
        assert (lane["state"], lane["episode"]) == (agent_runner.PREPARING_STATE, "S01E01")
        assert queue_service.release_item(queue_id) is False   # not an orphan: nothing to release
        assert queue_service.cancel_item(queue_id) is True
        go_on.set()
        _join(runner)
        assert TranslationQueue().get_item(queue_id)["status"] == "cancelled"
        assert all(el is None for el in _greek())
        assert "cancelled while it was being prepared" in _log(runner)

    def test_every_request_runs_in_one_folder_outside_omnisub(self, runner, tmp_path):
        _show(episodes=("S01E01", "S01E02"))
        runner.start({"lanes": 1, "fallback_models": []})
        _join(runner)
        cwds = {p.read_text(encoding="utf-8") for p in (tmp_path / "calls").glob("call_*.cwd")}
        assert len(cwds) == 1, cwds   # the same folder for every request: the CLI's preamble stays cacheable
        cwd = Path(cwds.pop()).resolve()
        assert cwd == agent_cli.call_dir().resolve()
        for tree in (REPO_ROOT, Path(paths.DATA_DIR).resolve()):
            assert tree not in (cwd, *cwd.parents)


# ===========================================================================
# The worker across a restart
# ===========================================================================

class TestWorkerShutdown:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("how, expected", [("shutdown", "pending"), ("cancel", "cancelled")])
    async def test_a_restart_does_not_cancel_the_translation_it_interrupts(self, monkeypatch, how, expected):
        from services import background_worker, project_bootstrap
        from services.worker.translation_item_worker import process_translation_item

        entered = asyncio.Event()

        async def hang(*args, **kwargs):
            entered.set()
            await asyncio.sleep(3600)

        monkeypatch.setattr(project_bootstrap, "ensure_project_context", hang)
        worker = background_worker.BackgroundTranslationWorker()
        monkeypatch.setattr(background_worker, "worker", worker)
        storage.create_project("Worker Show", {"show_name": "Worker Show", "target_language": "Greek"})
        storage.save_episode("Worker Show", "S01E01", _cues(LINES), {"line_count": len(LINES), "has_source_sub": True})
        queue = TranslationQueue()
        queue.enqueue(project_name="Worker Show", episode_name="S01E01", priority=PRIORITY_MANUAL, kind=KIND_TRANSLATION)
        item = queue.claim_next()
        task = asyncio.create_task(process_translation_item(queue, item, {}))
        worker.active_tasks[item["id"]] = task
        await asyncio.wait_for(entered.wait(), 30)
        if how == "shutdown":
            worker._shutdown_event.set()   # what worker.stop() does before cancelling its tasks
            task.cancel()
        else:
            assert queue_service.cancel_item(item["id"]) is True
        await asyncio.wait_for(task, 30)
        assert queue.get_item(item["id"])["status"] == expected


# ===========================================================================
# Processes and files the server leaves behind
# ===========================================================================

class TestCleanup:

    def test_shutdown_stops_one_off_agy_requests_in_flight(self, runner, monkeypatch):
        monkeypatch.setenv("FAKE_AGY_SLEEP", "60")
        outcome = {}

        def ask():
            try:
                agy_llm.complete("## Line to translate (1)\n1| Hello.\n", purpose="test", timeout=120)
            except agy_llm.AgyError as exc:
                outcome["kind"] = exc.kind

        thread = threading.Thread(target=ask)
        thread.start()
        assert _wait(lambda: len(agy_llm._live) == 1), "the request never started"
        started = time.time()
        agent_runner.stop_all()
        thread.join(timeout=20)
        assert not thread.is_alive() and time.time() - started < 15
        assert outcome == {"kind": "stopped"}   # not handed on to the fallback model
        assert agy_llm._live == {}

    @pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX; Windows stops the tree with taskkill /T")
    def test_a_stopped_cli_takes_the_helpers_it_started_with_it(self, tmp_path, monkeypatch):
        launcher = tmp_path / "launcher.py"
        launcher.write_text(
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])  # shares our stdout\n"
            "time.sleep(60)\n", encoding="utf-8")
        monkeypatch.setattr(agent_cli, "IDLE_TIMEOUT", 1)
        started = time.time()
        reply = agent_cli.call_model("antigravity", [sys.executable, str(launcher)], "", "hello", tmp_path,
                                     threading.Event())
        assert time.time() - started < 20, "a helper of the CLI kept its output open"
        assert reply.failure == "error"

    def test_closed_workspaces_are_pruned_after_a_week_open_ones_never(self, service):
        ids = _show(episodes=("S01E01", "S01E02", "S01E03"))
        old_done, recent_done, old_open = (service.prepare_task_workspace(queue_item_id=i) for i in ids)
        for prep in (old_done, recent_done):
            folder = Path(prep["workspace_dir"])
            manifest = service._read_manifest(folder)
            manifest["status"] = "completed"
            service._write_manifest(folder, manifest)
        week_ago = time.time() - 8 * 86400
        for prep in (old_done, old_open):
            os.utime(Path(prep["workspace_dir"]) / "manifest.json", (week_ago, week_ago))
        assert service.prune_closed_workspaces() == 1
        assert not Path(old_done["workspace_dir"]).exists()
        assert Path(recent_done["workspace_dir"]).is_dir() and Path(old_open["workspace_dir"]).is_dir()


def test_a_lane_building_the_show_context_is_not_shown_as_translating():
    from routers import activity
    assert activity._lane_message({"state": agent_runner.BOOTSTRAP_STATE, "model": "m"}).startswith("Building the show's")
    assert activity._lane_message({"state": "translating 3 line(s)", "model": "m"}) == "Translating with m"


def test_without_a_usable_temp_folder_requests_run_in_the_data_folder(monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("a file where the temp folder should be", encoding="utf-8")
    monkeypatch.setattr(agent_cli.tempfile, "gettempdir", lambda: str(blocker))
    folder = agent_cli.call_dir()
    assert folder == Path(paths.DATA_DIR) / "agy_calls" and folder.is_dir()
