"""The updater's `main` channel (follow origin/main), the cached check result behind the status endpoint,
and the UI build swap. Offline: git, pip, npm and the GitHub API are faked."""

import asyncio
import json
import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from services import update_service as us
from services.update_service import UpdateService
from utils import storage

SHA_A = "a" * 40
SHA_B = "b" * 40


def git_result(stdout="", returncode=0, stderr=""):
    res = MagicMock()
    res.returncode, res.stdout, res.stderr = returncode, stdout, stderr
    return res


class FakeGit:
    """_run_git stand-in: answers by the git subcommand line and records every call."""

    def __init__(self, answers=None, fail=()):
        self.answers = answers or {}
        self.fail = fail
        self.calls = []

    def __call__(self, args, timeout=15.0):
        cmd = " ".join(args)
        self.calls.append(cmd)
        if any(cmd.startswith(f) for f in self.fail):
            return git_result("", 1, "boom")
        for prefix, out in self.answers.items():
            if cmd.startswith(prefix):
                return git_result(out)
        return git_result("")


@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = UpdateService()
    monkeypatch.setattr(s, "is_git_repo", lambda: True)
    monkeypatch.setattr(s, "RELEASE_VERSION_FILE", tmp_path / "release_version.json")
    monkeypatch.setattr(s, "CHECK_CACHE_FILE", tmp_path / "update_check.json")
    monkeypatch.setattr(s, "frontend_dir", tmp_path / "frontend")
    monkeypatch.setattr(s, "static_dir", tmp_path / "backend" / "static")
    monkeypatch.setattr(s, "backend_dir", tmp_path / "backend")
    monkeypatch.setattr(s, "has_active_jobs", lambda: False)
    monkeypatch.setattr(s, "fetch_latest_github_release", lambda: pytest.fail("the main channel must not call GitHub"))
    return s


def set_config(monkeypatch, **values):
    monkeypatch.setattr(storage, "load_global_config", lambda: dict(values))


# ------------------------------------------------------------------ channel choice

def test_configured_channel_wins(svc, monkeypatch):
    svc._run_git = FakeGit({"tag --list": "v1\n"})
    set_config(monkeypatch, update_channel="main")
    assert svc.resolve_channel() == "main"
    set_config(monkeypatch, update_channel="releases")
    assert svc.resolve_channel() == "releases"


def test_auto_channel_is_main_until_the_repo_has_release_tags(svc, monkeypatch):
    set_config(monkeypatch)
    svc._run_git = FakeGit({"tag --list": ""})
    assert svc.resolve_channel() == "main"
    svc._run_git = FakeGit({"tag --list": "v1.0.0\n"})
    assert svc.resolve_channel() == "releases"


# ------------------------------------------------------------------ check on main

@pytest.mark.asyncio
async def test_main_check_reports_behind_ahead_and_commit_subjects(svc, monkeypatch):
    set_config(monkeypatch, update_channel="main")
    log = "".join(f"c{i:06d}|Fix thing {i}|Thanos|2026-10-19\n" for i in range(20))
    git = FakeGit({
        "rev-parse HEAD": SHA_A + "\n",
        "rev-parse origin/main": SHA_B + "\n",
        "rev-parse --abbrev-ref HEAD": "main\n",
        "rev-list --count HEAD..origin/main": "25\n",
        "rev-list --count origin/main..HEAD": "1\n",
        "log HEAD..origin/main": log,
    })
    svc._run_git = git

    status = await svc.check_for_updates(force=True)

    assert status["channel"] == "main"
    assert status["update_available"] is True
    assert status["behind"] == 25 and status["ahead"] == 1
    assert len(status["commits"]) == 20 and status["commits"][0]["message"] == "Fix thing 0"
    assert "fetch origin main" in git.calls
    assert any("--max-count=20" in c for c in git.calls)


# ------------------------------------------------------------------ apply on main

def apply_git(**extra):
    answers = {
        "status --porcelain": "",
        "rev-parse --abbrev-ref HEAD": "main\n",
        "rev-list --count origin/main..HEAD": "0\n",
        "diff --name-only": "backend/requirements.lock.txt\nfrontend/src/App.jsx\n",
        "rev-parse HEAD": SHA_B + "\n",
    }
    answers.update(extra)
    return FakeGit(answers)


@pytest.mark.asyncio
async def test_apply_main_resets_to_origin_main_installs_deps_builds_ui_and_restarts(svc, monkeypatch):
    set_config(monkeypatch, update_channel="main")
    git = apply_git()
    svc._run_git = git
    steps = []
    monkeypatch.setattr(svc, "_pip_install", lambda: steps.append("pip"))
    monkeypatch.setattr(svc, "_build_frontend", lambda: steps.append("ui"))
    monkeypatch.setattr(svc, "trigger_restart", lambda delay_seconds=1.0: steps.append("restart"))

    res = await svc.apply_update()

    assert res["success"] is True and res["status"] == "restarting"
    assert "fetch origin main" in git.calls
    assert "reset --hard origin/main" in git.calls
    assert steps == ["pip", "ui", "restart"]
    saved = json.loads(svc.RELEASE_VERSION_FILE.read_text(encoding="utf-8"))
    assert saved["commit_sha"] == SHA_B and saved["channel"] == "main" and saved["installed_at"]


@pytest.mark.asyncio
async def test_apply_main_skips_pip_and_ui_when_neither_changed(svc, monkeypatch):
    set_config(monkeypatch, update_channel="main")
    svc._run_git = apply_git(**{"diff --name-only": "backend/services/foo.py\n"})
    (svc.static_dir).mkdir(parents=True)
    (svc.static_dir / "index.html").write_text("<html></html>")
    steps = []
    monkeypatch.setattr(svc, "_pip_install", lambda: steps.append("pip"))
    monkeypatch.setattr(svc, "_build_frontend", lambda: steps.append("ui"))
    monkeypatch.setattr(svc, "trigger_restart", lambda delay_seconds=1.0: steps.append("restart"))

    await svc.apply_update()

    assert steps == ["restart"]


@pytest.mark.asyncio
async def test_apply_main_builds_the_ui_when_there_is_no_build_at_all(svc, monkeypatch):
    set_config(monkeypatch, update_channel="main")
    svc._run_git = apply_git(**{"diff --name-only": "backend/services/foo.py\n"})
    steps = []
    monkeypatch.setattr(svc, "_build_frontend", lambda: steps.append("ui"))
    monkeypatch.setattr(svc, "trigger_restart", lambda delay_seconds=1.0: None)

    await svc.apply_update()

    assert steps == ["ui"]


@pytest.mark.asyncio
async def test_apply_main_refuses_local_commits_dirty_tree_and_other_branches(svc, monkeypatch):
    set_config(monkeypatch, update_channel="main")
    monkeypatch.setattr(svc, "trigger_restart", lambda delay_seconds=1.0: pytest.fail("must not restart"))

    svc._run_git = apply_git(**{"rev-list --count origin/main..HEAD": "2\n"})
    res = await svc.apply_update()
    assert res["status"] == "local_commits"
    assert not any(c.startswith("reset") for c in svc._run_git.calls)

    svc._run_git = apply_git(**{"status --porcelain": " M backend/main.py\n"})
    res = await svc.apply_update()
    assert res["success"] is False and res["reason"] == "dirty_working_tree"

    svc._run_git = apply_git(**{"rev-parse --abbrev-ref HEAD": "feature-x\n"})
    res = await svc.apply_update()
    assert res["status"] == "wrong_branch"
    assert not any(c.startswith("reset") for c in svc._run_git.calls)
    assert svc._updating is False


@pytest.mark.asyncio
async def test_apply_main_stops_when_fetch_fails(svc, monkeypatch):
    set_config(monkeypatch, update_channel="main")
    svc._run_git = FakeGit({"status --porcelain": "", "rev-parse --abbrev-ref HEAD": "main\n"}, fail=("fetch",))
    res = await svc.apply_update()
    assert res["status"] == "fetch_failed"
    assert not any(c.startswith("reset") for c in svc._run_git.calls)


# ------------------------------------------------------------------ the UI build swap

def fake_build(monkeypatch, svc, tmp_path, fail_on=None):
    """npm present; `npm run build` writes frontend/dist/index.html."""
    ran = []
    monkeypatch.setattr(us.shutil, "which", lambda name: "npm" if name == "npm" else None)

    def run(cmd, **kwargs):
        ran.append(cmd)
        res = MagicMock(returncode=0, stderr="")
        if cmd == fail_on:
            res.returncode, res.stderr = 1, "npm exploded"
        elif cmd == ["npm", "run", "build"]:
            dist = svc.frontend_dir / "dist"
            dist.mkdir(parents=True, exist_ok=True)
            (dist / "index.html").write_text("new build")
        return res

    monkeypatch.setattr(us.subprocess, "run", run)
    return ran


def test_build_frontend_swaps_the_new_build_in_and_drops_the_old(svc, tmp_path, monkeypatch):
    svc.static_dir.mkdir(parents=True)
    (svc.static_dir / "index.html").write_text("old build")
    ran = fake_build(monkeypatch, svc, tmp_path)

    assert svc._build_frontend() is True

    assert ran == [["npm", "ci"], ["npm", "run", "build"]]
    assert (svc.static_dir / "index.html").read_text() == "new build"
    assert not svc.static_dir.with_name("static.old").exists()
    assert not svc.static_dir.with_name("static.new").exists()


def test_failed_build_keeps_the_previous_ui(svc, tmp_path, monkeypatch):
    svc.static_dir.mkdir(parents=True)
    (svc.static_dir / "index.html").write_text("old build")
    fake_build(monkeypatch, svc, tmp_path, fail_on=["npm", "run", "build"])

    assert svc._build_frontend() is False
    assert (svc.static_dir / "index.html").read_text() == "old build"


def test_build_frontend_without_npm_changes_nothing(svc, monkeypatch):
    monkeypatch.setattr(us.shutil, "which", lambda name: None)
    monkeypatch.setattr(us.subprocess, "run", lambda *a, **k: pytest.fail("no npm: nothing may run"))
    assert svc._build_frontend() is False


def test_swap_puts_the_old_build_back_when_the_rename_fails(svc, tmp_path, monkeypatch):
    svc.static_dir.mkdir(parents=True)
    (svc.static_dir / "index.html").write_text("old build")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("new build")

    real_rename = us.Path.rename

    def flaky_rename(self, target):
        if self.name == "static.new":
            raise OSError("in use")
        return real_rename(self, target)

    monkeypatch.setattr(us.Path, "rename", flaky_rename)
    with pytest.raises(OSError):
        svc._swap_static(dist)
    assert (svc.static_dir / "index.html").read_text() == "old build"


# ------------------------------------------------------------------ the cached check result

@pytest.mark.asyncio
async def test_check_result_is_cached_on_disk_and_survives_a_restart(svc, monkeypatch, tmp_path):
    set_config(monkeypatch, update_channel="main")
    svc._run_git = FakeGit({"rev-parse HEAD": SHA_A + "\n", "rev-parse origin/main": SHA_B + "\n",
                            "rev-parse --abbrev-ref HEAD": "main\n", "rev-list --count HEAD..origin/main": "3\n",
                            "log HEAD..origin/main": "c1|New thing|T|2026-10-19\n"})
    await svc.check_for_updates(force=True)
    assert svc.CHECK_CACHE_FILE.is_file()

    fresh = UpdateService()                      # "after a restart": no memory, same file and same commit
    fresh.CHECK_CACHE_FILE = svc.CHECK_CACHE_FILE
    fresh.is_git_repo = lambda: True
    fresh._run_git = FakeGit({"rev-parse HEAD": SHA_A + "\n", "rev-parse --abbrev-ref HEAD": "main\n"})
    cached = fresh.cached_status()
    assert cached["checked"] is True and cached["behind"] == 3
    assert not any(c.startswith("fetch") for c in fresh._run_git.calls)


def test_cached_status_never_touches_the_network(svc, monkeypatch):
    set_config(monkeypatch, update_channel="main")
    git = FakeGit({"rev-parse HEAD": SHA_A + "\n", "rev-parse --abbrev-ref HEAD": "main\n"})
    svc._run_git = git
    monkeypatch.setattr(us, "urlopen", lambda *a, **k: pytest.fail("network"))

    status = svc.cached_status()

    assert status["checked"] is False and status["update_available"] is False
    assert status["release_status"] == "not_checked"
    assert not any(c.startswith(("fetch", "ls-remote")) for c in git.calls)


@pytest.mark.asyncio
async def test_cache_of_another_commit_or_channel_is_not_served(svc, monkeypatch):
    set_config(monkeypatch, update_channel="main")
    svc._run_git = FakeGit({"rev-parse HEAD": SHA_A + "\n", "rev-parse origin/main": SHA_B + "\n",
                            "rev-list --count HEAD..origin/main": "1\n", "rev-parse --abbrev-ref HEAD": "main\n"})
    await svc.check_for_updates(force=True)
    assert svc.cached_status()["checked"] is True

    svc._run_git = FakeGit({"rev-parse HEAD": SHA_B + "\n", "rev-parse --abbrev-ref HEAD": "main\n"})  # updated since
    assert svc.cached_status()["checked"] is False


@pytest.mark.asyncio
async def test_plain_check_serves_a_fresh_cache_and_refetches_a_stale_one(svc, monkeypatch):
    set_config(monkeypatch, update_channel="main")
    git = FakeGit({"rev-parse HEAD": SHA_A + "\n", "rev-parse origin/main": SHA_A + "\n",
                   "rev-parse --abbrev-ref HEAD": "main\n", "rev-list --count": "0\n"})
    svc._run_git = git
    await svc.check_for_updates(force=True)
    fetches = lambda: sum(c.startswith("fetch") for c in git.calls)  # noqa: E731
    assert fetches() == 1

    await svc.check_for_updates(force=False)
    assert fetches() == 1, "a fresh result is served without fetching"

    svc._cached_status["checked_at"] = time.time() - us.CHECK_MAX_AGE_SECONDS - 5
    await svc.check_for_updates(force=False)
    assert fetches() == 2, "an old result is refreshed"


@pytest.mark.asyncio
async def test_background_refresh_is_single_flight_and_rate_limited(svc, monkeypatch):
    set_config(monkeypatch, update_channel="main")
    svc._run_git = FakeGit({"rev-parse HEAD": SHA_A + "\n", "rev-parse origin/main": SHA_A + "\n",
                            "rev-parse --abbrev-ref HEAD": "main\n", "rev-list --count": "0\n"})
    checks = []
    real_check = svc.check_for_updates

    async def counting_check(force=False, release_only=None):
        checks.append(force)
        await asyncio.sleep(0)
        return await real_check(force=force, release_only=release_only)

    monkeypatch.setattr(svc, "check_for_updates", counting_check)

    assert svc.schedule_refresh() is True          # nothing cached yet: refresh in the background
    assert svc.schedule_refresh() is False         # one is already running
    await svc._refresh_task
    assert checks == [True]
    assert svc.schedule_refresh() is False         # fresh now; and retried at most every CHECK_RETRY_SECONDS


@pytest.mark.asyncio
async def test_a_failed_background_refresh_is_not_retried_at_once(svc, monkeypatch):
    set_config(monkeypatch, update_channel="main")
    svc._run_git = FakeGit({"rev-parse HEAD": SHA_A + "\n", "rev-parse --abbrev-ref HEAD": "main\n"}, fail=("fetch",))
    monkeypatch.setattr(us, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert svc.schedule_refresh() is True
    await svc._refresh_task
    assert svc.is_stale()                           # the failed fetch left no cache ...
    assert svc.schedule_refresh() is False          # ... but the next attempt waits (GitHub rate limits)


def test_status_endpoint_serves_the_cache_without_waiting_for_a_check(svc, monkeypatch):
    from main import app
    set_config(monkeypatch, update_channel="main")
    svc._run_git = FakeGit({"rev-parse HEAD": SHA_A + "\n", "rev-parse --abbrev-ref HEAD": "main\n"})
    monkeypatch.setattr(us, "update_service", svc)
    import routers.system as system_router
    monkeypatch.setattr(system_router, "update_service", svc)
    refreshes = []
    monkeypatch.setattr(svc, "schedule_refresh", lambda: refreshes.append(True) or True)

    async def no_network(*a, **k):
        pytest.fail("the plain status endpoint must not run a check")

    monkeypatch.setattr(svc, "check_for_updates", no_network)

    resp = TestClient(app).get("/api/system/updates/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["channel"] == "main" and body["checked"] is False and body["stale"] is True
    assert refreshes == [True]


def test_explicit_check_endpoint_runs_the_check(svc, monkeypatch):
    from main import app
    import routers.system as system_router
    monkeypatch.setattr(system_router, "update_service", svc)
    called = []

    async def check(force=False, release_only=None):
        called.append(force)
        return {"channel": "main", "update_available": True, "behind": 2, "commits": [{"hash": "x", "message": "Fix"}]}

    monkeypatch.setattr(svc, "check_for_updates", check)
    body = TestClient(app).post("/api/system/updates/check").json()
    assert called == [True]
    assert body["release_notes"] == "- Fix" and body["stale"] is False
