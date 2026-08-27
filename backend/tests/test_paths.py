import importlib
from pathlib import Path

from utils import paths


def test_default_data_dir_is_backend_root(monkeypatch):
    monkeypatch.delenv("OMNISUB_DATA_DIR", raising=False)
    reloaded = importlib.reload(paths)
    try:
        assert reloaded.DATA_DIR == reloaded.BACKEND_ROOT
        assert reloaded.PROJECTS_DIR == reloaded.BACKEND_ROOT / "projects"
        assert reloaded.CONFIG_FILE == reloaded.BACKEND_ROOT / "config.json"
    finally:
        importlib.reload(paths)  # restore real state for subsequent tests


def test_omnisub_data_dir_relocates_everything(monkeypatch, tmp_path):
    data_dir = tmp_path / "omnisub-data"
    monkeypatch.setenv("OMNISUB_DATA_DIR", str(data_dir))
    reloaded = importlib.reload(paths)
    try:
        assert reloaded.DATA_DIR == data_dir.resolve()
        assert reloaded.PROJECTS_DIR == data_dir.resolve() / "projects"
        assert reloaded.CONFIG_FILE == data_dir.resolve() / "config.json"
        assert reloaded.DB_FILE == data_dir.resolve() / "omnisub.db"
        assert reloaded.SESSIONS_DB_FILE == data_dir.resolve() / "omnisub_sessions.db"
        assert reloaded.RATE_LIMITER_STATE_FILE == data_dir.resolve() / "rate_limiter_state.json"
        assert reloaded.TRANSLATION_MEMORY_DIR == data_dir.resolve() / "translation_memory"
        # The module creates the directory eagerly so downstream mkdir(exist_ok=True) calls never race.
        assert data_dir.is_dir()
    finally:
        monkeypatch.delenv("OMNISUB_DATA_DIR", raising=False)
        importlib.reload(paths)
