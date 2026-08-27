import pytest
from fastapi.testclient import TestClient

from main import app
from utils import storage


@pytest.fixture
def mock_config(monkeypatch, tmp_path):
    temp_config = tmp_path / "config.json"
    monkeypatch.setattr(storage, "CONFIG_FILE", temp_config)
    yield temp_config


@pytest.fixture
def client(mock_config):
    return TestClient(app)


def test_fresh_install_setup_not_completed(client, mock_config, monkeypatch):
    # Isolate from any earlier test in this session that left GOOGLE_API_KEY set
    # via os.environ directly (routers/settings.py's set_api_key does this, and
    # it isn't scoped to monkeypatch, so it can otherwise leak across tests).
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    resp = client.get("/api/setup/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["setup_completed"] is False
    assert body["auth_configured"] is False
    assert body["has_gemini_key"] is False
    assert body["arr_configured"] is False


def test_setup_status_reflects_configured_state(client, mock_config, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-for-test")
    from utils.auth import generate_api_key
    api_key = generate_api_key()
    storage.save_global_config({
        "setup_completed": True,
        "auth_enabled": True,
        "sonarr_enabled": True,
        "api_key": api_key,
    })
    # auth_enabled=True means this endpoint is now gated too — exercise it the
    # way the real "re-run wizard from Settings" flow does: already logged in.
    resp = client.get("/api/setup/status", headers={"X-Api-Key": api_key})
    body = resp.json()
    assert body["setup_completed"] is True
    assert body["auth_configured"] is True
    assert body["has_gemini_key"] is True
    assert body["arr_configured"] is True


def test_setup_status_reachable_without_auth_header(client, mock_config):
    """The wizard's first call happens before any credentials exist."""
    storage.save_global_config({"auth_enabled": False})
    resp = client.get("/api/setup/status")
    assert resp.status_code == 200


def test_migration_marks_existing_install_complete(mock_config, monkeypatch):
    # Simulate an install that already had a Gemini key before the wizard existed.
    storage.save_global_config({"api_key_obfuscated": "some-obfuscated-value"})
    assert storage.load_global_config().get("setup_completed") is not True

    with TestClient(app):
        pass  # triggers the lifespan migration on startup

    assert storage.load_global_config().get("setup_completed") is True


def test_migration_leaves_fresh_install_alone(mock_config):
    with TestClient(app):
        pass

    assert storage.load_global_config().get("setup_completed") is not True
