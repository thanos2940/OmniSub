"""
Dashboard / backlog-ETA endpoint (Plan 08).

Rolls up library progress, queue state, recent throughput, per-model quota, and an
estimated time to drain the backlog at the current rate.
"""

import time

from fastapi import APIRouter

from utils import storage
from utils.translation_queue import TranslationQueue
from utils.rate_limiter import per_model_rate_limiter

router = APIRouter()

_AVG_SCENES_PER_EPISODE = 8  # rough request-per-episode estimate when no history exists

# The dashboard aggregates the whole library + queue + quota; cache briefly so the
# 10s frontend poll doesn't recompute it from scratch every time.
_DASHBOARD_TTL = 8.0
_dashboard_cache = {"ts": 0.0, "data": None}


@router.get("/api/dashboard")
async def get_dashboard():
    now = time.time()
    if _dashboard_cache["data"] is not None and (now - _dashboard_cache["ts"]) < _DASHBOARD_TTL:
        return _dashboard_cache["data"]
    result = await _build_dashboard()
    _dashboard_cache["data"] = result
    _dashboard_cache["ts"] = now
    return result


async def _build_dashboard():
    library = storage.get_arr_library()
    series = library.get("series", [])
    movies = library.get("movies", [])
    entries = series + movies

    total_eps = sum(e.get("episode_count", 0) for e in entries)
    translated_eps = sum(e.get("translated_count", 0) for e in entries)
    missing = max(0, total_eps - translated_eps)

    queue = TranslationQueue()
    summary = queue.get_summary()
    throughput = queue.completions_by_day(14)
    by_priority = queue.counts_by_priority()

    config = storage.load_global_config()
    model = config.get("default_translation_model", "gemini-flash-lite-latest")
    limiter = per_model_rate_limiter.get_limiter(model)
    remaining_daily = max(0, limiter.daily_limit - limiter._daily_count)

    # Effective episodes/day: prefer a rolling average of recent completions,
    # else fall back to daily quota / avg-scenes-per-episode.
    recent = [d["count"] for d in throughput[:7]]
    eps_per_day_observed = (sum(recent) / len(recent)) if recent else 0.0
    eps_per_day_capacity = (limiter.daily_limit / _AVG_SCENES_PER_EPISODE) if _AVG_SCENES_PER_EPISODE else 0.0
    effective = eps_per_day_observed if eps_per_day_observed > 0 else eps_per_day_capacity
    eta_days = round(missing / effective, 1) if (missing and effective > 0) else (0 if not missing else None)

    return {
        "library": {
            "shows": len(series),
            "movies": len(movies),
            "disabled": library.get("totals", {}).get("disabled", 0),
            "total_episodes": total_eps,
            "translated_episodes": translated_eps,
            "missing_episodes": missing,
            "progress_pct": round((translated_eps / total_eps * 100), 1) if total_eps else 100.0,
            "last_sync": library.get("last_sync"),
        },
        "queue": {"summary": summary, "by_priority": by_priority},
        "throughput": list(reversed(throughput)),  # oldest → newest for charting
        "quota": {
            "model": model,
            "daily_count": limiter._daily_count,
            "daily_limit": limiter.daily_limit,
            "remaining_daily": remaining_daily,
            "all_models": per_model_rate_limiter.get_all_stats(),
        },
        "eta": {
            "days": eta_days,
            "episodes_per_day": round(effective, 1),
            "source": "observed" if eps_per_day_observed > 0 else "capacity_estimate",
        },
        # v2 D8 — token/cost telemetry rollup (30-day window).
        "usage": _usage_summary(),
    }


def _usage_summary():
    try:
        from utils.telemetry import summary
        return summary(days=30)
    except Exception:
        return {"calls": 0, "cost_usd": 0.0, "cost_today_usd": 0.0}
