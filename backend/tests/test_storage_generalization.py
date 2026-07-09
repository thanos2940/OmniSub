import pytest
from pathlib import Path
from utils import storage

SAMPLE_ASS_CONTENT = """[Script Info]
Title: Sample
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:02.00,0:00:04.00,Default,Alice,0,0,0,,Are you sure?
"""

SAMPLE_SRT_CONTENT = """1
00:00:02,000 --> 00:00:04,000
Are you sure?
"""

@pytest.fixture
def temp_projects_dir(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)
    return projects_dir

def test_save_original_subtitle_detect_from_filename(temp_projects_dir):
    project_name = "test_proj"
    episode_name = "E01"
    
    # Test saving with ASS filename
    storage.save_original_subtitle(project_name, episode_name, SAMPLE_ASS_CONTENT, filename="video.ass")
    
    episode_dir = temp_projects_dir / project_name / "episodes" / episode_name
    assert (episode_dir / "original.ass").exists()
    assert not (episode_dir / "original.srt").exists()
    
    # Check metadata
    meta = storage.load_episode_metadata(project_name, episode_name)
    assert meta is not None
    assert meta.get("original_extension") == "ass"
    assert meta.get("original_format") == "ass"

    # Test saving with SSA filename
    storage.save_original_subtitle(project_name, episode_name, SAMPLE_ASS_CONTENT, filename="video.ssa")
    assert (episode_dir / "original.ssa").exists()
    # The previous original.ass should have been cleaned up
    assert not (episode_dir / "original.ass").exists()
    
    meta = storage.load_episode_metadata(project_name, episode_name)
    assert meta.get("original_extension") == "ssa"
    assert meta.get("original_format") == "ass"

def test_save_original_subtitle_detect_from_content(temp_projects_dir):
    project_name = "test_proj"
    episode_name = "E02"
    
    # Sniff ASS content
    storage.save_original_subtitle(project_name, episode_name, SAMPLE_ASS_CONTENT)
    episode_dir = temp_projects_dir / project_name / "episodes" / episode_name
    assert (episode_dir / "original.ass").exists()
    
    meta = storage.load_episode_metadata(project_name, episode_name)
    assert meta.get("original_extension") == "ass"
    assert meta.get("original_format") == "ass"

    # Sniff SRT content
    storage.save_original_subtitle(project_name, episode_name, SAMPLE_SRT_CONTENT)
    assert (episode_dir / "original.srt").exists()
    assert not (episode_dir / "original.ass").exists()
    
    meta = storage.load_episode_metadata(project_name, episode_name)
    assert meta.get("original_extension") == "srt"
    assert meta.get("original_format") == "srt"

def test_load_original_subtitle_metadata_and_fallback(temp_projects_dir):
    project_name = "test_proj"
    episode_name = "E03"
    episode_dir = temp_projects_dir / project_name / "episodes" / episode_name
    episode_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Metadata first: Write a file and create matching metadata
    (episode_dir / "original.ass").write_text(SAMPLE_ASS_CONTENT, encoding="utf-8")
    storage.write_json_atomic(episode_dir / "metadata.json", {
        "original_extension": "ass",
        "original_format": "ass"
    })
    
    loaded = storage.load_original_srt(project_name, episode_name)
    assert loaded == SAMPLE_ASS_CONTENT
    
    # 2. Disk fallback: Remove metadata and ensure it still finds it
    storage.write_json_atomic(episode_dir / "metadata.json", {})
    loaded = storage.load_original_srt(project_name, episode_name)
    assert loaded == SAMPLE_ASS_CONTENT

def test_original_subtitle_exists(temp_projects_dir):
    project_name = "test_proj"
    episode_name = "E04"
    episode_dir = temp_projects_dir / project_name / "episodes" / episode_name
    
    assert not storage.original_subtitle_exists(project_name, episode_name)
    
    # Save a subtitle
    storage.save_original_subtitle(project_name, episode_name, SAMPLE_SRT_CONTENT, filename="ep.srt")
    assert storage.original_subtitle_exists(project_name, episode_name)
    
    # Test checking logic in _index_episode
    storage.save_episode(project_name, episode_name, [], update_stats=False)
    # Ensure index check doesn't crash and returns correct presence (this is a best-effort check)
    storage._index_episode(project_name, episode_name, {})
