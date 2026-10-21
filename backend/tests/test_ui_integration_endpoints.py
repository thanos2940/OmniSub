"""Endpoints behind the rebuilt UI: GET /api/queue?project= lists one project's items past the 100-row cap,
GET /integrations/webhooks gives Settings the webhook paths with the stored (otherwise masked) token, and
work that goes to agy (uploads, context and glossary jobs) needs no Gemini key in agent mode."""

import pytest
from fastapi.testclient import TestClient

from utils import storage
from utils.translation_queue import TranslationQueue


@pytest.fixture
def client():
    from main import app
    return TestClient(app)


@pytest.fixture
def queue(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    monkeypatch.setattr("utils.translation_queue.DB_FILE", db)
    return TranslationQueue(db_path=db)


def test_project_filter_reaches_items_beyond_the_global_cap(queue, client):
    for i in range(120):
        queue.enqueue("Busy Show", f"S01E{i:03d}", priority=1)
    for i in range(3):
        queue.enqueue("Quiet Show", f"S01E{i:02d}", priority=5)

    full = client.get("/api/queue").json()
    assert len(full["items"]) == 100
    assert not any(it["project_name"] == "Quiet Show" for it in full["items"])

    mine = client.get("/api/queue", params={"project": "quiet show"}).json()
    assert sorted(it["episode_name"] for it in mine["items"]) == ["S01E00", "S01E01", "S01E02"]
    assert {it["project_name"] for it in mine["items"]} == {"Quiet Show"}

    busy = client.get("/api/queue", params={"project": "Busy Show"}).json()
    assert len(busy["items"]) == 120


def test_project_filter_applies_to_extractions(queue, client):
    queue.enqueue("Busy Show", "S01E01", priority=1, kind="extraction")
    queue.enqueue("Quiet Show", "S01E01", priority=1, kind="extraction")

    mine = client.get("/api/queue", params={"project": "Quiet Show"}).json()
    assert [it["project_name"] for it in mine["extractions"]] == ["Quiet Show"]
    assert {it["project_name"] for it in mine["items"]} == {"Quiet Show"}  # items list every kind
    assert len(client.get("/api/queue").json()["extractions"]) == 2


def test_webhook_urls_carry_the_stored_token(client):
    cfg = storage.load_global_config()
    cfg["webhook_secret"] = "s3cret/+token"
    storage.save_global_config(cfg)

    body = client.get("/integrations/webhooks").json()
    assert body == {
        "configured": True,
        "sonarr": "/api/webhook/sonarr?token=s3cret%2F%2Btoken",
        "radarr": "/api/webhook/radarr?token=s3cret%2F%2Btoken",
    }
    # The settings endpoint itself still masks the token.
    assert client.get("/api/settings").json().get("webhook_secret") != "s3cret/+token"


def test_webhook_urls_without_a_token(client):
    cfg = storage.load_global_config()
    cfg["webhook_secret"] = ""
    storage.save_global_config(cfg)

    body = client.get("/integrations/webhooks").json()
    assert body == {"configured": False, "sonarr": "/api/webhook/sonarr", "radarr": "/api/webhook/radarr"}


def test_webhook_urls_need_the_api_key_when_login_is_on(client):
    cfg = storage.load_global_config()
    cfg.update({"auth_enabled": True, "api_key": "k-123", "webhook_secret": "abc"})
    storage.save_global_config(cfg)

    assert client.get("/integrations/webhooks").status_code == 401
    ok = client.get("/integrations/webhooks", headers={"X-Api-Key": "k-123"})
    assert ok.status_code == 200 and ok.json()["sonarr"].endswith("?token=abc")


# ---- agy-first: no Gemini key is needed for work that goes to agy

SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello there.\n\n2\n00:00:03,000 --> 00:00:04,000\nGood night.\n"


@pytest.fixture
def no_gemini_key(monkeypatch, client):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    # The background work would call agy (or Gemini): these tests only cover the endpoint gate.
    import routers.glossary_context as gc
    for name in ("_process_create_context", "_process_create_glossary", "_process_enhance_context", "_process_enhance_glossary"):
        monkeypatch.setattr(gc, name, lambda *a, **k: None)
    assert client.post("/projects", json={"name": "Keyless", "target_language": "Greek", "type": "show"}).status_code == 200


def _engine(name):
    cfg = storage.load_global_config()
    cfg["translation_engine"] = name
    cfg.pop("api_key_obfuscated", None)
    storage.save_global_config(cfg)


@pytest.mark.parametrize("engine", ["agent", "model"])
def test_uploading_a_subtitle_needs_no_api_key(no_gemini_key, client, engine):
    _engine(engine)
    res = client.post("/projects/Keyless/episodes/S01E01/upload", files={"file": ("S01E01.srt", SRT, "text/plain")})
    assert res.status_code == 200 and res.json()["line_count"] == 2


@pytest.mark.parametrize("path", ["context/create", "context/enhance", "glossary/create", "glossary/enhance"])
def test_context_and_glossary_jobs_start_without_a_key_in_agent_mode(no_gemini_key, client, path):
    _engine("agent")
    res = client.post(f"/projects/Keyless/{path}")
    assert res.status_code == 200 and res.json()["job_id"]


def test_context_jobs_still_need_the_key_with_the_model_engine(no_gemini_key, client):
    _engine("model")
    res = client.post("/projects/Keyless/context/create")
    assert res.status_code == 400 and res.json()["detail"]["error"] == "api_key_missing"
