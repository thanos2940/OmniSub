import pytest
from integrations.path_resolver import PathResolver


def test_resolve_basic_prefix_swap():
    resolver = PathResolver([{"remote": "/data", "local": "P:\\Media"}])
    assert resolver.resolve("/data/Show/ep.mkv") == "P:\\Media\\Show\\ep.mkv"


def test_resolve_does_not_match_sibling_directory_with_shared_prefix():
    """remote="/data" must not match "/database/..." — only a real path-segment
    boundary (exact match or followed by "/") counts as a prefix match."""
    resolver = PathResolver([{"remote": "/data", "local": "P:\\Media"}])
    assert resolver.resolve("/database/Show/ep.mkv") == "/database/Show/ep.mkv"


def test_resolve_exact_prefix_match_no_suffix():
    resolver = PathResolver([{"remote": "/data", "local": "P:\\Media"}])
    assert resolver.resolve("/data") == "P:\\Media"


def test_resolve_trailing_slash_on_remote_prefix():
    resolver = PathResolver([{"remote": "/data/", "local": "P:\\Media"}])
    assert resolver.resolve("/data/Show/ep.mkv") == "P:\\Media\\Show\\ep.mkv"


def test_resolve_no_matching_mapping_returns_unchanged():
    resolver = PathResolver([{"remote": "/data", "local": "P:\\Media"}])
    assert resolver.resolve("/other/Show/ep.mkv") == "/other/Show/ep.mkv"


def test_find_media_file_discovers_renamed_file(tmp_path):
    show_dir = tmp_path / "Show" / "Season 1"
    show_dir.mkdir(parents=True)
    real_file = show_dir / "Show - S01E12 - Title AAC 2.0.mkv"
    real_file.write_text("dummy")

    resolver = PathResolver([{"remote": "/remote", "local": str(tmp_path)}])
    
    # Remote path had EAC3 2.0 instead of AAC 2.0
    stale_remote = f"/remote/Show/Season 1/Show - S01E12 - Title EAC3 2.0.mkv"
    found = resolver.find_media_file(stale_remote, episode_name="S01E12")
    assert found == str(real_file)


def test_is_reachable_empty_path():
    resolver = PathResolver()
    assert resolver.is_reachable("") is False
    assert resolver.is_reachable(None) is False


def test_is_reachable_local_existing_and_missing(tmp_path):
    resolver = PathResolver()
    f = tmp_path / "test.mkv"
    f.write_text("content")
    assert resolver.is_reachable(str(f)) is True
    # Non-existent file in existing parent directory
    assert resolver.is_reachable(str(tmp_path / "missing.mkv")) is True
    # File on valid reachable drive
    assert resolver.is_reachable(str(tmp_path / "missing_dir" / "missing.mkv")) is True


def test_is_reachable_unc_share_without_series_subfolder(monkeypatch):
    from pathlib import Path
    resolver = PathResolver()

    # Mock Path.exists so UNC share root exists, but series subfolder does not
    original_exists = Path.exists

    def mock_exists(p):
        path_str = str(p).rstrip("\\/")
        if path_str == r"\\Morpiceserver\tv":
            return True
        if "NonExistentShow" in str(p):
            return False
        if "OfflineServer" in str(p):
            return False
        return original_exists(p)

    monkeypatch.setattr(Path, "exists", mock_exists)

    # A newly monitored series folder that doesn't exist on disk yet
    assert resolver.is_reachable(r"\\Morpiceserver\tv\NonExistentShow\S01E01.mkv") is True
    assert resolver.is_reachable(r"\\Morpiceserver\tv") is True
    assert resolver.is_reachable(r"\\OfflineServer\share\Show\ep.mkv") is False


def test_is_reachable_drive_letter(monkeypatch):
    from pathlib import Path
    resolver = PathResolver()

    original_exists = Path.exists

    def mock_exists(p):
        path_str = str(p).rstrip("\\/")
        if path_str.upper() in ("Z:", "Z:\\"):
            return True
        if path_str.upper() in ("X:", "X:\\"):
            return False
        return original_exists(p)

    monkeypatch.setattr(Path, "exists", mock_exists)

    assert resolver.is_reachable(r"Z:\NewShow\Season 1\ep.mkv") is True
    assert resolver.is_reachable(r"X:\NewShow\Season 1\ep.mkv") is False


