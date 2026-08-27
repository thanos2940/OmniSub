import os
import shutil
import tempfile
import pytest
from pathlib import Path

@pytest.fixture(autouse=True)
def isolate_test_data_dir(monkeypatch, tmp_path):
    """Isolate mutable state (config.json, projects, db) for every test run to prevent corrupting production config."""
    test_data_dir = tmp_path / "omnisub_test_data"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    
    # Set env var
    monkeypatch.setenv("OMNISUB_DATA_DIR", str(test_data_dir))
    
    # Patch paths module
    from utils import paths, storage
    monkeypatch.setattr(paths, "DATA_DIR", test_data_dir)
    monkeypatch.setattr(paths, "PROJECTS_DIR", test_data_dir / "projects")
    monkeypatch.setattr(paths, "CONFIG_FILE", test_data_dir / "config.json")
    monkeypatch.setattr(paths, "DB_FILE", test_data_dir / "omnisub.db")
    monkeypatch.setattr(paths, "SESSIONS_DB_FILE", test_data_dir / "omnisub_sessions.db")
    monkeypatch.setattr(paths, "RATE_LIMITER_STATE_FILE", test_data_dir / "rate_limiter_state.json")
    monkeypatch.setattr(paths, "TRANSLATION_MEMORY_DIR", test_data_dir / "translation_memory")
    
    # Patch storage module references
    monkeypatch.setattr(storage, "PROJECTS_DIR", test_data_dir / "projects")
    monkeypatch.setattr(storage, "CONFIG_FILE", test_data_dir / "config.json")
    
    (test_data_dir / "projects").mkdir(parents=True, exist_ok=True)
    (test_data_dir / "translation_memory").mkdir(parents=True, exist_ok=True)
    
    yield test_data_dir
