"""
Tests for Auto-Update, GitHub Webhook, and Version Management
============================================================
"""

import hmac
import hashlib
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from main import app
from services.update_service import UpdateService, update_service
from routers.webhooks import _verify_github_signature
from utils.jobs_manager import jobs, JobStatus


@pytest.fixture
def client():
    return TestClient(app)


# ===========================================================================
# 1. UpdateService Unit Tests
# ===========================================================================

def test_get_current_version_info():
    """Verify local version reading succeeds in repo."""
    info = update_service.get_current_version_info()
    assert "commit_hash" in info
    assert "branch" in info
    assert "commit_short" in info
    assert info["is_git"] is True
    assert len(info["commit_short"]) in (7, 40)


@pytest.mark.asyncio
async def test_check_for_updates_when_behind(monkeypatch):
    """Verify check_for_updates correctly identifies incoming commits."""
    svc = UpdateService()

    def mock_run_git(args, timeout=15.0):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        cmd = " ".join(args)
        if "rev-parse HEAD" in cmd:
            mock_proc.stdout = "1111111111111111111111111111111111111111\n"
        elif "rev-parse origin/main" in cmd:
            mock_proc.stdout = "2222222222222222222222222222222222222222\n"
        elif "rev-parse --abbrev-ref HEAD" in cmd:
            mock_proc.stdout = "main\n"
        elif "rev-list --count" in cmd:
            mock_proc.stdout = "2\n"
        elif "log HEAD..origin/main" in cmd:
            mock_proc.stdout = (
                "2222222|Add cool feature|Thanos|2026-09-17\n"
                "3333333|Fix audio sync issue|Thanos|2026-09-16\n"
            )
        else:
            mock_proc.stdout = ""
        return mock_proc

    monkeypatch.setattr(svc, "_run_git", mock_run_git)
    monkeypatch.setattr(svc, "is_git_repo", lambda: True)

    status = await svc.check_for_updates(force=True)
    assert status["update_available"] is True
    assert status["commits_behind"] == 2
    assert status["current_commit"] == "1111111"
    assert status["latest_commit"] == "2222222"
    assert len(status["changelog"]) == 2
    assert status["changelog"][0]["message"] == "Add cool feature"


@pytest.mark.asyncio
async def test_check_for_updates_when_up_to_date(monkeypatch):
    """Verify check_for_updates detects clean up-to-date status."""
    svc = UpdateService()

    def mock_run_git(args, timeout=15.0):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        cmd = " ".join(args)
        if "rev-parse HEAD" in cmd or "rev-parse origin/main" in cmd:
            mock_proc.stdout = "1111111111111111111111111111111111111111\n"
        elif "rev-parse --abbrev-ref HEAD" in cmd:
            mock_proc.stdout = "main\n"
        elif "rev-list --count" in cmd:
            mock_proc.stdout = "0\n"
        else:
            mock_proc.stdout = ""
        return mock_proc

    monkeypatch.setattr(svc, "_run_git", mock_run_git)
    monkeypatch.setattr(svc, "is_git_repo", lambda: True)

    status = await svc.check_for_updates(force=True)
    assert status["update_available"] is False
    assert status["commits_behind"] == 0
    assert len(status["changelog"]) == 0


def test_has_active_jobs():
    """Verify active job detection blocks disruptive operations."""
    svc = UpdateService()

    # Clear jobs
    jobs.clear()
    assert svc.has_active_jobs() is False

    # Add terminal job
    jobs["done_job"] = JobStatus(id="done_job", status="completed")
    assert svc.has_active_jobs() is False

    # Add active running job
    jobs["running_job"] = JobStatus(id="running_job", status="running")
    assert svc.has_active_jobs() is True

    # Cleanup
    jobs.clear()


@pytest.mark.asyncio
async def test_apply_update_blocks_when_active_jobs(monkeypatch):
    """Verify apply_update returns busy status when jobs are running."""
    svc = UpdateService()
    monkeypatch.setattr(svc, "has_active_jobs", lambda: True)

    res = await svc.apply_update(force=False)
    assert res["success"] is False
    assert res["status"] == "busy"


@pytest.mark.asyncio
async def test_apply_update_executes_when_idle(monkeypatch):
    """Verify apply_update executes git pull and restart when idle."""
    svc = UpdateService()
    monkeypatch.setattr(svc, "has_active_jobs", lambda: False)
    monkeypatch.setattr(svc, "is_git_repo", lambda: True)

    run_git_mock = MagicMock()
    run_git_mock.returncode = 0
    run_git_mock.stdout = ""
    monkeypatch.setattr(svc, "_run_git", lambda *args, **kwargs: run_git_mock)

    restart_called = []
    monkeypatch.setattr(svc, "trigger_restart", lambda delay_seconds=1.0: restart_called.append(True))

    res = await svc.apply_update(force=False)
    assert res["success"] is True
    assert res["status"] == "restarting"
    assert len(restart_called) == 1


# ===========================================================================
# 2. Webhook Signature & Route Tests
# ===========================================================================

def test_verify_github_signature():
    """Verify HMAC SHA-256 signature checking."""
    secret = "super_secret_key_123"
    payload = b'{"ref": "refs/heads/main"}'

    valid_sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    invalid_sig = "sha256=wrong_signature"

    assert _verify_github_signature(payload, valid_sig, secret) is True
    assert _verify_github_signature(payload, invalid_sig, secret) is False
    assert _verify_github_signature(payload, None, secret) is False
    # No secret configured allows request
    assert _verify_github_signature(payload, None, "") is True


def test_webhook_ping_event(client):
    """Verify ping event is handled with pong."""
    resp = client.post("/api/webhooks/github", headers={"X-GitHub-Event": "ping"}, json={"zen": "Design for failure."})
    assert resp.status_code == 200
    assert resp.json()["status"] == "pong"


def test_webhook_push_invalid_secret(client, monkeypatch):
    """Verify webhook rejects push event with bad signature when secret is set."""
    from utils import storage
    cfg = storage.load_global_config()
    cfg["github_webhook_secret"] = "my_secret"
    monkeypatch.setattr(storage, "load_global_config", lambda: cfg)

    resp = client.post(
        "/api/webhooks/github",
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": "sha256=invalid",
        },
        json={"ref": "refs/heads/main"}
    )
    assert resp.status_code == 401


def test_webhook_push_valid_secret(client, monkeypatch):
    """Verify webhook accepts push event and schedules update."""
    from utils import storage
    secret = "secret_12345"
    cfg = storage.load_global_config()
    cfg["github_webhook_secret"] = secret
    cfg["auto_update_enabled"] = True
    cfg["github_webhook_enabled"] = True
    monkeypatch.setattr(storage, "load_global_config", lambda: cfg)

    payload_dict = {
        "ref": "refs/heads/main",
        "head_commit": {"id": "abcdef12345", "message": "Updated translations"},
        "sender": {"login": "thanos2940"}
    }
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

    # Mock apply_update so test doesn't actually restart server
    applied = []
    async def mock_apply(force=False):
        applied.append(True)
        return {"success": True}

    monkeypatch.setattr("routers.webhooks.update_service.has_active_jobs", lambda: False)
    monkeypatch.setattr("routers.webhooks.update_service.apply_update", mock_apply)

    resp = client.post(
        "/api/webhooks/github",
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": sig,
            "Content-Type": "application/json",
        },
        content=payload_bytes
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "updating"


# ===========================================================================
# 3. System API Router Tests
# ===========================================================================

def test_system_updates_status_endpoint(client):
    """Verify GET /api/system/updates/status returns valid schema."""
    resp = client.get("/api/system/updates/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "current_commit" in data
    assert "update_available" in data
    assert "auto_update_enabled" in data
    assert "auto_update_interval_hours" in data


def _fake_git(outputs, calls):
    """_run_git stand-in: canned stdout per git subcommand, recording the order."""
    def run(args, timeout=15.0):
        calls.append(args[0])
        result = MagicMock()
        result.returncode = 0
        result.stdout = outputs.get(args[0], "")
        result.stderr = ""
        return result
    return run


@pytest.mark.asyncio
async def test_apply_update_refuses_to_erase_local_changes_even_when_forced(monkeypatch):
    """The update is a hard reset; a working checkout with edits must survive it."""
    svc = UpdateService()
    monkeypatch.setattr(svc, "has_active_jobs", lambda: False)
    monkeypatch.setattr(svc, "is_git_repo", lambda: True)
    calls = []
    monkeypatch.setattr(svc, "_run_git", _fake_git({"status": " M backend/main.py\n M README.md\n"}, calls))

    res = await svc.apply_update(force=True)

    assert res["success"] is False
    assert res["status"] == "local_changes"
    assert "2 file(s)" in res["message"]
    assert "fetch" not in calls and "reset" not in calls


@pytest.mark.asyncio
async def test_apply_update_refuses_to_drop_unpushed_commits(monkeypatch):
    svc = UpdateService()
    monkeypatch.setattr(svc, "has_active_jobs", lambda: False)
    monkeypatch.setattr(svc, "is_git_repo", lambda: True)
    calls = []
    monkeypatch.setattr(svc, "_run_git", _fake_git({"rev-list": "3\n"}, calls))

    res = await svc.apply_update(force=False)

    assert res["status"] == "local_commits"
    assert "reset" not in calls
    assert svc._updating is False, "a refused update must not leave the service locked"


def test_apply_endpoint_reports_local_changes_as_conflict(client, monkeypatch):
    from services.update_service import update_service

    async def refuse(force=False):
        return {"success": False, "status": "local_changes", "message": "2 file(s) have uncommitted changes"}

    monkeypatch.setattr(update_service, "apply_update", refuse)
    response = client.post("/api/system/updates/apply", json={"force": True})
    assert response.status_code == 409
    assert "uncommitted" in response.json()["detail"]
