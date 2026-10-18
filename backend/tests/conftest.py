import atexit
import os
import shutil
import tempfile
import pytest
from pathlib import Path

# Must run before any backend module is imported. Several of them bind
# utils.paths values at import time — TranslationQueue's and media_probe_cache's
# default db_path, main.py's log file handler, module-level config reads — and a
# per-test monkeypatch cannot reach a default argument. Pointing the whole session
# at a scratch dir first is what keeps the suite out of the real queue, library,
# config and logs. The fake key stops main.py's load_dotenv from pulling the real
# Gemini key in (it never overrides a variable that is already set).
_SESSION_DATA_DIR = tempfile.mkdtemp(prefix="omnisub-tests-")
os.environ["OMNISUB_DATA_DIR"] = _SESSION_DATA_DIR
os.environ["GOOGLE_API_KEY"] = "fake-test-key-for-unit-tests"
atexit.register(shutil.rmtree, _SESSION_DATA_DIR, ignore_errors=True)

@pytest.fixture(autouse=True)
def isolate_test_data_dir(monkeypatch, tmp_path):
    """Isolate mutable state (config.json, projects, db) for every test run to prevent corrupting production config."""
    test_data_dir = tmp_path / "omnisub_test_data"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    
    # Set env var
    monkeypatch.setenv("OMNISUB_DATA_DIR", str(test_data_dir))
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-test-key-for-unit-tests")
    
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
    # The storage submodules captured PROJECTS_DIR at import. atomic_io's resolver
    # returns its own copy whenever paths/storage agree on a value that differs from
    # it — which is exactly the state the two patches above create — so without these
    # every storage write in the suite landed in the real backend/projects library.
    from utils.storage_modules import atomic_io, episode_store, project_store
    for module in (atomic_io, episode_store, project_store):
        monkeypatch.setattr(module, "PROJECTS_DIR", test_data_dir / "projects")
    
    (test_data_dir / "projects").mkdir(parents=True, exist_ok=True)
    (test_data_dir / "translation_memory").mkdir(parents=True, exist_ok=True)

    yield test_data_dir


@pytest.fixture(autouse=True)
def no_real_plex_refresh():
    """Exports ask Plex for a rescan; a test that switches Plex on must never reach a real server.

    Tests that look at the refresh install their own refresher with a fake client.
    """
    from services import plex_refresh
    plex_refresh.set_refresher(plex_refresh.PlexRefresher(delay=0, client_factory=lambda cfg: None))
    yield
    plex_refresh.set_refresher(None)
