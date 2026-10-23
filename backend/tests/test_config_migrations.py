"""One-time settings migrations (utils/config_migrations.py)."""
import json

import pytest

from services import agent_cli
from utils.config_migrations import APPLIED_KEY, MIGRATIONS, apply_config_migrations
from utils.storage_modules import config_store


def _write(cfg):
    path = config_store._get_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg), encoding="utf-8")


def _read():
    return json.loads(config_store._get_config_file().read_text(encoding="utf-8"))


@pytest.fixture
def agy_installed(monkeypatch):
    monkeypatch.setattr(agent_cli, "engine_command", lambda engine: ["agy"] if engine == "antigravity" else None)


@pytest.fixture
def no_agent_cli(monkeypatch):
    monkeypatch.setattr(agent_cli, "engine_command", lambda engine: None)


def test_install_from_before_the_overhaul(agy_installed):
    """The owner's server config: Gemini worker paused, legacy smart auto-translate, releases channel."""
    _write({"translation_engine": "model", "queue_worker_paused": True, "arr_auto_translate": "smart",
            "arr_non_anime_delay_minutes": 30, "autopilot_enabled": False, "autopilot_backlog_mode": "off",
            "update_channel": "releases", "sonarr_url": "http://sonarr:8989"})

    changes = apply_config_migrations()

    cfg = _read()
    assert cfg["translation_engine"] == "agent" and "queue_worker_paused" not in cfg
    assert cfg["autopilot_enabled"] is True and cfg["autopilot_backlog_mode"] == "off" and cfg["autopilot_enabled_at"]
    assert "arr_auto_translate" not in cfg and "arr_non_anime_delay_minutes" not in cfg
    assert cfg["update_channel"] == "auto"
    assert cfg["sonarr_url"] == "http://sonarr:8989"  # everything else untouched
    assert cfg[APPLIED_KEY] == [mid for mid, _ in MIGRATIONS]
    assert len(changes) == 3
    assert config_store.GLOBAL_CONFIG["translation_engine"] == "agent"


def test_runs_once(agy_installed):
    _write({"translation_engine": "model", "arr_auto_translate": "smart"})
    apply_config_migrations()
    # The owner turns the Autopilot off again: a later start must not switch it back on.
    cfg = _read()
    cfg["autopilot_enabled"] = False
    _write(cfg)
    assert apply_config_migrations() == []
    assert _read()["autopilot_enabled"] is False


def test_legacy_mode_off_only_drops_the_keys(agy_installed):
    _write({"translation_engine": "agent", "arr_auto_translate": False, "arr_non_anime_delay_minutes": 30})
    apply_config_migrations()
    cfg = _read()
    assert "arr_auto_translate" not in cfg and "arr_non_anime_delay_minutes" not in cfg
    assert not cfg.get("autopilot_enabled")


def test_engine_switch_waits_for_an_agent_cli(no_agent_cli, monkeypatch):
    _write({"translation_engine": "model", "queue_worker_paused": True})
    apply_config_migrations()
    cfg = _read()
    assert cfg["translation_engine"] == "model" and cfg["queue_worker_paused"] is True
    assert "2026-10-19-agent-engine" not in cfg[APPLIED_KEY]

    monkeypatch.setattr(agent_cli, "engine_command", lambda engine: ["claude"] if engine == "claude" else None)
    apply_config_migrations()
    cfg = _read()
    assert cfg["translation_engine"] == "agent" and "queue_worker_paused" not in cfg
    assert "2026-10-19-agent-engine" in cfg[APPLIED_KEY]


def test_nothing_to_write_when_only_deferred_migrations_remain(no_agent_cli):
    _write({"translation_engine": "model"})
    apply_config_migrations()
    path = config_store._get_config_file()
    before = path.stat().st_mtime_ns
    assert apply_config_migrations() == []
    assert path.stat().st_mtime_ns == before


def test_main_channel_is_kept(agy_installed):
    _write({"update_channel": "main"})
    apply_config_migrations()
    assert _read()["update_channel"] == "main"


def test_sampling_follows_google_defaults(agy_installed):
    """The old defaults OmbiSub wrote (0.3 / 64 / 0.95) go, from the settings and the projects; values
    someone chose stay."""
    from utils import storage
    from utils.storage_modules import project_store
    storage.create_project("Kept", {"show_name": "Kept"})
    storage.create_project("Old", {"show_name": "Old"})
    for name, sampling in (("Kept", {"temperature": 0.7, "top_k": 64}), ("Old", {"temperature": 0.3, "top_k": 64, "top_p": 0.95})):
        meta = project_store.load_project_metadata(name)
        meta["settings"].update(sampling)
        project_store.save_project_metadata(name, meta)
    _write({"translation_engine": "agent", "temperature": 0.3, "top_k": 64, "top_p": 0.8, "max_output_tokens": 8192})

    changes = apply_config_migrations()

    cfg = _read()
    assert "temperature" not in cfg and "top_k" not in cfg and "max_output_tokens" not in cfg
    assert cfg["top_p"] == 0.8
    kept = project_store.load_project_metadata("Kept")["settings"]
    old = project_store.load_project_metadata("Old")["settings"]
    assert kept["temperature"] == 0.7 and "top_k" not in kept
    assert not {"temperature", "top_k", "top_p"} & set(old)
    assert any("google-sampling" in c for c in changes)


def test_new_projects_store_no_sampling():
    from utils import storage
    meta = storage.create_project("Fresh", {"show_name": "Fresh"})
    assert not {"temperature", "top_k", "top_p"} & set(meta["settings"])
