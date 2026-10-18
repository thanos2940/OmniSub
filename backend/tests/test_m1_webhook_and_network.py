"""
Regression tests for Milestone 1 Webhook Validation, Deduplication, and Network Hardening:
- INT-P1-01: Async SMB Probing with 5.0s Timeout.
- INT-P1-02: 60s sliding window webhook deduplication.
- INT-P1-06: Synchronous webhook payload validation returning HTTP 400.
- INT-P1-07: httpx.Limits and reconnect retry wrappers for Sonarr and Radarr.
"""

import time
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app
from utils import storage
from routers.integrations import _webhook_deduplicator, WebhookDeduplicator
import integrations.sonarr as sonarr_mod
import integrations.radarr as radarr_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = data_dir / "config.json"
    monkeypatch.setattr("utils.storage.CONFIG_FILE", cfg_file)
    storage.save_global_config({"webhook_secret": "secret123"})
    _webhook_deduplicator.clear()
    return TestClient(app)


# ----------------------------------------------------------------------
# INT-P1-06: Synchronous Webhook Payload Validation
# ----------------------------------------------------------------------

def test_sonarr_webhook_validation_empty_body(client):
    resp = client.post("/api/webhook/sonarr", json={}, headers={"X-OmniSub-Token": "secret123"})
    assert resp.status_code == 400
    data = resp.json()
    assert data["detail"]["error"] == "Invalid Sonarr webhook payload"
    assert "eventType" in data["detail"]["details"]


def test_sonarr_webhook_validation_missing_fields(client):
    payload = {"eventType": "Download"}
    resp = client.post("/api/webhook/sonarr", json=payload, headers={"X-OmniSub-Token": "secret123"})
    assert resp.status_code == 400
    details = resp.json()["detail"]["details"]
    assert "series.title" in details
    assert "episodes" in details
    assert "episodeFile.path" in details


def test_radarr_webhook_validation_missing_fields(client):
    payload = {"eventType": "Download"}
    resp = client.post("/api/webhook/radarr", json=payload, headers={"X-OmniSub-Token": "secret123"})
    assert resp.status_code == 400
    details = resp.json()["detail"]["details"]
    assert "movie.title" in details
    assert "movieFile.path" in details


def test_webhook_test_event_passes(client):
    resp = client.post("/api/webhook/sonarr", json={"eventType": "Test"}, headers={"X-OmniSub-Token": "secret123"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"

    resp_radarr = client.post("/api/webhook/radarr", json={"eventType": "Test"}, headers={"X-OmniSub-Token": "secret123"})
    assert resp_radarr.status_code == 200
    assert resp_radarr.json()["status"] == "success"


def test_webhook_ignored_event(client):
    resp = client.post("/api/webhook/sonarr", json={"eventType": "Grab"}, headers={"X-OmniSub-Token": "secret123"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


# ----------------------------------------------------------------------
# INT-P1-02: 60s Sliding Window Deduplication
# ----------------------------------------------------------------------

def test_sonarr_webhook_sliding_window_deduplication(client, monkeypatch):
    # Mock background tasks execution so it doesn't try actual imports
    monkeypatch.setattr("routers.integrations._process_sonarr_webhook", AsyncMock())

    valid_payload = {
        "eventType": "Download",
        "series": {"id": 42, "title": "Dark"},
        "episodes": [{"id": 101, "seasonNumber": 1, "episodeNumber": 1}],
        "episodeFile": {"id": 202, "path": "/data/Dark/S01E01.mkv"},
    }

    # First webhook -> accepted (queued)
    resp1 = client.post("/api/webhook/sonarr", json=valid_payload, headers={"X-OmniSub-Token": "secret123"})
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "queued"

    # Rapid-fire second webhook for same series & file (e.g. EpisodeFileRenamed) -> deduplicated (ignored)
    rename_payload = dict(valid_payload)
    rename_payload["eventType"] = "EpisodeFileRenamed"

    resp2 = client.post("/api/webhook/sonarr", json=rename_payload, headers={"X-OmniSub-Token": "secret123"})
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "ignored"
    assert resp2.json()["reason"] == "duplicate"


def test_radarr_webhook_sliding_window_deduplication(client, monkeypatch):
    monkeypatch.setattr("routers.integrations._process_radarr_webhook", AsyncMock())

    valid_payload = {
        "eventType": "Download",
        "movie": {"id": 99, "title": "Inception"},
        "movieFile": {"id": 303, "path": "/data/Movies/Inception.mkv"},
    }

    resp1 = client.post("/api/webhook/radarr", json=valid_payload, headers={"X-OmniSub-Token": "secret123"})
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "queued"

    rename_payload = dict(valid_payload)
    rename_payload["eventType"] = "MovieFileRenamed"

    resp2 = client.post("/api/webhook/radarr", json=rename_payload, headers={"X-OmniSub-Token": "secret123"})
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "ignored"
    assert resp2.json()["reason"] == "duplicate"


def test_webhook_deduplicator_sliding_window_expiry():
    dedup = WebhookDeduplicator(window_seconds=2.0)
    assert dedup.is_duplicate("sonarr", 1, 100) is False
    assert dedup.is_duplicate("sonarr", 1, 100) is True

    # Simulate 2.5s passing
    with patch("time.monotonic", return_value=time.monotonic() + 2.5):
        assert dedup.is_duplicate("sonarr", 1, 100) is False


# ----------------------------------------------------------------------
# INT-P1-07: HTTPX Limits and Reconnect Retry Wrappers
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sonarr_reconnect_on_transport_error():
    cfg = sonarr_mod.SonarrConfig(base_url="http://localhost:8989", api_key="test", enabled=True)
    calls = {"count": 0}

    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = [{"id": 1, "title": "Show"}]

    async def mock_get(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response")
        return mock_resp

    with patch.object(sonarr_mod, "get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.get = mock_get
        mock_get_client.return_value = mock_client

        series = await sonarr_mod.get_all_series(cfg)
        assert series == [{"id": 1, "title": "Show"}]
        assert calls["count"] == 2


@pytest.mark.asyncio
async def test_sonarr_reconnect_on_winerror_10054():
    cfg = sonarr_mod.SonarrConfig(base_url="http://localhost:8989", api_key="test", enabled=True)
    calls = {"count": 0}

    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = [{"id": 10, "path": "/media/Show/ep.mkv"}]

    async def mock_get(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")
        return mock_resp

    with patch.object(sonarr_mod, "get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.get = mock_get
        mock_get_client.return_value = mock_client

        ep_files = await sonarr_mod.get_episode_files(cfg, 1)
        assert ep_files == [{"id": 10, "path": "/media/Show/ep.mkv"}]
        assert calls["count"] == 2


@pytest.mark.asyncio
async def test_radarr_reconnect_on_transport_error():
    cfg = radarr_mod.RadarrConfig(base_url="http://localhost:7878", api_key="test", enabled=True)
    calls = {"count": 0}

    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = [{"id": 1, "title": "Movie"}]

    async def mock_get(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ConnectError("Network is unreachable")
        return mock_resp

    with patch.object(radarr_mod, "get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.get = mock_get
        mock_get_client.return_value = mock_client

        movies = await radarr_mod.get_all_movies(cfg)
        assert movies == [{"id": 1, "title": "Movie"}]
        assert calls["count"] == 2


@pytest.mark.asyncio
async def test_permission_error_fails_immediately_without_retry():
    cfg = sonarr_mod.SonarrConfig(base_url="http://localhost:8989", api_key="wrong_key", enabled=True)
    mock_resp = MagicMock(status_code=401)

    async def mock_get(*args, **kwargs):
        return mock_resp

    with patch.object(sonarr_mod, "get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.get = mock_get
        mock_get_client.return_value = mock_client

        with pytest.raises(PermissionError) as exc_info:
            await sonarr_mod.get_all_series(cfg)
        assert "401 Unauthorized" in str(exc_info.value)


# ----------------------------------------------------------------------
# INT-P1-01: Async SMB Timeout Protection
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_arr_test_path_timeout_on_hanging_smb(monkeypatch):
    def fake_hanging_probe(*args, **kwargs):
        time.sleep(10.0)
        return ("dummy", True, False, False)

    monkeypatch.setattr("routers.integrations._probe_path_sync", fake_hanging_probe)

    from routers.schemas import PathTestRequest
    from routers.integrations import arr_test_path

    t0 = time.time()
    resp = await arr_test_path(PathTestRequest(remote_path=r"\\OfflineNAS\tv\Show", path_mappings=[]))
    elapsed = time.time() - t0

    assert 4.8 <= elapsed <= 7.0
    assert resp["exists"] is False
    assert "timed out after 5.0s" in resp["error"]
