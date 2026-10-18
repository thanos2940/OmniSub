"""
Tests for Phase 1 Tasks 1.2 & 1.3:
Security Hardening, Atomic Locks, Lifespan Config & Safe Auto-Updater (F-03, F-06, F-13, F-15, D-03).
"""

import builtins
import typing
builtins.Any = typing.Any

import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app, lifespan
from routers.schemas import SettingsRequest
from services.agent_task_service import agent_task_service
from services.update_service import UpdateService, update_service
from utils import storage
from utils.storage_modules import config_store, project_store


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Arbitrary File Read in _resolve_content (F-06)
# ---------------------------------------------------------------------------

def test_resolve_content_security_default_blocks_file_reads(tmp_path):
    """When allow_file_path=False (default), it treats strings as literal subtitle text."""
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("SUPER_SECRET_PAYLOAD", encoding="utf-8")

    # Path exists on disk, but allow_file_path is False by default
    result = agent_task_service._resolve_content(str(secret_file))
    # It must return the string literal path, NOT the contents of secret.txt!
    assert result == str(secret_file)
    assert "SUPER_SECRET_PAYLOAD" not in (result or "")


def test_resolve_content_blocks_sensitive_files(tmp_path):
    """Even if allow_file_path=True, sensitive files (.env, config.json, *.db) are blocked."""
    # Create fake sensitive files in tmp_path
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text('{"api_key": "stolen_key"}', encoding="utf-8")

    env_file = tmp_path / ".env"
    env_file.write_text('SECRET=compromised', encoding="utf-8")

    db_file = tmp_path / "app.db"
    db_file.write_text('sqlite header', encoding="utf-8")

    for f in (cfg_file, env_file, db_file):
        res = agent_task_service._resolve_content(str(f), allow_file_path=True, allowed_dir=tmp_path)
        assert res == str(f)
        assert "stolen_key" not in (res or "")
        assert "compromised" not in (res or "")
        assert "sqlite header" not in (res or "")


def test_resolve_content_blocks_paths_outside_allowed_dirs(tmp_path):
    """Files outside allowed_dir, tempdir, or agent_workspace are not read."""
    unauthorized_dir = tmp_path / "other"
    unauthorized_dir.mkdir()
    unauthorized_file = unauthorized_dir / "unauthorized.srt"
    unauthorized_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nLeak", encoding="utf-8")

    allowed_dir = tmp_path / "workspace"
    allowed_dir.mkdir()

    res = agent_task_service._resolve_content(
        str(unauthorized_file),
        allow_file_path=True,
        allowed_dir=allowed_dir,
    )
    # Outside allowed_dir and not in temp/agent_workspace
    assert res == str(unauthorized_file)


def test_resolve_content_allows_safe_workspace_or_temp_files(tmp_path):
    """Safe files inside allowed_dir or tempdir are read when allow_file_path=True."""
    allowed_dir = tmp_path / "workspace"
    allowed_dir.mkdir()
    safe_sub = allowed_dir / "safe.srt"
    safe_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello Greek", encoding="utf-8")

    res = agent_task_service._resolve_content(
        str(safe_sub),
        allow_file_path=True,
        allowed_dir=allowed_dir,
    )
    assert res == "1\n00:00:01,000 --> 00:00:02,000\nHello Greek"


# ---------------------------------------------------------------------------
# 2. Redact webhook_secret in /api/health/full (F-06)
# ---------------------------------------------------------------------------

def test_health_full_redacts_webhook_secret(client):
    """Verify /api/health/full redacts webhook_secret and reports configured flag."""
    secret = "classified-secret-token-987"
    storage.save_global_config({"webhook_secret": secret})

    resp = client.get("/api/health/full")
    assert resp.status_code == 200
    data = resp.json()

    # Secret must never be in payload or URLs
    assert secret not in resp.text
    webhooks = data.get("webhooks", {})
    assert "webhook_secret" not in webhooks
    assert webhooks.get("webhook_secret_configured") is True
    assert webhooks.get("sonarr_url") == "/api/webhook/sonarr"
    assert webhooks.get("radarr_url") == "/api/webhook/radarr"

    # When secret is empty
    storage.save_global_config({"webhook_secret": ""})
    resp_empty = client.get("/api/health/full")
    assert resp_empty.status_code == 200
    assert resp_empty.json()["webhooks"]["webhook_secret_configured"] is False


# ---------------------------------------------------------------------------
# 3. Atomic & Locked Metadata/Config Writes (F-13)
# ---------------------------------------------------------------------------

def test_atomic_project_metadata_concurrent_writes(tmp_path, monkeypatch):
    """Multiple threads saving project metadata under lock do not corrupt file."""
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path / "projects")
    project_name = "LockedProject"
    storage.create_project(project_name)

    lock = project_store._get_project_lock(project_name)
    assert isinstance(lock, type(threading.RLock()))

    def write_worker(idx):
        for step in range(5):
            meta = storage.load_project_metadata(project_name) or {}
            meta[f"thread_{idx}_step_{step}"] = True
            storage.save_project_metadata(project_name, meta)

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(write_worker, i) for i in range(8)]
        for f in futures:
            f.result()

    final_meta = storage.load_project_metadata(project_name)
    assert isinstance(final_meta, dict)
    assert final_meta.get("show_name") == project_name


def test_atomic_global_config_concurrent_writes(tmp_path, monkeypatch):
    """Concurrent updates to global config merge and write atomically under _config_lock."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(storage, "CONFIG_FILE", cfg_file)

    def config_worker(idx):
        for step in range(5):
            storage.save_global_config({f"key_{idx}_{step}": f"val_{idx}_{step}"})

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(config_worker, i) for i in range(8)]
        for f in futures:
            f.result()

    cfg = storage.load_global_config()
    assert isinstance(cfg, dict)
    # Check that storage.GLOBAL_CONFIG was updated
    assert storage.GLOBAL_CONFIG is not None
    assert isinstance(storage.GLOBAL_CONFIG, dict)


# ---------------------------------------------------------------------------
# 4. Ensure load_config_and_state() Runs in lifespan (F-15)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lifespan_loads_config_and_state(tmp_path, monkeypatch):
    """Lifespan ensures config.json and keys are loaded into storage.GLOBAL_CONFIG."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text('{"api_key": "test_google_key", "custom_param": 42}', encoding="utf-8")
    monkeypatch.setattr(storage, "CONFIG_FILE", cfg_file)

    storage.GLOBAL_CONFIG.clear()
    assert not storage.GLOBAL_CONFIG

    async with lifespan(app):
        assert storage.GLOBAL_CONFIG.get("custom_param") == 42


# ---------------------------------------------------------------------------
# 5. Safe Auto-Updater & Restart (F-03, D-03)
# ---------------------------------------------------------------------------

def test_auto_update_enabled_defaults_to_false():
    """auto_update_enabled must default to False in settings schema."""
    req = SettingsRequest()
    assert req.auto_update_enabled is False


def test_has_active_jobs_checks_agent_runner(monkeypatch):
    """has_active_jobs returns True if agent runner status is running."""
    import utils.jobs_manager as jm
    monkeypatch.setattr(jm, "jobs", {})
    svc = UpdateService()
    mock_runner = MagicMock()
    mock_runner.status.return_value.running = True
    monkeypatch.setattr("services.agent_runner.get_runner", lambda: mock_runner)

    assert svc.has_active_jobs() is True

    mock_runner.status.return_value.running = False
    assert svc.has_active_jobs() is False


@pytest.mark.asyncio
async def test_apply_update_skips_on_dirty_working_tree_when_not_forced(monkeypatch):
    """When force=False and working tree is dirty, apply_update returns skipped with reason."""
    svc = UpdateService()
    monkeypatch.setattr(svc, "has_active_jobs", lambda: False)
    monkeypatch.setattr(svc, "is_git_repo", lambda: True)

    def mock_git(args, timeout=15.0):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        cmd = " ".join(args)
        if "status --porcelain" in cmd:
            mock_proc.stdout = " M backend/main.py\n"
        else:
            mock_proc.stdout = ""
        return mock_proc

    monkeypatch.setattr(svc, "_run_git", mock_git)

    res = await svc.apply_update(force=False)
    assert res["success"] is False
    assert res["status"] == "skipped"
    assert res["reason"] == "dirty_working_tree"
    assert "Working tree has uncommitted changes" in res["message"]


def test_trigger_restart_schedules_delayed_exit(monkeypatch):
    """trigger_restart spawns helper and starts a timer to call os._exit(0)."""
    svc = UpdateService()
    popen_called = []
    timer_started = []

    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: popen_called.append(True))

    class MockTimer:
        def __init__(self, interval, fn):
            self.interval = interval
            self.fn = fn

        def start(self):
            timer_started.append(self)

    monkeypatch.setattr("threading.Timer", MockTimer)

    svc.trigger_restart()

    assert len(popen_called) == 1
    assert len(timer_started) == 1
    assert timer_started[0].interval == 0.5
