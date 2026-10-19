"""A runner that stopped on an error starts again by itself after a while (agent_runner.recover)."""
import pytest

from services import agent_runner
from utils import storage


@pytest.fixture
def runner(monkeypatch):
    r = agent_runner.get_runner()
    started = []
    monkeypatch.setattr(r, "running", lambda: False)
    monkeypatch.setattr(r, "start", lambda cfg=None, resumed=False: started.append((cfg, resumed)))
    monkeypatch.setattr(r, "log", lambda *a, **k: None)
    monkeypatch.setattr(agent_runner, "_error_seen_at", None)
    r.started_calls = started
    yield r


def _later(monkeypatch, seconds):
    now = agent_runner.time.time()
    monkeypatch.setattr(agent_runner.time, "time", lambda: now + seconds)


def test_an_error_is_retried_after_the_wait(runner, monkeypatch):
    monkeypatch.setattr(runner, "status", "error")
    assert agent_runner.recover() is False          # first sighting starts the clock
    assert agent_runner.recover() is False          # too early
    _later(monkeypatch, agent_runner.RECOVER_AFTER_SECONDS + 1)
    assert agent_runner.recover() is True
    cfg, resumed = runner.started_calls[0]
    assert cfg["watch"] is True and resumed is True


@pytest.mark.parametrize("status", ["stopped", "done", "idle"])
def test_a_runner_stopped_by_hand_or_done_stays_as_it_is(runner, monkeypatch, status):
    monkeypatch.setattr(runner, "status", status)
    agent_runner.recover()
    _later(monkeypatch, agent_runner.RECOVER_AFTER_SECONDS + 1)
    assert agent_runner.recover() is False and runner.started_calls == []


def test_no_retry_when_autostart_is_off(runner, monkeypatch):
    storage.save_global_config({"agent_runner_autostart": False})
    monkeypatch.setattr(runner, "status", "error")
    agent_runner.recover()
    _later(monkeypatch, agent_runner.RECOVER_AFTER_SECONDS + 1)
    assert agent_runner.recover() is False and runner.started_calls == []
