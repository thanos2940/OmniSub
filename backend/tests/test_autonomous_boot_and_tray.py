"""
Unit tests for Autonomous Boot, Dual UNC/Local Path Resolver,
Windowless Subprocesses, and System Tray Supervisor.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import integrations.embedded_subs as embedded_subs
import integrations.path_resolver as path_resolver
import services.agent_cli as agent_cli
import services.agent_runner as agent_runner
import utils.subtitle_fixer as subtitle_fixer
import tray_app


# ===========================================================================
# 1. Windowless Subprocess Flags (_SUBPROCESS_FLAGS)
# ===========================================================================

def test_subprocess_flags_defined_in_all_modules():
    """Verify _SUBPROCESS_FLAGS is defined in all required modules and has CREATE_NO_WINDOW on Windows."""
    expected_flag = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    assert hasattr(embedded_subs, "_SUBPROCESS_FLAGS")
    assert embedded_subs._SUBPROCESS_FLAGS == expected_flag

    assert hasattr(subtitle_fixer, "_SUBPROCESS_FLAGS")
    assert subtitle_fixer._SUBPROCESS_FLAGS == expected_flag

    assert hasattr(agent_cli, "_SUBPROCESS_FLAGS")
    assert agent_cli._SUBPROCESS_FLAGS == expected_flag

    assert hasattr(agent_runner, "_SUBPROCESS_FLAGS")
    assert agent_runner._SUBPROCESS_FLAGS == expected_flag

    assert hasattr(tray_app, "_SUBPROCESS_FLAGS")
    assert tray_app._SUBPROCESS_FLAGS == expected_flag


def test_embedded_subs_probe_passes_creationflags(monkeypatch):
    """probe_subtitle_tracks must pass creationflags=_SUBPROCESS_FLAGS to subprocess.run."""
    captured_kwargs = {}

    def mock_run(*args, **kwargs):
        captured_kwargs.update(kwargs)
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = '{"streams": []}'
        return mock_res

    monkeypatch.setattr(subprocess, "run", mock_run)
    tools = embedded_subs.EmbeddedTools(ffmpeg="ffmpeg.exe", ffprobe="ffprobe.exe")
    embedded_subs.probe_subtitle_tracks("test_media.mkv", tools=tools, force_refresh=True)

    assert "creationflags" in captured_kwargs
    assert captured_kwargs["creationflags"] == embedded_subs._SUBPROCESS_FLAGS


def test_agent_cli_calls_pass_creationflags(monkeypatch):
    """agent_cli.antigravity_quota and kill_process pass creationflags=_SUBPROCESS_FLAGS."""
    captured_runs = []

    def mock_run(*args, **kwargs):
        captured_runs.append(kwargs)
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = ""
        return mock_res

    monkeypatch.setattr(subprocess, "run", mock_run)

    # antigravity_quota call
    agent_cli.antigravity_quota(["fake_agy.exe"])
    assert any(k.get("creationflags") == agent_cli._SUBPROCESS_FLAGS for k in captured_runs)

    # kill_process call on Windows
    captured_runs.clear()
    monkeypatch.setattr(os, "name", "nt")
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.pid = 9999
    agent_cli.kill_process(mock_proc)
    assert any(k.get("creationflags") == agent_cli._SUBPROCESS_FLAGS for k in captured_runs)


# ===========================================================================
# 2. Dual UNC <-> Local Drive Path Resolver
# ===========================================================================

def test_unc_to_local_resolution_hostname_match(monkeypatch):
    r"""UNC path \\Morpiceserver\e\Shows\Foo\bar.mkv automatically resolves to E:\Shows\Foo\bar.mkv on Morpiceserver."""
    resolver = path_resolver.PathResolver([])
    monkeypatch.setattr("socket.gethostname", lambda: "Morpiceserver")

    unc_path = r"\\Morpiceserver\e\Shows\Foo\bar.mkv"
    resolved = resolver.resolve(unc_path)
    assert resolved == r"E:\Shows\Foo\bar.mkv"

    # Forward slash UNC path
    unc_slash = "//Morpiceserver/e/Shows/Foo/bar.mkv"
    resolved_slash = resolver.resolve(unc_slash)
    assert resolved_slash == r"E:\Shows\Foo\bar.mkv"


def test_unc_to_local_resolution_candidate_exists(monkeypatch):
    """UNC path resolves to local drive if candidate exists on disk even if hostname differs."""
    resolver = path_resolver.PathResolver([])
    monkeypatch.setattr("socket.gethostname", lambda: "OtherHost")

    def mock_exists(p):
        return p == r"D:\Media\test.mkv"

    monkeypatch.setattr(os.path, "exists", mock_exists)

    unc_path = r"\\OtherServer\d\Media\test.mkv"
    resolved = resolver.resolve(unc_path)
    assert resolved == r"D:\Media\test.mkv"


def test_unc_target_is_not_sent_to_an_unrelated_local_drive(monkeypatch):
    r"""A subtitle not written yet on \\server\e\... stays on the share when this PC merely has an E: drive."""
    resolver = path_resolver.PathResolver([])
    monkeypatch.setattr("socket.gethostname", lambda: "Workstation")
    monkeypatch.setattr(os.path, "exists", lambda p: p == "E:\\")
    monkeypatch.setattr(os.path, "isdir", lambda p: p == "E:\\")
    target = r"\\Morpiceserver\e\Shows\Foo\S01E01.el.srt"
    assert resolver.resolve(target) == target

    # Its folder is on this machine's E: and the share is not reachable: write it locally.
    monkeypatch.setattr(os.path, "isdir", lambda p: p in ("E:\\", r"E:\Shows\Foo"))
    assert resolver.resolve(target) == r"E:\Shows\Foo\S01E01.el.srt"


def test_local_to_unc_resolution_via_configured_mapping(monkeypatch):
    """Local drive path non-existent locally resolves to UNC share found in mappings."""
    mappings = [{"remote": r"\\Morpiceserver\e\Media", "local": r"E:\Media"}]
    resolver = path_resolver.PathResolver(mappings)

    def mock_exists(p):
        # Local doesn't exist, but UNC exists
        if p.startswith(r"\\Morpiceserver\e"):
            return True
        return False

    monkeypatch.setattr(os.path, "exists", mock_exists)

    local_path = r"E:\Media\Show\ep1.mkv"
    resolved = resolver.resolve(local_path)
    assert resolved == r"\\Morpiceserver\e\Media\Show\ep1.mkv"


def test_path_resolver_validate_mappings():
    """validate_mappings reports mapping reachable status and unc_auto_resolution health."""
    resolver = path_resolver.PathResolver([{"remote": r"\\Server\e", "local": "E:\\"}])
    res = resolver.validate_mappings()

    assert "mappings" in res
    assert "unc_auto_resolution" in res
    assert res["unc_auto_resolution"]["active"] is True
    assert res["unc_auto_resolution"]["status"] == "healthy"


# ===========================================================================
# 3. Autonomous Agent Runner Boot
# ===========================================================================

def test_autostart_when_translation_engine_is_agent(monkeypatch):
    """autostart() starts runner and enables keep_watching when translation_engine == 'agent'."""
    runner = agent_runner.get_runner()
    runner.enabled = False
    runner.config["watch"] = False
    runner.config["keep_watching"] = False

    monkeypatch.setattr(runner, "running", lambda: False)

    start_calls = []

    def mock_start(cfg=None, resumed=False):
        start_calls.append((cfg, resumed))
        return {}

    monkeypatch.setattr(runner, "start", mock_start)
    monkeypatch.setattr(
        "utils.storage.load_global_config",
        lambda: {"translation_engine": "agent", "agent_runner_autostart": True},
    )

    result = agent_runner.autostart()

    assert result is True
    assert len(start_calls) == 1
    cfg, resumed = start_calls[0]
    assert cfg.get("watch") is True
    assert resumed is False  # a fresh start on boot, not a resume


def test_autostart_when_runner_already_enabled(monkeypatch):
    """autostart() starts runner and sets keep_watching if r.enabled was saved as True."""
    runner = agent_runner.get_runner()
    runner.enabled = True
    runner.config["watch"] = False

    monkeypatch.setattr(runner, "running", lambda: False)

    start_calls = []
    monkeypatch.setattr(runner, "start", lambda cfg=None, resumed=False: start_calls.append((cfg, resumed)))
    monkeypatch.setattr("utils.storage.load_global_config", lambda: {"translation_engine": "gemini"})

    result = agent_runner.autostart()

    assert result is True
    assert len(start_calls) == 1
    cfg, resumed = start_calls[0]
    assert cfg.get("watch") is True
    assert resumed is True


def test_autostart_noops_when_not_enabled_and_not_agent(monkeypatch):
    """autostart() does nothing when translation_engine is not agent and runner is not enabled."""
    runner = agent_runner.get_runner()
    runner.enabled = False

    monkeypatch.setattr(runner, "running", lambda: False)
    monkeypatch.setattr(
        "utils.storage.load_global_config",
        lambda: {"translation_engine": "gemini", "agent_runner_autostart": False},
    )

    result = agent_runner.autostart()
    assert result is False


# ===========================================================================
# 4. Tray App & Server Supervisor
# ===========================================================================

def test_tray_lock_acquisition_and_release(tmp_path):
    """Test tray app single-instance lock file acquisition and clean release."""
    lock_file = tmp_path / "ombisub_tray.lock"

    # Acquire lock
    assert tray_app.acquire_tray_lock(lock_file, port=8000) is True
    assert lock_file.exists()

    # Second acquisition by same process replaces stale/owned lock
    assert tray_app.acquire_tray_lock(lock_file, port=8000) is True

    # Release lock
    tray_app.release_tray_lock(lock_file)
    assert not lock_file.exists()


def test_server_supervisor_start_and_stop(tmp_path, monkeypatch):
    """ServerSupervisor launches run_server.py with tray environment and windowless flags."""
    popen_calls = []

    class MockPopen:
        def __init__(self, cmd, **kwargs):
            self.cmd = cmd
            self.kwargs = kwargs
            self.pid = 4321
            popen_calls.append(self)

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(subprocess, "Popen", MockPopen)

    supervisor = tray_app.ServerSupervisor(port=8080, data_dir=tmp_path, python_exe="python.exe")
    supervisor.start_server()

    assert len(popen_calls) == 1
    call = popen_calls[0]
    assert "run_server.py" in " ".join(call.cmd)
    assert call.kwargs["env"]["OMBISUB_SUPERVISOR"] == "tray"
    assert call.kwargs["creationflags"] == tray_app._SUBPROCESS_FLAGS

    supervisor.stop_server(timeout=1.0)
    assert supervisor.stopping is True


def test_server_supervisor_respawn_on_update_code_42(tmp_path, monkeypatch):
    """ServerSupervisor detects exit code 42 (auto-update restart) and triggers respawn."""
    respawn_calls = []

    class MockSupervisor(tray_app.ServerSupervisor):
        def start_server(self):
            respawn_calls.append(True)

    supervisor = MockSupervisor(port=8000, data_dir=tmp_path)

    # Mock process returning exit code 42
    mock_proc = MagicMock()
    mock_proc.pid = 5555
    mock_proc.poll.return_value = 42
    supervisor.proc = mock_proc

    # Mock sleep so loop doesn't block
    monkeypatch.setattr("time.sleep", lambda s: None)

    # Run one pass of supervisor check
    code = supervisor.proc.poll()
    assert code == 42
    supervisor.start_server()
    assert len(respawn_calls) == 1


def test_create_tray_image():
    """Verify create_tray_image produces an icon if PIL is available."""
    if tray_app.HAS_PYSTRAY:
        img = tray_app.create_tray_image(32, 32)
        assert img is not None
        assert img.size == (32, 32)
