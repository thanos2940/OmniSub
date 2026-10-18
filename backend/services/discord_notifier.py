import httpx
import logging
from typing import Dict, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class DiscordNotifier:
    """Send rich embed notifications to a Discord webhook."""
    
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url

    async def notify(self, event: str, details: Dict):
        """Send a Discord embed for a translation event."""
        if not self.webhook_url:
            return

        colors = {
            "translation_completed": 0x2ECC71,  # Green
            "daily_limit_reached": 0xF39C12,    # Orange
            "translation_failed": 0xE74C3C,     # Red
            "sync_completed": 0x3498DB,          # Blue
            "batch_completed": 0x9B59B6,         # Purple
        }

        # Format display name
        title = f"Omnisub — {event.replace('_', ' ').title()}"
        description = details.get("message", "")
        
        # Build fields
        fields = []
        for k, v in details.items():
            if k != "message":
                display_key = k.replace('_', ' ').title()
                fields.append({"name": display_key, "value": str(v), "inline": True})

        embed = {
            "title": title,
            "description": description,
            "color": colors.get(event, 0x95A5A6),
            "fields": fields,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self.webhook_url, json={"embeds": [embed]})
                if response.status_code >= 400:
                    logger.error(f"Discord webhook failed with status {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"Failed to send Discord notification: {e}")


def build_embed(title: str, description: str = "", color: int = 0x95A5A6, fields: Optional[Dict] = None) -> Dict:
    """A plain Discord embed dict (used by the autopilot alerts)."""
    return {
        "title": title,
        "description": description,
        "color": color,
        "fields": [{"name": str(k), "value": str(v), "inline": True} for k, v in (fields or {}).items()],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def send_embed_sync(webhook_url: Optional[str], embed: Dict, timeout: float = 10.0) -> bool:
    """Post one embed to a Discord webhook (blocking). Never raises; False on any failure."""
    if not webhook_url:
        return False
    try:
        response = httpx.post(webhook_url, json={"embeds": [embed]}, timeout=timeout)
        if response.status_code >= 400:
            logger.error(f"Discord webhook failed with status {response.status_code}: {response.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"Failed to send Discord notification: {e}")
        return False
