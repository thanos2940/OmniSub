"""
Unit tests for storage modularization and load_episode_data method.
"""

import os
import json
import pytest
import tempfile
from pathlib import Path

from utils import storage
from utils.storage_modules import (
    atomic_io,
    config_store,
    project_store,
    episode_store,
    subtitle_store,
    stats_and_index,
)


def test_safe_name_validation():
    assert atomic_io.safe_name("My Show") == "My Show"
    assert atomic_io.safe_name("Attack on Titan S01E01") == "Attack on Titan S01E01"
    
    with pytest.raises(ValueError):
        atomic_io.safe_name("../escape")
    with pytest.raises(ValueError):
        atomic_io.safe_name("CON")
    with pytest.raises(ValueError):
        atomic_io.safe_name("PRN.txt")
    with pytest.raises(ValueError):
        atomic_io.safe_name("invalid/name")


def test_write_json_atomic(tmp_path):
    target_file = tmp_path / "test.json"
    data = {"key": "value", "numbers": [1, 2, 3]}
    atomic_io.write_json_atomic(target_file, data)

    assert target_file.exists()
    with open(target_file, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded == data


def test_get_episode_lock():
    lock1 = atomic_io.get_episode_lock("ProjectA", "S01E01")
    lock2 = atomic_io.get_episode_lock("ProjectA", "S01E01")
    lock3 = atomic_io.get_episode_lock("ProjectA", "S01E02")

    assert lock1 is lock2
    assert lock1 is not lock3


def test_load_episode_data_normalizes_translations(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(atomic_io, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(project_store, "PROJECTS_DIR", tmp_path)

    project_name = "TestProject"
    episode_name = "S01E01"
    storage.create_project(project_name, {"target_language": "Greek"})

    # Save episode with 'translated' field
    data = [
        {"index": 1, "original": "Hello", "translated": "Γεια σας"},
        {"index": 2, "original": "Goodbye", "translations": {"el": "Αντίο"}},
    ]
    storage.save_episode(project_name, episode_name, data)

    # Call load_episode_data
    loaded_data = storage.load_episode_data(project_name, episode_name)
    assert loaded_data is not None
    assert len(loaded_data) == 2
    assert loaded_data[0]["original"] == "Hello"
    assert loaded_data[0]["translation"] == "Γεια σας"
    assert loaded_data[1]["original"] == "Goodbye"
    assert loaded_data[1]["translation"] == "Αντίο"


def test_universe_inheritance_and_cycle_prevention(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(atomic_io, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(project_store, "PROJECTS_DIR", tmp_path)

    # Parent project
    storage.create_project("ParentUniv", {
        "glossary": {"terms": [{"term": "Hero", "translation": "Ήρωας"}]},
        "context_guide": "Parent guide"
    })

    # Child project
    storage.create_project("ChildShow", {
        "parent_project": "ParentUniv",
        "glossary": {"terms": [{"term": "Villain", "translation": "Κακός"}]},
        "context_guide": "Child guide"
    })

    resolved = storage.load_resolved_project_metadata("ChildShow")
    terms = {t["term"]: t["translation"] for t in resolved["glossary"]["terms"]}
    assert terms["Hero"] == "Ήρωας"
    assert terms["Villain"] == "Κακός"
    assert "Parent guide" in resolved["context_guide"]
    assert "Child guide" in resolved["context_guide"]

    # Test cycle safety (A -> B -> A)
    storage.save_project_metadata("ParentUniv", {
        "parent_project": "ChildShow",
        "glossary": {"terms": [{"term": "Hero", "translation": "Ήρωας"}]}
    })
    # Must not raise RecursionError
    cycle_resolved = storage.load_resolved_project_metadata("ChildShow")
    assert cycle_resolved is not None
