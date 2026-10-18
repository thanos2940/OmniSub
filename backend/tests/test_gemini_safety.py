"""
Gemini API safety thresholds
============================
Every Gemini request Omnisub makes carries the thresholds of the ``gemini_safety_filter``
setting (default ``off``: subtitles of films and series contain violence and crude language):
live calls through ``cached_translator``, Batch API requests and the ADK agents. A response a
filter stopped is reported with its reason. Network-free: the Gemini client is a fake.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from google.genai import types

from utils import gemini_safety, storage


def _thresholds(settings):
    return {s.category.name: s.threshold.name for s in settings}


class TestLevels:

    def test_default_is_off_for_the_four_text_categories(self):
        settings = gemini_safety.safety_settings()
        assert _thresholds(settings) == {c: "OFF" for c in gemini_safety.CATEGORIES}

    def test_levels_from_the_settings(self):
        storage.save_global_config({"gemini_safety_filter": "only_high"})
        assert set(_thresholds(gemini_safety.safety_settings()).values()) == {"BLOCK_ONLY_HIGH"}
        storage.save_global_config({"gemini_safety_filter": "default"})
        assert gemini_safety.safety_settings() is None   # Google's own thresholds
        storage.save_global_config({"gemini_safety_filter": "nonsense"})
        assert gemini_safety.current_level() == "off"

    def test_an_explicit_level_wins_over_the_settings(self):
        storage.save_global_config({"gemini_safety_filter": "default"})
        assert set(_thresholds(gemini_safety.safety_settings("off")).values()) == {"OFF"}


class TestDescribeBlock:

    def test_blocked_prompt(self):
        resp = SimpleNamespace(prompt_feedback=SimpleNamespace(block_reason=types.BlockedReason.PROHIBITED_CONTENT),
                               candidates=[])
        assert gemini_safety.describe_block(resp) == "prompt blocked: PROHIBITED_CONTENT"

    def test_answer_stopped_by_a_category(self):
        rating = SimpleNamespace(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, blocked=True)
        cand = SimpleNamespace(finish_reason=types.FinishReason.SAFETY, safety_ratings=[rating])
        resp = SimpleNamespace(prompt_feedback=None, candidates=[cand])
        assert gemini_safety.describe_block(resp) == "SAFETY: HARM_CATEGORY_HARASSMENT"

    def test_recitation_and_normal_answers(self):
        cand = SimpleNamespace(finish_reason=types.FinishReason.RECITATION, safety_ratings=None)
        assert gemini_safety.describe_block(SimpleNamespace(prompt_feedback=None, candidates=[cand])) == "RECITATION"
        ok = SimpleNamespace(finish_reason=types.FinishReason.STOP, safety_ratings=None)
        assert gemini_safety.describe_block(SimpleNamespace(prompt_feedback=None, candidates=[ok])) is None


class _FakeModels:
    def __init__(self, response):
        self.response = response
        self.configs = []

    def generate_content(self, model, contents, config):
        self.configs.append(config)
        return self.response


def _fake_client(response):
    models = _FakeModels(response)
    return models, (lambda *a, **k: SimpleNamespace(models=models))


async def _through(coro_factory, rate_limiter=None, **kwargs):
    return await coro_factory()


class TestLiveCalls:

    @pytest.mark.asyncio
    async def test_live_requests_carry_the_thresholds(self, monkeypatch):
        from services.cached_translator import generate_cached
        models, client = _fake_client(SimpleNamespace(text="Γεια", prompt_feedback=None, candidates=[]))
        monkeypatch.setattr("google.genai.Client", client)
        monkeypatch.setattr("utils.api_call_wrapper.rate_limited_call", _through)
        assert await generate_cached("gemini-3.1-flash-lite", "Hello") == "Γεια"
        assert set(_thresholds(models.configs[0].safety_settings).values()) == {"OFF"}

    @pytest.mark.asyncio
    async def test_default_level_sends_no_thresholds(self, monkeypatch):
        from services.cached_translator import generate_cached
        storage.save_global_config({"gemini_safety_filter": "default"})
        models, client = _fake_client(SimpleNamespace(text="Γεια", prompt_feedback=None, candidates=[]))
        monkeypatch.setattr("google.genai.Client", client)
        monkeypatch.setattr("utils.api_call_wrapper.rate_limited_call", _through)
        await generate_cached("gemini-3.1-flash-lite", "Hello")
        assert not models.configs[0].safety_settings

    @pytest.mark.asyncio
    async def test_a_filtered_answer_is_reported_with_its_reason(self, monkeypatch):
        from services.cached_translator import generate_cached
        resp = SimpleNamespace(text=None, prompt_feedback=SimpleNamespace(block_reason=types.BlockedReason.OTHER),
                               candidates=[])
        _, client = _fake_client(resp)
        monkeypatch.setattr("google.genai.Client", client)
        monkeypatch.setattr("utils.api_call_wrapper.rate_limited_call", _through)
        reasons = []
        assert await generate_cached("gemini-3.1-flash-lite", "Hello", on_blocked=reasons.append) == ""
        assert reasons == ["prompt blocked: OTHER"]

    @pytest.mark.asyncio
    async def test_generate_passes_on_blocked_through_the_seam(self):
        from adk_agents.llm_factory import generate
        seen = []
        with patch("services.cached_translator.generate_cached", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = "ok"
            await generate("gemini-3.1-flash-lite", "Hello", role="translation", on_blocked=seen.append)
            assert mock_gen.call_args.kwargs["on_blocked"] == seen.append
            await generate("gemini-3.1-flash-lite", "Hello", role="translation")
            assert "on_blocked" not in mock_gen.call_args.kwargs   # unchanged call when nobody listens


class TestBatchAndAgents:

    def test_batch_requests_carry_the_thresholds(self):
        from services.batch_translator import BatchTranslator
        from services.prompt_assembler import TranslationRequest
        req = TranslationRequest(system_instruction="SYS", prompt="P", response_schema=None, expected_count=1,
                                 structured=False, index_map=[0])
        inline = BatchTranslator._to_inline_request(req, "gemini-3.1-flash-lite")
        assert set(_thresholds(inline.config.safety_settings).values()) == {"OFF"}

    @pytest.mark.asyncio
    async def test_adk_agents_send_the_thresholds(self, monkeypatch):
        from google.adk.models.google_llm import Gemini
        from google.adk.models.llm_request import LlmRequest
        from adk_agents.llm_factory import SafetyAwareGemini, create_model
        seen = []

        async def parent(self, llm_request, stream=False):
            seen.append(llm_request.config)
            yield "response"

        monkeypatch.setattr(Gemini, "generate_content_async", parent)
        model = create_model("gemini-3.1-flash-lite", role="translation")
        assert isinstance(model, SafetyAwareGemini)
        out = [r async for r in model.generate_content_async(LlmRequest(model="gemini-3.1-flash-lite", contents=[]))]
        assert out == ["response"]
        assert set(_thresholds(seen[0].safety_settings).values()) == {"OFF"}
