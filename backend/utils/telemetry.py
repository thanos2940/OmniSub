"""
LLM usage telemetry (v2 plan, D8) — token & cost accounting as a first-class signal.

Every LLM response's usage metadata is recorded into a small SQLite table
(``llm_usage`` in omnisub.db). Rollups feed the dashboard cost panel, the
token-based cost preflight, and the daily budget cap.

Call sites don't pass project/episode explicitly — the worker / batch lane set a
context variable (``usage_ctx``) once per item, and ``record_usage`` reads it, so
the capture point can live inside the low-level generate functions.
"""

import contextvars
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from utils.paths import DB_FILE

logger = logging.getLogger(__name__)

# Set by the worker / batch lane: {"project": ..., "episode": ..., "lane": "live"|"batch"}
usage_ctx: contextvars.ContextVar[Optional[Dict]] = contextvars.ContextVar("usage_ctx", default=None)

# Indicative $/1M-token prices per model family (input, output). Output price covers
# thinking tokens too (billed as output). Estimates for cost *display* — keep loose.
_PRICES = [
    ("flash-lite", (0.10, 0.40)),
    ("flash", (0.30, 2.50)),
    ("pro", (1.25, 10.00)),
]


def _price_for(model: str):
    m = (model or "").lower()
    if m.startswith("local/") or m.startswith("openai/"):
        return (0.0, 0.0)
    for marker, p in _PRICES:
        if marker in m:
            return p
    return (0.30, 2.50)  # default to flash pricing


_schema_ready = False


def _ensure_schema(conn) -> None:
    global _schema_ready
    if _schema_ready:
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            project TEXT, episode TEXT,
            role TEXT, model TEXT, lane TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            thoughts_tokens INTEGER DEFAULT 0,
            cached_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_proj ON llm_usage(project, episode)")
    _schema_ready = True


def extract_usage(resp: Any) -> Dict[str, int]:
    """Pull token counts from either a google-genai response (usage_metadata) or a
    litellm/OpenAI response (usage). Missing fields default to 0."""
    out = {"prompt_tokens": 0, "output_tokens": 0, "thoughts_tokens": 0, "cached_tokens": 0}
    um = getattr(resp, "usage_metadata", None)
    if um is not None:
        out["prompt_tokens"] = int(getattr(um, "prompt_token_count", 0) or 0)
        out["output_tokens"] = int(getattr(um, "candidates_token_count", 0) or 0)
        out["thoughts_tokens"] = int(getattr(um, "thoughts_token_count", 0) or 0)
        out["cached_tokens"] = int(getattr(um, "cached_content_token_count", 0) or 0)
        return out
    u = getattr(resp, "usage", None)
    if u is not None:
        out["prompt_tokens"] = int(getattr(u, "prompt_tokens", 0) or 0)
        out["output_tokens"] = int(getattr(u, "completion_tokens", 0) or 0)
    return out


def record_usage(model: str, role: str, usage: Dict[str, int], lane: Optional[str] = None) -> None:
    """Persist one call's usage. Never raises — telemetry must not break translation."""
    try:
        ctx = usage_ctx.get() or {}
        pin, pout = _price_for(model)
        billable_out = usage.get("output_tokens", 0) + usage.get("thoughts_tokens", 0)
        # Cached input is discounted ~75%; approximate.
        cached = usage.get("cached_tokens", 0)
        uncached_in = max(0, usage.get("prompt_tokens", 0) - cached)
        cost = (uncached_in * pin + cached * pin * 0.25 + billable_out * pout) / 1_000_000.0
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        try:
            _ensure_schema(conn)
            conn.execute(
                """INSERT INTO llm_usage
                   (ts, project, episode, role, model, lane,
                    prompt_tokens, output_tokens, thoughts_tokens, cached_tokens, cost_usd)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (datetime.now(timezone.utc).isoformat(),
                 ctx.get("project"), ctx.get("episode"), role, model,
                 lane or ctx.get("lane"),
                 usage.get("prompt_tokens", 0), usage.get("output_tokens", 0),
                 usage.get("thoughts_tokens", 0), usage.get("cached_tokens", 0),
                 round(cost, 8)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"telemetry record_usage skipped: {e}")


def summary(days: int = 30) -> Dict:
    """Aggregate cost/token stats for the dashboard."""
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            _ensure_schema(conn)
            cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
            cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat()
            row = conn.execute(
                """SELECT COUNT(*) AS calls,
                          COALESCE(SUM(prompt_tokens),0) AS p,
                          COALESCE(SUM(output_tokens),0) AS o,
                          COALESCE(SUM(thoughts_tokens),0) AS t,
                          COALESCE(SUM(cached_tokens),0) AS c,
                          COALESCE(SUM(cost_usd),0) AS cost
                   FROM llm_usage WHERE ts >= ?""", (cutoff_iso,)).fetchone()
            today = datetime.now(timezone.utc).date().isoformat()
            day_row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd),0) AS cost FROM llm_usage WHERE ts >= ?",
                (today,)).fetchone()
            per_ep = conn.execute(
                """SELECT COALESCE(AVG(ep_cost),0) AS avg_cost, COALESCE(AVG(ep_calls),0) AS avg_calls FROM (
                       SELECT project, episode, SUM(cost_usd) AS ep_cost, COUNT(*) AS ep_calls
                       FROM llm_usage
                       WHERE ts >= ? AND episode IS NOT NULL AND role = 'translation'
                       GROUP BY project, episode)""", (cutoff_iso,)).fetchone()
            return {
                "window_days": days,
                "calls": row["calls"],
                "prompt_tokens": row["p"],
                "output_tokens": row["o"],
                "thoughts_tokens": row["t"],
                "cached_tokens": row["c"],
                "cost_usd": round(row["cost"], 4),
                "cost_today_usd": round(day_row["cost"], 4),
                "avg_cost_per_episode": round(per_ep["avg_cost"], 5),
                "avg_translation_calls_per_episode": round(per_ep["avg_calls"], 1),
            }
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"telemetry summary failed: {e}")
        return {"window_days": days, "calls": 0, "cost_usd": 0.0, "cost_today_usd": 0.0}


def cost_today() -> float:
    """Total recorded cost for the current UTC day (for the daily budget cap)."""
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        try:
            _ensure_schema(conn)
            today = datetime.now(timezone.utc).date().isoformat()
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd),0) FROM llm_usage WHERE ts >= ?", (today,)
            ).fetchone()
            return float(row[0] or 0.0)
        finally:
            conn.close()
    except Exception:
        return 0.0


def avg_tokens_per_line(project: Optional[str] = None) -> Dict[str, float]:
    """Observed per-line token averages for the cost preflight; falls back to
    char-based heuristics when there's no history yet."""
    # Defaults: ~13 source tokens/line in, ~14 out (Greek slightly longer + JSON).
    return {"in_per_line": 13.0, "out_per_line": 14.0}


def prune(max_age_days: int = 90) -> int:
    """Drop raw rows older than the window (called from the maintenance loop)."""
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        try:
            _ensure_schema(conn)
            cutoff = datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() - max_age_days * 86400, timezone.utc
            ).isoformat()
            cur = conn.execute("DELETE FROM llm_usage WHERE ts < ?", (cutoff,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
    except Exception:
        return 0
