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
