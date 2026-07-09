"""End-to-end integration tests for the core episode translation path (v2 plan).

Drives ``_translate_episode_atomic`` with a scripted fake LLM (no network) over a
temp project, covering:

  - whole-episode mode (D1): one request translates everything; the retry loop
    span-repairs dropped lines; new_terms (D7) land in suggestions;
  - scene mode (local lane / fallback): partial failure fills + flags gaps
    (<=20%), heavy failure (>20%) fails the episode atomically.

Fake translations use Greek text so the QC funnel's Latin-ratio validator doesn't
ticket them (the funnel runs live in these tests — that's intentional coverage).
"""

import re
import json
import asyncio
import pytest

import utils.storage as storage
import utils.metadata_index as metadata_index
from utils.rate_limiter import no_op_rate_limiter
from utils.jobs_manager import jobs, JobStatus
from services import translation_service


BASE_CONFIG = {
    "tm_enabled": False,
    "episode_summaries_enabled": False,
    "intra_episode_reconcile_enabled": False,
    "conformance_enabled": False,
    "condense_enabled": False,
    "enable_reviewer": False,
    "character_profiles_enabled": False,
    "glossary_enforce_enabled": False,
    "repair_pass_enabled": False,
    "new_terms_suggest_enabled": False,
    "source_clean_enabled": False,
    "context_cache_enabled": False,
    "max_lines_per_scene": 50,
    "whole_episode_max_cues": 1200,
    "scene_lookahead_lines": 0,
    "incremental_save_throttle_seconds": 0,
    "sequential_scenes": False,
}


def _make_rows(n):
    return [{
        "id": str(i + 1),
        "timecode": f"00:00:{i:02d},000 --> 00:00:{i:02d},500",
        "original": f"Hello number {i}",
        "translated": "",
        "translations": {},
    } for i in range(n)]


def _setup(tmp_path, monkeypatch, n_lines, provider="local"):
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path / "projects")
    # Keep the real metadata index DB untouched by this temp project.
    monkeypatch.setattr(metadata_index, "upsert", lambda *a, **k: None)
    monkeypatch.setattr(metadata_index, "count_translated", lambda *a, **k: None)
    monkeypatch.setattr(metadata_index, "set_review_count", lambda *a, **k: None)

    storage.create_project("TP", {
        "target_language": "Greek",
        "settings": {"ai_provider": provider, "local_translation_model": "test-model"},
    })
    storage.save_episode("TP", "E01", _make_rows(n_lines), {"arr_media_path": None})

    job_id = "itest"
    jobs[job_id] = JobStatus(id=job_id, status="running", progress=0.0, message="", logs=[])
    return job_id


class FakeLLM:
    """Scripted generate(): echoes 'N| ΜΕΤ N' per numbered prompt line; can drop
    indices on specific calls and append a new_terms JSON payload."""

    def __init__(self, drop_on_call=None, new_terms_on_first=False):
        self.calls = 0
        self.drop_on_call = drop_on_call or {}   # {call_number: {local_indices_to_drop}}
        self.new_terms_on_first = new_terms_on_first

    async def __call__(self, model_name, prompt, **kwargs):
        self.calls += 1
        nums = re.findall(r'^(\d+)\|', prompt, re.M)
        drops = self.drop_on_call.get(self.calls, set())
        out_lines = [f"{n}| ΜΕΤ {n}" for n in nums if int(n) not in drops]
        if self.new_terms_on_first and self.calls == 1:
            payload = {
                "lines": [{"i": int(n), "t": f"ΜΕΤ {n}"} for n in nums if int(n) not in drops],
                "new_terms": [{"term": "Hollow", "translation": "Χόλοου", "type": "other"}],
            }
            return json.dumps(payload, ensure_ascii=False)
        return "\n".join(out_lines)


async def _run(job_id, model, config):
    return await translation_service._translate_episode_atomic(
        job_id=job_id, project_name="TP", episode_name="E01", target_lang="Greek",
        shared_agent=None, rate_limiter=no_op_rate_limiter, semaphore=asyncio.Semaphore(2),
        global_config=config, total_episodes=1, episode_idx=0, start_progress=0.0,
        model_name=model,
    )


# ---------------------------------------------------------------------------
# Whole-episode mode (Gemini lane)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_whole_episode_single_call_translates_all(tmp_path, monkeypatch):
    job_id = _setup(tmp_path, monkeypatch, n_lines=12)
    fake = FakeLLM()
    monkeypatch.setattr("adk_agents.llm_factory.generate", fake)
    cfg = {**BASE_CONFIG, "episode_request_mode": "whole"}

    ok = await _run(job_id, "gemini-2.5-flash", cfg)

    assert ok is True
    # One whole-episode request, no scene calls needed (D1: requests/episode -> 1).
    assert fake.calls == 1
    data = storage.load_episode("TP", "E01")["data"]
    assert all(l["translations"]["el"].startswith("ΜΕΤ") for l in data)
    meta = storage.load_episode_metadata("TP", "E01")
    assert meta.get("bible_fp")          # D4: bible version stamped
    assert meta.get("qc_stages", {}).get("translate") == "ok"


@pytest.mark.asyncio
async def test_whole_episode_retry_repairs_dropped_span(tmp_path, monkeypatch):
    job_id = _setup(tmp_path, monkeypatch, n_lines=10)
    # First call drops lines 4-6; the retry (attempt 2) covers the remainder.
    fake = FakeLLM(drop_on_call={1: {4, 5, 6}})
    monkeypatch.setattr("adk_agents.llm_factory.generate", fake)
    cfg = {**BASE_CONFIG, "episode_request_mode": "whole"}

    ok = await _run(job_id, "gemini-2.5-flash", cfg)

    assert ok is True
    assert fake.calls == 2  # initial + span-repair retry
    data = storage.load_episode("TP", "E01")["data"]
    assert all(l["translations"]["el"] for l in data)
    assert not any(l.get("needs_review") for l in data)


@pytest.mark.asyncio
async def test_whole_episode_harvests_new_terms(tmp_path, monkeypatch):
    job_id = _setup(tmp_path, monkeypatch, n_lines=5)
    fake = FakeLLM(new_terms_on_first=True)
    monkeypatch.setattr("adk_agents.llm_factory.generate", fake)
    cfg = {**BASE_CONFIG, "episode_request_mode": "whole", "new_terms_suggest_enabled": True}

    ok = await _run(job_id, "gemini-2.5-flash", cfg)

    assert ok is True
    # D7: new_terms from the same response land in the suggestions inbox.
    sf = storage.PROJECTS_DIR / "TP" / "suggestions.json"
    assert sf.exists()
    suggestions = json.loads(sf.read_text(encoding="utf-8"))
    assert any(s["term"] == "Hollow" for s in suggestions)


# ---------------------------------------------------------------------------
# Scene mode (local lane / fallback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scene_mode_happy_path(tmp_path, monkeypatch):
    job_id = _setup(tmp_path, monkeypatch, n_lines=8)
    fake = FakeLLM()
    monkeypatch.setattr("adk_agents.llm_factory.generate", fake)
    cfg = {**BASE_CONFIG, "episode_request_mode": "scenes"}

    ok = await _run(job_id, "local/test-model", cfg)

    assert ok is True
    data = storage.load_episode("TP", "E01")["data"]
    assert all(l["translated"].startswith("ΜΕΤ") for l in data)


@pytest.mark.asyncio
async def test_scene_mode_partial_failure_flags_gaps(tmp_path, monkeypatch):
    job_id = _setup(tmp_path, monkeypatch, n_lines=10)
    # Drop scene-local line 5 on every call -> 10% missing -> fill + flag, complete.
    fake = FakeLLM(drop_on_call={1: {5}})
    monkeypatch.setattr("adk_agents.llm_factory.generate", fake)
    cfg = {**BASE_CONFIG, "episode_request_mode": "scenes"}

    ok = await _run(job_id, "local/test-model", cfg)

    assert ok is True
    data = storage.load_episode("TP", "E01")["data"]
    flagged = [l for l in data if l.get("needs_review")]
    assert len(flagged) == 1
    assert flagged[0]["translated"] == ""  # gap stays empty, never silent English


@pytest.mark.asyncio
async def test_scene_mode_heavy_failure_fails_atomically(tmp_path, monkeypatch):
    job_id = _setup(tmp_path, monkeypatch, n_lines=10)
    fake = FakeLLM(drop_on_call={1: {1, 2, 3, 4, 5}})  # 50% missing
    monkeypatch.setattr("adk_agents.llm_factory.generate", fake)
    cfg = {**BASE_CONFIG, "episode_request_mode": "scenes"}

    ok = await _run(job_id, "local/test-model", cfg)

    assert ok is False
