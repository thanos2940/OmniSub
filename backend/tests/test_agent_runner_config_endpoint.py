"""PUT /api/agent/runner/config saves the runner's settings from the Settings page.

A stopped runner only stores them (it is not started, and they survive a restart); a running one
applies the live keys at once and keeps the rest for its next start.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services import agent_runner
from services.agent_runner import AgentRunner
from services.agent_task_service import AgentTaskService

FAKE = [sys.executable, str(Path(__file__).with_name("fake_agent_cli.py"))]


def _new_runner(tmp_path):
    return AgentRunner(service=AgentTaskService(tasks_dir=tmp_path / "agent_tasks"),
                       exe_override={"antigravity": FAKE}, state_dir=tmp_path / "state")


@pytest.fixture
def runner(tmp_path, monkeypatch):
    r = _new_runner(tmp_path)
    monkeypatch.setattr(agent_runner, "_runner", r)
    return r


@pytest.fixture
def client():
    from main import app
    return TestClient(app)


def test_saving_while_stopped_does_not_start_and_persists(runner, client, tmp_path):
    res = client.put("/api/agent/runner/config", json={
        "model": "gemini-4-argon-high", "fallback_models": ["claude-sonnet-5-5-low", "gpt-oss-120b-medium"],
        "lanes": 2, "per_show": 1, "api_rescue": False, "rescue_model": "gemini-2.5-flash",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["running"] is False and body["enabled"] is False and runner.threads == []
    assert body["config"]["model"] == "gemini-4-argon-high"
    assert body["config"]["fallback_models"] == ["claude-sonnet-5-5-low", "gpt-oss-120b-medium"]
    assert body["config"]["lanes"] == 2 and body["config"]["api_rescue"] is False
    assert body["config"]["rescue_model"] == "gemini-2.5-flash"
    # Stored: a new runner over the same state directory sees it.
    again = _new_runner(tmp_path)
    assert again.config["model"] == "gemini-4-argon-high" and again.config["lanes"] == 2
    assert again.config["api_rescue"] is False


def test_keys_left_out_keep_their_values(runner, client):
    client.put("/api/agent/runner/config", json={"model": "gemini-3.8-flash-high", "chunk_lines": 120})
    body = client.put("/api/agent/runner/config", json={"fix_rounds": 1}).json()
    assert body["config"]["model"] == "gemini-3.8-flash-high"
    assert body["config"]["chunk_lines"] == 120 and body["config"]["fix_rounds"] == 1
    assert body["config"]["api_rescue"] is True  # the default, never reset by a save that did not mention it


def test_bad_engine_is_rejected_and_nothing_changes(runner, client):
    before = dict(runner.config)
    res = client.put("/api/agent/runner/config", json={"engine": "notepad", "lanes": 5})
    assert res.status_code == 400
    assert dict(runner.config) == before


def test_saving_while_running_applies_live_keys_now(runner, client):
    started = client.post("/api/agent/runner/start", json={"lanes": 1, "watch": True}).json()
    assert started["running"] is True
    try:
        res = client.put("/api/agent/runner/config", json={"model": "claude-opus-5-5-medium", "per_show": 1, "lanes": 4})
        assert res.status_code == 200
        body = res.json()
        assert body["running"] is True
        assert body["config"]["model"] == "claude-opus-5-5-medium"  # live key: applied at once
        assert body["config"]["per_show"] == 1
        assert body["config"]["lanes"] == 1                          # not live: changes at the next start
    finally:
        runner.stop(now=True)
        for t in list(runner.threads):
            t.join(timeout=30)
