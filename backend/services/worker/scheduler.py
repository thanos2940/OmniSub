"""
Worker Scheduling and Off-Peak Window Management.
"""

from datetime import datetime
from typing import Optional


def is_within_window(now: datetime, start_str: str, end_str: str) -> bool:
    """True if local time ``now`` falls within [start, end) (handles midnight wrap)."""
    try:
        sh, sm = (int(x) for x in start_str.split(":"))
        eh, em = (int(x) for x in end_str.split(":"))
    except Exception:
        return True  # malformed config -> impose no restriction
    cur = now.hour * 60 + now.minute
    start, end = sh * 60 + sm, eh * 60 + em
    if start == end:
        return True
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end  # window wraps past midnight


def get_schedule_ceiling(config: dict) -> Optional[int]:
    """Off-peak scheduling: max claimable priority right now, or None for unrestricted."""
    if not config.get("worker_schedule_enabled", False):
        return None
    now = datetime.now()
    if is_within_window(now, config.get("worker_window_start", "01:00"),
                        config.get("worker_window_end", "08:00")):
        return None
    return config.get("worker_schedule_priority_cutoff", 1)
