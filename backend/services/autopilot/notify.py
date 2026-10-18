"""Autopilot alerts and the daily digest, sent through the Discord webhook.

Rate limited per (event, key); never raises; does nothing when no webhook URL is set.
"""

import logging
import threading
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Tuple

from services import discord_notifier
from utils import storage

logger = logging.getLogger(__name__)

GREEN, ORANGE, RED, BLUE, GREY = 0x2ECC71, 0xF39C12, 0xE74C3C, 0x3498DB, 0x95A5A6

# Minimum seconds between two messages of the same (event, key). None = once per key
# (failures per item; no_source carries the day in its key, so it is once per series per day).
DEFAULT_INTERVAL = 30 * 60
_INTERVALS = {
    "item_failed": None,
    "no_source": None,
    "foreign_repaired": None,
    "item_done": None,
    "arr_unreachable": 6 * 3600,
    "arr_recovered": DEFAULT_INTERVAL,
    "engine_missing": 6 * 3600,
    "runner_problem": 6 * 3600,
    "daily_digest": 20 * 3600,
}


def item_label(item: Dict) -> str:
    """Human name of an item: 'Show S01E02' or the movie title."""
    title = (item.get("title") or item.get("project_name") or item.get("episode_name") or "A video").strip()
    season, episode = item.get("season"), item.get("episode")
    if item.get("kind") == "episode" and season is not None and episode is not None:
        try:
            return f"{title} S{int(season):02d}E{int(episode):02d}"
        except (TypeError, ValueError):
            pass
    ep_name = item.get("episode_name")
    if item.get("kind") == "episode" and ep_name:
        return f"{title} {ep_name}"
    return title


def _series_key(item: Dict) -> str:
    return str(item.get("project_name") or item.get("title") or item.get("id") or "?").lower()


class AutopilotNotifier:
    def __init__(self, config_provider: Optional[Callable[[], Dict]] = None,
                 clock: Optional[Callable[[], datetime]] = None,
                 sender: Optional[Callable[[str, Dict], bool]] = None):
        self._config_provider = config_provider or storage.load_global_config
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sender = sender or discord_notifier.send_embed_sync
        self._last: Dict[Tuple[str, str], datetime] = {}
        self._lock = threading.Lock()

    # -- plumbing -------------------------------------------------------------
    def _config(self) -> Dict:
        try:
            return self._config_provider() or {}
        except Exception as e:
            logger.warning("Autopilot notifier could not read the config: %s", e)
            return {}

    def _send(self, event: str, key: str, title: str, message: str, color: int,
              fields: Optional[Dict] = None) -> bool:
        """Send once the rate limit for (event, key) allows."""
        try:
            url = (self._config().get("discord_webhook_url") or "").strip()
            if not url:
                return False
            interval = _INTERVALS.get(event, DEFAULT_INTERVAL)
            now = self._clock()
            slot = (event, key)
            with self._lock:
                last = self._last.get(slot)
                if last is not None and (interval is None or (now - last).total_seconds() < interval):
                    return False
                self._last[slot] = now
            try:
                ok = bool(self._sender(url, discord_notifier.build_embed(title, message, color, fields)))
            except Exception as e:
                logger.warning("Autopilot notification '%s' could not be sent: %s", event, e)
                ok = False
            if not ok:
                with self._lock:  # let a later attempt retry a failed delivery
                    if self._last.get(slot) == now:
                        self._last.pop(slot, None)
            return ok
        except Exception as e:
            logger.warning("Autopilot notification '%s' failed: %s", event, e)
            return False

    # -- events ---------------------------------------------------------------
    def item_done(self, item: Dict) -> bool:
        if not self._config().get("autopilot_notify_success", False):
            return False
        return self._send("item_done", str(item.get("id")), "Greek subtitles ready",
                          f"{item_label(item)} now has Greek subtitles.", GREEN)

    def item_failed(self, item: Dict) -> bool:
        why = item.get("detail") or item.get("reason") or "unknown error"
        return self._send("item_failed", str(item.get("id")), "Subtitles failed",
                          f"Could not make Greek subtitles for {item_label(item)}: {why}. "
                          f"Open the Autopilot page and press Retry.", RED)

    def no_source(self, item: Dict) -> bool:
        day = self._clock().astimezone(timezone.utc).strftime("%Y-%m-%d")
        reasons = {
            "image_only": "its only subtitles are image-based (PGS/VobSub) and cannot be translated",
            "no_subtitles": "it has no English subtitles",
            "ffmpeg_missing": "ffmpeg is missing, so the embedded subtitles cannot be read",
        }
        why = reasons.get(item.get("reason") or "", item.get("detail") or "no usable English subtitle was found")
        return self._send("no_source", f"{_series_key(item)}|{day}", "No subtitle source",
                          f"{item_label(item)} was left without Greek subtitles: {why}. Add an English "
                          f"subtitle (for example through Bazarr) and OmbiSub will pick it up.", ORANGE)

    def foreign_repaired(self, item: Dict, path) -> bool:
        return self._send("foreign_repaired", f"{item.get('id')}|{path}", "Greek subtitle repaired",
                          f"The Greek subtitle of {item_label(item)} had broken characters; OmbiSub fixed "
                          f"it in place and kept a backup.", BLUE, {"File": path})

    def arr_unreachable(self, source: str, error, since) -> bool:
        name = str(source).title()
        return self._send("arr_unreachable", str(source), f"{name} unreachable",
                          f"OmbiSub cannot reach {name} (since {since}): {error}. New downloads are not "
                          f"noticed until it is back. Check that {name} is running and that the URL and "
                          f"API key in Settings are right.", RED)

    def arr_recovered(self, source: str) -> bool:
        """Announce recovery, but only if an outage was announced before."""
        name = str(source).title()
        with self._lock:
            had_alert = ("arr_unreachable", str(source)) in self._last
            self._last.pop(("arr_unreachable", str(source)), None)
        if not had_alert:
            return False
        return self._send("arr_recovered", str(source), f"{name} is back",
                          f"OmbiSub can reach {name} again and is catching up.", GREEN)

    def engine_missing(self, waiting: int) -> bool:
        return self._send("engine_missing", "engine", "No translation engine running",
                          f"{waiting} subtitle(s) are waiting but no translation engine is running. Start "
                          f"the agent runner (watch mode) or the Gemini worker.", ORANGE)

    def runner_problem(self, status, message) -> bool:
        return self._send("runner_problem", str(status), "Agent runner problem",
                          f"The agent runner is '{status}': {message}", RED)

    def daily_digest(self, stats: Dict) -> bool:
        stats = stats or {}

        def num(key: str) -> int:
            try:
                return int(stats.get(key, 0) or 0)
            except (TypeError, ValueError):
                return 0

        done, has_greek, skipped, failed, no_source = (
            num("done"), num("has_greek"), num("skipped"), num("failed"), num("no_source"))
        summary = (f"Last 24 hours: {done} translated, {has_greek} already had Greek, "
                   f"{skipped} skipped, {failed} failed")
        summary += f", {no_source} without a usable source." if no_source else "."
        lines = [summary]
        attention = [str(a) for a in (stats.get("attention") or [])]
        if failed or no_source or attention:
            lines.append("Needs attention:")
            lines += [f"- {a}" for a in attention[:10]] or ["- Open the Autopilot page for the details."]
        elif not (done or has_greek or skipped):
            lines.append("Nothing new came in.")
        else:
            lines.append("Nothing needs your attention.")
        color = RED if failed else (ORANGE if no_source or attention else BLUE)
        day = self._clock().astimezone(timezone.utc).strftime("%Y-%m-%d")
        return self._send("daily_digest", day, "OmbiSub daily summary", "\n".join(lines), color)
