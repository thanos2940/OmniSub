"""
Unit tests for background worker modularization.
"""

from datetime import datetime
import pytest

from services.worker.scheduler import is_within_window, get_schedule_ceiling
from services.worker.notification_handler import WorkerNotifier


def test_is_within_window():
    # Normal daytime window
    dt_noon = datetime(2026, 1, 1, 12, 0)
    assert is_within_window(dt_noon, "10:00", "14:00") is True
    assert is_within_window(dt_noon, "13:00", "15:00") is False

    # Window crossing midnight (e.g. 22:00 to 06:00)
    dt_night = datetime(2026, 1, 1, 23, 30)
    dt_morning = datetime(2026, 1, 1, 4, 30)
    dt_day = datetime(2026, 1, 1, 15, 0)

    assert is_within_window(dt_night, "22:00", "06:00") is True
    assert is_within_window(dt_morning, "22:00", "06:00") is True
    assert is_within_window(dt_day, "22:00", "06:00") is False


def test_get_schedule_ceiling():
    # When disabled, ceiling is None (unrestricted)
    assert get_schedule_ceiling({"worker_schedule_enabled": False}) is None

    # When enabled and outside window
    config = {
        "worker_schedule_enabled": True,
        "worker_window_start": "01:00",
        "worker_window_end": "01:00", # 0-length window -> always within
        "worker_schedule_priority_cutoff": 1,
    }
    assert get_schedule_ceiling(config) is None


@pytest.mark.asyncio
async def test_worker_notifier_handles_none_webhook():
    notifier = WorkerNotifier(None)
    # None webhook should be a graceful no-op and never raise
    await notifier.notify_translation_completed("Proj", "Ep01", "Ep01.el.srt")
    await notifier.notify_daily_limit_reached("gemini-2.5-flash", 1500)
    await notifier.notify_translation_failed("Proj", "Ep01", "Error message", 3)
