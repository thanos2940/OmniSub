"""LLM usage telemetry (v2 D8) — record/summary/budget round-trip on a temp DB."""

import tempfile
from pathlib import Path

import pytest

import utils.telemetry as tel


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    db = Path(tempfile.mkdtemp()) / "telem.db"
    monkeypatch.setattr(tel, "DB_FILE", db)
    monkeypatch.setattr(tel, "_schema_ready", False)
    yield


def test_record_and_summary():
    tel.usage_ctx.set({"project": "P", "episode": "E1", "lane": "live"})
    tel.record_usage("gemini-2.5-flash", "translation",
                     {"prompt_tokens": 5000, "output_tokens": 7000, "thoughts_tokens": 0, "cached_tokens": 0})
    tel.record_usage("gemini-2.5-flash", "translation",
                     {"prompt_tokens": 4000, "output_tokens": 6000, "thoughts_tokens": 0, "cached_tokens": 2000})
    s = tel.summary(30)
    assert s["calls"] == 2
    assert s["cost_usd"] > 0
    # Both calls were one episode -> avg calls/episode = 2.
    assert s["avg_translation_calls_per_episode"] == 2.0
    assert tel.cost_today() > 0


def test_local_model_is_free():
    tel.usage_ctx.set({"project": "P", "episode": "E2", "lane": "live"})
    tel.record_usage("local/mistral", "translation",
                     {"prompt_tokens": 9999, "output_tokens": 9999, "thoughts_tokens": 0, "cached_tokens": 0})
    s = tel.summary(30)
    assert s["calls"] == 1
    assert s["cost_usd"] == 0.0  # local pricing is zero


def test_thinking_tokens_count_as_output_cost():
    tel.usage_ctx.set({"project": "P", "episode": "E3", "lane": "live"})
    tel.record_usage("gemini-2.5-flash", "translation",
                     {"prompt_tokens": 0, "output_tokens": 0, "thoughts_tokens": 1_000_000, "cached_tokens": 0})
    s = tel.summary(30)
    # 1M thinking tokens billed at the flash output rate (~$2.50).
    assert s["thoughts_tokens"] == 1_000_000
    assert s["cost_usd"] == pytest.approx(2.50, rel=0.01)


def test_prune_removes_nothing_recent():
    tel.usage_ctx.set({"project": "P", "episode": "E4", "lane": "live"})
    tel.record_usage("gemini-2.5-flash", "translation",
                     {"prompt_tokens": 10, "output_tokens": 10, "thoughts_tokens": 0, "cached_tokens": 0})
    assert tel.prune(max_age_days=90) == 0
    assert tel.summary(30)["calls"] == 1
