"""
Discord Webhook Notification Dispatcher for Background Worker Events.
"""

import logging
from typing import Optional, Dict, Any

from services.discord_notifier import DiscordNotifier

logger = logging.getLogger(__name__)


class WorkerNotifier:
    """Wrapper around DiscordNotifier for structured background worker events."""

    def __init__(self, webhook_url: Optional[str] = None):
        self.notifier = DiscordNotifier(webhook_url)

    async def notify_translation_completed(self, project_name: str, episode_name: str, output_file: str) -> None:
        try:
            await self.notifier.notify("translation_completed", {
                "message": f"Successfully translated subtitle for episode: {episode_name} of series {project_name}.",
                "series": project_name,
                "episode": episode_name,
                "output_file": output_file
            })
        except Exception as e:
            logger.warning(f"Failed to send translation_completed notification: {e}")

    async def notify_daily_limit_reached(self, model_name: str, daily_limit: int, episode_name: Optional[str] = None, project_name: Optional[str] = None) -> None:
        try:
            msg = f"Omnisub paused background worker because daily limit has been reached for model: {model_name}."
            if episode_name:
                msg = f"Daily API quota exceeded while translating: {episode_name}."
            payload: Dict[str, Any] = {
                "message": msg,
                "model": model_name,
                "daily_limit": daily_limit,
            }
            if project_name:
                payload["series"] = project_name
            if episode_name:
                payload["episode"] = episode_name
            await self.notifier.notify("daily_limit_reached", payload)
        except Exception as e:
            logger.warning(f"Failed to send daily_limit_reached notification: {e}")

    async def notify_translation_failed(self, project_name: str, episode_name: str, error: str, attempts: int) -> None:
        try:
            await self.notifier.notify("translation_failed", {
                "message": f"Subtitle translation failed permanently for {episode_name} after {attempts} attempts.",
                "series": project_name,
                "episode": episode_name,
                "error": str(error)
            })
        except Exception as e:
            logger.warning(f"Failed to send translation_failed notification: {e}")
