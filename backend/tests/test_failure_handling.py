"""How the worker lanes fail: permanently vs. transiently, and what they log.

The live log was dominated by failures that could never succeed being retried for
days (episodes deleted or with nothing to translate from), each attempt logging a
full traceback at ERROR, plus transient share blips logged the same way.
"""
import asyncio
import json
import logging
import sqlite3
from pathlib import Path

import pytest

from utils import paths, storage
from utils.translation_queue import KIND_EXTRACTION, PRIORITY_MANUAL, TranslationQueue

SAMPLE_SRT = "1\n00:00:01,000 --> 00:00:03,000\nHello there.\n"


@pytest.fixture
def queue():
    return TranslationQueue(paths.DB_FILE)


def _claim(queue, project, episode):
    queue.enqueue(project, episode, PRIORITY_MANUAL, {})
    item = queue.claim_next()
    assert item["project_name"] == project
    return item


def _errors(caplog):
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


# ---------------------------------------------------------------------------
# Translation lane
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_episode_fails_once_without_a_traceback(queue, caplog):
    from services.worker.translation_item_worker import process_translation_item

    storage.create_project("Gone", {"target_language": "Greek"})
    item = _claim(queue, "Gone", "S01E01")

    with caplog.at_level(logging.WARNING):
        await process_translation_item(queue, item, {})

    row = queue.get_item(item["id"])
    assert row["status"] == "failed", "retrying a deleted episode can never succeed"
    assert row["last_error"] == "Episode metadata not found in storage."
    assert not _errors(caplog)


@pytest.mark.asyncio
async def test_episode_without_media_or_source_fails_permanently(queue, caplog):
    from services.worker.translation_item_worker import process_translation_item

    storage.create_project("NoSource", {"target_language": "Greek"})
    storage.episode_dir("NoSource", "S01E01").mkdir(parents=True, exist_ok=True)
    storage.save_episode("NoSource", "S01E01", [], {"has_source_sub": False, "line_count": 0})
    item = _claim(queue, "NoSource", "S01E01")

    with caplog.at_level(logging.WARNING):
        await process_translation_item(queue, item, {})

    row = queue.get_item(item["id"])
    assert row["status"] == "failed"
    assert "has no source subtitle cues" in row["last_error"]
    assert not _errors(caplog)


@pytest.mark.asyncio
async def test_unreachable_media_is_retried_and_logged_as_a_warning(queue, caplog, tmp_path):
    from services.worker.translation_item_worker import process_translation_item

    storage.create_project("Blip", {"target_language": "Greek"})
    storage.episode_dir("Blip", "S01E01").mkdir(parents=True, exist_ok=True)
    # The share holding this file is "down": the path is known but not reachable.
    storage.save_episode("Blip", "S01E01", [], {
        "has_source_sub": False, "line_count": 0,
        "arr_media_path": str(tmp_path / "offline-share" / "Show.S01E01.mkv"),
    })
    item = _claim(queue, "Blip", "S01E01")

    with caplog.at_level(logging.WARNING):
        await process_translation_item(queue, item, {})

    row = queue.get_item(item["id"])
    assert row["status"] == "pending"
    assert row["next_retry_at"] is not None
    assert not _errors(caplog), "a retried share blip is a warning, not an error"
    assert any("will retry" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Extraction lane
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_existing_sidecar_is_extracted_again(queue, tmp_path, monkeypatch):
    from integrations import embedded_subs
    from services.worker.extraction_worker import process_extraction_item

    media = tmp_path / "Show.S01E02.mkv"
    media.write_bytes(b"\x1aE\xdf\xa3")
    sidecar = tmp_path / "Show.S01E02.en.srt"
    sidecar.write_text("", encoding="utf-8")  # left behind empty

    calls = []

    async def fake_extract(path, track, config=None):
        calls.append(track.index)
        return SAMPLE_SRT

    monkeypatch.setattr(embedded_subs, "extract_track", fake_extract)
    storage.create_project("Show", {"target_language": "Greek"})
    queue.enqueue("Show", "S01E02", PRIORITY_MANUAL, {
        "media_path": str(media), "stream_index": 3, "source_lang_code": "en",
        "output_format": "srt", "track": {"codec": "subrip"},
    }, kind=KIND_EXTRACTION)
    item = queue.claim_next_extraction()

    await process_extraction_item(queue, item, {})

    assert calls == [3]
    assert "Hello there." in sidecar.read_text(encoding="utf-8-sig")
    assert queue.get_item(item["id"])["status"] == "completed"
    assert storage.original_subtitle_exists("Show", "S01E02")


@pytest.mark.asyncio
async def test_extraction_retry_is_a_warning_until_the_last_attempt(queue, tmp_path, caplog):
    from services.worker.extraction_worker import process_extraction_item

    storage.create_project("Show", {"target_language": "Greek"})
    queue.enqueue("Show", "S01E03", PRIORITY_MANUAL, {
        "media_path": str(tmp_path / "missing.mkv"), "stream_index": 2, "output_format": "srt",
    }, kind=KIND_EXTRACTION)
    item = queue.claim_next_extraction()

    with caplog.at_level(logging.WARNING):
        await process_extraction_item(queue, item, {})
    assert queue.get_item(item["id"])["status"] == "pending"
    assert not _errors(caplog)

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        await process_extraction_item(queue, {**item, "attempts": 3}, {})
    assert queue.get_item(item["id"])["status"] == "failed"
    assert _errors(caplog), "the final failure still reaches the error log"


# ---------------------------------------------------------------------------
# Reading subtitles off the shares
# ---------------------------------------------------------------------------

def test_read_file_retries_a_refused_read_and_normalises_newlines(tmp_path, monkeypatch):
    from integrations.media_sync_engine import MediaSyncEngine

    sub = tmp_path / "x.en.srt"
    sub.write_bytes(b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHi\r\n")
    real_read = Path.read_bytes
    attempts = {"n": 0}

    def flaky_read(self):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise PermissionError("locked by another process")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", flaky_read)
    monkeypatch.setattr("integrations.media_sync_engine.time.sleep", lambda s: None)

    assert MediaSyncEngine._read_file(str(sub)) == "1\n00:00:01,000 --> 00:00:02,000\nHi\n"
    assert attempts["n"] == 2


def test_read_file_reads_the_file_once(tmp_path, monkeypatch):
    from integrations.media_sync_engine import MediaSyncEngine

    sub = tmp_path / "x.en.srt"
    sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nCafé\n", encoding="cp1252")
    real_read = Path.read_bytes
    reads = []
    monkeypatch.setattr(Path, "read_bytes", lambda self: reads.append(1) or real_read(self))
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: pytest.fail("second read"))

    assert "Café" in MediaSyncEngine._read_file(str(sub))
    assert len(reads) == 1


# ---------------------------------------------------------------------------
# Job history
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_job_history_table_is_created_on_first_write(tmp_path, monkeypatch):
    from utils import jobs_manager

    db = tmp_path / "fresh.db"  # no TranslationQueue has ever touched it
    monkeypatch.setattr(jobs_manager, "DB_FILE", db)
    jid = jobs_manager.create_job("create_glossary", "P", "E1")
    jobs_manager.update_job(jid, status="completed", progress=100.0)

    row = None
    for _ in range(100):
        await asyncio.sleep(0.02)
        if db.exists():
            conn = sqlite3.connect(db)
            try:
                row = conn.execute("SELECT status FROM job_history WHERE id = ?", (jid,)).fetchone()
            except sqlite3.OperationalError:
                row = None
            conn.close()
            if row and row[0] == "completed":
                break
    assert row == ("completed",)


@pytest.mark.asyncio
async def test_job_history_survives_executor_shutdown(tmp_path, monkeypatch):
    """At shutdown the default executor is gone; the final write must not be lost
    (it used to surface as 'Task exception was never retrieved')."""
    from utils import jobs_manager

    db = tmp_path / "shutdown.db"
    monkeypatch.setattr(jobs_manager, "DB_FILE", db)

    async def executor_gone(*a, **k):
        raise RuntimeError("Executor shutdown has been called")

    monkeypatch.setattr(jobs_manager.asyncio, "to_thread", executor_gone)
    snapshot = ("job-x", "create", "P", "E1", "completed", None, "[]", "2026-09-24T00:00:00+00:00",
                "2026-09-24T00:00:00+00:00")
    await jobs_manager._write_job_snapshot_off_loop(snapshot)

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT status FROM job_history WHERE id = 'job-x'").fetchone() == ("completed",)
    conn.close()


# ---------------------------------------------------------------------------
# Event loop noise
# ---------------------------------------------------------------------------

def test_client_disconnects_are_not_logged_as_errors():
    from unittest.mock import MagicMock

    from main import _quiet_client_disconnects

    loop = MagicMock()
    _quiet_client_disconnects(loop, {
        "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)",
        "exception": ConnectionResetError(10054, "An existing connection was forcibly closed"),
    })
    loop.default_exception_handler.assert_not_called()

    real_problem = {"message": "Task exception was never retrieved", "exception": ValueError("boom")}
    _quiet_client_disconnects(loop, real_problem)
    loop.default_exception_handler.assert_called_once_with(real_problem)
