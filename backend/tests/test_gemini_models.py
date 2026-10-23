"""Gemini API requests for every kind of model name, the -latest aliases included (utils/gemini_models.py).

Offline: the google-genai client is replaced by a fake that answers like the API, including its 400 for a
thinking setting the model does not accept.
"""

import pytest
from google.genai import errors, types

from utils import gemini_models


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    # Limiter state other tests left behind (a model latched as daily-exhausted) must not leak in.
    from utils.rate_limiter import per_model_rate_limiter
    monkeypatch.setattr(per_model_rate_limiter, "_limiters", {})
    monkeypatch.setattr(per_model_rate_limiter, "save_state", lambda *a, **k: None)
    gemini_models.forget_refusals()
    yield
    gemini_models.forget_refusals()


def _rejection(message="Budget 0 is invalid. This model only works in thinking mode."):
    return errors.ClientError(400, {"error": {"code": 400, "message": message, "status": "INVALID_ARGUMENT"}})


@pytest.mark.parametrize("model", ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash",
                                   "gemini-3.1-flash-lite", "models/gemini-flash-latest"])
def test_flash_models_and_aliases_get_the_budget(model):
    assert gemini_models.thinking_config(model, 0).thinking_budget == 0


@pytest.mark.parametrize("model", ["gemini-pro-latest", "gemini-2.5-pro"])
def test_pro_models_are_never_asked_to_stop_thinking(model):
    assert gemini_models.thinking_config(model, 0) is None
    assert gemini_models.thinking_config(model, 1024).thinking_budget == 1024


@pytest.mark.parametrize("model", ["gemini-2.0-flash", "gemini-1.5-flash", "gemma-4-31b-it", "", None])
def test_models_without_thinking_get_no_setting(model):
    assert gemini_models.thinking_config(model, 0) is None


def test_no_budget_keeps_the_model_default():
    assert gemini_models.thinking_config("gemini-flash-latest", None) is None
    assert gemini_models.thinking_config("gemini-flash-latest", -1) is None


def test_a_refusal_is_remembered_and_only_thinking_400s_count():
    cfg = types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_budget=0))
    assert not gemini_models.drop_rejected_thinking("gemini-flash-latest", cfg, _rejection("API key not valid"))
    assert not gemini_models.drop_rejected_thinking("gemini-flash-latest", cfg, RuntimeError("thinking too long"))
    assert cfg.thinking_config is not None

    assert gemini_models.drop_rejected_thinking("gemini-flash-latest", cfg, _rejection())
    assert cfg.thinking_config is None
    assert gemini_models.refused("gemini-flash-latest")
    assert gemini_models.thinking_config("gemini-flash-latest", 0) is None
    assert gemini_models.thinking_config("gemini-flash-lite-latest", 0) is not None  # other models unaffected


class FakeModels:
    """client.models: refuses a thinking setting for the models in ``refuse``, otherwise answers."""

    def __init__(self, refuse=()):
        self.refuse = set(refuse)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(config.thinking_config)
        if model in self.refuse and config.thinking_config is not None:
            raise _rejection()
        return types.GenerateContentResponse(candidates=[types.Candidate(
            content=types.Content(role="model", parts=[types.Part(text="1| Γεια σου.")]))])


@pytest.fixture
def fake_client(monkeypatch):
    holder = {}

    def make(refuse=()):
        holder["models"] = FakeModels(refuse)
        monkeypatch.setattr("google.genai.Client", lambda *a, **k: type("C", (), {"models": holder["models"]})())
        return holder["models"]
    return make


@pytest.mark.asyncio
async def test_api_call_to_a_latest_alias_sends_budget_zero(fake_client):
    from services.cached_translator import generate_cached
    models = fake_client()
    text = await generate_cached("gemini-flash-latest", "1| Hello.", thinking_budget=0, role="translation")
    assert text == "1| Γεια σου."
    assert [c.thinking_budget for c in models.calls] == [0]


@pytest.mark.asyncio
async def test_an_alias_that_refuses_the_budget_is_asked_again_without_it(fake_client):
    from services.cached_translator import generate_cached
    models = fake_client(refuse={"gemini-flash-latest"})
    first = await generate_cached("gemini-flash-latest", "1| Hello.", thinking_budget=0, role="translation")
    second = await generate_cached("gemini-flash-latest", "1| Hello.", thinking_budget=0, role="translation")
    assert first == second == "1| Γεια σου."
    # refused once, retried without; the next request goes without the setting straight away
    assert [c is not None for c in models.calls] == [True, False, False]


@pytest.mark.asyncio
async def test_other_400s_are_not_retried(fake_client, monkeypatch):
    from services.cached_translator import generate_cached
    models = fake_client()

    def bad_key(model, contents, config):
        models.calls.append(config.thinking_config)
        raise _rejection("API key not valid. Please pass a valid API key.")

    monkeypatch.setattr(models, "generate_content", bad_key)
    with pytest.raises(errors.ClientError):
        await generate_cached("gemini-flash-latest", "1| Hello.", thinking_budget=0, role="translation")
    assert len(models.calls) == 1


@pytest.mark.asyncio
async def test_agents_retry_without_a_refused_thinking_setting(monkeypatch):
    from unittest.mock import MagicMock
    from adk_agents.llm_factory import create_model

    sent = []

    async def fake_super(self, req, stream=False):
        sent.append(req.config.thinking_config)
        if req.config.thinking_config is not None:
            raise _rejection()
        yield "answer"

    monkeypatch.setattr("google.adk.models.google_llm.Gemini.generate_content_async", fake_super)
    model = create_model("gemini-flash-latest", role="translation")
    for _ in range(2):
        req = MagicMock()
        req.config = None
        assert [r async for r in model.generate_content_async(req)] == ["answer"]
    assert [c is not None for c in sent] == [True, False, False]


@pytest.mark.asyncio
async def test_unset_sampling_is_not_sent(fake_client):
    """Temperature and friends are left to the model (Google's defaults) unless someone set them."""
    from services.cached_translator import generate_cached
    sent = []
    models = fake_client()
    real = models.generate_content
    models.generate_content = lambda model, contents, config: sent.append(config) or real(model, contents, config)
    await generate_cached("gemini-flash-latest", "1| Hello.", thinking_budget=0, role="translation")
    await generate_cached("gemini-flash-latest", "1| Hello.", temperature=0.7, thinking_budget=0, role="translation")
    assert sent[0].temperature is None and sent[0].top_k is None and sent[0].top_p is None
    assert sent[1].temperature == 0.7


def test_agents_get_no_sampling_unless_set():
    from adk_agents.llm_factory import create_model
    cfg = getattr(create_model("gemini-flash-latest", role="review"), "generation_config", None) or {}
    assert not {"temperature", "top_k", "top_p"} & set(cfg)
