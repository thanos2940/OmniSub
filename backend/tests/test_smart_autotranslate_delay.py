import asyncio
import os
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from utils.translation_queue import TranslationQueue, PRIORITY_WEBHOOK, KIND_TRANSLATION
from services.queue_service import enqueue_translation, enqueue_webhook_episode
from utils import storage


@pytest.fixture
def temp_queue(tmp_path):
    db_file = tmp_path / "test_queue.db"
    return TranslationQueue(db_file)


class TestQueueDelayMechanism:
    def test_enqueue_with_delay_sets_next_retry_at(self, temp_queue):
        item_id = temp_queue.enqueue(
            project_name="TestProj",
            episode_name="Ep01",
            priority=PRIORITY_WEBHOOK,
            delay_seconds=1800,
        )
        item = temp_queue.get_item(item_id)
        assert item is not None
        assert item["status"] == "pending"
        assert item["next_retry_at"] is not None

        # Verify retry is ~30 minutes in future
        next_dt = datetime.fromisoformat(item["next_retry_at"])
        now_dt = datetime.now(timezone.utc)
        diff_sec = (next_dt - now_dt).total_seconds()
        assert 1700 < diff_sec <= 1800

    def test_claim_next_respects_future_delay(self, temp_queue):
        # Enqueue item with 30 min future delay
        temp_queue.enqueue(
            project_name="TestProjDelayed",
            episode_name="Ep01",
            priority=PRIORITY_WEBHOOK,
            delay_seconds=1800,
        )
        # claim_next should not find it
        claimed = temp_queue.claim_next()
        assert claimed is None

        # Enqueue immediate item
        temp_queue.enqueue(
            project_name="TestProjImmediate",
            episode_name="Ep01",
            priority=PRIORITY_WEBHOOK,
            delay_seconds=0,
        )
        claimed_imm = temp_queue.claim_next()
        assert claimed_imm is not None
        assert claimed_imm["project_name"] == "TestProjImmediate"

    def test_claim_next_claims_after_delay_expires(self, temp_queue):
        # Enqueue with past next_retry_at
        past_str = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        item_id = temp_queue.enqueue(
            project_name="TestProjPast",
            episode_name="Ep01",
            priority=PRIORITY_WEBHOOK,
            next_retry_at=past_str,
        )
        claimed = temp_queue.claim_next()
        assert claimed is not None
        assert claimed["id"] == item_id


class TestSmartAutoTranslatePolicy:
    def test_anime_webhook_enqueued_immediately(self, tmp_path, monkeypatch):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)

        # Create anime project
        anime_proj = "Naruto"
        storage.create_project(anime_proj, {
            "series_type": "anime",
            "target_language": "Greek",
            "arr_disabled": False,
        })
        storage.save_episode(anime_proj, "S01E01", [{"index": 1, "text": "Hello"}], {"line_count": 1})
        storage.save_original_subtitle(anime_proj, "S01E01", "1\n00:00:01,000 --> 00:00:02,000\nHello\n", "S01E01.en.srt")

        config_mock = {
            "arr_auto_translate": "smart",
            "arr_non_anime_delay_minutes": 30,
            "default_translation_model": "gemini-3.1-flash-lite",
        }
        monkeypatch.setattr(storage, "load_global_config", lambda: config_mock)

        db_path = tmp_path / "queue.db"
        queue = TranslationQueue(db_path)
        monkeypatch.setattr("services.queue_service.TranslationQueue", lambda *args, **kwargs: queue)

        enqueued = enqueue_webhook_episode(anime_proj, "S01E01")
        assert len(enqueued) == 1
        item = queue.get_item(enqueued[0])
        # Anime should have NO next_retry_at delay (or None)
        assert item["next_retry_at"] is None

    def test_non_anime_webhook_enqueued_with_delay(self, tmp_path, monkeypatch):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)

        # Create live-action TV project
        show_proj = "Breaking Bad"
        storage.create_project(show_proj, {
            "series_type": "standard",
            "target_language": "Greek",
            "arr_disabled": False,
        })
        storage.save_episode(show_proj, "S01E01", [{"index": 1, "text": "Science"}], {"line_count": 1})
        storage.save_original_subtitle(show_proj, "S01E01", "1\n00:00:01,000 --> 00:00:02,000\nScience\n", "S01E01.en.srt")

        config_mock = {
            "arr_auto_translate": "smart",
            "arr_non_anime_delay_minutes": 30,
            "default_translation_model": "gemini-3.1-flash-lite",
        }
        monkeypatch.setattr(storage, "load_global_config", lambda: config_mock)

        db_path = tmp_path / "queue.db"
        queue = TranslationQueue(db_path)
        monkeypatch.setattr("services.queue_service.TranslationQueue", lambda *args, **kwargs: queue)

        enqueued = enqueue_webhook_episode(show_proj, "S01E01")
        assert len(enqueued) == 1
        item = queue.get_item(enqueued[0])
        # Non-anime should have next_retry_at ~30 minutes in future
        assert item["next_retry_at"] is not None
        next_dt = datetime.fromisoformat(item["next_retry_at"])
        diff = (next_dt - datetime.now(timezone.utc)).total_seconds()
        assert 1700 < diff <= 1800


@pytest.mark.asyncio
class TestZeroTokenBazarrPreCheck:
    async def test_worker_skips_when_bazarr_target_exists(self, tmp_path, monkeypatch):
        from services.worker.translation_item_worker import process_translation_item
        from utils.jobs_manager import jobs

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)

        media_dir = tmp_path / "media"
        media_dir.mkdir()
        video_file = media_dir / "Movie.2024.mkv"
        video_file.write_text("fake video content")
        # Simulate Bazarr downloaded Greek subtitle during 30 min wait
        greek_sub = media_dir / "Movie.2024.el.srt"
        greek_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nΓεια σου κόσμε!\n", encoding="utf-8")

        proj_name = "Movie 2024"
        ep_name = "Movie 2024"
        storage.create_project(proj_name, {
            "target_language": "Greek",
            "series_type": "movie",
        })
        storage.save_episode(proj_name, ep_name, [{"index": 1, "text": "Hello world!"}], {
            "arr_media_path": str(video_file),
            "arr_sub_fingerprint": "mock_fp_123",
            "has_source_sub": True,
            "line_count": 1,
        })
        storage.save_original_subtitle(proj_name, ep_name, "1\n00:00:01,000 --> 00:00:02,000\nHello world!\n", "Movie.2024.en.srt")

        db_path = tmp_path / "queue.db"
        queue = TranslationQueue(db_path)
        item_id = queue.enqueue(proj_name, ep_name, priority=0)
        item = queue.get_item(item_id)

        config_mock = {
            "arr_auto_translate": "smart",
            "arr_source_language": "en",
            "default_translation_model": "gemini-3.1-flash-lite",
        }

        # Run worker processing
        await process_translation_item(queue, item, config_mock)

        # Verify item status was marked completed
        updated_item = queue.get_item(item_id)
        assert updated_item["status"] == "completed"

        # Verify job was completed with skip message
        job_id = f"bg_{item_id}"
        assert job_id in jobs
        assert jobs[job_id].status == "completed"
        assert "target exists" in jobs[job_id].message.lower()

        # Verify episode metadata recorded target
        ep_meta = storage.load_episode_metadata(proj_name, ep_name)
        assert ep_meta.get("arr_has_target") is True
        assert str(greek_sub) in str(ep_meta.get("arr_target_path"))
