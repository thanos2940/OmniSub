"""
Library audit
=============
``audit_library`` runs the project audit over every project and keeps what needs attention. The
API runs it as a background job (one at a time, progress per project, the last result kept on
disk) and ``scripts/agent_task.py audit --all`` starts and follows that job - or, with no server
running, does the same work in its own process.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app
from services import library_audit_job
from services.agent_task_service import AgentTaskService
from utils import paths, storage
from utils.translation_queue import TranslationQueue

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "agent_task.py"

SOURCES = ["Where were you last night?", "I was at the station.", "He never came.", "Did he answer?", "No.",
           "We should go to his place.", "What?"]
GREEK = ["Πού ήσουν χθες το βράδυ;", "Ήμουν στο σταθμό.", "Δεν ήρθε ποτέ.", "Απάντησε;", "Όχι.",
         "Πρέπει να πάμε σπίτι του.", "Τι;"]
GARBLED = [t.encode("cp1253").decode("cp1252") for t in GREEK]
LEAKY = list(GREEK)
LEAKY[1] = "1| It happened, like, right here! Just this morning!"
LEAKY[2] = "[prev] Θα ακούσουμε τι έχετε να πείτε στην υπηρεσία."


@pytest.fixture(autouse=True)
def isolated_state():
    def wipe():
        with TranslationQueue()._conn() as conn:
            conn.execute("DELETE FROM translation_queue")
            conn.commit()
    wipe()
    library_audit_job.wait(30)
    library_audit_job.reset()
    yield
    library_audit_job.wait(30)
    library_audit_job.reset()
    wipe()


@pytest.fixture
def service(tmp_path):
    return AgentTaskService(tasks_dir=tmp_path / "agent_tasks")


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def exports(monkeypatch):
    calls = []

    def fake_export(project, episode, cues):
        calls.append((project, episode))
        return f"/media/{project}/{episode}.el.srt"

    monkeypatch.setattr(AgentTaskService, "_export", staticmethod(fake_export))
    return calls


def _episode(project, episode, translations, **meta):
    cues = [{"index": i + 1, "start": f"00:00:{i * 2 + 1:02d},000", "end": f"00:00:{i * 2 + 2:02d},000",
             "original": src, "translated": tgt, "translations": {"el": tgt}}
            for i, (src, tgt) in enumerate(zip(SOURCES, translations))]
    storage.save_episode(project, episode, cues)
    m = storage.load_episode_metadata(project, episode) or {}
    m.update(translated=True, translation_status="completed", **meta)
    storage.save_episode_metadata(project, episode, m)


def _srt(translations):
    return "".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},900\n{t}\n\n" for i, t in enumerate(translations, 1))


@pytest.fixture
def library(tmp_path):
    """Four projects: one imported with mojibake (episode and file), one with leaked text, one clean, one empty."""
    for name in ("Clean Show", "Empty Show", "Leaky Show", "Mojibake Show"):
        storage.create_project(name, {"show_name": name, "target_language": "Greek"})
    agent = {"translation_source": "agent", "last_translated_at": "2026-10-01T10:00:00"}
    _episode("Clean Show", "S01E01", GREEK, **agent)
    _episode("Leaky Show", "S01E01", LEAKY, **agent)
    _episode("Leaky Show", "S01E02", GREEK, **agent)
    target = tmp_path / "media" / "Mojibake.Show.S01E01.el.srt"
    target.parent.mkdir()
    target.write_bytes(_srt(GARBLED).encode("utf-8"))
    _episode("Mojibake Show", "S01E01", GARBLED, arr_has_target=True, arr_target_path=str(target))
    return target


# --------------------------------------------------------------------------- audit_library

def test_audit_library_keeps_only_what_needs_attention(service, library, exports):
    res = service.audit_library()
    assert res["success"] is True
    assert [p["project_name"] for p in res["projects"]] == ["Leaky Show", "Mojibake Show"]
    leaky, mojibake = res["projects"]
    assert [e["episode_name"] for e in leaky["episodes"]] == ["S01E01"]  # S01E02 is fine and left out
    # the numbered English leak is an untranslated line to the audit anyway; mojibake is not
    assert leaky["broken"] == ["S01E01"] and leaky["incomplete"] == ["S01E01"] and leaky["misplaced"] == []
    assert leaky["episodes"][0]["broken"] == {"numbered": 1, "context_echo": 1}
    assert mojibake["broken"] == ["S01E01"] and mojibake["episodes"][0]["broken"] == {"mojibake": 7}
    assert mojibake["incomplete"] == []
    assert not {"repaired_lines", "broken_cleared", "cleared_lines", "disk_repaired", "reexported", "enqueue"} & set(leaky)
    assert res["totals"] == {
        "projects": 4, "episodes_checked": 4, "incomplete": 1, "misplaced": 0, "misplaced_imported": 0,
        "broken": 2, "broken_lines": 9, "repaired_lines": 0, "cleared_lines": 0, "broken_cleared": 0, "enqueued": 0}
    assert exports == []  # looking changes nothing
    assert library.read_bytes().decode("utf-8") == _srt(GARBLED)


def test_audit_library_repairs(service, library, exports):
    res = service.audit_library(repair=True)
    leaky, mojibake = res["projects"]
    assert leaky["repaired_lines"] == 0 and leaky["broken_cleared"] == 2 and leaky["reexported"] == ["S01E01"]
    assert leaky["incomplete"] == ["S01E01"]  # the cleared lines are gaps now
    assert mojibake["repaired_lines"] == 7 and mojibake["broken_cleared"] == 0
    assert mojibake["disk_repaired"] == [{"episode": "S01E01", "path": str(library), "lines": 7}]
    assert mojibake["reexported"] == [] and "enqueue" not in mojibake
    assert library.read_bytes().decode("utf-8") == _srt(GREEK)
    assert exports == [("Leaky Show", "S01E01")]
    t = res["totals"]
    assert (t["repaired_lines"], t["broken_cleared"], t["cleared_lines"], t["enqueued"]) == (7, 2, 2, 0)
    assert t["incomplete"] == 1 and t["broken"] == 2 and t["broken_lines"] == 9


def test_audit_library_requeues(service, library, exports):
    res = service.audit_library(requeue=True)
    leaky = next(p for p in res["projects"] if p["project_name"] == "Leaky Show")
    assert leaky["enqueue"]["episodes"] == ["S01E01"] and leaky["enqueue"]["enqueued_count"] == 1
    assert res["totals"]["enqueued"] == 1
    assert [(p["project_name"], p["episode_name"]) for p in service.list_pending_tasks()] == [("Leaky Show", "S01E01")]


def test_cleared_lines_total_counts_misplaced_and_broken_alike(service, library, exports, monkeypatch):
    real = AgentTaskService.audit_project

    def with_misplaced(self, name, **kw):
        res = real(self, name, **kw)
        if name == "Clean Show":
            res["cleared_lines"] = 5
        return res

    monkeypatch.setattr(AgentTaskService, "audit_project", with_misplaced)
    totals = service.audit_library(repair=True)["totals"]
    assert totals["cleared_lines"] == 5 + 2 and totals["broken_cleared"] == 2


def test_audit_library_reports_progress_and_survives_a_failing_project(service, library, exports, monkeypatch):
    real = AgentTaskService.audit_project

    def flaky(self, name, **kw):
        if name == "Leaky Show":
            raise RuntimeError("boom")
        return real(self, name, **kw)

    monkeypatch.setattr(AgentTaskService, "audit_project", flaky)
    calls = []

    def progress(done, total, name):
        calls.append((done, total, name))
        raise ValueError("a broken callback must not stop the audit")

    res = service.audit_library(progress=progress)
    assert calls == [(1, 4, "Clean Show"), (2, 4, "Empty Show"), (3, 4, "Leaky Show"), (4, 4, "Mojibake Show")]
    assert res["projects"][0] == {"project_name": "Leaky Show", "error": "boom"}
    assert res["projects"][1]["project_name"] == "Mojibake Show" and res["projects"][1]["broken"] == ["S01E01"]
    assert res["totals"]["projects"] == 4 and res["totals"]["broken"] == 1


def test_audit_library_of_an_empty_library(service):
    res = service.audit_library()
    assert res["projects"] == [] and res["totals"]["projects"] == 0


# --------------------------------------------------------------------------- the background job

def _wait_finished(client, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get("/api/agent/library-audit").json()
        if state["status"] in ("done", "error"):
            return state
        time.sleep(0.05)
    pytest.fail("the library audit did not finish")


def test_job_idle_state(client):
    state = client.get("/api/agent/library-audit").json()
    assert state == {"status": "idle", "started_at": None, "finished_at": None,
                     "options": {"repair": False, "requeue": False, "misplaced": True},
                     "progress": {"done": 0, "total": 0, "project": None}, "result": None, "error": None}


def test_job_runs_and_keeps_its_result(client, library, exports):
    started = client.post("/api/agent/library-audit", json={"repair": True})
    assert started.status_code == 200
    body = started.json()
    assert body["status"] == "running" and body["started_at"] and body["finished_at"] is None
    assert body["options"] == {"repair": True, "requeue": False, "misplaced": True}

    state = _wait_finished(client)
    assert state["status"] == "done" and state["error"] is None and state["finished_at"]
    assert state["options"] == {"repair": True, "requeue": False, "misplaced": True}
    assert state["progress"] == {"done": 4, "total": 4, "project": "Mojibake Show"}
    assert [p["project_name"] for p in state["result"]["projects"]] == ["Leaky Show", "Mojibake Show"]
    assert state["result"]["totals"]["repaired_lines"] == 7 and state["result"]["totals"]["broken_cleared"] == 2
    assert library.read_bytes().decode("utf-8") == _srt(GREEK)

    # the last finished state is kept on disk: a restarted server still has it
    saved = json.loads((paths.DATA_DIR / "library_audit.json").read_text(encoding="utf-8"))
    assert saved["status"] == "done" and saved["result"]["totals"] == state["result"]["totals"]
    library_audit_job.reset()
    assert client.get("/api/agent/library-audit").json() == state

    # and a new run can follow
    again = client.post("/api/agent/library-audit", json={"misplaced": False})
    assert again.status_code == 200 and again.json()["options"] == {"repair": False, "requeue": False, "misplaced": False}
    assert _wait_finished(client)["result"]["totals"]["repaired_lines"] == 0


def test_job_accepts_an_empty_request(client, library):
    assert client.post("/api/agent/library-audit").status_code == 200
    state = _wait_finished(client)
    assert state["options"] == {"repair": False, "requeue": False, "misplaced": True}
    assert state["result"]["totals"]["broken"] == 2


def test_only_one_run_at_a_time(client, monkeypatch):
    entered, gate = threading.Event(), threading.Event()

    def slow(self, requeue=False, repair=False, misplaced=True, progress=None):
        entered.set()
        gate.wait(30)
        progress(1, 2, "Some Show")
        return {"success": True, "projects": [], "totals": {"projects": 2}}

    monkeypatch.setattr(AgentTaskService, "audit_library", slow)
    assert client.post("/api/agent/library-audit", json={"repair": True}).status_code == 200
    assert entered.wait(10)
    busy = client.post("/api/agent/library-audit", json={"requeue": True})
    assert busy.status_code == 409
    assert busy.json()["status"] == "running" and busy.json()["options"]["repair"] is True  # the run that is going
    assert client.get("/api/agent/library-audit").json()["status"] == "running"
    gate.set()
    state = _wait_finished(client)
    assert state["status"] == "done" and state["progress"] == {"done": 1, "total": 2, "project": "Some Show"}


def test_a_run_that_fails_is_reported(client, monkeypatch):
    def broken(self, **kwargs):
        raise RuntimeError("the library is on fire")

    monkeypatch.setattr(AgentTaskService, "audit_library", broken)
    assert client.post("/api/agent/library-audit").status_code == 200
    state = _wait_finished(client)
    assert state["status"] == "error" and state["error"] == "the library is on fire" and state["result"] is None
    assert json.loads((paths.DATA_DIR / "library_audit.json").read_text(encoding="utf-8"))["status"] == "error"


def test_a_damaged_state_file_reads_as_idle(client):
    (paths.DATA_DIR / "library_audit.json").write_text("{not json", encoding="utf-8")
    assert client.get("/api/agent/library-audit").json()["status"] == "idle"


def test_the_project_audit_endpoint_takes_repair(client, library, exports):
    res = client.post("/api/agent/audit", json={"project_name": "Mojibake Show", "repair": True})
    assert res.status_code == 200
    body = res.json()
    assert body["repaired_lines"] == 7 and body["broken"] == ["S01E01"] and len(body["disk_repaired"]) == 1
    plain = client.post("/api/agent/audit", json={"project_name": "Leaky Show"}).json()
    assert plain["broken"] == ["S01E01"] and "repaired_lines" not in plain


# --------------------------------------------------------------------------- the CLI

def _cli(*args, env_extra=None):
    env = {**os.environ, "OMNISUB_DATA_DIR": str(paths.DATA_DIR), "OMNISUB_AGENT_OFFLINE": "1",
           "PYTHONIOENCODING": "utf-8", **(env_extra or {})}
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, encoding="utf-8",
                          env=env, cwd=str(ROOT), timeout=240)


def test_cli_audit_needs_a_project_or_all():
    for args in (["audit"], ["audit", "-p", "Some Show", "--all"]):
        res = _cli(*args)
        assert res.returncode == 2 and "either --project NAME or --all" in res.stderr


def test_cli_audit_all_offline_reports_then_repairs(library):
    res = _cli("audit", "--all")
    assert res.returncode == 0, res.stderr
    assert "[4/4] Mojibake Show" in res.stdout and "Library audit: 4 project(s), 4 episode(s) checked" in res.stdout
    assert "Leaky Show" in res.stdout and "mojibake 7" in res.stdout and "numbered 1" in res.stdout
    assert "audit --all --repair" in res.stdout
    assert library.read_bytes().decode("utf-8") == _srt(GARBLED)

    res = _cli("audit", "--all", "--repair", "--no-misplaced")
    assert res.returncode == 0, res.stderr
    assert "Repaired 7 line(s), cleared 2" in res.stdout and "1 subtitle file(s) repaired in place" in res.stdout
    assert library.read_bytes().decode("utf-8") == _srt(GREEK)


def test_cli_audit_one_project_offline(library):
    res = _cli("audit", "-p", "Mojibake Show", "--repair")
    assert res.returncode == 0, res.stderr
    assert "1 episode(s) with broken text" in res.stdout and "mojibake 7" in res.stdout
    assert "Repaired 7 broken line(s), cleared 0" in res.stdout and "7 line(s) fixed in" in res.stdout
    as_json = _cli("audit", "-p", "Leaky Show", "--json")
    assert json.loads(as_json.stdout)["broken"] == ["S01E01"]


def test_cli_audit_all_follows_the_server_job(library, exports, monkeypatch):
    uvicorn = pytest.importorskip("uvicorn")
    real = AgentTaskService.audit_library

    def slow(self, **kwargs):
        inner = kwargs["progress"]

        def progress(done, total, name):
            inner(done, total, name)
            time.sleep(1.2)  # long enough for the CLI's two-second poll to see it mid-run

        return real(self, **{**kwargs, "progress": progress})

    monkeypatch.setattr(AgentTaskService, "audit_library", slow)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 30
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started
        port = server.servers[0].sockets[0].getsockname()[1]
        env = {"OMNISUB_AGENT_OFFLINE": "", "OMNISUB_API_URL": f"http://127.0.0.1:{port}"}
        res = _cli("audit", "--all", "--repair", env_extra=env)
    finally:
        server.should_exit = True
        thread.join(30)
    assert res.returncode == 0, res.stdout + res.stderr
    assert re.search(r"^\[\d/4\] \w+ Show$", res.stdout, re.M)  # progress, as polled
    assert "Library audit: 4 project(s)" in res.stdout and "Repaired 7 line(s), cleared 2" in res.stdout
    assert library.read_bytes().decode("utf-8") == _srt(GREEK)
