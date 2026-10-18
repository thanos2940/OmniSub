"""Autopilot settings: one typed view over the global config (docs/PLAN_autopilot.md 4.11)."""

from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Optional

BACKLOG_MODES = ("off", "newest", "oldest")

MIN_POLL_SECONDS = 15
MIN_RECONCILE_HOURS = 1.0


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off", ""):
            return False
        return default
    return bool(value)


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


@dataclass
class AutopilotSettings:
    enabled: bool = False
    grace_minutes: float = 5.0
    poll_seconds: int = 60
    reconcile_hours: float = 6.0
    backlog_mode: str = "off"
    backlog_daily_limit: int = 20
    count_embedded_greek: bool = True
    skip_original_greek: bool = True
    skip_tags: List[str] = field(default_factory=lambda: ["ombisub-skip", "no-greek"])
    monitored_only: bool = True
    protect_foreign_subs: bool = True
    repair_foreign_encoding: bool = True
    notify_success: bool = False
    digest_hour: int = 9
    enabled_at: Optional[str] = None
    plex_refresh_enabled: bool = False
    plex_url: str = "http://localhost:32400"
    plex_token: str = ""

    @property
    def grace_seconds(self) -> float:
        return self.grace_minutes * 60.0

    @classmethod
    def from_config(cls, cfg: Optional[Dict]) -> "AutopilotSettings":
        """Build from the global config dict; missing or malformed values fall back to defaults."""
        cfg = cfg or {}
        d = cls()

        def get(key: str, default: Any) -> Any:
            value = cfg.get(f"autopilot_{key}")
            return default if value is None else value

        mode = str(get("backlog_mode", d.backlog_mode)).strip().lower()
        raw_tags = get("skip_tags", ",".join(d.skip_tags))
        if isinstance(raw_tags, (list, tuple)):
            raw_tags = ",".join(str(t) for t in raw_tags)
        enabled_at = cfg.get("autopilot_enabled_at")
        plex_url = cfg.get("plex_url")

        return cls(
            enabled=_bool(cfg.get("autopilot_enabled"), d.enabled),
            grace_minutes=max(0.0, _number(get("grace_minutes", d.grace_minutes), d.grace_minutes)),
            poll_seconds=max(MIN_POLL_SECONDS, _int(get("poll_seconds", d.poll_seconds), d.poll_seconds)),
            reconcile_hours=max(MIN_RECONCILE_HOURS, _number(get("reconcile_hours", d.reconcile_hours), d.reconcile_hours)),
            backlog_mode=mode if mode in BACKLOG_MODES else "off",
            backlog_daily_limit=max(0, _int(get("backlog_daily_limit", d.backlog_daily_limit), d.backlog_daily_limit)),
            count_embedded_greek=_bool(cfg.get("autopilot_count_embedded_greek"), d.count_embedded_greek),
            skip_original_greek=_bool(cfg.get("autopilot_skip_original_greek"), d.skip_original_greek),
            skip_tags=[t.strip().lower() for t in str(raw_tags).split(",") if t.strip()],
            monitored_only=_bool(cfg.get("autopilot_monitored_only"), d.monitored_only),
            protect_foreign_subs=_bool(cfg.get("autopilot_protect_foreign_subs"), d.protect_foreign_subs),
            repair_foreign_encoding=_bool(cfg.get("autopilot_repair_foreign_encoding"), d.repair_foreign_encoding),
            notify_success=_bool(cfg.get("autopilot_notify_success"), d.notify_success),
            digest_hour=max(-1, min(23, _int(get("digest_hour", d.digest_hour), d.digest_hour))),
            enabled_at=str(enabled_at) if enabled_at else None,
            plex_refresh_enabled=_bool(cfg.get("plex_refresh_enabled"), d.plex_refresh_enabled),
            plex_url=str(plex_url).strip() if plex_url and str(plex_url).strip() else d.plex_url,
            plex_token=str(cfg.get("plex_token") or ""),
        )

    def to_dict(self, *, hide_secrets: bool = True) -> Dict[str, Any]:
        out = {f.name: getattr(self, f.name) for f in fields(self)}
        if hide_secrets and out.get("plex_token"):
            out["plex_token"] = "***"
        return out
