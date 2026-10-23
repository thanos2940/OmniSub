"""Choosing who translates the queue (Settings › Translation): the agy runner or the Gemini API.

Switching to the Gemini API stops the runner after the episodes in hand and hands the episodes queued for
it to the worker; switching back starts the runner. Plus the "send a test line" check of a Gemini model.
"""

import json

import pytest
from fastapi.testclient import TestClient
from google.genai import types

from utils import storage
from utils.translation_queue import TranslationQueue, KIND_TRANSLATION


@pytest.fixture
def client():
    from main import app
    return TestClient(app)


@pytest.fixture
def queue(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    monkeypatch.setattr("utils.translation_queue.DB_FILE", db)
    return TranslationQueue(db_path=db)


class FakeRunner:
    def __init__(self, running):
        self._running, self.stopped = running, None

    def running(self):
        return self._running

    def stop(self, now=False, reason=""):
        self.stopped = reason


def _engine(name):
    cfg = storage.load_global_config()
    cfg["translation_engine"] = name
    storage.save_global_config(cfg)


def _options(queue, item_id):
    row = next(i for i in queue.get_all(include_all_kinds=True) if i["id"] == item_id)
    return json.loads(row["options"] or "{}")


def test_switching_to_the_gemini_api_stops_the_runner_and_hands_over_the_queue(client, queue, monkeypatch):
    from services import agent_runner
    runner = FakeRunner(running=True)
    monkeypatch.setattr(agent_runner, "get_runner", lambda: runner)
    _engine("agent")
    gaps = queue.enqueue("Show", "S01E01", priority=1, options={"engine": "agent", "mode": "gaps"}, kind=KIND_TRANSLATION)
    full = queue.enqueue("Show", "S01E02", priority=1, options={"engine": "agent", "mode": "full"}, kind=KIND_TRANSLATION)
    plain = queue.enqueue("Show", "S01E03", priority=1, options={"model": "gemini-flash-latest"}, kind=KIND_TRANSLATION)

    res = client.post("/api/settings", json={"translation_engine": "model"}).json()

    assert res["engine_change"] == {"engine": "model", "runner_stopping": True, "handed_to_worker": 2}
    assert "Gemini API" in runner.stopped
    assert _options(queue, gaps) == {}
    assert _options(queue, full) == {"force": True}
    assert _options(queue, plain) == {"model": "gemini-flash-latest"}
    # the worker now takes them
    assert queue.claim_next(exclude_agent=True)["episode_name"] in ("S01E01", "S01E02", "S01E03")


def test_switching_back_starts_the_runner(client, monkeypatch):
    from services import agent_runner
    calls = []
    monkeypatch.setattr(agent_runner, "autostart", lambda: calls.append(1) or True)
    _engine("model")
    res = client.post("/api/settings", json={"translation_engine": "agent"}).json()
    assert res["engine_change"] == {"engine": "agent", "runner_started": True}
    assert calls == [1]


def test_saving_without_a_change_touches_nothing(client, monkeypatch):
    from services import agent_runner
    monkeypatch.setattr(agent_runner, "get_runner", lambda: pytest.fail("runner touched"))
    monkeypatch.setattr(agent_runner, "autostart", lambda: pytest.fail("runner started"))
    _engine("agent")
    res = client.post("/api/settings", json={"translation_engine": "agent", "target_language": "Greek"}).json()
    assert "engine_change" not in res


def test_an_unknown_engine_is_refused(client):
    _engine("agent")
    assert client.post("/api/settings", json={"translation_engine": "gemini"}).status_code == 422
    assert storage.load_global_config()["translation_engine"] == "agent"


# ---- "Send a test line" for a Gemini model

class FakeModels:
    def __init__(self):
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "thinking": config.thinking_config, "contents": contents})
        return types.GenerateContentResponse(
            model_version="gemini-3.1-flash-lite-preview-10-2026",
            candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text="Περίμενέ με στον σταθμό.")]))],
        )


@pytest.fixture
def fake_gemini(monkeypatch):
    from utils.rate_limiter import per_model_rate_limiter
    monkeypatch.setattr(per_model_rate_limiter, "_limiters", {})
    monkeypatch.setattr(per_model_rate_limiter, "save_state", lambda *a, **k: None)
    models = FakeModels()
    keys = []

    def client_factory(*a, **k):
        keys.append(k.get("api_key"))
        return type("C", (), {"models": models})()

    monkeypatch.setattr("google.genai.Client", client_factory)
    models.keys = keys
    return models


def test_test_line_reports_what_answered(client, fake_gemini):
    res = client.post("/api/config/test-gemini-model", json={"model": "gemini-flash-lite-latest"}).json()
    assert res["ok"] is True
    assert res["model"] == "gemini-flash-lite-latest"
    assert res["answered_by"] == "gemini-3.1-flash-lite-preview-10-2026"
    assert res["answer"] == "Περίμενέ με στον σταθμό."
    assert res["thinking_budget"] == 0
    assert fake_gemini.calls[0]["model"] == "gemini-flash-lite-latest"


def test_test_line_uses_a_typed_key(client, fake_gemini):
    client.post("/api/config/test-gemini-model", json={"model": "gemini-flash-latest", "api_key": "AIza-typed"})
    assert fake_gemini.keys == ["AIza-typed"]


def test_test_line_without_a_key(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    cfg = storage.load_global_config()
    cfg.pop("api_key_obfuscated", None)
    storage.save_global_config(cfg)
    res = client.post("/api/config/test-gemini-model", json={}).json()
    assert res["ok"] is False and "No Gemini API key" in res["error"]


def test_test_line_reports_an_api_error(client, monkeypatch):
    from google.genai import errors

    def boom(*a, **k):
        raise errors.ClientError(404, {"error": {"code": 404, "message": "models/gemini-nope is not found",
                                                 "status": "NOT_FOUND"}})
    monkeypatch.setattr("google.genai.Client", lambda *a, **k: type("C", (), {"models": type("M", (), {"generate_content": staticmethod(boom)})()})())
    res = client.post("/api/config/test-gemini-model", json={"model": "gemini-nope"}).json()
    assert res["ok"] is False and "not found" in res["error"]
