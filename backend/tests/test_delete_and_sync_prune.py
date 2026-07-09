"""Dashboard delete = translation-only; Sync prunes removed-source episodes.

- Deleting from the dashboard removes only the translation (in-app + exported .srt) and
  leaves the original subtitle intact, in-app and on disk.
- Sync removes arr-sourced episodes whose source subtitle is gone from disk (and their
  exported translations), but keeps manual uploads and is safe against a total scan miss.
"""

import os

import pytest

from utils import storage
from routers.episodes import _delete_episode_translation
from integrations.media_sync_engine import MediaSyncEngine, SyncResult


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path / "projects")
    storage.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    name = "Show"
    storage.create_project(name, {"show_name": name, "target_language": "Greek"})
    yield name


def _rows():
    return [
        {"index": 1, "original": "Hello", "translated": "Γεια", "translations": {"el": "Γεια"}},
        {"index": 2, "original": "World", "translated": "Κόσμε", "translations": {"el": "Κόσμε"}},
    ]


# --- delete = translation-only -------------------------------------------------

def test_delete_keeps_original_removes_translation_and_target_file(proj, tmp_path):
    target = tmp_path / "Show.S01E01.el.srt"
    target.write_text("1\n00:00:01,000 --> 00:00:02,000\nΓεια\n", encoding="utf-8")
    storage.save_episode(proj, "S01E01", _rows(), {
        "arr_source": True, "translated": True,
        "arr_has_target": True, "arr_target_path": str(target),
    })

    assert _delete_episode_translation(proj, "S01E01") is True

    data = storage.load_episode(proj, "S01E01")
    meta = storage.load_episode_metadata(proj, "S01E01")
    # Episode still exists, originals intact.
    assert data is not None
    assert [l["original"] for l in data["data"]] == ["Hello", "World"]
    # Translation cleared in-app...
    assert all(not l.get("translated") and not l.get("translations") for l in data["data"])
    assert meta.get("translated") is False
    # ...exported file deleted, target flags cleared.
    assert not target.exists()
    assert "arr_target_path" not in meta and "arr_has_target" not in meta


def test_delete_missing_episode_returns_false(proj):
    assert _delete_episode_translation(proj, "S09E09") is False


# --- sync prune ----------------------------------------------------------------

def _engine():
    eng = MediaSyncEngine.__new__(MediaSyncEngine)  # no network deps needed for prune
    return eng


def test_prune_removes_unseen_arr_episode_and_its_target(proj, tmp_path):
    target = tmp_path / "Show.S01E02.el.srt"
    target.write_text("x", encoding="utf-8")
    storage.save_episode(proj, "S01E01", _rows(), {"arr_source": True})
    storage.save_episode(proj, "S01E02", _rows(), {"arr_source": True, "arr_target_path": str(target)})

    result = SyncResult()
    # Only S01E01 was seen on disk this sync → S01E02 is pruned.
    removed = _engine()._prune_removed_episodes(proj, {"S01E01"}, result)

    assert removed == 1
    assert "S01E01" in storage.list_episodes(proj)
    assert "S01E02" not in storage.list_episodes(proj)
    assert not target.exists()  # exported translation removed too


def test_prune_leaves_manual_uploads(proj):
    storage.save_episode(proj, "S01E01", _rows(), {"arr_source": True})
    storage.save_episode(proj, "MANUAL", _rows(), {})  # no arr_source → manual upload

    result = SyncResult()
    removed = _engine()._prune_removed_episodes(proj, {"S01E01"}, result)

    assert removed == 0
    assert "MANUAL" in storage.list_episodes(proj)


def test_prune_safety_valve_when_nothing_seen(proj):
    storage.save_episode(proj, "S01E01", _rows(), {"arr_source": True})
    storage.save_episode(proj, "S01E02", _rows(), {"arr_source": True})

    result = SyncResult()
    # Empty seen-set + existing episodes ⇒ suspected transient failure ⇒ no removal.
    removed = _engine()._prune_removed_episodes(proj, set(), result)

    assert removed == 0
    assert set(storage.list_episodes(proj)) == {"S01E01", "S01E02"}
    assert result.warnings and "skipping episode" in result.warnings[0]
