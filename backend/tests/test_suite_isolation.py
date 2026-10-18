"""Guards the suite's own isolation (see the top of conftest.py).

Before this held, running the tests wrote queue rows into the real omnisub.db —
which the live worker then tried to translate — plus test projects into the real
library and test noise into the real log.
"""
import inspect
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def _outside_backend(path) -> bool:
    return not Path(path).resolve().is_relative_to(BACKEND)


def test_import_time_db_defaults_point_away_from_the_real_queue():
    from utils import media_probe_cache
    from utils.translation_queue import TranslationQueue

    assert _outside_backend(inspect.signature(TranslationQueue.__init__).parameters["db_path"].default)
    assert _outside_backend(inspect.signature(media_probe_cache.get).parameters["db_path"].default)


def test_storage_writes_land_outside_the_real_library():
    from utils import storage

    assert _outside_backend(storage.project_dir("ZZ_isolation_probe"))


def test_the_app_log_file_is_not_the_real_one():
    import main  # noqa: F401 - importing installs the file handler

    files = [h.baseFilename for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
    assert files and all(_outside_backend(f) for f in files)


def test_the_real_gemini_key_is_never_loaded():
    import os

    import main  # noqa: F401 - main runs load_dotenv at import

    assert os.environ["GOOGLE_API_KEY"].startswith("fake-")
