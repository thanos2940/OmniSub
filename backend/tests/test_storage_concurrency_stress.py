"""
Empirical Stress Tests for Storage Modularization, Atomic Operations,
Path Traversal Safeguards, and Episode RLock Synchronization.
"""

import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import pytest

from utils import storage
from utils.storage_modules import (
    atomic_io,
    config_store,
    project_store,
    episode_store,
    subtitle_store,
    stats_and_index,
)


class TestAtomicIOPPathTraversalSafeguards:
    """Stress-test path traversal and malicious name inputs."""

    @pytest.mark.parametrize("malicious_name", [
        "../escape",
        "..\\escape",
        "../../root",
        "..",
        ".",
        "CON",
        "prn",
        "AUX",
        "nul",
        "COM1",
        "com9",
        "LPT1",
        "lpt9",
        "CON.txt",
        "prn.json",
        "aux.dat",
        "foo/bar",
        "foo\\bar",
        "foo\x00bar",
        "has<tag>",
        "has:colon",
        "has\"quote",
        "has|pipe",
        "has?question",
        "has*star",
        " trailing_space ",
        "  leading_space",
        "",
    ])
    def test_safe_name_rejects_adversarial_inputs(self, malicious_name):
        with pytest.raises(ValueError):
            atomic_io.safe_name(malicious_name)

    @pytest.mark.parametrize("valid_name", [
        "Project_Alpha",
        "Project-Beta",
        "Attack on Titan S01E01",
        "Greek Ελληνικά 1080p",
        "Show.Name.2026",
        "Sub_Title_v1.0",
        "日本語アニメ",
    ])
    def test_safe_name_accepts_valid_names(self, valid_name):
        assert atomic_io.safe_name(valid_name) == valid_name

    def test_safe_project_dir_and_safe_episode_dir_containment(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(atomic_io, "PROJECTS_DIR", tmp_path)

        # Valid resolution stays inside root
        proj_dir = atomic_io._safe_project_dir("ValidProject")
        assert proj_dir == (tmp_path / "ValidProject").resolve()
        assert proj_dir.is_relative_to(tmp_path.resolve())

        ep_dir = atomic_io._safe_episode_dir("ValidProject", "S01E01")
        assert ep_dir == (tmp_path / "ValidProject" / "episodes" / "S01E01").resolve()
        assert ep_dir.is_relative_to(tmp_path.resolve())

        # Path traversal attempts via safe functions must raise ValueError
        with pytest.raises(ValueError):
            atomic_io._safe_project_dir("../escaped_project")

        with pytest.raises(ValueError):
            atomic_io._safe_episode_dir("ValidProject", "../escaped_episode")


class TestWriteJsonAtomicConcurrency:
    """Stress-test write_json_atomic with concurrent threads."""

    def test_concurrent_atomic_writes_no_corruption(self, tmp_path):
        target_file = tmp_path / "concurrent_test.json"
        num_threads = 20
        writes_per_thread = 50
        errors = []

        def writer_task(thread_id):
            for i in range(writes_per_thread):
                data = {
                    "thread_id": thread_id,
                    "iteration": i,
                    "timestamp": time.time(),
                    "payload": list(range(100)),
                    "nested": {"a": "test", "b": [1, 2, 3]},
                }
                try:
                    atomic_io.write_json_atomic(target_file, data)
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=writer_task, args=(tid,)) for tid in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Encountered write errors: {errors}"
        assert target_file.exists()

        # Verify final JSON is completely valid and uncorrupted
        with open(target_file, "r", encoding="utf-8") as f:
            final_data = json.load(f)

        assert "thread_id" in final_data
        assert "iteration" in final_data
        assert len(final_data["payload"]) == 100

        # Ensure no dangling temporary files left in parent directory
        tmp_files = list(tmp_path.glob("*.tmp*"))
        assert len(tmp_files) == 0, f"Dangling tmp files found: {tmp_files}"

    def test_write_json_atomic_creates_parent_directories_if_missing(self, tmp_path):
        deep_target = tmp_path / "a" / "b" / "c" / "deep_file.json"
        data = {"deep": True, "status": "ok"}

        atomic_io.write_json_atomic(deep_target, data)

        assert deep_target.exists()
        with open(deep_target, "r", encoding="utf-8") as f:
            assert json.load(f) == data


class TestEpisodeRLockSynchronization:
    """Stress-test get_episode_lock and concurrent reads/writes on episodes."""

    def test_rlock_isolation_and_reentrancy(self):
        lock1 = atomic_io.get_episode_lock("ProjectX", "Episode1")
        lock2 = atomic_io.get_episode_lock("ProjectX", "Episode1")
        lock3 = atomic_io.get_episode_lock("ProjectX", "Episode2")
        lock4 = atomic_io.get_episode_lock("ProjectY", "Episode1")

        # Same project and episode must share the exact same RLock instance
        assert lock1 is lock2
        # Different episodes or projects must have distinct locks
        assert lock1 is not lock3
        assert lock1 is not lock4

        # Verify reentrancy
        with lock1:
            with lock1:
                with lock1:
                    assert True

    def test_concurrent_episode_save_and_load_stress(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(atomic_io, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(project_store, "PROJECTS_DIR", tmp_path)

        project_name = "ConcurrentShow"
        episode_name = "S01E01"
        storage.create_project(project_name, {"target_language": "Greek"})

        num_threads = 16
        ops_per_thread = 40
        read_errors = []
        write_errors = []
        read_counts = [0]
        write_counts = [0]

        def writer_worker(thread_id):
            for i in range(ops_per_thread):
                data = [
                    {
                        "index": j,
                        "original": f"Line {j} from worker {thread_id}",
                        "translated": f"Γραμμή {j} από {thread_id}_{i}",
                        "needs_review": (j % 2 == 0),
                    }
                    for j in range(1, 20)
                ]
                metadata = {
                    "line_count": len(data),
                    "season": 1,
                    "episode": 1,
                    "updated_by": thread_id,
                    "iteration": i,
                }
                try:
                    storage.save_episode(project_name, episode_name, data, metadata=metadata, update_stats=False)
                    write_counts[0] += 1
                except Exception as e:
                    write_errors.append(e)

        def reader_worker():
            for _ in range(ops_per_thread * 2):
                try:
                    ep = storage.load_episode(project_name, episode_name)
                    if ep is not None:
                        assert "data" in ep
                        assert isinstance(ep["data"], list)
                        # Ensure every entry read has valid structure
                        for entry in ep["data"]:
                            assert "original" in entry
                            assert "translations" in entry
                    read_counts[0] += 1
                except Exception as e:
                    read_errors.append(e)

        threads = []
        for tid in range(num_threads // 2):
            threads.append(threading.Thread(target=writer_worker, args=(tid,)))
            threads.append(threading.Thread(target=reader_worker))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(write_errors) == 0, f"Write errors: {write_errors}"
        assert len(read_errors) == 0, f"Read errors: {read_errors}"
        assert write_counts[0] == (num_threads // 2) * ops_per_thread

        # Final verification
        final_ep = storage.load_episode(project_name, episode_name)
        assert final_ep is not None
        assert len(final_ep["data"]) == 19
        assert final_ep["metadata"]["season"] == 1


class TestStorageFacadeAndSubmodulesCompleteness:
    """Verify that storage facade re-exports all essential functions and constants."""

    def test_facade_reexports_all_submodule_methods(self):
        expected_exports = [
            "safe_name",
            "project_dir",
            "episode_dir",
            "ensure_projects_dir",
            "get_episode_lock",
            "write_json_atomic",
            "load_global_config",
            "save_global_config",
            "get_project_setting",
            "list_projects",
            "create_project",
            "load_project_metadata",
            "load_resolved_project_metadata",
            "strip_inherited_context",
            "get_descendant_projects",
            "save_project_metadata",
            "delete_project",
            "episode_has_target",
            "episode_translation_is_stale",
            "list_episodes",
            "list_episodes_with_metadata",
            "save_episode",
            "save_episode_metadata",
            "load_episode_metadata",
            "load_episode",
            "load_episode_data",
            "delete_episode",
            "rename_episode",
            "save_original_subtitle",
            "save_original_srt",
            "load_original_subtitle",
            "load_original_srt",
            "original_subtitle_exists",
            "invalidate_arr_library_cache",
            "update_project_stats_on_change",
            "get_arr_library",
            "get_project_line_frequencies",
        ]
        for name in expected_exports:
            assert hasattr(storage, name), f"storage facade missing attribute {name}"
            assert callable(getattr(storage, name)), f"storage attribute {name} is not callable"
