"""
Adversarial Stress-Testing Suite for Milestone 1 Remediation:
1. INT-P1-02: Rapid Webhook Deduplication under High Concurrency Bursts (50 rapid requests) & Window Expiry.
2. INT-P1-06: Webhook Payload Schema Fuzzing (missing, null, invalid types, empty) ensuring HTTP 400.
3. INT-P1-03: Subtitle Atomic Write Sharing Violation Retry & Cleanup under simulated WinError 32 locks.
"""

import os
import sys
import time
import uuid
import queue
import shutil
import logging
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from main import app
from utils import storage
from routers.integrations import _webhook_deduplicator, WebhookDeduplicator
from integrations.media_sync_engine import MediaSyncEngine
from integrations.embedded_subs import write_sidecar


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Setup isolated test configuration and client."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = data_dir / "config.json"
    monkeypatch.setattr("utils.storage.CONFIG_FILE", cfg_file)
    storage.save_global_config({"webhook_secret": "test-secret-42"})
    _webhook_deduplicator.clear()
    return TestClient(app)


# ==============================================================================
# 1. INT-P1-02: Webhook Deduplication High Concurrency Bursts (50 requests)
# ==============================================================================

def test_sonarr_webhook_50_concurrent_burst_deduplication(client, monkeypatch):
    """
    Stress test: 50 identical Sonarr webhooks dispatched concurrently across 20 worker threads.
    Requirement: Exactly 1 request is queued and 49 are deduplicated (ignored).
    The background processor must be scheduled exactly ONCE.
    """
    mock_process = AsyncMock()
    monkeypatch.setattr("routers.integrations._process_sonarr_webhook", mock_process)

    payload = {
        "eventType": "Download",
        "series": {"id": 1001, "title": "Attack on Titan"},
        "episodes": [{"id": 5001, "seasonNumber": 1, "episodeNumber": 1}],
        "episodeFile": {"id": 9001, "path": "/media/AoT/S01E01.mkv"},
    }
    headers = {"X-OmniSub-Token": "test-secret-42"}

    responses = []
    num_requests = 50

    def send_webhook(req_id):
        # We use fresh test client requests in concurrent threads
        resp = client.post("/api/webhook/sonarr", json=payload, headers=headers)
        return resp.status_code, resp.json()

    # Launch 50 requests simultaneously across 20 threads
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(send_webhook, i) for i in range(num_requests)]
        for f in as_completed(futures):
            responses.append(f.result())

    # Validate results
    assert len(responses) == 50, f"Expected 50 responses, got {len(responses)}"
    all_200 = all(code == 200 for code, _ in responses)
    assert all_200, "All 50 responses should return HTTP 200"

    queued_responses = [body for code, body in responses if body.get("status") == "queued"]
    ignored_responses = [body for code, body in responses if body.get("status") == "ignored" and body.get("reason") == "duplicate"]

    assert len(queued_responses) == 1, (
        f"CRITICAL RACE CONDITION: Expected exactly 1 queued event, but got {len(queued_responses)}! "
        f"Ignored count: {len(ignored_responses)}"
    )
    assert len(ignored_responses) == 49, (
        f"Expected exactly 49 deduplicated events, but got {len(ignored_responses)}"
    )

    # Ensure background processor was only scheduled for the single winner
    assert mock_process.call_count == 1, (
        f"Background worker was invoked {mock_process.call_count} times instead of 1!"
    )


def test_radarr_webhook_50_concurrent_burst_deduplication(client, monkeypatch):
    """
    Stress test: 50 identical Radarr webhooks dispatched concurrently across 20 worker threads.
    Requirement: Exactly 1 request is queued and 49 are deduplicated.
    """
    mock_process = AsyncMock()
    monkeypatch.setattr("routers.integrations._process_radarr_webhook", mock_process)

    payload = {
        "eventType": "Download",
        "movie": {"id": 2002, "title": "Interstellar"},
        "movieFile": {"id": 8002, "path": "/media/Movies/Interstellar.mkv"},
    }
    headers = {"X-OmniSub-Token": "test-secret-42"}

    responses = []
    num_requests = 50

    def send_webhook(req_id):
        resp = client.post("/api/webhook/radarr", json=payload, headers=headers)
        return resp.status_code, resp.json()

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(send_webhook, i) for i in range(num_requests)]
        for f in as_completed(futures):
            responses.append(f.result())

    assert len(responses) == 50
    queued = [body for code, body in responses if body.get("status") == "queued"]
    ignored = [body for code, body in responses if body.get("status") == "ignored" and body.get("reason") == "duplicate"]

    assert len(queued) == 1, f"Expected 1 queued event, got {len(queued)}"
    assert len(ignored) == 49, f"Expected 49 deduplicated events, got {len(ignored)}"
    assert mock_process.call_count == 1


def test_multi_entity_50_concurrent_burst(client, monkeypatch):
    """
    Stress test: 50 requests across 10 distinct entities (5 duplicates per entity) concurrently.
    Requirement: Exactly 10 queued (1 per entity), exactly 40 deduplicated.
    """
    mock_process = AsyncMock()
    monkeypatch.setattr("routers.integrations._process_sonarr_webhook", mock_process)

    headers = {"X-OmniSub-Token": "test-secret-42"}

    # 10 entities, 5 requests each = 50 requests total
    tasks = []
    for entity_idx in range(10):
        series_id = 100 + entity_idx
        ep_file_id = 1000 + entity_idx
        p = {
            "eventType": "Download",
            "series": {"id": series_id, "title": f"Series {entity_idx}"},
            "episodes": [{"id": 500 + entity_idx, "seasonNumber": 1, "episodeNumber": entity_idx + 1}],
            "episodeFile": {"id": ep_file_id, "path": f"/media/Series{entity_idx}.mkv"},
        }
        for dup_idx in range(5):
            tasks.append((series_id, ep_file_id, p))

    responses = []
    def send_req(item):
        _, _, payload = item
        resp = client.post("/api/webhook/sonarr", json=payload, headers=headers)
        return resp.status_code, resp.json()

    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(send_req, t) for t in tasks]
        for f in as_completed(futures):
            responses.append(f.result())

    assert len(responses) == 50
    queued = [body for code, body in responses if body.get("status") == "queued"]
    ignored = [body for code, body in responses if body.get("status") == "ignored" and body.get("reason") == "duplicate"]

    assert len(queued) == 10, f"Expected 10 distinct queued requests, got {len(queued)}"
    assert len(ignored) == 40, f"Expected 40 deduplicated requests, got {len(ignored)}"
    assert mock_process.call_count == 10


def test_sliding_window_60s_expiry_and_memory_eviction(client, monkeypatch):
    """
    Test sliding window expiry at 60s boundary and verify memory eviction under high key load.
    """
    mock_process = AsyncMock()
    monkeypatch.setattr("routers.integrations._process_sonarr_webhook", mock_process)

    payload = {
        "eventType": "Download",
        "series": {"id": 777, "title": "Severance"},
        "episodes": [{"id": 1, "seasonNumber": 1, "episodeNumber": 1}],
        "episodeFile": {"id": 888, "path": "/media/Severance/S01E01.mkv"},
    }
    headers = {"X-OmniSub-Token": "test-secret-42"}

    # 1. First event -> queued
    r1 = client.post("/api/webhook/sonarr", json=payload, headers=headers)
    assert r1.status_code == 200
    assert r1.json()["status"] == "queued"

    # 2. Duplicate within window -> ignored
    r2 = client.post("/api/webhook/sonarr", json=payload, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["status"] == "ignored"

    # 3. Simulate time advancing by 59.9s -> still within window -> duplicate
    current_time = time.monotonic()
    with patch("time.monotonic", return_value=current_time + 59.9):
        r3 = client.post("/api/webhook/sonarr", json=payload, headers=headers)
        assert r3.status_code == 200
        assert r3.json()["status"] == "ignored"

    # 4. Simulate time advancing by 60.1s -> window expired -> accepted and queued!
    with patch("time.monotonic", return_value=current_time + 60.1):
        r4 = client.post("/api/webhook/sonarr", json=payload, headers=headers)
        assert r4.status_code == 200
        assert r4.json()["status"] == "queued"

    # 5. Eviction stress test: populate 5,000 unique keys, then advance time past window
    dedup = WebhookDeduplicator(window_seconds=10.0)
    base_t = time.monotonic()
    with patch("time.monotonic", return_value=base_t):
        for i in range(5000):
            dedup.is_duplicate("sonarr", i, i + 10000)
        assert len(dedup._seen) == 5000

    # Advance time past window and insert 1 new key -> all 5,000 expired keys evicted
    with patch("time.monotonic", return_value=base_t + 11.0):
        dedup.is_duplicate("sonarr", 99999, 99999)
        assert len(dedup._seen) == 1, f"Expected 1 key remaining after eviction, got {len(dedup._seen)}"


# ==============================================================================
# 2. INT-P1-06: Webhook Payload Schema Fuzzing Matrix (Ensuring HTTP 400)
# ==============================================================================

SONARR_MALFORMED_PAYLOADS = [
    ("empty_dict", {}),
    ("missing_eventType", {"series": {"title": "X"}, "episodes": [{"id": 1}], "episodeFile": {"path": "/a"}}),
    ("null_eventType", {"eventType": None, "series": {"title": "X"}}),
    ("int_eventType", {"eventType": 12345}),
    ("bool_eventType", {"eventType": True}),
    ("list_eventType", {"eventType": ["Download"]}),
    ("download_missing_series", {"eventType": "Download", "episodes": [{"id": 1}], "episodeFile": {"path": "/a"}}),
    ("download_null_series", {"eventType": "Download", "series": None, "episodes": [{"id": 1}], "episodeFile": {"path": "/a"}}),
    ("download_str_series", {"eventType": "Download", "series": "Show", "episodes": [{"id": 1}], "episodeFile": {"path": "/a"}}),
    ("download_empty_series_dict", {"eventType": "Download", "series": {}, "episodes": [{"id": 1}], "episodeFile": {"path": "/a"}}),
    ("download_series_null_title", {"eventType": "Download", "series": {"title": None}, "episodes": [{"id": 1}], "episodeFile": {"path": "/a"}}),
    ("download_series_empty_title", {"eventType": "Download", "series": {"title": ""}, "episodes": [{"id": 1}], "episodeFile": {"path": "/a"}}),
    ("download_missing_episodes", {"eventType": "Download", "series": {"title": "X"}, "episodeFile": {"path": "/a"}}),
    ("download_null_episodes", {"eventType": "Download", "series": {"title": "X"}, "episodes": None, "episodeFile": {"path": "/a"}}),
    ("download_empty_episodes_list", {"eventType": "Download", "series": {"title": "X"}, "episodes": [], "episodeFile": {"path": "/a"}}),
    ("download_str_episodes", {"eventType": "Download", "series": {"title": "X"}, "episodes": "ep1", "episodeFile": {"path": "/a"}}),
    ("download_int_episodes", {"eventType": "Download", "series": {"title": "X"}, "episodes": 42, "episodeFile": {"path": "/a"}}),
    ("download_missing_episodeFile", {"eventType": "Download", "series": {"title": "X"}, "episodes": [{"id": 1}]}),
    ("download_null_episodeFile", {"eventType": "Download", "series": {"title": "X"}, "episodes": [{"id": 1}], "episodeFile": None}),
    ("download_str_episodeFile", {"eventType": "Download", "series": {"title": "X"}, "episodes": [{"id": 1}], "episodeFile": "/media/ep.mkv"}),
    ("download_empty_episodeFile_dict", {"eventType": "Download", "series": {"title": "X"}, "episodes": [{"id": 1}], "episodeFile": {}}),
    ("download_episodeFile_null_path", {"eventType": "Download", "series": {"title": "X"}, "episodes": [{"id": 1}], "episodeFile": {"path": None}}),
    ("download_episodeFile_empty_path", {"eventType": "Download", "series": {"title": "X"}, "episodes": [{"id": 1}], "episodeFile": {"path": ""}}),
    ("renamed_missing_series", {"eventType": "EpisodeFileRenamed", "episodes": [{"id": 1}], "episodeFile": {"path": "/a"}}),
    ("renamed_empty_episodes", {"eventType": "EpisodeFileRenamed", "series": {"title": "X"}, "episodes": [], "episodeFile": {"path": "/a"}}),
    ("renamed_null_path", {"eventType": "EpisodeFileRenamed", "series": {"title": "X"}, "episodes": [{"id": 1}], "episodeFile": {"path": None}}),
]

RADARR_MALFORMED_PAYLOADS = [
    ("empty_dict", {}),
    ("missing_eventType", {"movie": {"title": "X"}, "movieFile": {"path": "/a"}}),
    ("null_eventType", {"eventType": None, "movie": {"title": "X"}}),
    ("int_eventType", {"eventType": 999}),
    ("bool_eventType", {"eventType": False}),
    ("download_missing_movie", {"eventType": "Download", "movieFile": {"path": "/a"}}),
    ("download_null_movie", {"eventType": "Download", "movie": None, "movieFile": {"path": "/a"}}),
    ("download_str_movie", {"eventType": "Download", "movie": "Inception", "movieFile": {"path": "/a"}}),
    ("download_empty_movie_dict", {"eventType": "Download", "movie": {}, "movieFile": {"path": "/a"}}),
    ("download_movie_null_title", {"eventType": "Download", "movie": {"title": None}, "movieFile": {"path": "/a"}}),
    ("download_movie_empty_title", {"eventType": "Download", "movie": {"title": ""}, "movieFile": {"path": "/a"}}),
    ("download_missing_movieFile", {"eventType": "Download", "movie": {"title": "X"}}),
    ("download_null_movieFile", {"eventType": "Download", "movie": {"title": "X"}, "movieFile": None}),
    ("download_str_movieFile", {"eventType": "Download", "movie": {"title": "X"}, "movieFile": "/media/movie.mkv"}),
    ("download_empty_movieFile_dict", {"eventType": "Download", "movie": {"title": "X"}, "movieFile": {}}),
    ("download_movieFile_null_path", {"eventType": "Download", "movie": {"title": "X"}, "movieFile": {"path": None}}),
    ("download_movieFile_empty_path", {"eventType": "Download", "movie": {"title": "X"}, "movieFile": {"path": ""}}),
    ("renamed_missing_movie", {"eventType": "MovieFileRenamed", "movieFile": {"path": "/a"}}),
    ("renamed_null_path", {"eventType": "MovieFileRenamed", "movie": {"title": "X"}, "movieFile": {"path": None}}),
]


@pytest.mark.parametrize("case_name,payload", SONARR_MALFORMED_PAYLOADS)
def test_sonarr_webhook_schema_fuzzing(client, case_name, payload):
    """
    Fuzz Sonarr webhook endpoint with malformed payloads.
    Must return HTTP 400 with structured diagnostic error detail.
    Must never crash with HTTP 500 or silently accept with HTTP 200.
    """
    headers = {"X-OmniSub-Token": "test-secret-42"}
    resp = client.post("/api/webhook/sonarr", json=payload, headers=headers)
    assert resp.status_code == 400, (
        f"Case '{case_name}' failed! Expected HTTP 400, got HTTP {resp.status_code}. Response: {resp.text}"
    )
    data = resp.json()
    assert "detail" in data
    assert data["detail"]["error"] == "Invalid Sonarr webhook payload"
    assert "details" in data["detail"]
    assert len(data["detail"]["details"]) > 0


@pytest.mark.parametrize("case_name,payload", RADARR_MALFORMED_PAYLOADS)
def test_radarr_webhook_schema_fuzzing(client, case_name, payload):
    """
    Fuzz Radarr webhook endpoint with malformed payloads.
    Must return HTTP 400 with structured diagnostic error detail.
    """
    headers = {"X-OmniSub-Token": "test-secret-42"}
    resp = client.post("/api/webhook/radarr", json=payload, headers=headers)
    assert resp.status_code == 400, (
        f"Case '{case_name}' failed! Expected HTTP 400, got HTTP {resp.status_code}. Response: {resp.text}"
    )
    data = resp.json()
    assert "detail" in data
    assert data["detail"]["error"] == "Invalid Radarr webhook payload"
    assert "details" in data["detail"]
    assert len(data["detail"]["details"]) > 0


# ==============================================================================
# 3. INT-P1-03: Subtitle Atomic Write Sharing Violation & WinError 32 Simulation
# ==============================================================================

def test_write_sidecar_real_windows_lock_transient_recovery(tmp_path):
    """
    Simulate real Windows file locking (WinError 32) using standard file descriptor locking.
    The target file is locked by a background thread for 0.35s.
    write_sidecar retries with backoff [0.1s, 0.25s, 0.5s...] and succeeds once released.
    Temporary files MUST be cleaned up.
    """
    target = tmp_path / "Subtitles" / "Season 01" / "Show.S01E01.el.srt"
    target.parent.mkdir(parents=True, exist_ok=True)
    initial_content = "1\n00:00:00,000 --> 00:00:01,000\nOld text\n"
    target.write_text(initial_content, encoding="utf-8-sig")

    new_content = "1\n00:00:01,000 --> 00:00:05,000\nΑναβαθμισμένοι υπότιτλοι\n"

    lock_released = threading.Event()

    def lock_file():
        # Open in exclusive write mode without FILE_SHARE_DELETE / FILE_SHARE_WRITE
        # On Windows, os.replace onto an open file handle raises WinError 32
        with open(target, "r+b") as handle:
            time.sleep(0.35)
        lock_released.set()

    t = threading.Thread(target=lock_file)
    t.start()
    time.sleep(0.05)  # ensure lock thread has acquired handle

    # Execute write_sidecar
    t_start = time.time()
    write_sidecar(target, new_content)
    duration = time.time() - t_start

    t.join()

    # Assertions
    assert target.exists()
    assert target.read_text(encoding="utf-8-sig") == new_content
    assert duration >= 0.30, f"Write should have waited for lock release, took {duration:.2f}s"

    # Temp file cleanup verification: no .omnisub-tmp files in folder
    temp_files = list(target.parent.glob("*.omnisub-tmp*"))
    assert temp_files == [], f"Found orphaned temp files after transient lock recovery: {temp_files}"


def test_write_sidecar_permanent_lock_exhaustion_cleans_temp_file(tmp_path, monkeypatch):
    """
    Simulate persistent sharing violation where lock is NEVER released.
    write_sidecar retries all attempts, fails, and re-raises PermissionError.
    Crucial verification: temporary file (.omnisub-tmp-*) MUST be cleaned up even on failure!
    """
    target = tmp_path / "Show.el.srt"
    target.write_text("initial", encoding="utf-8-sig")
    new_content = "New text"

    # Mock sleep so retry backoff finishes immediately
    monkeypatch.setattr(time, "sleep", lambda s: None)

    # Mock os.replace to always raise WinError 32 PermissionError
    def mock_replace_always_locked(src, dst):
        raise PermissionError(13, "WinError 32: The process cannot access the file because it is being used by another process")

    monkeypatch.setattr(os, "replace", mock_replace_always_locked)
    # Also mock path.write_text to fail so final fallback also fails
    def mock_write_text_fail(*args, **kwargs):
        raise PermissionError(13, "WinError 32: The process cannot access the file")
    monkeypatch.setattr(Path, "write_text", mock_write_text_fail)

    with pytest.raises(PermissionError) as exc_info:
        write_sidecar(target, new_content)

    assert "WinError 32" in str(exc_info.value)

    # Verification: Temporary files must have been cleaned up by finally: block
    temp_files = list(tmp_path.glob("*.omnisub-tmp*"))
    assert temp_files == [], f"LEAKED TEMP FILES ON FAILURE: {temp_files}"


def test_write_sidecar_high_concurrency_stress(tmp_path):
    """
    Stress test: 20 threads simultaneously writing to the same sidecar file.
    Must not crash, must not corrupt, and must leave zero leaked temp files.
    """
    target = tmp_path / "ConcurrentShow.el.srt"
    num_threads = 20
    errors = queue.Queue()

    def write_worker(idx):
        try:
            content = f"1\n00:00:0{idx:02d},000 --> 00:00:0{idx+1:02d},000\nContent from thread {idx}\n"
            write_sidecar(target, content)
        except Exception as e:
            errors.put((idx, str(e)))

    threads = [threading.Thread(target=write_worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors.empty(), f"Errors during concurrent write: {list(errors.queue)}"
    assert target.exists()
    final_text = target.read_text(encoding="utf-8-sig")
    assert "Content from thread" in final_text

    # No temp files leaked
    temp_files = list(tmp_path.glob("*.omnisub-tmp*"))
    assert temp_files == [], f"Found leaked temp files after concurrent writes: {temp_files}"


def test_write_sidecar_creates_missing_parents(tmp_path):
    """Verify write_sidecar automatically creates deep nested parent directories."""
    nested_target = tmp_path / "Series" / "Anime" / "Season 02" / "Subtitles" / "Ep01.el.srt"
    content = "1\n00:00:01,000 --> 00:00:02,000\nTest\n"
    write_sidecar(nested_target, content)

    assert nested_target.exists()
    assert nested_target.read_text(encoding="utf-8-sig") == content
