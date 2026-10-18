"""
TDD Tests for Phase 1: LLM Seam, Explicit Caching, Character Profiles, ADK Config & Agent API Rescue.
Covers F-07, F-08, F-09, F-12.
"""

import os
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from pydantic import BaseModel
from google.genai import types


# ============================================================================
# Item 1: Explicit Caching & Character Profiles (F-07)
# ============================================================================

@pytest.mark.asyncio
async def test_atomic_runner_get_or_create_cache_sync_call(tmp_path, monkeypatch):
    """Verify get_or_create_cache is called synchronously with (metadata, model_name, system_instruction)."""
    from services.translation.atomic_runner import _translate_episode_atomic
    from utils import storage

    mock_get_or_create_cache = MagicMock(return_value=("cache-12345", {"context_cache_name": "cache-12345"}))
    monkeypatch.setattr("utils.cache_manager.get_or_create_cache", mock_get_or_create_cache)

    captured_char_context = None

    def fake_assemble(bible, lines, indices, **kwargs):
        nonlocal captured_char_context
        captured_char_context = kwargs.get("character_context")
        req = MagicMock()
        req.prompt = "translate lines"
        req.system_instruction = "inst"
        req.response_schema = None
        return req

    monkeypatch.setattr("services.prompt_assembler.assemble_episode_request", fake_assemble)
    monkeypatch.setattr("services.prompt_assembler.compile_bible", lambda *a, **k: MagicMock())

    gen_mock = AsyncMock(return_value='[{"index": 1, "text": "Γεια"}]')
    monkeypatch.setattr("adk_agents.llm_factory.generate", gen_mock)

    parsed_srt = [{"index": 1, "original": "Hello", "start": "00:00:01,000", "end": "00:00:02,000"}]
    project_meta = {"glossary": {"terms": []}, "context_guide": "Guide", "target_language": "Greek"}

    monkeypatch.setattr(storage, "load_project_metadata", lambda p: project_meta)
    monkeypatch.setattr(storage, "load_episode", lambda p, e: {"data": parsed_srt})
    monkeypatch.setattr(storage, "episode_dir", lambda p, e: tmp_path)
    monkeypatch.setattr(storage, "save_episode", lambda *a, **k: None)
    monkeypatch.setattr(storage, "load_episode_metadata", lambda *a, **k: {})
    monkeypatch.setattr("services.translation.atomic_runner.auto_export_translated_subtitle", lambda *a, **k: None)
    monkeypatch.setattr("services.translation.atomic_runner.get_ephemeral_session_service", lambda: MagicMock())
    monkeypatch.setattr("utils.jobs_manager.jobs", {})

    # Mock CharacterProfileManager
    mock_mgr = MagicMock()
    mock_mgr.load_all_resolved.return_value = []
    mock_mgr.get_profiles_for_chunk.return_value = [{"name": "Hero"}]
    mock_mgr.build_profile_context.return_value = "### Character: Hero\nBrave character"
    monkeypatch.setattr("services.translation.atomic_runner.CharacterProfileManager", lambda proj: mock_mgr)

    shared_agent = MagicMock()
    shared_agent.instruction = "Instruction for cache"

    success = await _translate_episode_atomic(
        job_id="test_cache_job",
        project_name="TestProject",
        episode_name="S01E01",
        target_lang="Greek",
        shared_agent=shared_agent,
        rate_limiter=MagicMock(),
        model_name="gemini-2.5-flash",
        global_config={
            "context_cache_enabled": True,
            "episode_request_mode": "whole",
            "whole_episode_max_cues": 100,
            "character_profiles_enabled": True,
            "tm_enabled": False,
            "sequential_scenes": True,
        },
    )

    assert success is True
    # Verify synchronous call with (metadata, model_name, system_instruction)
    assert mock_get_or_create_cache.call_count == 1
    args, _ = mock_get_or_create_cache.call_args
    assert args[0] == project_meta
    assert args[1] == "gemini-2.5-flash"
    assert args[2] == "Instruction for cache"

    # Verify character_context was passed to assemble_episode_request
    assert captured_char_context == "### Character: Hero\nBrave character"

    # Verify generate was called directly with cache_name
    assert gen_mock.call_count >= 1
    call_kwargs = gen_mock.call_args_list[0].kwargs
    assert call_kwargs["cache_name"] == "cache-12345"


# ============================================================================
# Item 2: Removal of Duplicate rate_limited_call Wrappers (F-08)
# ============================================================================

@pytest.mark.asyncio
async def test_qc_funnel_calls_generate_directly(monkeypatch):
    """Verify run_repair_pass calls generate directly without rate_limited_call."""
    from utils.qc_funnel import run_repair_pass

    gen_called = False

    async def fake_generate(**kwargs):
        nonlocal gen_called
        gen_called = True
        return "1| Διορθωμένο κείμενο"

    monkeypatch.setattr("adk_agents.llm_factory.generate", fake_generate)

    # rate_limited_call in api_call_wrapper should NOT be invoked by qc_funnel
    rate_limit_called = False

    async def fake_rate_limited_call(func, *args, **kwargs):
        nonlocal rate_limit_called
        rate_limit_called = True
        return await func()

    monkeypatch.setattr("utils.api_call_wrapper.rate_limited_call", fake_rate_limited_call)

    parsed_srt = [{"index": 1, "original": "Hello world", "translated": "Hello world"}]
    tickets = [{"index": 0, "issues": ["untranslated"]}]

    fixed_count = await run_repair_pass(
        parsed_srt=parsed_srt,
        tickets=tickets,
        target_lang_code="el",
        target_lang_name="Greek",
        primary_code="el",
        model_name="gemini-2.5-flash",
        rate_limiter=MagicMock(),
    )

    assert gen_called is True
    assert rate_limit_called is False
    assert fixed_count == 1
    assert parsed_srt[0]["translated"] == "Διορθωμένο κείμενο"


@pytest.mark.asyncio
async def test_line_recovery_calls_generate_directly(monkeypatch):
    """Verify line recovery functions call generate directly without rate_limited_call."""
    from services.translation.line_recovery import _retranslate_minority_lines, _translate_missing_line_with_context
    from utils import storage

    rate_limit_called = False

    async def fake_rate_limited_call(func, *args, **kwargs):
        nonlocal rate_limit_called
        rate_limit_called = True
        return await func()

    monkeypatch.setattr("utils.api_call_wrapper.rate_limited_call", fake_rate_limited_call)
    monkeypatch.setattr(storage, "load_project_metadata", lambda p: {"target_language": "Greek"})

    # 1. Test _retranslate_minority_lines
    async def fake_generate_reconcile(**kwargs):
        return "1| Επανασυμφιλιωμένη γραμμή"

    monkeypatch.setattr("adk_agents.llm_factory.generate", fake_generate_reconcile)

    parsed_srt = [{"index": 1, "original": "Term line", "translated": "Old line"}]
    drift_findings = [{
        "term": "Term",
        "canonical": "Όρος",
        "minority_lines": [{"index": 0, "rendering": "Old line"}],
    }]
    fixed = await _retranslate_minority_lines(
        project_name="TestProject",
        parsed_srt=parsed_srt,
        drift_findings=drift_findings,
        target_lang_name="Greek",
        target_lang_code="el",
        job_id="test_reconcile",
        rate_limiter=MagicMock(),
    )

    assert rate_limit_called is False
    assert fixed == 1
    assert parsed_srt[0]["translated"] == "Επανασυμφιλιωμένη γραμμή"

    # 2. Test _translate_missing_line_with_context
    async def fake_generate_missing(**kwargs):
        return "Λείπουσα γραμμή"

    monkeypatch.setattr("adk_agents.llm_factory.generate", fake_generate_missing)

    res = await _translate_missing_line_with_context(
        project_name="TestProject",
        episode_name="S01E01",
        line_idx=0,
        parsed_srt=parsed_srt,
        translated_map={},
        target_lang_name="Greek",
        target_lang_code="el",
        model_name="gemini-2.5-flash",
        rate_limiter=MagicMock(),
    )

    assert rate_limit_called is False
    assert res == "Λείπουσα γραμμή"


# ============================================================================
# Item 3: SafetyAwareGemini generation_config Propagation (F-08)
# ============================================================================

@pytest.mark.asyncio
async def test_safety_aware_gemini_propagates_generation_config(monkeypatch):
    """Verify SafetyAwareGemini copies generation_config properties onto llm_request.config."""
    from adk_agents.llm_factory import SafetyAwareGemini

    model = SafetyAwareGemini(model="gemini-2.5-flash")
    schema = type("DummySchema", (BaseModel,), {})
    thinking_cfg = types.ThinkingConfig(thinking_budget=1024)

    object.__setattr__(model, "generation_config", {
        "temperature": 0.4,
        "thinking_config": thinking_cfg,
        "response_mime_type": "application/json",
        "response_schema": schema,
        "top_k": 40,
        "top_p": 0.95,
    })

    # Mock super().generate_content_async to inspect llm_request
    captured_request = None

    async def fake_super_generate(self, req, stream=False):
        nonlocal captured_request
        captured_request = req
        yield "fake_resp"

    monkeypatch.setattr(
        "google.adk.models.google_llm.Gemini.generate_content_async",
        fake_super_generate
    )

    req = MagicMock()
    req.config = None

    responses = [r async for r in model.generate_content_async(req)]
    assert responses == ["fake_resp"]
    assert captured_request is not None
    assert captured_request.config is not None

    cfg = captured_request.config
    assert cfg.temperature == 0.4
    assert cfg.thinking_config == thinking_cfg
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_schema == schema
    assert cfg.top_k == 40
    assert cfg.top_p == 0.95


# ============================================================================
# Item 4: source_text Truncation & verify_echo (F-09)
# ============================================================================

def test_parse_structured_no_source_text_truncation():
    """Verify parse_structured preserves source_text beyond 80 characters without truncating."""
    from utils.structured_output import parse_structured

    long_source = "A" * 160
    json_text = json.dumps({
        "lines": [
            {"i": 1, "s": long_source, "t": "Μεγάλο κείμενο"}
        ]
    })

    results = parse_structured(json_text)
    assert len(results) == 1
    item = results[0]
    assert item["index"] == 1
    assert item["text"] == "Μεγάλο κείμενο"
    assert item["source_text"] == long_source
    assert len(item["source_text"]) == 160


def test_parse_translation_response_wires_verify_echo():
    """Verify parse_translation_response filters items with verify_echo=True."""
    from utils.structured_output import parse_translation_response

    # Simulated model output where line 1 echoed neighbour line 2's source ("By the river")
    source_lines = ["Take the sword now", "By the river bank we sat"]

    json_text = json.dumps({
        "lines": [
            {"i": 1, "s": "By the river", "t": "Κοντά στο ποτάμι"},
        ]
    })

    # 1. With verify_echo=False: item returned
    unverified = parse_translation_response(json_text, expected_indices=2, verify_echo=False, source_lines=source_lines)
    assert len(unverified) == 1

    # 2. With verify_echo=True and source_lines: shifted line 1 is dropped by filter_by_source_echo
    verified = parse_translation_response(json_text, expected_indices=2, verify_echo=True, source_lines=source_lines)
    assert len(verified) == 0


# ============================================================================
# Item 5: Agent Runner api_rescue, _VALID_MODES, and config.json Key (F-12)
# ============================================================================

def test_routers_agent_valid_modes_and_runner_start_request():
    """Verify _VALID_MODES has 'gaps' and RunnerStartRequest contains api_rescue and rescue_model."""
    from routers.agent import _VALID_MODES, RunnerStartRequest

    assert "gaps" in _VALID_MODES
    assert "auto" in _VALID_MODES
    assert "full" in _VALID_MODES
    assert "missing" in _VALID_MODES
    assert "Sign" in _VALID_MODES

    req = RunnerStartRequest(api_rescue=True, rescue_model="gemini-2.5-flash")
    assert req.api_rescue is True
    assert req.rescue_model == "gemini-2.5-flash"


def test_agent_runner_rescue_model_resolves_key_from_global_config(monkeypatch):
    """Verify _rescue_model finds Gemini API key in storage.GLOBAL_CONFIG even when os.environ has none."""
    from services.agent_runner import AgentRunner
    from utils import storage

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    monkeypatch.setattr(storage, "GLOBAL_CONFIG", {
        "gemini_api_key": "cfg-key-test-12345"
    })

    runner = AgentRunner()
    runner.config = {
        "api_rescue": True,
        "rescue_model": "gemini-2.5-flash"
    }

    model = runner._rescue_model("TestProject")
    assert model == "gemini-2.5-flash"

    # If api_rescue is False, it should return None
    runner.config["api_rescue"] = False
    assert runner._rescue_model("TestProject") is None

    # If no key in env and no key in storage, it should return None
    runner.config["api_rescue"] = True
    monkeypatch.setattr(storage, "GLOBAL_CONFIG", {})
    assert runner._rescue_model("TestProject") is None
