"""Efficiency + quality optimizations:

* batched intra-episode reconciliation (one call, never per-line)
* source-echo alignment guard (schema, parsing, verification filter, wiring)
"""

import pytest
from unittest.mock import patch

from utils.structured_output import (
    parse_structured,
    use_source_echo,
    VerifiedEpisodeTranslationResponse,
    SceneTranslationResponse,
)
from utils.alignment_audit import filter_by_source_echo
from services.prompt_assembler import Bible, assemble_episode_request
from services.translation_service import _retranslate_minority_lines


def _line(orig, trans):
    return {"original": orig, "translated": trans, "translations": {"el": trans}}


class _NoLimiter:
    async def acquire(self):
        return


# --- #1 batched reconciliation ------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_is_one_batched_call_for_all_drifting_lines():
    parsed = [
        _line("Take the sword, Rin", "Πάρε το σπαθί, Ριυ"),   # wrong rendering of Rin
        _line("Rin is here", "Η Ριν είναι εδώ"),
        _line("Goodbye Rin", "Αντίο Ριυ"),                    # wrong rendering of Rin
    ]
    drift = [{
        "term": "Rin",
        "canonical": "Ριν",
        "minority_lines": [
            {"index": 0, "original": parsed[0]["original"], "current_translation": parsed[0]["translated"]},
            {"index": 2, "original": parsed[2]["original"], "current_translation": parsed[2]["translated"]},
        ],
    }]

    calls = {"n": 0}

    async def fake_generate(model_name, prompt, **kwargs):
        calls["n"] += 1
        assert kwargs.get("role") == "reconciliation"
        # Both ticketed lines arrive in the SAME prompt as numbered items.
        assert "1|" in prompt and "2|" in prompt
        return "1| Πάρε το σπαθί, Ριν\n2| Αντίο Ριν"

    with patch("adk_agents.llm_factory.generate", fake_generate), \
         patch("utils.model_resolver.resolve_model", return_value="gemini-flash-lite-latest"), \
         patch("utils.storage.load_project_metadata", return_value={"target_language": "Greek"}), \
         patch("services.translation_service.update_job"):
        fixed = await _retranslate_minority_lines(
            "proj", parsed, drift, "el", "Greek", "job1", _NoLimiter())

    assert calls["n"] == 1   # ONE call for both lines, never one-per-line
    assert fixed == 2
    assert parsed[0]["translations"]["el"] == "Πάρε το σπαθί, Ριν"
    assert parsed[2]["translations"]["el"] == "Αντίο Ριν"


@pytest.mark.asyncio
async def test_reconcile_noop_when_no_drift():
    calls = {"n": 0}

    async def fake_generate(*a, **k):
        calls["n"] += 1
        return ""

    with patch("adk_agents.llm_factory.generate", fake_generate), \
         patch("services.translation_service.update_job"):
        fixed = await _retranslate_minority_lines(
            "proj", [_line("Hi", "Γεια")], [], "el", "Greek", "job1", _NoLimiter())

    assert fixed == 0
    assert calls["n"] == 0   # no drift → no call at all


# --- #4 source-echo guard: parsing --------------------------------------------

def test_parse_structured_captures_source_echo():
    text = '{"lines":[{"i":1,"s":"Take the sword","t":"Πάρε το σπαθί"}]}'
    res = parse_structured(text, expected_count=1)
    assert res[0]["index"] == 1
    assert res[0]["text"] == "Πάρε το σπαθί"
    assert res[0]["src_head"] == "Take the sword"


def test_parse_structured_without_echo_defaults_empty():
    text = '{"lines":[{"i":1,"t":"Πάρε το σπαθί"}]}'
    res = parse_structured(text, expected_count=1)
    assert res[0]["src_head"] == ""


# --- #4 source-echo guard: verification filter --------------------------------

def test_echo_filter_keeps_consistent_and_missing_echoes():
    items = [
        {"index": 1, "text": "A", "src_head": "Take the sword"},  # matches src[0]
        {"index": 2, "text": "B", "src_head": ""},                # no echo → kept
    ]
    src = ["Take the sword now", "Whatever line two is here"]
    kept, dropped = filter_by_source_echo(items, src)
    assert dropped == 0
    assert len(kept) == 2


def test_echo_filter_drops_when_echo_belongs_to_neighbour():
    # The model says it translated "By the river" but stamped index 1 (src[0]).
    items = [{"index": 1, "text": "A", "src_head": "By the river"}]
    src = ["Take the sword now", "By the river bank we sat"]
    kept, dropped = filter_by_source_echo(items, src)
    assert dropped == 1
    assert kept == []   # dropped → safe gap, repaired from its own source later


def test_echo_filter_keeps_when_echo_matches_nowhere():
    # A paraphrased / garbled echo that matches no line must NOT be dropped.
    items = [{"index": 1, "text": "A", "src_head": "zzz qqq wxyz"}]
    src = ["Take the sword now", "By the river bank we sat"]
    kept, dropped = filter_by_source_echo(items, src)
    assert dropped == 0
    assert len(kept) == 1


# --- #4 source-echo guard: request assembly wiring ----------------------------

def _bible():
    return Bible(
        project_name="", target_language="Greek", system_instruction="SYS",
        fingerprint="", glossary={"terms": []}, structured=True,
        model_name="gemini-flash-lite-latest",
    )


def test_episode_request_uses_verified_schema_when_echo_on():
    with patch("services.prompt_assembler.use_source_echo", return_value=True):
        req = assemble_episode_request(_bible(), [{"original": "Hello there friend"}], [0])
    assert req.verify_echo is True
    assert req.response_schema is VerifiedEpisodeTranslationResponse
    assert '"s"' in req.system_instruction   # echo rule appended


def test_episode_request_uses_plain_schema_when_echo_off():
    with patch("services.prompt_assembler.use_source_echo", return_value=False):
        req = assemble_episode_request(_bible(), [{"original": "Hello there friend"}], [0])
    assert req.verify_echo is False
    assert req.response_schema is SceneTranslationResponse
    assert '"s"' not in req.system_instruction


def test_use_source_echo_off_for_local_model():
    # Local models route through use_structured_output (off by default) → no echo.
    assert use_source_echo("local/llama-3-8b") is False
