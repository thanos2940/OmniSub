import pytest
from fastapi.testclient import TestClient

from main import app
from utils import storage
from utils.auth import hash_password, generate_api_key
import routers.auth as auth_router


@pytest.fixture
def mock_config(monkeypatch, tmp_path):
    temp_config = tmp_path / "config.json"
    monkeypatch.setattr(storage, "CONFIG_FILE", temp_config)
    yield temp_config


@pytest.fixture(autouse=True)
def _reset_login_failure_tracker():
    # TestClient requests all share the same synthetic client host, so the
    # in-memory brute-force tracker would otherwise leak failure counts
    # between tests.
    auth_router._login_failures.clear()
    yield
    auth_router._login_failures.clear()


@pytest.fixture
def client(mock_config):
    return TestClient(app)


@pytest.fixture
def auth_configured(mock_config):
    """Persist a working set of auth credentials directly (bypassing the
    /api/auth/credentials endpoint, which we test separately)."""
    api_key = generate_api_key()
    storage.save_global_config({
        "auth_enabled": True,
        "auth_username": "admin",
        "auth_password_hash": hash_password("correct-horse-battery-staple"),
        "api_key": api_key,
    })
    return api_key


def test_protected_endpoint_requires_key(client, auth_configured):
    resp = client.get("/projects")
    assert resp.status_code == 401

    resp = client.get("/projects", headers={"X-Api-Key": auth_configured})
    assert resp.status_code == 200


def test_wrong_key_rejected(client, auth_configured):
    resp = client.get("/projects", headers={"X-Api-Key": "not-the-real-key"})
    assert resp.status_code == 401


def test_auth_disabled_by_default_leaves_api_open(client, mock_config):
    # No credentials configured at all — auth_enabled defaults to falsy.
    resp = client.get("/projects")
    assert resp.status_code == 200


def test_health_is_always_exempt(client, auth_configured):
    # /api/health (not bare /health — that path is reserved for the built SPA's
    # own Health page once Docker mounts it; see docs/PLAN_docker_deployment.md).
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_non_api_paths_are_never_gated(client, auth_configured):
    """Paths outside _PROTECTED_PREFIXES (the SPA shell in prod, anything Vite
    would serve in dev) must never 401 — only the registered API surface is
    gated. An unmatched path here 404s from FastAPI itself, not from the auth
    middleware; the point of this test is that it's never a 401."""
    resp = client.get("/some-frontend-route-the-backend-knows-nothing-about")
    assert resp.status_code != 401


def test_auth_status_and_login_are_exempt(client, auth_configured):
    resp = client.get("/api/auth/status")
    assert resp.status_code == 200
    assert resp.json()["auth_enabled"] is True
    assert resp.json()["authenticated"] is False

    resp = client.post("/api/auth/login", json={"username": "nope", "password": "nope"})
    assert resp.status_code == 401  # exempt from the key gate, but login itself fails


def test_webhook_paths_are_exempt_from_key_gate(client, auth_configured):
    # No X-Api-Key sent; should not be rejected by the auth middleware (401).
    # It may still fail for other reasons (bad payload / missing webhook_secret),
    # but never with the API-key-gate's 401.
    resp = client.post("/api/webhook/sonarr", json={"eventType": "Test"})
    assert resp.status_code != 401 or "Api-Key" not in resp.text


def test_login_success_returns_api_key(client, auth_configured):
    resp = client.post("/api/auth/login", json={
        "username": "admin", "password": "correct-horse-battery-staple",
    })
    assert resp.status_code == 200
    assert resp.json()["api_key"] == auth_configured


def test_login_wrong_password_rejected(client, auth_configured):
    resp = client.post("/api/auth/login", json={
        "username": "admin", "password": "wrong-password",
    })
    assert resp.status_code == 401


def test_login_lockout_after_repeated_failures(client, auth_configured):
    for _ in range(5):
        resp = client.post("/api/auth/login", json={
            "username": "admin", "password": "wrong",
        })
        assert resp.status_code == 401

    # 6th attempt (even with the correct password) is locked out.
    resp = client.post("/api/auth/login", json={
        "username": "admin", "password": "correct-horse-battery-staple",
    })
    assert resp.status_code == 429


def test_set_credentials_when_not_yet_enabled(client, mock_config):
    resp = client.post("/api/auth/credentials", json={
        "username": "newadmin", "password": "a-decent-password",
    })
    assert resp.status_code == 200
    assert resp.json()["api_key"]

    config = storage.load_global_config()
    assert config["auth_enabled"] is True
    assert config["auth_username"] == "newadmin"


def test_set_credentials_requires_key_once_enabled(client, auth_configured):
    resp = client.post("/api/auth/credentials", json={
        "username": "attacker", "password": "hijack-attempt",
    })
    assert resp.status_code == 401

    resp = client.post(
        "/api/auth/credentials",
        json={"username": "attacker", "password": "hijack-attempt"},
        headers={"X-Api-Key": auth_configured},
    )
    assert resp.status_code == 200


def test_set_credentials_rejects_short_password(client, mock_config):
    resp = client.post("/api/auth/credentials", json={
        "username": "admin", "password": "short",
    })
    assert resp.status_code == 422


def test_options_preflight_never_gated(client, auth_configured):
    resp = client.options("/projects", headers={"Origin": "http://example.com"})
    assert resp.status_code != 401


# --- Secret redaction (routers/settings.py) ---------------------------------

from routers.settings import SECRET_KEYS, SECRET_SENTINEL  # noqa: E402


def test_get_settings_masks_secrets(client, mock_config, auth_configured):
    storage.save_global_config({
        "sonarr_api_key": "real-sonarr-key",
        "radarr_api_key": "real-radarr-key",
        "discord_webhook_url": "https://discord.com/api/webhooks/real",
    })
    resp = client.get("/api/settings", headers={"X-Api-Key": auth_configured})
    assert resp.status_code == 200
    body = resp.json()
    assert body["sonarr_api_key"] == SECRET_SENTINEL
    assert body["radarr_api_key"] == SECRET_SENTINEL
    assert body["discord_webhook_url"] == SECRET_SENTINEL
    assert body["secrets_set"]["sonarr_api_key"] is True
    assert body["secrets_set"]["discord_webhook_url"] is True
    # auth_password_hash must never appear, masked or otherwise.
    assert "auth_password_hash" not in body


def test_get_settings_never_exposes_password_hash(client, mock_config, auth_configured):
    resp = client.get("/api/settings", headers={"X-Api-Key": auth_configured})
    assert "auth_password_hash" not in resp.json()


def test_post_settings_with_sentinel_preserves_existing_secret(client, mock_config, auth_configured):
    storage.save_global_config({"sonarr_api_key": "real-sonarr-key"})

    resp = client.post(
        "/api/settings",
        json={"sonarr_api_key": SECRET_SENTINEL, "default_target_language": "Greek"},
        headers={"X-Api-Key": auth_configured},
    )
    assert resp.status_code == 200
    assert storage.load_global_config()["sonarr_api_key"] == "real-sonarr-key"
    assert storage.load_global_config()["default_target_language"] == "Greek"


def test_post_settings_with_real_value_overwrites_secret(client, mock_config, auth_configured):
    storage.save_global_config({"sonarr_api_key": "old-key"})

    resp = client.post(
        "/api/settings",
        json={"sonarr_api_key": "new-real-key"},
        headers={"X-Api-Key": auth_configured},
    )
    assert resp.status_code == 200
    assert storage.load_global_config()["sonarr_api_key"] == "new-real-key"


# --- Executable-path hardening (routers/settings.py) ------------------------

def test_subtitle_edit_path_rejects_relative_path(client, mock_config, auth_configured):
    resp = client.post(
        "/api/settings",
        json={"subtitle_edit_path": "SubtitleEdit.exe"},
        headers={"X-Api-Key": auth_configured},
    )
    assert resp.status_code == 422


def test_subtitle_edit_path_rejects_non_exe(client, mock_config, auth_configured, tmp_path):
    fake = tmp_path / "SubtitleEdit.bat"
    fake.write_text("echo hi")
    resp = client.post(
        "/api/settings",
        json={"subtitle_edit_path": str(fake)},
        headers={"X-Api-Key": auth_configured},
    )
    assert resp.status_code == 422


def test_subtitle_edit_path_rejects_wrong_basename(client, mock_config, auth_configured, tmp_path):
    fake = tmp_path / "cmd.exe"
    fake.write_text("not really cmd")
    resp = client.post(
        "/api/settings",
        json={"subtitle_edit_path": str(fake)},
        headers={"X-Api-Key": auth_configured},
    )
    assert resp.status_code == 422


def test_subtitle_edit_path_rejects_nonexistent_file(client, mock_config, auth_configured):
    resp = client.post(
        "/api/settings",
        json={"subtitle_edit_path": "C:\\nowhere\\SubtitleEdit.exe"},
        headers={"X-Api-Key": auth_configured},
    )
    assert resp.status_code == 422


def test_subtitle_edit_path_accepts_valid_executable(client, mock_config, auth_configured, tmp_path):
    fake = tmp_path / "SubtitleEdit.exe"
    fake.write_text("not really an exe, just needs to exist")
    resp = client.post(
        "/api/settings",
        json={"subtitle_edit_path": str(fake)},
        headers={"X-Api-Key": auth_configured},
    )
    assert resp.status_code == 200
    assert storage.load_global_config()["subtitle_edit_path"] == str(fake)


def test_subtitle_edit_path_empty_string_allowed(client, mock_config, auth_configured):
    """Clearing the field (disabling the integration) must not be blocked."""
    resp = client.post(
        "/api/settings",
        json={"subtitle_edit_path": ""},
        headers={"X-Api-Key": auth_configured},
    )
    assert resp.status_code == 200


def test_browse_executable_rejects_non_loopback(client, mock_config, auth_configured):
    resp = client.post(
        "/settings/browse-executable",
        headers={"X-Api-Key": auth_configured, "X-Forwarded-For": "8.8.8.8"},
    )
    # TestClient's default client host is "testclient", not a loopback address,
    # so this should be rejected regardless of the spoofable X-Forwarded-For header.
    assert resp.status_code == 403


def test_settings_cannot_set_auth_fields_directly(client, mock_config, auth_configured):
    """auth_enabled/auth_username/auth_password_hash/api_key aren't declared on
    SettingsRequest, so POSTing them through the generic endpoint must be a
    silent no-op — only /api/auth/* may change them."""
    original_key = storage.load_global_config()["api_key"]
    resp = client.post(
        "/api/settings",
        json={
            "auth_enabled": False,
            "auth_username": "hacker",
            "auth_password_hash": "fake-hash",
            "api_key": "attacker-chosen-key",
        },
        headers={"X-Api-Key": auth_configured},
    )
    assert resp.status_code == 200
    config = storage.load_global_config()
    assert config["auth_enabled"] is True
    assert config["auth_username"] == "admin"
    assert config["api_key"] == original_key
