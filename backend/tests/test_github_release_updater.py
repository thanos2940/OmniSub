"""
TDD Tests for Phase 3: Official GitHub Releases Auto-Updater & Updates UI
========================================================================
Validates that OmbiSub tracks Official GitHub Releases ONLY (not untagged/arbitrary
commits on main), fetches release metadata, installs tags, rebuilds static bundles,
handles tray supervisor exit code 42, and exposes clean system router endpoints.
"""

import builtins
import typing
builtins.Any = typing.Any

import os
import sys
import json
import io
from urllib.error import HTTPError, URLError
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
import pytest
from fastapi.testclient import TestClient

from main import app
from services.update_service import UpdateService, update_service
from utils import paths, storage


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. get_installed_release() tests
# ---------------------------------------------------------------------------

def test_get_installed_release_from_version_file(tmp_path, monkeypatch):
    """When release_version.json exists, get_installed_release parses it."""
    svc = UpdateService()
    version_file = tmp_path / "release_version.json"
    version_file.write_text(
        json.dumps({
            "tag_name": "v2.5.0",
            "commit_sha": "abc1234567890",
            "installed_at": "2026-10-18T10:00:00Z"
        }),
        encoding="utf-8"
    )
    monkeypatch.setattr(svc, "RELEASE_VERSION_FILE", version_file)

    info = svc.get_installed_release()
    assert info["tag_name"] == "v2.5.0"
    assert info["commit_sha"] == "abc1234567890"
    assert info["installed_at"] == "2026-10-18T10:00:00Z"


def test_get_installed_release_fallback_to_git(tmp_path, monkeypatch):
    """When release_version.json is missing, falls back to git describe and commit info."""
    svc = UpdateService()
    version_file = tmp_path / "nonexistent_release_version.json"
    monkeypatch.setattr(svc, "RELEASE_VERSION_FILE", version_file)

    def mock_git(args, timeout=15.0):
        mock_res = MagicMock()
        mock_res.returncode = 0
        cmd = " ".join(args)
        if "describe --tags" in cmd:
            mock_res.stdout = "v2.4.1\n"
        elif "rev-parse HEAD" in cmd:
            mock_res.stdout = "fedcba9876543210\n"
        elif "rev-parse --abbrev-ref HEAD" in cmd:
            mock_res.stdout = "main\n"
        elif "log -1" in cmd:
            mock_res.stdout = "Initial commit|Author|2026-10-18\n"
        else:
            mock_res.stdout = ""
        return mock_res

    monkeypatch.setattr(svc, "_run_git", mock_git)
    monkeypatch.setattr(svc, "is_git_repo", lambda: True)

    info = svc.get_installed_release()
    assert info["tag_name"] == "v2.4.1"
    assert info["commit_sha"] == "fedcba9876543210"


# ---------------------------------------------------------------------------
# 2. fetch_latest_github_release() tests
# ---------------------------------------------------------------------------

def test_fetch_latest_github_release_success(monkeypatch):
    """Parses GitHub /releases/latest JSON payload on HTTP 200."""
    svc = UpdateService()

    release_payload = {
        "tag_name": "v3.0.0",
        "name": "Release v3.0.0 - Major Overhaul",
        "body": "### Changes\n- Add Bazarr priority support\n- Tray helper",
        "published_at": "2026-10-18T12:00:00Z",
        "html_url": "https://github.com/thanos2940/OmbiSub---Google-Seminar-Capstone-Project/releases/tag/v3.0.0",
        "prerelease": False,
        "draft": False,
        "assets": [{"name": "static.zip", "browser_download_url": "https://example.com/static.zip"}],
        "target_commitish": "9999999999999999999999999999999999999999"
    }

    class MockResponse:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass
        def read(self):
            return json.dumps(release_payload).encode("utf-8")

    monkeypatch.setattr("services.update_service.urlopen", lambda req, timeout=10.0: MockResponse())

    rel = svc.fetch_latest_github_release()
    assert rel is not None
    assert rel["tag_name"] == "v3.0.0"
    assert rel["name"] == "Release v3.0.0 - Major Overhaul"
    assert "Bazarr priority" in rel["body"]
    assert rel["published_at"] == "2026-10-18T12:00:00Z"
    assert len(rel["assets"]) == 1
    assert rel["assets"][0]["name"] == "static.zip"


def test_fetch_latest_github_release_404_no_releases(monkeypatch):
    """When GitHub returns HTTP 404 (no releases published), returns None."""
    svc = UpdateService()

    def mock_urlopen_404(req, timeout=10.0):
        raise HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b'{"message": "Not Found"}'))

    monkeypatch.setattr("services.update_service.urlopen", mock_urlopen_404)

    rel = svc.fetch_latest_github_release()
    assert rel is None


def test_fetch_latest_github_release_network_error_fallback_git_tags(monkeypatch):
    """On URLError/timeout, falls back to ls-remote git tags."""
    svc = UpdateService()

    def mock_urlopen_err(req, timeout=10.0):
        raise URLError("Connection refused")

    monkeypatch.setattr("services.update_service.urlopen", mock_urlopen_err)

    def mock_git(args, timeout=15.0):
        mock_res = MagicMock()
        mock_res.returncode = 0
        if "ls-remote" in " ".join(args):
            mock_res.stdout = (
                "aaaa1111\trefs/tags/v2.8.0\n"
                "bbbb2222\trefs/tags/v2.7.0\n"
            )
        else:
            mock_res.stdout = ""
        return mock_res

    monkeypatch.setattr(svc, "_run_git", mock_git)

    rel = svc.fetch_latest_github_release()
    assert rel is not None
    assert rel["tag_name"] == "v2.8.0"


# ---------------------------------------------------------------------------
# 3. check_for_updates() Official Release Semantics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_for_updates_flags_newer_official_release(monkeypatch):
    """Detects newer GitHub release tag (v2.1.0 > installed v2.0.0)."""
    svc = UpdateService()
    monkeypatch.setattr(svc, "is_git_repo", lambda: True)
    monkeypatch.setattr(svc, "get_installed_release", lambda: {
        "tag_name": "v2.0.0",
        "commit_sha": "111111111111",
        "installed_at": "2026-10-01T00:00:00Z"
    })
    monkeypatch.setattr(svc, "fetch_latest_github_release", lambda: {
        "tag_name": "v2.1.0",
        "name": "Release v2.1.0",
        "body": "Changelog details here",
        "published_at": "2026-10-18T10:00:00Z",
        "html_url": "https://github.com/releases/v2.1.0",
        "prerelease": False,
        "draft": False,
        "assets": [],
        "target_commitish": "222222222222"
    })

    status = await svc.check_for_updates(force=True, release_only=True)
    assert status["update_available"] is True
    assert status["release_mode"] == "github_release"
    assert status["latest_release"]["tag_name"] == "v2.1.0"
    assert status["installed_release"]["tag_name"] == "v2.0.0"
    assert "Changelog details" in status["latest_release"]["body"]


@pytest.mark.asyncio
async def test_check_for_updates_ignores_untracked_commits_on_main(monkeypatch):
    """When installed is v2.1.0 and latest release is v2.1.0, untracked commits do NOT trigger update."""
    svc = UpdateService()
    monkeypatch.setattr(svc, "is_git_repo", lambda: True)
    monkeypatch.setattr(svc, "get_installed_release", lambda: {
        "tag_name": "v2.1.0",
        "commit_sha": "aaaa0000",
        "installed_at": "2026-10-18T00:00:00Z"
    })
    monkeypatch.setattr(svc, "fetch_latest_github_release", lambda: {
        "tag_name": "v2.1.0",
        "name": "Release v2.1.0",
        "body": "Already up to date",
        "published_at": "2026-10-18T00:00:00Z",
        "html_url": "https://github.com/releases/v2.1.0",
        "prerelease": False,
        "draft": False,
        "assets": [],
        "target_commitish": "aaaa0000"
    })

    # Even if git rev-list reports commits behind on main
    def mock_git(args, timeout=15.0):
        mock_res = MagicMock()
        mock_res.returncode = 0
        cmd = " ".join(args)
        if "rev-list --count" in cmd:
            mock_res.stdout = "5\n"
        elif "rev-parse" in cmd:
            mock_res.stdout = "bbbb1111\n"
        else:
            mock_res.stdout = ""
        return mock_res

    monkeypatch.setattr(svc, "_run_git", mock_git)

    status = await svc.check_for_updates(force=True, release_only=True)
    assert status["update_available"] is False
    assert status["release_mode"] == "github_release"
    assert status["latest_release"]["tag_name"] == "v2.1.0"


@pytest.mark.asyncio
async def test_check_for_updates_http_404_reports_no_releases_published(monkeypatch):
    """When no releases exist on GitHub (HTTP 404), update_available is False."""
    svc = UpdateService()
    monkeypatch.setattr(svc, "is_git_repo", lambda: True)
    monkeypatch.setattr(svc, "get_installed_release", lambda: {
        "tag_name": "untagged",
        "commit_sha": "current_sha",
        "installed_at": ""
    })
    monkeypatch.setattr(svc, "fetch_latest_github_release", lambda: None)

    status = await svc.check_for_updates(force=True, release_only=True)
    assert status["update_available"] is False
    assert status["release_status"] == "no_releases_published"
    assert status["latest_release"] is None


# ---------------------------------------------------------------------------
# 4. apply_update() Tag Checkout, Requirements, Static & Version File
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_update_release_tag_writes_version_file_and_restarts(tmp_path, monkeypatch):
    """apply_update pulls release tag, checks diffs, writes release_version.json and schedules restart."""
    svc = UpdateService()
    monkeypatch.setattr(svc, "has_active_jobs", lambda: False)
    monkeypatch.setattr(svc, "is_git_repo", lambda: True)

    version_file = tmp_path / "release_version.json"
    monkeypatch.setattr(svc, "RELEASE_VERSION_FILE", version_file)
    monkeypatch.setattr(svc, "CHECK_CACHE_FILE", tmp_path / "update_check.json")
    monkeypatch.setattr(svc, "frontend_dir", tmp_path / "frontend")  # a real build must not replace backend/static
    monkeypatch.setattr(svc, "static_dir", tmp_path / "static")

    calls = []
    def mock_git(args, timeout=15.0):
        cmd = " ".join(args)
        calls.append(cmd)
        mock_res = MagicMock()
        mock_res.returncode = 0
        if "status --porcelain" in cmd:
            mock_res.stdout = ""  # Clean tree
        elif "diff --name-only" in cmd:
            mock_res.stdout = "backend/requirements.txt\nfrontend/src/App.jsx\n"
        elif "rev-parse HEAD" in cmd:
            mock_res.stdout = "target_commit_sha_12345\n"
        else:
            mock_res.stdout = ""
        return mock_res

    monkeypatch.setattr(svc, "_run_git", mock_git)

    pip_called = []
    def mock_subp_run(cmd, *args, **kwargs):
        if "pip" in cmd:
            pip_called.append(cmd)
        mock_res = MagicMock()
        mock_res.returncode = 0
        return mock_res

    monkeypatch.setattr("subprocess.run", mock_subp_run)

    restarted = []
    monkeypatch.setattr(svc, "trigger_restart", lambda delay_seconds=1.5: restarted.append(True))

    res = await svc.apply_update(force=False, target_tag="v2.1.0")
    assert res["success"] is True
    assert res["status"] == "restarting"
    assert len(restarted) == 1

    # Check that git fetched tags and reset to tags/v2.1.0
    assert any("fetch --tags origin --force" in c for c in calls)
    assert any("reset --hard tags/v2.1.0" in c for c in calls)

    # Check pip install was triggered for requirements.txt diff
    assert len(pip_called) >= 1

    # Verify release_version.json was written
    assert version_file.is_file()
    saved = json.loads(version_file.read_text(encoding="utf-8"))
    assert saved["tag_name"] == "v2.1.0"
    assert saved["commit_sha"] == "target_commit_sha_12345"
    assert "installed_at" in saved


def test_trigger_restart_tray_supervisor_exit_code_42(monkeypatch):
    """When OMBISUB_SUPERVISOR=tray, trigger_restart exits with code 42."""
    svc = UpdateService()
    monkeypatch.setenv("OMBISUB_SUPERVISOR", "tray")

    exit_codes = []
    timer_started = []

    class MockTimer:
        def __init__(self, interval, fn):
            self.interval = interval
            self.fn = fn
        def start(self):
            timer_started.append(self)
            # Execute callback to capture exit code
            self.fn()

    monkeypatch.setattr("threading.Timer", MockTimer)
    monkeypatch.setattr("os._exit", lambda code: exit_codes.append(code))

    svc.trigger_restart()
    assert 42 in exit_codes


# ---------------------------------------------------------------------------
# 5. System Router API Endpoints Verification
# ---------------------------------------------------------------------------

def test_system_router_endpoints(client, monkeypatch):
    """Verify /api/system/version, /api/system/check-update, /api/system/update, /api/system/auto-update."""
    from services.update_service import update_service

    monkeypatch.setattr(update_service, "get_installed_release", lambda: {
        "tag_name": "v2.2.0",
        "commit_sha": "abcdef123456",
        "installed_at": "2026-10-18T00:00:00Z"
    })

    async def mock_check(force=False, release_only=None):
        return {
            "update_available": True,
            "release_mode": "github_release",
            "latest_release": {
                "tag_name": "v2.3.0",
                "name": "Release v2.3.0",
                "body": "Exciting new features",
                "published_at": "2026-10-18T10:00:00Z",
                "html_url": "https://github.com/releases/v2.3.0",
            },
            "installed_release": {
                "tag_name": "v2.2.0",
                "commit_sha": "abcdef123456",
                "installed_at": "2026-10-18T00:00:00Z"
            }
        }

    monkeypatch.setattr(update_service, "check_for_updates", mock_check)
    monkeypatch.setattr(update_service, "schedule_refresh", lambda: False)  # no network from the status endpoint

    # 1. /api/system/version
    res_ver = client.get("/api/system/version")
    assert res_ver.status_code == 200
    data_ver = res_ver.json()
    assert data_ver["tag_name"] == "v2.2.0"
    assert data_ver["installed_release"]["tag_name"] == "v2.2.0"

    # 2. /api/system/updates/check (the old duplicate /api/system/check-update is gone)
    assert client.post("/api/system/check-update").status_code in (404, 405)
    res_check = client.post("/api/system/updates/check")
    assert res_check.status_code == 200
    data_check = res_check.json()
    assert data_check["update_available"] is True
    assert data_check["latest_release"]["tag_name"] == "v2.3.0"
    assert "Exciting new features" in data_check["release_notes"]

    # 3. /api/system/auto-update
    res_auto_get = client.get("/api/system/auto-update")
    assert res_auto_get.status_code == 200
    assert "auto_update_enabled" in res_auto_get.json()

    res_auto_post = client.post("/api/system/auto-update", json={"enabled": True, "channel": "releases"})
    assert res_auto_post.status_code == 200
    assert res_auto_post.json()["auto_update_enabled"] is True

    # 4. /api/system/updates/apply (the old duplicate /api/system/update is gone)
    async def mock_apply(force=False, target_tag=None):
        return {"success": True, "status": "restarting", "message": "Server restarting..."}

    monkeypatch.setattr(update_service, "apply_update", mock_apply)
    assert client.post("/api/system/update", json={}).status_code in (404, 405)
    res_up = client.post("/api/system/updates/apply", json={"force": False, "target_tag": "v2.3.0"})
    assert res_up.status_code == 200
    assert res_up.json()["status"] == "restarting"


@pytest.mark.asyncio
async def test_apply_update_release_tag_refuses_to_drop_local_commits(monkeypatch):
    """A checkout with commits the release does not contain is not reset onto the tag."""
    svc = UpdateService()
    monkeypatch.setattr(svc, "has_active_jobs", lambda: False)
    monkeypatch.setattr(svc, "is_git_repo", lambda: True)

    calls = []

    def mock_git(args, timeout=15.0):
        cmd = " ".join(args)
        calls.append(cmd)
        res = MagicMock()
        res.returncode = 0
        res.stdout = "3\n" if cmd == "rev-list --count tags/v2.1.0..HEAD" else ""
        return res

    monkeypatch.setattr(svc, "_run_git", mock_git)

    res = await svc.apply_update(force=False, target_tag="v2.1.0")
    assert res["success"] is False
    assert res["status"] == "local_commits"
    assert not any(c.startswith("reset --hard") for c in calls)
    assert svc._updating is False
