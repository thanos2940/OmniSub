import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from main import app
from utils import storage
from integrations.subtitle_scanner import SubtitleScannerService
from integrations.media_sync_engine import MediaSyncEngine
from routers.schemas import ArrSyncRequest

client = TestClient(app)

def test_subtitle_scanner_include_ass_toggle(tmp_path):
    media_dir = tmp_path / "Show" / "Season 01"
    media_dir.mkdir(parents=True)
    video_file = media_dir / "Show.S01E01.mkv"
    video_file.touch()

    ass_file = media_dir / "Show.S01E01.en.ass"
    ass_file.write_text("[Script Info]\nTitle: test", encoding="utf-8")

    # With include_ass=True (default)
    scanner_with_ass = SubtitleScannerService(include_ass=True)
    res_with = scanner_with_ass.scan_media_file(str(video_file))
    assert res_with.has_source_sub is True
    assert res_with.source_sub_path == str(ass_file)

    # With include_ass=False
    scanner_no_ass = SubtitleScannerService(include_ass=False)
    res_without = scanner_no_ass.scan_media_file(str(video_file))
    assert res_without.has_source_sub is False
    assert res_without.source_sub_path is None

    # Now add an SRT file
    srt_file = media_dir / "Show.S01E01.en.srt"
    srt_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nHi", encoding="utf-8")

    # Scanner with ASS prefers ASS
    res_both_with_ass = scanner_with_ass.scan_media_file(str(video_file))
    assert res_both_with_ass.source_sub_path == str(ass_file)

    # Scanner without ASS picks SRT only
    res_both_no_ass = scanner_no_ass.scan_media_file(str(video_file))
    assert res_both_no_ass.has_source_sub is True
    assert res_both_no_ass.source_sub_path == str(srt_file)


def test_media_sync_engine_scan_ass_toggle():
    engine_with_ass = MediaSyncEngine(embedded_extraction=True, scan_ass=True)
    assert engine_with_ass.scan_ass is True
    assert engine_with_ass.scanner.include_ass is True
    assert engine_with_ass.embedded_extraction is True

    engine_no_ass = MediaSyncEngine(embedded_extraction=True, scan_ass=False)
    assert engine_no_ass.scan_ass is False
    assert engine_no_ass.scanner.include_ass is False
    assert engine_no_ass.embedded_extraction is False


def test_sync_endpoints_accept_options(monkeypatch):
    storage.save_global_config({"sonarr_api_key": "test-key", "radarr_api_key": "test-key"})
    
    # Test POST /integrations/arr/sync with custom options
    payload = {
        "scan_ass": False,
        "extract_embedded_ass": False,
        "source": "sonarr"
    }
    res = client.post("/integrations/arr/sync", json=payload)
    assert res.status_code == 200
    assert "job_id" in res.json()

    # Create dummy project
    p_name = "_test_sync_opt_proj"
    try:
        storage.create_project(p_name, {"target_language": "Greek"})
        res_proj = client.post(f"/projects/{p_name}/sync", json=payload)
        assert res_proj.status_code == 200
        assert "job_id" in res_proj.json()
    finally:
        storage.delete_project(p_name)
