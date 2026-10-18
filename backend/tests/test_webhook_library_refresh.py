"""Autopilot off: a Sonarr/Radarr webhook only refreshes the library and never queues a translation.

The legacy auto-translate mode (``arr_auto_translate``, "Bazarr wait delay") is gone; old
configs that still carry its keys behave as if they were not there.
"""
import pytest
from fastapi.testclient import TestClient

from main import app
from routers import integrations as integrations_router
from services import queue_service
from utils import storage
from utils.translation_queue import TranslationQueue

SONARR = {"eventType": "Download", "series": {"id": 3, "title": "Severance"},
          "episodes": [{"id": 11, "seasonNumber": 1, "episodeNumber": 1, "episodeFileId": 77}],
          "episodeFile": {"id": 77, "path": "/tv/Severance/Severance.S01E01.mkv"}}
RADARR = {"eventType": "Download", "movie": {"id": 5, "title": "Dune", "year": 2021},
          "movieFile": {"id": 9, "path": "/movies/Dune.2021.mkv"}}


@pytest.fixture
def webhook_env(monkeypatch):
    storage.save_global_config({
        "webhook_secret": "s3cret", "autopilot_enabled": False,
        # keys of the removed legacy mode, as an old config still has them
        "arr_auto_translate": "smart", "arr_non_anime_delay_minutes": 0,
    })
    integrations_router._webhook_deduplicator.clear()
    env = type("Env", (), {})()
    env.imported, env.enqueued = [], []

    class Engine:
        async def process_sonarr_webhook(self, payload, storage_mod, parse_srt):
            env.imported.append(("sonarr", payload))
            return "Severance/S01E01"

        async def process_radarr_webhook(self, payload, storage_mod, parse_srt):
            env.imported.append(("radarr", payload))
            return "Dune/Dune"

    monkeypatch.setattr(integrations_router, "_build_arr_engine", lambda *a, **k: Engine())
    monkeypatch.setattr(queue_service, "enqueue_translation", lambda *a, **k: env.enqueued.append(a) or "id")
    return env


@pytest.mark.parametrize("path,payload,source", [
    ("/api/webhook/sonarr", SONARR, "sonarr"),
    ("/api/webhook/radarr", RADARR, "radarr"),
])
def test_a_webhook_imports_into_the_library_and_queues_nothing(webhook_env, path, payload, source):
    before = len(TranslationQueue().get_all(limit=1000, include_all_kinds=True))
    res = TestClient(app).post(f"{path}?token=s3cret", json=payload)
    assert res.status_code == 200 and res.json() == {"status": "queued"}
    assert [s for s, _ in webhook_env.imported] == [source]
    assert webhook_env.enqueued == []
    assert len(TranslationQueue().get_all(limit=1000, include_all_kinds=True)) == before


def test_the_legacy_settings_are_accepted_and_dropped():
    client = TestClient(app)
    res = client.post("/api/settings", json={"arr_auto_translate": True, "arr_non_anime_delay_minutes": 45,
                                             "arr_sync_interval": 15})
    assert res.status_code == 200
    saved = storage.load_global_config()
    assert saved["arr_sync_interval"] == 15
    assert "arr_auto_translate" not in saved and "arr_non_anime_delay_minutes" not in saved
