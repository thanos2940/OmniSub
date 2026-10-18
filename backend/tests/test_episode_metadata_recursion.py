"""load_episode_metadata must not recurse through original_subtitle_exists.

For an episode with no cues yet (every subtitle-less placeholder a sync creates)
the two functions used to call each other until the recursion limit tripped and a
bare except swallowed it — ~500 frames and ~1000 path resolutions per read, which
made a no-op sync of a one-series library take seconds.
"""
from utils import storage
from utils.storage_modules import atomic_io


def _placeholder(project="Show", episode="S01E05"):
    storage.create_project(project, {"target_language": "Greek"})
    storage.episode_dir(project, episode).mkdir(parents=True, exist_ok=True)
    storage.save_episode(project, episode, [], {"has_source_sub": False, "line_count": 0})


def test_reading_a_placeholder_touches_the_disk_a_handful_of_times(monkeypatch):
    _placeholder()
    real = atomic_io._safe_episode_dir
    calls = []
    monkeypatch.setattr(
        "utils.storage_modules.episode_store._safe_episode_dir",
        lambda *a: calls.append(a) or real(*a),
    )

    meta = storage.load_episode_metadata("Show", "S01E05")

    assert meta is not None
    assert len(calls) <= 2, f"{len(calls)} directory resolutions for one metadata read"


def test_source_flag_still_follows_the_original_file():
    _placeholder()
    assert not storage.load_episode_metadata("Show", "S01E05").get("has_source_sub")

    (storage.episode_dir("Show", "S01E05") / "original.ass").write_text("[Script Info]\n", encoding="utf-8")
    assert storage.load_episode_metadata("Show", "S01E05")["has_source_sub"] is True
